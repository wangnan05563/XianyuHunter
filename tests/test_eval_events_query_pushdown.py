"""A2 回归：评估族只读 GET 的取数口径（等值 eval.scored，不多读通知事件、不发无人用的 COUNT）

判据来源 `perf/2026-10-31-full-api/a2_evals_probe.py` / `a2_enrich_probe.py`：
- 真实库 18 388 条 events 里 `eval.%` 命中 1867 条，但其中 1490 条是 NotifierHub 推送
  钉钉时写入的 `eval.passed`（payload=None、stage=notify），路由紧接着就在 Python 端丢弃；
- `type LIKE 'eval.%' ORDER BY created_at DESC LIMIT n` 走 `ix_events_created_type` 全表扫，
  等值才走 `ix_events_type_created`；
- 方法附带的 `count(*)` 在评估族所有只读调用点都被 `rows, _ =` 丢弃。
本文件钉住四件事：等值语义、单趟 SQL、只读端点的行为不变、以及**两个 POST 路径刻意保留宽口径**。
"""
from __future__ import annotations

import ast
import json
import tempfile
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine

from xianyu_hunter.config import Settings
from xianyu_hunter.infra.repository import Repository
from xianyu_hunter.web.app import app
from xianyu_hunter.web.deps import get_container
from xianyu_hunter.web.routes.evaluations_common import (
    _EVAL_SCORED_TYPE,
    _EVAL_TYPE_PREFIX,
    load_eval_scored_events,
)

_TEST_TOKEN = "test-token-a2-eval-pushdown"

_ROUTES_DIR = Path(__file__).resolve().parents[1] / "backend" / "xianyu_hunter" / "web" / "routes"


@pytest.fixture
def tmp_repo() -> Repository:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        repo = Repository(str(Path(d) / "test.db"))
        yield repo
        repo.engine.dispose()


@pytest.fixture
def client(tmp_repo: Repository, monkeypatch) -> TestClient:
    test_settings = Settings(web_token=_TEST_TOKEN)

    def _fake_settings():
        return test_settings

    monkeypatch.setattr("xianyu_hunter.config.get_settings", _fake_settings)
    monkeypatch.setattr("xianyu_hunter.web.app.get_settings", _fake_settings, raising=False)

    class _FakeContainer:
        def __init__(self, repo):
            self.repo = repo
            self.collector = None
            self.browser = None
            self.price_strategy = None

    app.dependency_overrides[get_container] = lambda: _FakeContainer(tmp_repo)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    # WEB_TOKEN 直通时中间件把 request.state.user_id 置为 "default"，
    # 所以种子事件的归属也必须是 default（与生产多账号口径一致）
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


def _seed_scored(repo: Repository, item_id: str, *, task_id: str = "t1", score: int = 80,
                 price: float = 100.0, user_id: str = "default") -> None:
    repo.upsert_eval_event({
        "type": "eval.scored",
        "task_id": task_id,
        "item_id": item_id,
        "stage": "eval",
        "level": "info",
        "message": f"{item_id} 评估分 {score}",
        "payload": json.dumps({
            "task_id": task_id, "item_id": item_id, "item_title": f"DDR4 商品 {item_id}",
            "item_price": price, "score": score, "is_passed": True,
        }, ensure_ascii=False),
    }, user_id=user_id)


def _seed_notify(repo: Repository, item_id: str, *, task_id: str = "t1",
                 user_id: str = "default") -> None:
    """NotifierHub 推钉钉时写入的 eval.passed：payload=None、stage=notify"""
    repo.save_event({
        "type": "eval.passed",
        "task_id": task_id,
        "item_id": item_id,
        "stage": "notify",
        "level": "info",
        "message": f"{item_id} 已推送通知",
        "payload": None,
        "user_id": user_id,
    })


# ============== 仓储层：等值 + 单趟 ==============
def test_list_events_by_type_is_equality_not_prefix(tmp_repo: Repository) -> None:
    """等值语义：`eval.scored` 不能顺带命中 `eval.scored_extra`（LIKE 'eval.scored%' 会）"""
    _seed_scored(tmp_repo, "i1")
    _seed_notify(tmp_repo, "i2")
    tmp_repo.save_event({
        "type": "eval.scored_extra", "task_id": "t1", "item_id": "i3",
        "stage": "eval", "level": "info", "message": "同前缀的其它类型",
        "payload": None, "user_id": "default",
    })

    rows = tmp_repo.list_events_by_type(_EVAL_SCORED_TYPE)
    assert {r["item_id"] for r in rows} == {"i1"}, \
        "等值过滤只能取 eval.scored；前缀语义会把 eval.scored_extra / eval.passed 一起带进来"

    prefix_rows, _ = tmp_repo.list_events_by_type_prefix(_EVAL_SCORED_TYPE)
    assert {r["item_id"] for r in prefix_rows} == {"i1", "i3"}, \
        "对照组：LIKE 'eval.scored%' 仍会带出 eval.scored_extra"


