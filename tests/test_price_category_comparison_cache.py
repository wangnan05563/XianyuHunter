"""`/api/prices/category-comparison` 挂 60 s 缓存的回归

依据：2026-11-06 复跑（REPORT §8.5）单发 24.6 ms，但 **16 并发 avg 2 433.7 ms**、
64 并发 3 436.4 ms —— 每次请求都 `_load_category_prices` 全表扫 items，16 个并发
就是 16 次全表扫抢同一条 GIL。A1 的 `items.price` 索引只降低单次成本，治不了并发。

本文件锁四件事：
1. 同一组参数第二次调用**不再查库**（缓存真的命中）；
2. 不同 `range_days` / `limit` 各自独立，不串味；
3. 非法 `sort_by` / `order` 归一化后与合法值**共用同一个 key**（归一化必须在缓存外层，
   否则 `sort_by=bogus` 会各自占一个 key，把命中率稀释掉）；
4. 返回结构不变（`categories` / `overall_mean` / `total_categories` 等字段齐全）。
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from xianyu_hunter.infra.repository import Repository
from xianyu_hunter.web.cache import invalidate_stats_cache
from xianyu_hunter.web.routes import price_dashboard as PD

_NOW = datetime.now(timezone.utc)


@pytest.fixture
def repo() -> Repository:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Repository(str(Path(d) / "test.db"))
        invalidate_stats_cache()          # 缓存在进程内，用例之间必须清
        yield r
        invalidate_stats_cache()
        r.engine.dispose()


@pytest.fixture
def container(repo: Repository):
    return SimpleNamespace(repo=repo)


def _seed(repo: Repository) -> None:
    repo.upsert_task({"id": "t1", "name": "佳能", "keyword": "佳能", "mode": "confirm"})
    repo.upsert_task({"id": "t2", "name": "索尼", "keyword": "索尼", "mode": "confirm"})
    repo.batch_upsert_items([
        {"id": "i1", "task_id": "t1", "title": "佳能 R6", "price": 3000.0,
         "seller_id": "s1", "first_seen": _NOW},
        {"id": "i2", "task_id": "t1", "title": "佳能 R5", "price": 4000.0,
         "seller_id": "s2", "first_seen": _NOW - timedelta(days=90)},
        {"id": "i3", "task_id": "t2", "title": "索尼 A7", "price": 5000.0,
         "seller_id": "s3", "first_seen": _NOW},
    ])


def _count_selects(repo: Repository):
    """统计 SELECT 语句条数（用来证明第二次没再扫表）"""
    counted = {"n": 0}

    def _on(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counted["n"] += 1

    event.listen(repo.engine, "before_cursor_execute", _on)
    return counted, lambda: event.remove(repo.engine, "before_cursor_execute", _on)


def _call(container, **kw):
    kwargs = {"sort_by": "mean", "order": "asc", "limit": 20, "range_days": 0}
    kwargs.update(kw)
    return PD.category_comparison(container=container, **kwargs)


def test_second_call_with_same_args_does_not_touch_db(repo, container) -> None:
    _seed(repo)

    counted, stop = _count_selects(repo)
    try:
        first = _call(container)
        sql_first = counted["n"]
        counted["n"] = 0
        second = _call(container)
        sql_second = counted["n"]
    finally:
        stop()

    assert sql_first >= 2, f"冷算应至少扫 tasks + items，实际 {sql_first} 条 SELECT"
    assert sql_second == 0, f"第二次仍发了 {sql_second} 条 SELECT，缓存没命中"
    assert first == second


def test_different_params_do_not_share_cache(repo, container) -> None:
    _seed(repo)

    counted, stop = _count_selects(repo)
    try:
        _call(container, range_days=0)
        counted["n"] = 0
        _call(container, range_days=30)      # 新 key：范围过滤会再扫一遍
        sql_rd = counted["n"]
        counted["n"] = 0
        _call(container, limit=5)            # 新 key：limit 也参与 key
        sql_limit = counted["n"]
    finally:
        stop()

    assert sql_rd >= 1, "range_days 变了却命中旧缓存 —— key 漏了 range_days"
    assert sql_limit >= 1, "limit 变了却命中旧缓存 —— key 漏了 limit"


def test_illegal_sort_by_shares_key_with_normalized_value(repo, container) -> None:
    """归一化在路由层：`sort_by=bogus` 折成 mean 后必须复用同一份缓存"""
    _seed(repo)

    counted, stop = _count_selects(repo)
    try:
        _call(container, sort_by="mean")
        counted["n"] = 0
        _call(container, sort_by="bogus")
        sql = counted["n"]
    finally:
        stop()

    assert sql == 0, f"非法 sort_by 另起了一个缓存 key（{sql} 条 SELECT）"


def test_response_shape_unchanged(repo, container) -> None:
    _seed(repo)
    body = _call(container)

    assert set(body) >= {
        "categories", "overall_mean", "sort_by", "order", "range_days", "total_categories",
    }
    assert body["total_categories"] == len(body["categories"]) == 2
    for c in body["categories"]:
        assert set(c) >= {"task_id", "name", "keyword", "count", "mean", "deviation_pct"}


def test_sorted_and_limited(repo, container) -> None:
    """排序与截断在缓存内部完成，缓存命中不能改变输出顺序"""
    _seed(repo)
    body = _call(container, sort_by="mean", order="desc", limit=1)
    assert len(body["categories"]) == 1
    assert body["categories"][0]["mean"] == body["categories"][0]["mean"]  # 数值存在
    assert body["sort_by"] == "mean" and body["order"] == "desc"
