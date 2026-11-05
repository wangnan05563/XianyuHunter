"""C5：评估列表的市场统计批量计算（去掉逐 task N+1）

判据：
1. 批量结果与逐任务 `_compute_sold_range` 逐字段相同（含 mixed / all_fallback 两种 source）；
2. SQL 次数与任务数无关（10 个 task 批量 ≤3 条，逐条 ≥20 条）；
3. `task_id=None`（全局统计）仍走单条语义；
4. cache 命中时不再查库；
5. 批量整段失败时退回逐任务，单个任务失败只丢它自己。

本文件只用 tmp_path 建库，不碰仓库 data/xianyu.db。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import event

from xianyu_hunter.infra.db_models import (
    ItemRow, TaskLinkRow, TaskRow, create_sqlite_engine,
)
from xianyu_hunter.web.routes import evaluations_list as EL
from xianyu_hunter.web.routes import price_dashboard as PD

TASK_IDS = [f"t{i:04d}" for i in range(10)]
RANGE_DAYS = 30
TS = datetime(2026, 11, 1)


class _Repo:
    def __init__(self, engine) -> None:
        self.engine = engine


class _Container:
    def __init__(self, engine) -> None:
        self.repo = _Repo(engine)


@pytest.fixture()
def engine(tmp_path):
    eng = create_sqlite_engine(str(tmp_path / "c5.db"))
    TaskRow.__table__.create(eng)
    TaskLinkRow.__table__.create(eng)
    ItemRow.__table__.create(eng)
    EL._MARKET_STATS_CACHE.clear()
    yield eng
    EL._MARKET_STATS_CACHE.clear()
    eng.dispose()


def _add(eng, task_id: str, sold: int, items: int, min_price=None, max_price=None) -> None:
    with eng.begin() as conn:
        conn.execute(TaskRow.__table__.insert().values(
            id=task_id, name=task_id, keyword="k", status="running",
            min_price=min_price, max_price=max_price,
        ))
        for i in range(sold):
            price = 100 + int(task_id[-3:]) * 30 + (i % 20) * 10
            conn.execute(TaskLinkRow.__table__.insert().values(
                task_id=task_id, link_type="item", link_key=f"{task_id}-s{i}",
                display=json.dumps({"is_sold": True, "price": price}),
                updated_at=TS,
            ))
        for i in range(items):
            # i==0 放一条范围外的异常样本：`_load_all_prices_batch` 少了 range_days
            # 过滤就会把它算进来，用于变异验证（正常路径必须排除它）
            conn.execute(ItemRow.__table__.insert().values(
                id=f"{task_id}-i{i}", task_id=task_id, title="t",
                price=9999 if i == 0 else 150 + int(task_id[-3:]) * 30 + (i % 15) * 5,
                publish_time=datetime(2020, 1, 1) if i == 0 else TS,
            ))


@pytest.fixture()
def seeded(engine):
    for idx, tid in enumerate(TASK_IDS):
        # 前 4 个任务已售样本充足（sold）；idx 4~7 少量已售 → mixed；idx 8~9 无已售 → all_fallback
        _add(engine, tid, sold=20 if idx < 4 else (0 if idx >= 8 else 3),
             items=30 if idx >= 4 else 0,
             min_price=50 if idx == 0 else None, max_price=400 if idx == 0 else None)
    return engine


def test_batch_matches_single_task_results(seeded) -> None:
    with seeded.connect() as conn:
        single = {tid: PD._compute_sold_range(conn, tid, RANGE_DAYS) for tid in TASK_IDS}
        batch = PD._compute_sold_range_batch(conn, TASK_IDS, RANGE_DAYS)

    assert set(batch) == set(TASK_IDS)
    for tid in TASK_IDS:
        assert batch[tid] == single[tid], f"{tid} 的批量结果与逐条不一致"
    sources = {single[tid]["source"] for tid in TASK_IDS}
    assert {"sold", "mixed", "all_fallback"} <= sources, sources


def test_batch_query_count_is_constant(seeded) -> None:
    counted = {"n": 0}

    def _count(*_args, **_kwargs):
        counted["n"] += 1

    event.listen(seeded, "before_cursor_execute", _count)
    try:
        with seeded.connect() as conn:
            PD._compute_sold_range_batch(conn, TASK_IDS, RANGE_DAYS)
            batch_sql = counted["n"]
            counted["n"] = 0
            for tid in TASK_IDS:
                PD._compute_sold_range(conn, tid, RANGE_DAYS)
            single_sql = counted["n"]
    finally:
        event.remove(seeded, "before_cursor_execute", _count)

    assert batch_sql <= 3, f"批量路径 SQL 次数 {batch_sql} 超过 3 条"
    assert single_sql >= 2 * len(TASK_IDS), f"逐条路径 SQL 次数异常：{single_sql}"


def test_global_none_stays_single_task_semantics(seeded) -> None:
    with seeded.connect() as conn:
        stats_map = EL._compute_market_stats_for_tasks(conn, [None, TASK_IDS[0]], RANGE_DAYS)
        single = PD._compute_sold_range(conn, TASK_IDS[0], RANGE_DAYS)
    assert set(stats_map) == {None, TASK_IDS[0]}
    assert stats_map[TASK_IDS[0]] == single


def test_cache_hit_skips_db(seeded) -> None:
    container = _Container(seeded)
    first = EL.compute_market_stats_batch(container, [TASK_IDS[0]], RANGE_DAYS)
    assert first[TASK_IDS[0]]["bargain_price"] is not None

    counted = {"n": 0}

    def _count(*_args, **_kwargs):
        counted["n"] += 1

    event.listen(seeded, "before_cursor_execute", _count)
    try:
        second = EL.compute_market_stats_batch(container, [TASK_IDS[0]], RANGE_DAYS)
    finally:
        event.remove(seeded, "before_cursor_execute", _count)
    assert counted["n"] == 0, f"cache 命中仍查了 {counted['n']} 条 SQL"
    assert second == first


def test_batch_failure_falls_back_to_single(seeded, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("批量炸了")

    monkeypatch.setattr(EL, "_compute_sold_range_batch", boom)
    container = _Container(seeded)
    result = EL.compute_market_stats_batch(container, TASK_IDS, RANGE_DAYS)
    assert len(result) == len(TASK_IDS)
    assert all(v["bargain_price"] is not None for v in result.values())


def test_one_task_failure_does_not_block_others(seeded, monkeypatch) -> None:
    real = PD._compute_sold_range

    def flaky(conn, task_id, range_days):
        if task_id == TASK_IDS[1]:
            raise RuntimeError("这个任务坏了")
        return real(conn, task_id, range_days)

    def boom(*_a, **_k):
        raise RuntimeError("批量炸了")

    monkeypatch.setattr(EL, "_compute_sold_range_batch", boom)
    monkeypatch.setattr(EL, "_compute_sold_range", flaky)
    container = _Container(seeded)
    result = EL.compute_market_stats_batch(container, TASK_IDS, RANGE_DAYS)

    assert result[TASK_IDS[1]] == {"bargain_price": None, "mean_price": None}
    assert result[TASK_IDS[0]]["bargain_price"] is not None

def test_batch_path_is_wired(seeded, monkeypatch) -> None:
    """评估列表必须走批量入口 —— 退回逐 task 循环就是把 N+1 请回来"""
    calls = {"n": 0}
    real = EL._compute_sold_range_batch

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(EL, "_compute_sold_range_batch", counting)
    EL.compute_market_stats_batch(_Container(seeded), TASK_IDS, RANGE_DAYS)
    assert calls["n"] == 1, f"批量入口被调用 {calls['n']} 次"