def test_list_events_by_type_issues_exactly_one_statement(tmp_repo: Repository) -> None:
    """单趟：不再附发被调用方丢弃的 count(*)"""
    _seed_scored(tmp_repo, "i1")
    _seed_notify(tmp_repo, "i2")
    statements: list[str] = []

    @sa_event.listens_for(tmp_repo.engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(str(statement).split())[:40])

    try:
        tmp_repo.list_events_by_type(_EVAL_SCORED_TYPE)
        exact_run = list(statements)
        statements.clear()
        tmp_repo.list_events_by_type_prefix(_EVAL_TYPE_PREFIX)
        prefix_run = list(statements)
    finally:
        sa_event.remove(tmp_repo.engine, "before_cursor_execute", _capture)

    assert len(exact_run) == 1, f"等值取数应当只有一条 SQL，实际 {exact_run}"
    assert "count(" not in exact_run[0].lower()
    assert len(prefix_run) == 2, "前缀方法仍带 COUNT（POST 路径还在用，不能改它的形状）"
    assert any("count(" in s.lower() for s in prefix_run)


def test_list_events_by_type_honours_user_scope_and_order(tmp_repo: Repository) -> None:
    _seed_scored(tmp_repo, "mine")
    _seed_scored(tmp_repo, "theirs", user_id="other")
    rows = tmp_repo.list_events_by_type(_EVAL_SCORED_TYPE, user_id="default")
    assert [r["item_id"] for r in rows] == ["mine"]
    assert all(r["user_id"] == "default" for r in rows)

    both = tmp_repo.list_events_by_type(_EVAL_SCORED_TYPE)
    # user_id=None 是「不过滤」而不是「查不到」，与 list_events 的口径一致
    assert {r["item_id"] for r in both} == {"mine", "theirs"}
    assert len(both) == 2


def test_load_eval_scored_events_helper(tmp_repo: Repository, monkeypatch) -> None:
    """共享入口返回 list（不是 (rows, total) 元组），且用等值类型"""
    _seed_scored(tmp_repo, "i1")
    _seed_notify(tmp_repo, "i2")
    seen: list[str] = []
    real = Repository.list_events_by_type

    def _spy(self, type_value, *a, **k):
        seen.append(type_value)
        return real(self, type_value, *a, **k)

    monkeypatch.setattr(Repository, "list_events_by_type", _spy)

    rows = load_eval_scored_events(tmp_repo, "default")
    assert isinstance(rows, list)
    assert [r["item_id"] for r in rows] == ["i1"]
    assert load_eval_scored_events(tmp_repo, None) == rows
    # 前缀字面量传进来会把结果变成 0 行（等值语义），所以入参必须是精确类型
    assert seen == [_EVAL_SCORED_TYPE, _EVAL_SCORED_TYPE]


def test_evaluations_list_is_scoped_to_current_user(client: TestClient, tmp_repo: Repository) -> None:
    """跨账号隔离在**端点层**也成立：路由传 user_id、仓储按它过滤，缺一即红

    仓储层那条同名的用例只证明方法本身；路由漏传（`user_id=None` = 不过滤）时
    列表会把别人的评估记录混进来，而结果看起来「只是多了几条」——不会报错。
    """
    tmp_repo.upsert_task({"id": "t1", "name": "任务", "keyword": "DDR4"})
    _seed_scored(tmp_repo, "mine")
    _seed_scored(tmp_repo, "theirs", user_id="other")

    data = client.get("/api/evaluations", params={"sold_filter": "all"}, headers=_auth()).json()
    assert data["total"] == 1, f"别人的评估记录混进了当前账号：{[i['item_id'] for i in data['items']]}"
    assert {i["item_id"] for i in data["items"]} == {"mine"}


