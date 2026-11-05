"""D3 回归：recompute 的 payload 批量写入

逐条 `update_event_payload` 每条一个 `engine.begin()`：500 条实测 99.4 ms / 500 SQL，
合并成单事务后 6.0 ms / 1 SQL（`perf/2026-10-31-full-api/d3_write_probe.py`）。
本文件锁四件事：
1. 单事务：批量方法只发 1 条 UPDATE；
2. 语义不变：每条 payload 都被更新，且 UPDATE 只按 id 命中自己的行；
3. 隔离不变：`user_id` 过滤仍然生效；
4. 接线：`recompute_evaluations` 主循环不再逐条调用 `update_event_payload`。
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from xianyu_hunter.config import Settings
from xianyu_hunter.container import Container
from xianyu_hunter.infra.db_models import EventRow
from xianyu_hunter.infra.repository import Repository
from xianyu_hunter.modules.price_strategy import PriceConfig, PriceStrategy
from xianyu_hunter.web.app import app
from xianyu_hunter.web.deps import get_container
from xianyu_hunter.web.routes import evaluations_recompute as ER

_TEST_TOKEN = "test-token-recompute-batch"
_NOW = datetime.now(timezone.utc)


@pytest.fixture
def tmp_repo() -> Repository:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        repo = Repository(str(Path(d) / "test.db"))
        yield repo
        repo.engine.dispose()


def _seed_events(repo: Repository, count: int, user_id: str = "default") -> list[int]:
    with repo.engine.begin() as conn:
        conn.execute(EventRow.__table__.insert(), [
            {
                "type": "eval.scored", "task_id": "t1", "item_id": f"i{n:04d}",
                "stage": "eval", "level": "info", "message": "seed",
                "payload": json.dumps({"item_id": f"i{n:04d}", "item_title": f"商品 {n}"}),
                "created_at": _NOW, "user_id": user_id,
            }
            for n in range(count)
        ])
    with repo.engine.connect() as conn:
        return [r[0] for r in conn.execute(
            EventRow.__table__.select().with_only_columns(EventRow.id)
            .where(EventRow.user_id == user_id)
        ).all()]


def _count_updates(repo: Repository):
    """返回 (计数器, 停止函数)：只统计 UPDATE events 的语句"""
    counted = {"n": 0}

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE EVENTS"):
            counted["n"] += 1

    event.listen(repo.engine, "before_cursor_execute", _on_exec)
    return counted, lambda: event.remove(repo.engine, "before_cursor_execute", _on_exec)


def test_batch_update_uses_single_statement(tmp_repo: Repository) -> None:
    ids = _seed_events(tmp_repo, 3)
    counted, stop = _count_updates(tmp_repo)
    try:
        n = tmp_repo.batch_update_event_payloads([
            (i, json.dumps({"n": i})) for i in ids
        ])
    finally:
        stop()

    assert n == 3
    assert counted["n"] == 1, f"逐条写会发 {len(ids)} 条 UPDATE，实际 {counted['n']} 条"
    with tmp_repo.engine.connect() as conn:
        payloads = [r[0] for r in conn.execute(
            EventRow.__table__.select().with_only_columns(EventRow.payload)
        ).all()]
    assert sorted(json.loads(p)["n"] for p in payloads) == sorted(ids)


def test_batch_update_respects_user_id(tmp_repo: Repository) -> None:
    alice = _seed_events(tmp_repo, 1, user_id="alice")
    bob = _seed_events(tmp_repo, 1, user_id="bob")

    tmp_repo.batch_update_event_payloads([(bob[0], '{"who":"alice-tried"}')], user_id="alice")

    with tmp_repo.engine.connect() as conn:
        rows = {r[0]: r[1] for r in conn.execute(
            EventRow.__table__.select().with_only_columns(EventRow.id, EventRow.payload)
        ).all()}
    assert "who" not in rows[bob[0]], "user_id 过滤失效：批量写改到了别人的行"
    assert json.loads(rows[alice[0]]) == {"item_id": "i0000", "item_title": "商品 0"}


def test_batch_update_empty_is_noop(tmp_repo: Repository) -> None:
    counted, stop = _count_updates(tmp_repo)
    try:
        assert tmp_repo.batch_update_event_payloads([]) == 0
    finally:
        stop()
    assert counted["n"] == 0


# ==================== 端到端：/api/evaluations/recompute ====================

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
            self.evaluator = SimpleNamespace(evaluate=lambda detail, seller: SimpleNamespace(
                score=75,
                risk_level=SimpleNamespace(value="medium"),
                dimension_scores={},
                reject_reasons=[],
                is_passed=True,
                data_quality="ok",
            ))
            self.price_strategy = PriceStrategy(PriceConfig())
            self.build_task_price_strategy = lambda raw: Container.build_task_price_strategy(
                self, raw
            )

    app.dependency_overrides[get_container] = lambda: _FakeContainer(tmp_repo)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_recompute(repo: Repository, count: int) -> None:
    repo.upsert_task({"id": "t1", "name": "任务", "keyword": "k", "mode": "confirm"})
    with repo.engine.begin() as conn:
        conn.execute(EventRow.__table__.insert(), [
            {
                "type": "eval.scored", "task_id": "t1", "item_id": f"i{n:04d}",
                "stage": "eval", "level": "info", "message": "old",
                "payload": json.dumps({
                    "task_id": "t1", "item_id": f"i{n:04d}", "item_title": f"商品 {n}",
                    "item_price": 100.0 + n, "seller_id": "s1", "seller_nick": "卖家",
                    "score": 10, "risk_level": "high",
                }, ensure_ascii=False),
                "created_at": _NOW, "user_id": "default",
            }
            for n in range(count)
        ])


def test_recompute_end_to_end_writes_payloads_in_one_statement(
    client: TestClient, tmp_repo: Repository
) -> None:
    count = 200
    _seed_recompute(tmp_repo, count)

    counted, stop = _count_updates(tmp_repo)
    try:
        resp = client.post("/api/evaluations/recompute", headers={
            "Authorization": f"Bearer {_TEST_TOKEN}"
        })
    finally:
        stop()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recomputed"] == count, body
    assert counted["n"] == 1, f"recompute 发了 {counted['n']} 条 UPDATE，批量写应只有 1 条"

    with tmp_repo.engine.connect() as conn:
        payloads = [json.loads(r[0]) for r in conn.execute(
            EventRow.__table__.select().with_only_columns(EventRow.payload)
        ).all()]
    assert len(payloads) == count
    assert all("recomputed_at" in p for p in payloads), "批量写没落到每一行"
    assert {p["score"] for p in payloads} == {75}


def _func_body(src: str, name: str) -> str:
    """截取模块内某个函数的源码（AST 太重，这里只做门禁用的粗切）"""
    m = re.search(rf"\ndef {name}\(", src)
    assert m, f"找不到函数 {name}"
    start = m.start()
    nxt = re.search(r"\n(?:@|def |class )", src[start + 1:])
    return src[start: start + 1 + nxt.start()] if nxt else src[start:]


def test_recompute_no_longer_calls_single_update() -> None:
    src = Path(ER.__file__).read_text(encoding="utf-8")
    body = _func_body(src, "recompute_evaluations")
    assert "batch_update_event_payloads" in body, "主循环没有接线到批量写"
    assert "update_event_payload(" not in body, "主循环仍在逐条 update_event_payload"
    assert "pending: list[tuple[int, str]]" in body, "缺少待写队列"
