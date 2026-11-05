"""A1：三个按实测补建的索引 + 两条「判定不落」的反向门禁

判据来自 2 万行合成表的 EXPLAIN + 计时（见 OPTIMIZATIONS §A1 落地记录）：
1. items.price：区间过滤由「publish_time 索引全扫」改为「price 索引区间 + 临时排序」；
2. evaluations.risk_level：无 WHERE 的 `SUM(CASE)` 变覆盖索引扫描；
3. orders(type, created_at)：type 过滤 + 排序，去掉临时 B-TREE（单列 type 实测反而更慢）；
4. price_history.user_id / task_links.updated_at **刻意不建** —— 反向门禁挡「顺手补齐 5 条」。

本文件只在 tmp_path 建库，不碰仓库 data/xianyu.db。
"""
from __future__ import annotations

import sqlite3

import pytest

from xianyu_hunter.infra import db_models

ITEMS_PRICE_SQL = (
    "select price, publish_time from items where price >= 100 and price <= 500 "
    "order by publish_time desc limit 2000"
)
EVAL_AGG_SQL = "select sum(case when risk_level = 'low' then 1 else 0 end) from evaluations"
ORDERS_SQL = "select * from orders where type = 'dry_run' order by created_at desc limit 20"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("a1") / "xianyu.db"
    db_models.init_db(str(path))
    return str(path)


def index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma index_list({table})")}


def query_plan(conn: sqlite3.Connection, sql: str) -> str:
    return " | ".join(str(row[3]) for row in conn.execute("explain query plan " + sql))


def test_three_indexes_exist(db_path) -> None:
    with sqlite3.connect(db_path) as conn:
        assert "ix_items_price" in index_names(conn, "items")
        assert "ix_evaluations_risk_level" in index_names(conn, "evaluations")
        assert "ix_orders_type_created" in index_names(conn, "orders")


def test_items_price_query_uses_new_index(db_path) -> None:
    with sqlite3.connect(db_path) as conn:
        plan = query_plan(conn, ITEMS_PRICE_SQL)
    assert "ix_items_price" in plan, plan


def test_evaluations_aggregate_is_covering_index_scan(db_path) -> None:
    with sqlite3.connect(db_path) as conn:
        plan = query_plan(conn, EVAL_AGG_SQL)
    assert "COVERING INDEX ix_evaluations_risk_level" in plan, plan


def test_orders_type_query_avoids_temp_btree(db_path) -> None:
    with sqlite3.connect(db_path) as conn:
        plan = query_plan(conn, ORDERS_SQL)
    assert "ix_orders_type_created" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_rejected_indexes_are_not_created(db_path) -> None:
    """反向门禁：这两条实测无收益（计划不变 / 收益 4% 且写代价 +46%），别再补齐"""
    with sqlite3.connect(db_path) as conn:
        assert not {n for n in index_names(conn, "price_history") if "user" in n}
        assert not {n for n in index_names(conn, "task_links") if "updated" in n}


def test_orm_metadata_declares_the_same_three_indexes() -> None:
    """新建库走 `Base.metadata.create_all`，ORM 里没声明就只会在迁移时补上"""
    names = set()
    for table in (db_models.ItemRow.__table__, db_models.EvaluationRow.__table__,
                  db_models.OrderRow.__table__):
        names |= {ix.name for ix in table.indexes}
    assert {"ix_items_price", "ix_evaluations_risk_level", "ix_orders_type_created"} <= names


def test_init_db_is_idempotent(db_path) -> None:
    db_models.init_db(db_path)  # 迁移全部是 if-not-exists 语义，重复调用不得报错
    with sqlite3.connect(db_path) as conn:
        assert "ix_items_price" in index_names(conn, "items")


def test_migration_backfills_indexes_for_existing_db(tmp_path) -> None:
    """旧库路径：`create_all` 不会给已存在的表补索引，只能靠迁移行

    先建库再把 3 个新索引 drop 掉（模拟升级前的库），第二次 init_db 必须补回来。
    """
    path = str(tmp_path / "legacy.db")
    db_models.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("drop index ix_items_price")
        conn.execute("drop index ix_evaluations_risk_level")
        conn.execute("drop index ix_orders_type_created")
        conn.commit()
    db_models.init_db(path)
    with sqlite3.connect(path) as conn:
        assert "ix_items_price" in index_names(conn, "items")
        assert "ix_evaluations_risk_level" in index_names(conn, "evaluations")
        assert "ix_orders_type_created" in index_names(conn, "orders")