# ============== 端点行为：混入通知事件也不改变结果 ==============
def test_evaluations_list_ignores_notify_events(client: TestClient, tmp_repo: Repository) -> None:
    tmp_repo.upsert_task({"id": "t1", "name": "任务", "keyword": "DDR4"})
    _seed_scored(tmp_repo, "i1", score=90)
    _seed_scored(tmp_repo, "i2", score=70)
    _seed_notify(tmp_repo, "i1")
    _seed_notify(tmp_repo, "i3")

    data = client.get("/api/evaluations", params={"sold_filter": "all"},
                      headers=_auth()).json()
    assert data["total"] == 2
    assert {i["item_id"] for i in data["items"]} == {"i1", "i2"}


def test_evaluations_list_rejects_prefix_loader(client: TestClient, tmp_repo: Repository,
                                                monkeypatch) -> None:
    """只读端点不得再走前缀取数：把前缀方法打成炸弹，任何残留调用都会显式失败

    为什么需要这条：`rows, _ =` 的丢弃式调用在改错时不会报错，只会静默多读 4 倍行。
    """
    tmp_repo.upsert_task({"id": "t1", "name": "任务", "keyword": "DDR4"})
    _seed_scored(tmp_repo, "i1")
    _seed_notify(tmp_repo, "i2")

    def _boom(*a, **k):
        raise AssertionError("只读端点不应再调用 list_events_by_type_prefix")

    monkeypatch.setattr(tmp_repo, "list_events_by_type_prefix", _boom)
    resp = client.get("/api/evaluations", params={"sold_filter": "all"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_brands_and_distribution_and_feedback_use_exact_loader(
        client: TestClient, tmp_repo: Repository, monkeypatch) -> None:
    tmp_repo.upsert_task({"id": "t1", "name": "任务", "keyword": "DDR4"})
    _seed_scored(tmp_repo, "i1", score=90)
    _seed_scored(tmp_repo, "i2", score=40)
    _seed_notify(tmp_repo, "i1")

    calls: list[tuple] = []
    real = Repository.list_events_by_type

    def _spy(self, type_value, *a, **k):
        calls.append((type_value, k.get("user_id")))
        return real(self, type_value, *a, **k)

    monkeypatch.setattr(Repository, "list_events_by_type", _spy)

    assert client.get("/api/evaluations/brands", headers=_auth()).status_code == 200
    assert client.get("/api/evaluations/distribution", headers=_auth()).status_code == 200
    assert client.get("/api/evaluations/feedback/stats", headers=_auth()).status_code == 200

    assert len(calls) == 3, f"三个只读端点应各取数一次，实际 {calls}"
    assert {c[0] for c in calls} == {_EVAL_SCORED_TYPE}
    assert {c[1] for c in calls} == {"default"}

    dist = client.get("/api/evaluations/distribution", headers=_auth()).json()
    # 通知事件（无 score）若被读进来会被误算成 insufficient_count（历史缺陷，勿回退）
    assert dist.get("insufficient_count", 0) == 0


# ============== 刻意保留：两个 POST 路径按 eval.* 宽口径界定范围 ==============
def _function_body(src_file: str, func_name: str) -> str:
    path = _ROUTES_DIR / src_file
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                return segment
    raise AssertionError(f"{src_file} 里找不到函数 {func_name}")


@pytest.mark.parametrize("src_file,func_name", [
    ("evaluations_batch.py", "_collect_evaluated_ids"),
    ("evaluations_recompute.py", "recompute_evaluations"),
])
def test_write_paths_keep_broad_prefix_scope(src_file: str, func_name: str) -> None:
    """batch/recompute 的「已评估范围」刻意按 eval.* 宽口径统计，改动会把它们变成漏判

    为什么用 AST 而不是跑请求：这两条路径会触发真实评估/通知，不该在单测里执行。
    """
    body = textwrap.dedent(_function_body(src_file, func_name))
    assert "list_events_by_type_prefix" in body, (
        f"{src_file}::{func_name} 必须继续用 list_events_by_type_prefix（宽口径），"
        "改成等值会让 eval.passed 覆盖的条目从「已评估」范围里消失"
    )


@pytest.mark.parametrize("src_file,func_name", [
    ("evaluations_list.py", "list_evaluations"),
    ("evaluations_list.py", "list_evaluation_brands"),
    ("evaluations_distribution.py", "evaluations_distribution"),
    ("evaluations_feedback.py", "feedback_stats"),
])
def test_read_routes_use_shared_loader(src_file: str, func_name: str) -> None:
    body = textwrap.dedent(_function_body(src_file, func_name))
    assert "load_eval_scored_events" in body, f"{src_file}::{func_name} 应使用共享等值取数入口"
    assert "list_events_by_type_prefix" not in body, f"{src_file}::{func_name} 不应回退到前缀取数"
