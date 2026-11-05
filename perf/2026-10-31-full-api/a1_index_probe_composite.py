"""A1 追加实验：单列索引在 orders/task_links 上未被选用，试复合形态

用法：先跑主探针并保留库（`A1_PROBE_DIR=D:/tmp/a1-idx-probe python a1_index_probe.py`），
再 `python a1_index_probe_composite.py D:/tmp/a1-idx-probe/probe.db`。
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
import time
from pathlib import Path

DB = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:/tmp/a1-idx-probe/probe.db")
ROUNDS = 15

ORDERS_SQL = "select * from orders where type = 'dry_run' order by created_at desc limit 20"
LINKS_SQL = (
    "select task_id, json_extract(display, '$.price') from task_links "
    "where link_type = 'item' and json_extract(display, '$.is_sold') = 1 "
    "and updated_at >= '2026-10-01 00:00:00' limit 5000"
)


def explain(c, sql):
    return " | ".join(str(r[3]) for r in c.execute("explain query plan " + sql))


def bench(c, sql):
    s = []
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        c.execute(sql).fetchall()
        s.append((time.perf_counter() - t0) * 1000)
    return statistics.median(s)


def main() -> None:
    c = sqlite3.connect(str(DB))
    for label, sql, index_sql in (
        ("orders (type, created_at) 复合", ORDERS_SQL,
         "create index if not exists ix_orders_type_created on orders(type, created_at)"),
        ("task_links (link_type, updated_at) 复合", LINKS_SQL,
         "create index if not exists ix_task_links_type_updated on task_links(link_type, updated_at)"),
    ):
        before_plan = explain(c, sql)
        before_ms = bench(c, sql)
        c.execute(index_sql)
        c.commit()
        after_plan = explain(c, sql)
        after_ms = bench(c, sql)
        print(f"\n[{label}]")
        print(f"  加索引前：{before_ms:7.2f} ms   {before_plan}")
        print(f"  加索引后：{after_ms:7.2f} ms   {after_plan}")
    c.close()


if __name__ == "__main__":
    main()
