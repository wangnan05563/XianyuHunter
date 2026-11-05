"""D3 判据：写侧「逐条独立事务」与「单事务 executemany」的真实差距

D3 原文把写侧饱和归给「逐条 UPSERT」，但先清点（`d3_write_inventory.py`）发现
压测写组 61.6% 的样本是夹具 4xx、根本不写库，真写接口单发也都在 30 ms 以内。
批量写真正有 N+1 形态的落点是 `recompute`：它按事件逐条 `update_event_payload`
（每条一个事务），无 eval 事件时又按 task_link 逐条 `upsert_item` + `upsert_eval_event`。

本探针只量仓储层这两种写形态的差距，不启动服务、不跑 evaluator：
- events：500 / 2 000 条 payload UPDATE，逐条 `engine.begin()` vs 单事务 executemany；
- items：500 / 2 000 条 UPSERT，逐条 `upsert_item` vs 已有的 `batch_upsert_items`。

口径：中位耗时 + SQL 语句条数（`before_cursor_execute`），SQL 条数用来证明
「一次事务」而不是「碰巧更快」。
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/d3-write-probe")
ROUNDS = 5

sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import bindparam, event, select  # noqa: E402

from xianyu_hunter.infra.db_models import EventRow  # noqa: E402
from xianyu_hunter.infra.repository import Repository  # noqa: E402

NOW = datetime.now(timezone.utc)


def _payload(i: int) -> str:
    return json.dumps({
        "task_id": f"t{i % 20:04d}", "item_id": f"i{i:05d}", "item_title": f"测试商品 {i}",
        "item_price": 100.0 + i, "seller_id": f"s{i % 50}", "seller_nick": "卖家",
        "score": 80, "risk_level": "low", "is_passed": True, "data_quality": "good",
        "recomputed_at": NOW.isoformat(),
    }, ensure_ascii=False)


def _bench(fn, rounds: int = ROUNDS) -> tuple[float, int, int]:
    """返回 (中位毫秒, SQL 条数, 影响行数)"""
    counted = {"n": 0}

    def _count(*_a, **_k):
        counted["n"] += 1

    samples = []
    affected = 0
    for r in range(rounds):
        counted["n"] = 0
        event.listen(fn_engine[0], "before_cursor_execute", _count)
        t0 = time.perf_counter()
        affected = fn()
        samples.append((time.perf_counter() - t0) * 1000)
        event.remove(fn_engine[0], "before_cursor_execute", _count)
        if r == 0:
            sql = counted["n"]
    return statistics.median(samples), sql, affected


fn_engine: list = [None]


def _single_updates(repo: Repository, ids: list[int]) -> int:
    for i in ids:
        repo.update_event_payload(i, _payload(i))
    return len(ids)


def _bulk_updates(repo: Repository, ids: list[int]) -> int:
    table = EventRow.__table__
    stmt = (
        table.update()
        .where(table.c.id == bindparam("_id"))
        .values(payload=bindparam("_payload"))
    )
    with repo.engine.begin() as conn:
        conn.execute(stmt, [{"_id": i, "_payload": _payload(i)} for i in ids])
    return len(ids)


def _single_items(repo: Repository, count: int) -> int:
    for i in range(count):
        repo.upsert_item({
            "id": f"item{i:05d}", "task_id": "t0000", "title": f"商品 {i}",
            "price": 100.0 + i, "seller_id": f"s{i % 50}", "first_seen": NOW,
        })
    return count


def _bulk_items(repo: Repository, count: int) -> int:
    return repo.batch_upsert_items([
        {"id": f"item{i:05d}", "task_id": "t0000", "title": f"商品 {i}",
         "price": 100.0 + i, "seller_id": f"s{i % 50}", "first_seen": NOW}
        for i in range(count)
    ])


def main() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    db = str(SCRATCH / "d3.db")
    repo = Repository(db_path=db)
    fn_engine[0] = repo.engine

    out: dict[str, dict] = {}
    for count in (500, 2000):
        with repo.engine.begin() as conn:
            conn.execute(EventRow.__table__.delete().where(EventRow.type == "eval.scored"))
            conn.execute(EventRow.__table__.insert(), [
                {"type": "eval.scored", "task_id": f"t{i % 20:04d}", "item_id": f"i{i:05d}",
                 "stage": "eval", "level": "info", "message": "seed",
                 "payload": _payload(i), "created_at": NOW}
                for i in range(count)
            ])
        with repo.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(select(EventRow.id)).all()]

        ms_s, sql_s, _ = _bench(lambda: _single_updates(repo, ids))
        ms_b, sql_b, _ = _bench(lambda: _bulk_updates(repo, ids))
        out[f"events_{count}"] = {
            "single_ms": round(ms_s, 2), "single_sql": sql_s,
            "bulk_ms": round(ms_b, 2), "bulk_sql": sql_b,
            "speedup": round(ms_s / ms_b, 2) if ms_b else 0.0,
        }

        ms_s, sql_s, _ = _bench(lambda: _single_items(repo, count))
        ms_b, sql_b, _ = _bench(lambda: _bulk_items(repo, count))
        out[f"items_{count}"] = {
            "single_ms": round(ms_s, 2), "single_sql": sql_s,
            "bulk_ms": round(ms_b, 2), "bulk_sql": sql_b,
            "speedup": round(ms_s / ms_b, 2) if ms_b else 0.0,
        }

    results = SCRATCH / "d3_write_probe.json"
    results.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in out.items():
        print(f"  {k:<14} 逐条 {v['single_ms']:>8.1f} ms / {v['single_sql']:>4} SQL"
              f"   →  单事务 {v['bulk_ms']:>7.1f} ms / {v['bulk_sql']:>3} SQL"
              f"   {v['speedup']:>5.2f}×")
    print(f"\n结果写入 {results}（临时目录，不入库）")
    repo.engine.dispose()


if __name__ == "__main__":
    main()
