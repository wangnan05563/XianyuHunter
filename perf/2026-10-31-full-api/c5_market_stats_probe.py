"""C5 判据：多任务评估列表的市场统计批量化前后（进程内直调，SQL 次数 + 耗时）

合成库：20 个任务 × (60 条已售 link + 60 条 items)，模拟多任务用户的评估列表
cache miss 场景。判据：
1. 批量路径 SQL 次数封顶 3 条，逐条路径 ≈ 每任务 2~3 条；
2. 20 个任务的中位耗时下降幅度。

只建临时库，不碰仓库 data/xianyu.db。
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/c5-market-stats-probe")
TASKS = 20
ROUNDS = 7
TS = datetime(2026, 11, 1)

sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import event  # noqa: E402

from xianyu_hunter.infra.db_models import (  # noqa: E402
    ItemRow, TaskLinkRow, TaskRow, create_sqlite_engine,
)


def build() -> str:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    db = str(SCRATCH / "c5.db")
    engine = create_sqlite_engine(db)
    TaskRow.__table__.create(engine)
    TaskLinkRow.__table__.create(engine)
    ItemRow.__table__.create(engine)
    with engine.begin() as conn:
        for t in range(TASKS):
            tid = f"t{t:04d}"
            conn.execute(TaskRow.__table__.insert().values(
                id=tid, name=tid, keyword="k", status="running",
                min_price=50, max_price=400,
            ))
            for i in range(60):
                conn.execute(TaskLinkRow.__table__.insert().values(
                    task_id=tid, link_type="item", link_key=f"{tid}-s{i}",
                    display=json.dumps({"is_sold": True, "price": 100 + (i % 20) * 10}),
                    updated_at=TS,
                ))
            for i in range(60):
                conn.execute(ItemRow.__table__.insert().values(
                    id=f"{tid}-i{i}", task_id=tid, title="t",
                    price=150 + (i % 15) * 5, publish_time=TS,
                ))
    return db


def main() -> None:
    db = build()
    engine = create_sqlite_engine(db)
    from xianyu_hunter.web.routes import evaluations_list as EL
    from xianyu_hunter.web.routes import price_dashboard as PD

    class Repo:
        pass

    container = type("C", (), {})()
    container.repo = Repo()
    container.repo.engine = engine
    tids = [f"t{t:04d}" for t in range(TASKS)]

    counted = {"n": 0}

    def _count(*_a, **_k):
        counted["n"] += 1

    batch_ms, single_ms = [], []
    batch_sql, single_sql = 0, 0

    for r in range(ROUNDS):
        EL._MARKET_STATS_CACHE.clear()
        counted["n"] = 0
        event.listen(engine, "before_cursor_execute", _count)
        t0 = time.perf_counter()
        EL.compute_market_stats_batch(container, tids, 30)
        batch_ms.append((time.perf_counter() - t0) * 1000)
        event.remove(engine, "before_cursor_execute", _count)
        if r == 0:
            batch_sql = counted["n"]

        counted["n"] = 0
        event.listen(engine, "before_cursor_execute", _count)
        t0 = time.perf_counter()
        with engine.connect() as conn:
            for tid in tids:
                PD._compute_sold_range(conn, tid, 30)
        single_ms.append((time.perf_counter() - t0) * 1000)
        event.remove(engine, "before_cursor_execute", _count)
        if r == 0:
            single_sql = counted["n"]

    print(f"[{TASKS} 个任务 × (60 sold links + 60 items)，{ROUNDS} 轮中位]")
    print(f"  逐条（旧实现）  SQL {single_sql:>3} 条  中位 {statistics.median(single_ms):8.1f} ms")
    print(f"  批量（C5）      SQL {batch_sql:>3} 条  中位 {statistics.median(batch_ms):8.1f} ms")
    print(f"  比值 {statistics.median(batch_ms) / statistics.median(single_ms):.2f}")
    engine.dispose()


if __name__ == "__main__":
    main()
