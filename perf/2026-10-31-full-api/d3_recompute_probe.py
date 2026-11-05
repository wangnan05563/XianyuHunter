"""D3 端到端判据：500 条评估的 recompute，逐条 UPDATE vs 批量 UPDATE

A 臂复刻旧实现：跑同一条重算循环，再把每条 payload 逐条
`update_event_payload`（每条一个事务）。
B 臂就是当前 `recompute_evaluations`（循环内只登记，最后一次 executemany）。

两臂共用同一个 fake evaluator 与同一份数据，差异只在写侧事务数；
SQL 条数由 `before_cursor_execute` 统计（只数 UPDATE events）。
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/d3-recompute-probe")
ROUNDS = 3
COUNT = 500

sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import event  # noqa: E402

from xianyu_hunter.container import Container  # noqa: E402
from xianyu_hunter.infra.db_models import EventRow  # noqa: E402
from xianyu_hunter.infra.repository import Repository  # noqa: E402
from xianyu_hunter.modules.price_strategy import PriceConfig, PriceStrategy  # noqa: E402
from xianyu_hunter.web.routes import evaluations_recompute as ER  # noqa: E402

NOW = datetime.now(timezone.utc)


def _payload(n: int) -> dict:
    return {
        "task_id": "t1", "item_id": f"i{n:04d}", "item_title": f"商品 {n}",
        "item_price": 100.0 + n, "seller_id": "s1", "seller_nick": "卖家",
        "score": 10, "risk_level": "high",
    }


def _seed(repo: Repository) -> list[int]:
    with repo.engine.begin() as conn:
        conn.execute(EventRow.__table__.delete())
        conn.execute(EventRow.__table__.insert(), [
            {"type": "eval.scored", "task_id": "t1", "item_id": f"i{n:04d}",
             "stage": "eval", "level": "info", "message": "seed",
             "payload": json.dumps(_payload(n), ensure_ascii=False),
             "created_at": NOW, "user_id": "default"}
            for n in range(COUNT)
        ])
    with repo.engine.connect() as conn:
        return [r[0] for r in conn.execute(
            EventRow.__table__.select().with_only_columns(EventRow.id)
        ).all()]


def _container(repo: Repository):
    fake = SimpleNamespace()
    fake.repo = repo
    fake.evaluator = SimpleNamespace(evaluate=lambda detail, seller: SimpleNamespace(
        score=75, risk_level=SimpleNamespace(value="medium"), dimension_scores={},
        reject_reasons=[], is_passed=True, data_quality="ok",
    ))
    fake.price_strategy = PriceStrategy(PriceConfig())
    fake.build_task_price_strategy = lambda raw: Container.build_task_price_strategy(fake, raw)
    return fake


def _count_sql(repo: Repository):
    counted = {"n": 0}

    def _on(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE EVENTS"):
            counted["n"] += 1

    event.listen(repo.engine, "before_cursor_execute", _on)
    return counted, lambda: event.remove(repo.engine, "before_cursor_execute", _on)


def _arm_legacy(repo: Repository, fake, ids: list[int]) -> int:
    """复刻旧实现：同样的重算 + 逐条 UPDATE"""
    events, _ = repo.list_events_by_type_prefix(type_prefix="eval.")
    item_map, item_rows = ER._load_recompute_item_map(fake, events)
    seller_map = ER._load_recompute_seller_map(fake, item_rows)
    get_price_strategy = ER.make_price_strategy_getter(fake, log_prefix="recompute")
    pending: list[tuple[int, str]] = []
    for e in events:
        ER._recompute_single_eval(
            e, fake, item_map, seller_map, get_price_strategy, fake.evaluator,
            None, pending=pending,
        )
    for eid, payload in pending:
        repo.update_event_payload(eid, payload)
    return len(pending)


def _arm_batch(repo: Repository, fake) -> int:
    request = SimpleNamespace(state=SimpleNamespace(user_id="default"))
    result = ER.recompute_evaluations(
        request=request, task_id=None, notify=False, container=fake
    )
    return result["recomputed"]


def main() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    repo = Repository(str(SCRATCH / "d3.db"))
    repo.upsert_task({"id": "t1", "name": "任务", "keyword": "k", "mode": "confirm"})
    fake = _container(repo)

    out: dict[str, dict] = {}
    for name, arm in (("legacy_per_row", _arm_legacy), ("batch", _arm_batch)):
        samples, sqls = [], []
        for _ in range(ROUNDS):
            ids = _seed(repo)
            counted, stop = _count_sql(repo)
            try:
                t0 = time.perf_counter()
                n = arm(repo, fake, ids) if name == "legacy_per_row" else arm(repo, fake)
                samples.append((time.perf_counter() - t0) * 1000)
            finally:
                stop()
            sqls.append(counted["n"])
            assert n == COUNT, f"{name} 只处理了 {n}/{COUNT}"
        out[name] = {
            "count": COUNT,
            "median_ms": round(statistics.median(samples), 2),
            "update_sql": sqls[0],
        }

    speed = out["legacy_per_row"]["median_ms"] / out["batch"]["median_ms"]
    out["speedup"] = round(speed, 2)
    (SCRATCH / "d3_recompute_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{COUNT} 条 eval.scored，{ROUNDS} 轮中位]")
    for name in ("legacy_per_row", "batch"):
        r = out[name]
        print(f"  {name:<16} {r['median_ms']:>8.1f} ms   UPDATE {r['update_sql']:>4} 条")
    print(f"  加速 {speed:.2f}×")
    print(f"结果写入 {SCRATCH / 'd3_recompute_probe.json'}（临时目录，不入库）")
    repo.engine.dispose()


if __name__ == "__main__":
    main()
