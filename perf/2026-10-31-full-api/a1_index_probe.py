"""A1 取证：5 个候选索引在「放大后的合成表」上到底有没有用（含写侧代价）

为什么用合成库而不是主库：生产副本里 price_history / orders 都是 0 行、evaluations 6 行，
在这三个表上根本量不出索引效果；A1 本身是「防恶化」条目，必须在增长口径下判。
合成比例为：items 2 万行、task_links 2 万行、orders 2 万行、price_history 2 万行、
evaluations 2 万行（真实库 items 774 / task_links 2 050，约 10~25×）。

判据：
1. 加索引后 `EXPLAIN QUERY PLAN` 是否真的改用它；
2. 同一查询的 15 次中位耗时是否下降（不降或变慢即判定不落）；
3. 写侧：同表插入 3 000 行的耗时增量为多少。
"""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import statistics
import time
from pathlib import Path

# 默认带 PID：固定目录名在并发探针下会互相 rmtree（D1 的事故形态）；
# 需要把库留给 a1_index_probe_composite.py 时用 A1_PROBE_DIR 指一个固定目录
SCRATCH = Path(os.environ.get("A1_PROBE_DIR") or f"D:/tmp/a1-idx-probe-{os.getpid()}")
ROWS = 20000
WRITE_ROWS = 3000
ROUNDS = 15
rng = random.Random(20261106)


def reset() -> sqlite3.Connection:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    return sqlite3.connect(str(SCRATCH / "probe.db"))


def build_items(c: sqlite3.Connection) -> None:
    c.execute("create table items (id integer primary key, price real, publish_time text)")
    c.execute("create index ix_items_publish_time on items(publish_time)")
    base = time.time()
    rows = []
    for i in range(ROWS):
        price = rng.uniform(10, 5000)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base - rng.uniform(0, 180 * 86400)))
        rows.append((i, price, ts))
    c.executemany("insert into items values (?,?,?)", rows)
    c.commit()


def build_orders(c: sqlite3.Connection) -> None:
    c.execute(
        "create table orders (id integer primary key, status text, type text, task_id text, "
        "item_id text, created_at text)"
    )
    c.execute("create index ix_orders_status_created on orders(status, created_at)")
    base = time.time()
    rows = []
    for i in range(ROWS):
        rows.append((
            i, rng.choice(("pending", "success", "failed", "cancelled")),
            rng.choice(("dry_run", "real")), f"t{rng.randint(1, 200):05d}",
            f"i{rng.randint(1, 5000)}",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base - rng.uniform(0, 90 * 86400))),
        ))
    c.executemany("insert into orders values (?,?,?,?,?,?)", rows)
    c.commit()


def build_task_links(c: sqlite3.Connection) -> None:
    c.execute(
        "create table task_links (id integer primary key, link_type text, link_key text, "
        "task_id text, source text, display text, created_at text, updated_at text)"
    )
    c.execute("create index ix_task_links_type_key on task_links(link_type, link_key)")
    c.execute("create index ix_task_links_task_type_created on task_links(task_id, link_type, created_at)")
    base = time.time()
    rows = []
    for i in range(ROWS):
        sold = 1 if rng.random() < 0.3 else 0
        display = f'{{"is_sold": {sold}, "price": {rng.uniform(10, 5000):.2f}}}'
        rows.append((
            i, rng.choice(("item", "item", "item", "keyword", "seller")),
            f"k{rng.randint(1, 9000)}", f"t{rng.randint(1, 200):05d}",
            rng.choice(("search", "official", "live")), display,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base - rng.uniform(0, 120 * 86400))),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base - rng.uniform(0, 30 * 86400))),
        ))
    c.executemany("insert into task_links values (?,?,?,?,?,?,?,?)", rows)
    c.commit()


def build_price_history(c: sqlite3.Connection) -> None:
    c.execute(
        "create table price_history (id integer primary key, item_id text, user_id text, "
        "price real, captured_at text)"
    )
    c.execute("create index ix_price_history_item_captured on price_history(item_id, captured_at)")
    base = time.time()
    rows = []
    for i in range(ROWS):
        rows.append((
            i, f"i{rng.randint(1, 3000)}", rng.choice(("u1", "u2")),
            rng.uniform(10, 5000),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base - rng.uniform(0, 120 * 86400))),
        ))
    c.executemany("insert into price_history values (?,?,?,?,?)", rows)
    c.commit()


def build_evaluations(c: sqlite3.Connection) -> None:
    c.execute("create table evaluations (id integer primary key, risk_level text, item_id text)")
    rows = [(i, rng.choice(("low", "medium", "high", "extreme")), f"i{rng.randint(1, 5000)}")
            for i in range(ROWS)]
    c.executemany("insert into evaluations values (?,?,?)", rows)
    c.commit()


def explain(c: sqlite3.Connection, sql: str) -> str:
    return " | ".join(str(r[3]) for r in c.execute("explain query plan " + sql))


def bench(c: sqlite3.Connection, sql: str) -> float:
    samples = []
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        c.execute(sql).fetchall()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def report(c: sqlite3.Connection, label: str, sql: str, index_sql: str) -> None:
    before_plan = explain(c, sql)
    before_ms = bench(c, sql)
    c.execute(index_sql)
    after_plan = explain(c, sql)
    after_ms = bench(c, sql)
    print(f"\n[{label}]")
    print(f"  加索引前：{before_ms:7.2f} ms   {before_plan}")
    print(f"  加索引后：{after_ms:7.2f} ms   {after_plan}")
    print(f"  索引：{index_sql}")


TABLE_DDL = {
    "items": (
        "create table items (id integer primary key, price real, publish_time text)",
        "create index ix_items_publish_time on items(publish_time)",
        "create index if not exists ix_items_price on items(price)",
        [(ROWS + i, rng.uniform(10, 5000), "2026-11-01 00:00:00") for i in range(WRITE_ROWS)],
    ),
    "orders": (
        "create table orders (id integer primary key, status text, type text, task_id text, "
        "item_id text, created_at text)",
        "create index ix_orders_status_created on orders(status, created_at)",
        "create index if not exists ix_orders_type on orders(type)",
        [(ROWS + i, "pending", "dry_run", "t00001", "i1", "2026-11-01 00:00:00")
         for i in range(WRITE_ROWS)],
    ),
    "task_links": (
        "create table task_links (id integer primary key, link_type text, link_key text, "
        "task_id text, source text, display text, created_at text, updated_at text)",
        "create index ix_task_links_type_key on task_links(link_type, link_key)",
        "create index if not exists ix_task_links_updated_at on task_links(updated_at)",
        [(ROWS + i, "item", f"k{i}", "t00001", "search",
          '{"is_sold": 0, "price": 100}', "2026-11-01 00:00:00", "2026-11-01 00:00:00")
         for i in range(WRITE_ROWS)],
    ),
}


def insert_cost(table: str, with_new_index: bool) -> float:
    """独立空库 + 同表结构，插 3 000 行的总耗时（避免表大小差异干扰对比）"""
    ddl, existing_index, new_index, rows = TABLE_DDL[table]
    path = SCRATCH / f"w_{table}_{'idx' if with_new_index else 'base'}.db"
    if path.exists():
        path.unlink()
    c = sqlite3.connect(str(path))
    c.execute(ddl)
    c.execute(existing_index)
    if with_new_index:
        c.execute(new_index)
    t0 = time.perf_counter()
    c.executemany(
        f"insert into {table} values ({','.join('?' * len(rows[0]))})", rows
    )
    c.commit()
    dt = (time.perf_counter() - t0) * 1000
    c.close()
    return dt


def main() -> None:
    c = reset()
    build_items(c)
    build_orders(c)
    build_task_links(c)
    build_price_history(c)
    build_evaluations(c)
    c.commit()
    print(f"合成库：{ROWS} 行/表，查询 {ROUNDS} 次取中位")

    report(c, "items.price（区间 + ORDER BY publish_time LIMIT 2000）",
           "select price, publish_time from items where price >= 100 and price <= 500 "
           "order by publish_time desc limit 2000",
           "create index ix_items_price on items(price)")
    report(c, "evaluations.risk_level（无 WHERE 的 SUM(CASE)）",
           "select sum(case when risk_level = 'low' then 1 else 0 end) from evaluations",
           "create index ix_evaluations_risk_level on evaluations(risk_level)")
    report(c, "price_history.user_id（已由 item_id 前缀覆盖）",
           "select * from price_history where item_id = 'i123' "
           "and captured_at >= '2025-01-01 00:00:00' order by captured_at asc",
           "create index ix_price_history_user on price_history(user_id)")
    report(c, "orders.type（无 status 过滤 + ORDER BY created_at）",
           "select * from orders where type = 'dry_run' order by created_at desc limit 20",
           "create index ix_orders_type on orders(type)")
    report(c, "task_links.updated_at（link_type 等值 + 时间范围）",
           "select task_id, json_extract(display, '$.price') from task_links "
           "where link_type = 'item' and json_extract(display, '$.is_sold') = 1 "
           "and updated_at >= '2026-10-01 00:00:00' limit 5000",
           "create index ix_task_links_updated_at on task_links(updated_at)")

    print("\n=== 写侧代价（独立空库插入 3 000 行，重复 3 轮取中位）")
    for table in ("items", "orders", "task_links"):
        for w in (False, True):
            samples = [insert_cost(table, w) for _ in range(3)]
            print(f"  {table:<12} {'带新索引' if w else '现状    '} "
                  f"插入 {WRITE_ROWS} 行：{statistics.median(samples):7.1f} ms  "
                  f"（{['%.1f' % s for s in samples]}）")


if __name__ == "__main__":
    main()
