"""A2 取证：`GET /api/evaluations` 的 `eval.*` 事件读取到底贵在哪（只读真实库的副本）

OPTIMIZATIONS A2 的原方案是「把过滤/分页/总数全部下推 SQL」。落地前先证明三件事：
1. `list_events_by_type_prefix("eval.")` 这一次读入本身值多少毫秒 —— 拆开 SELECT /
   `_row_to_dict`（含 JSON 解析）/ 被丢弃的 COUNT 三段；
2. 真实库里 `eval.%` 前缀比真正需要的 `eval.scored` 多读多少行（`eval.passed` 是
   NotifierHub 写的通知事件，Python 端随即丢弃），以及 `LIKE 'eval.%' + ORDER BY
   created_at DESC + LIMIT 50000` 让 SQLite 走了哪个索引（EXPLAIN QUERY PLAN）；
3. 该端点自身的成本里，事件读取和后续 5 次批量 enrich 各占多少 —— 每请求 SQL 往返数。

隔离：`config/` 复制 + DB 走 sqlite backup API 取一致快照 → chdir 到 scratch，
全程不写真实 `data/xianyu.db`、不启动调度器/浏览器。增长对照（行数翻倍）只在
scratch 副本里合成插入，用于回答「抗增长才是真收益」。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/xh-a2-probe")

SQL_LOG: list[str] = []


def install_sql_counter() -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        SQL_LOG.append(" ".join(str(statement).split()))


def build_scaffold() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "config").mkdir(parents=True)
    for f in ("config.yaml", "menu_registry.yaml", "eval.yaml", "param_rules.yaml"):
        src = REPO / "config" / f
        if src.exists():
            shutil.copy2(src, SCRATCH / "config" / f)
    (SCRATCH / "data").mkdir()
    with sqlite3.connect(str(REPO / "data" / "xianyu.db")) as s, \
            sqlite3.connect(str(SCRATCH / "data" / "xianyu.db")) as d:
        s.backup(d)


def bench(name: str, fn, n: int = 7) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(ts)
    print(f"  {name:<52} {med:8.1f} ms  (min {min(ts):6.1f} / max {max(ts):7.1f})")
    return med


class FakeRequest:
    def __init__(self, user_id: str | None = None) -> None:
        self.state = type("S", (), {"user_id": user_id})()
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.query_params: dict = {}


def explain(conn: sqlite3.Connection, label: str, sql: str) -> None:
    plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    print(f"  {label}")
    print(f"    SQL: {sql[:110]}")
    for row in plan:
        print(f"    -> {row[3]}")


def main() -> None:
    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    build_scaffold()
    os.chdir(SCRATCH)
    install_sql_counter()

    sys.path.insert(0, str(REPO / "backend"))
    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.infra.repository_base import RepositoryBase
    from xianyu_hunter.web.routes import evaluations_list as EL

    db = str(SCRATCH / "data" / "xianyu.db")
    repo = Repository(db_path=db)
    container = type("C", (), {"repo": repo, "collector": None, "browser": None,
                               "price_strategy": None})()

    raw = sqlite3.connect(db)
    n_events = raw.execute("select count(*) from events").fetchone()[0]
    n_prefix = raw.execute("select count(*) from events where type like 'eval.%'").fetchone()[0]
    n_scored = raw.execute("select count(*) from events where type='eval.scored'").fetchone()[0]
    n_items = raw.execute("select count(*) from items").fetchone()[0]
    n_links = raw.execute("select count(*) from task_links").fetchone()[0]
    print("=" * 92)
    print(f"真实库规模：events={n_events}  eval.%={n_prefix}  eval.scored={n_scored} "
          f"items={n_items}  task_links={n_links}")
    print(f"多读的行（前缀 vs 精确）：{n_prefix - n_scored} 行 = "
          f"{(n_prefix / max(n_scored, 1)):.1f}×")
    print("=" * 92)

    print("\n[1] EXPLAIN QUERY PLAN（SQLite 实际选路）")
    explain(raw, "a. 现方案：LIKE 前缀 + ORDER BY created_at DESC + LIMIT 50000",
            "select * from events where type like 'eval.%' order by created_at desc limit 50000")
    explain(raw, "b. 去掉 LIMIT（现方案的等价形态：LIMIT 远大于行数）",
            "select * from events where type like 'eval.%' order by created_at desc")
    explain(raw, "c. 改精确类型：type = 'eval.scored' + ORDER BY + LIMIT",
            "select * from events where type = 'eval.scored' order by created_at desc limit 200")
    explain(raw, "d. 被丢弃的 COUNT（LIKE 前缀）",
            "select count(*) from events where type like 'eval.%'")
    explain(raw, "e. 同一 COUNT 改精确类型",
            "select count(*) from events where type = 'eval.scored'")

    print("\n[2] list_events_by_type_prefix 三段拆分（现方案，全量读入）")
    prefix_method_ms = bench("整方法 rows,total = list_events_by_type_prefix('eval.')",
                             lambda: repo.list_events_by_type_prefix("eval."))

    from sqlalchemy import func, select
    from xianyu_hunter.infra.db_models import EventRow

    def select_only():
        with repo.engine.connect() as conn:
            stmt = (select(EventRow).where(EventRow.type.like("eval.%"))
                    .order_by(EventRow.created_at.desc()).limit(50000))
            return list(conn.execute(stmt).all())

    def count_only():
        with repo.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(EventRow)
                                    .where(EventRow.type.like("eval.%"))).scalar() or 0)

    rows_raw = select_only()
    t_sel = bench("  仅 SELECT + fetchall（不解 dict）", select_only)
    t_cnt = bench("  仅 COUNT(*)（结果被调用方丢弃）", count_only)
    t_dict = bench(f"  仅 _row_to_dict × {len(rows_raw)} 行（含 payload JSON 解析）",
                   lambda: [RepositoryBase._row_to_dict(r) for r in rows_raw])
    print(f"  ── 合计 {t_sel + t_cnt + t_dict:.1f} ms / SELECT {t_sel:.1f} + dict {t_dict:.1f} "
          f"+ COUNT {t_cnt:.1f}")

    def select_exact():
        with repo.engine.connect() as conn:
            stmt = (select(EventRow).where(EventRow.type == "eval.scored")
                    .order_by(EventRow.created_at.desc()).limit(50000))
            return list(conn.execute(stmt).all())

    rows_exact = select_exact()
    t_exact = bench(f"  对照：仅 SELECT type='eval.scored'（{len(rows_exact)} 行）", select_exact)
    t_exact_dict = bench("  对照：仅 _row_to_dict 精确集合",
                         lambda: [RepositoryBase._row_to_dict(r) for r in rows_exact])
    print(f"  ── 前缀→精确可省的读入：SELECT {t_sel - t_exact:.1f} ms + "
          f"dict {t_dict - t_exact_dict:.1f} ms + COUNT {t_cnt:.1f} ms")

    print("\n[3] 端点整体：直调 list_evaluations（默认参数 page_size=20）")
    req = FakeRequest()
    # 直调函数时 FastAPI 的 Query(...) 默认值不会解包，显式传参还原「无过滤」的默认请求
    NO_FILTER = dict(item_id=None, task_id=None, brand=None, min_score=None, max_score=None,
                     start_time=None, end_time=None, sold_filter="all", result_category=None,
                     min_price=None, max_price=None, include_out_of_range=False,
                     limit=200, offset=0, page_num=1, page_size=20)

    def one_call(**kw):
        SQL_LOG.clear()
        t0 = time.perf_counter()
        out = EL.list_evaluations(req, container=container, **{**NO_FILTER, **kw})
        dt = (time.perf_counter() - t0) * 1000
        return dt, out, list(SQL_LOG)

    cold = one_call()
    warm = [one_call() for _ in range(3)]
    call_times = [cold[0]] + [dt for dt, _, _ in warm]
    print(f"  冷 {cold[0]:8.1f} ms（市场统计缓存未命中）| SQL={len(cold[2])} 条 | "
          f"total={cold[1]['total']}")
    for i, (dt, out, sqls) in enumerate(warm):
        print(f"  热第{i + 1}次 {dt:8.1f} ms | SQL={len(sqls)} 条 | "
              f"total={out['total']} count={out['count']}")
    dt, out, sqls = warm[-1]
    print("  每请求 SQL 明细（截断 120 字符）：")
    from collections import Counter
    shape = Counter(s[:60] for s in sqls)
    for k, v in sorted(shape.items(), key=lambda kv: -kv[1]):
        print(f"    {v:>2}× {k}…")
    print(f"  {'─' * 60}\n  完整首条：{sqls[0][:200] if sqls else '—'}")

    print("\n[4] 端点内部逐阶段（用第 3 步同一份 rows）")
    rows, _ = repo.list_events_by_type_prefix("eval.")
    ids = EL._collect_eval_event_item_ids(rows)
    print(f"  事件涉及 item_id 去重后：{len(ids)} 个")
    bench("_load_eval_item_map（items IN 批量）",
          lambda: EL._load_eval_item_map(container, ids))
    item_map = EL._load_eval_item_map(container, ids) or {}
    bench("repo.list_link_displays_by_keys（task_links 批量）",
          lambda: repo.list_link_displays_by_keys(list(ids), link_type="item", user_id=None))
    link_map = repo.list_link_displays_by_keys(list(ids), link_type="item", user_id=None)
    bench("_load_seller_nick_map（sellers IN 批量）",
          lambda: EL._load_seller_nick_map(container, item_map))
    seller_map = EL._load_seller_nick_map(container, item_map)
    bench("repo.list_orders_by_item_ids（orders 批量）",
          lambda: repo.list_orders_by_item_ids(list(ids), include_failed=True, user_id=None))
    order_map = repo.list_orders_by_item_ids(list(ids), include_failed=True, user_id=None)

    scored = [r for r in rows if str(r.get("type", "")) == EL._EVAL_SCORED_TYPE]
    print(f"  Python 端丢弃的非 eval.scored 行：{len(rows) - len(scored)}")
    bench(f"_enrich_eval_record × {len(scored)} 行",
          lambda: [EL._enrich_eval_record(r, item_map, link_map, order_map, seller_map)
                   for r in scored])
    bench(f"_enrich_condition_tags × {len(scored)} 行",
          lambda: EL._enrich_condition_tags(scored), n=5)
    bench(f"_enrich_bargain_and_profit × {len(scored)} 行（市场统计缓存热态）",
          lambda: EL._enrich_bargain_and_profit(scored, container), n=5)
    # 冷态：清空 TTL 缓存后，market stats 逐 task_id 计算
    EL._MARKET_STATS_CACHE.clear()
    bench(f"_enrich_bargain_and_profit × {len(scored)} 行（缓存冷：逐 task 计算）",
          lambda: EL._enrich_bargain_and_profit(scored, container), n=1)
    SQL_LOG.clear()
    EL._enrich_bargain_and_profit(scored, container)
    print(f"  ── 市场统计热态每请求 SQL：{len(SQL_LOG)} 条")

    def filter_loop():
        for r in scored:
            EL._should_skip_eval_record(
                r, r["payload"], None, None, None,
                "all", {}, None, None, False,
                None, None, None, None, None,
                None, None, 0,
            )

    bench(f"全量过滤循环 _should_skip_eval_record × {len(scored)} 行", filter_loop, n=5)

    print("\n[5] 增长对照：把 eval.* 事件行复制 5 倍（只在 scratch 副本里合成插入）")
    cols = ("created_at, level, stage, type, task_id, item_id, request_id,"
            " user_id, message, payload, score_value")
    for k in range(4):
        raw.execute(f"""
            insert into events ({cols})
            select created_at, level, stage, type, task_id,
                   'probe-dup{k}-' || coalesce(item_id, 'na'), request_id, user_id,
                   message, payload, score_value
            from events
            where type like 'eval.%' and coalesce(item_id, 'na') not like 'probe-dup%'
        """)
    raw.commit()
    n2 = raw.execute("select count(*) from events").fetchone()[0]
    p2 = raw.execute("select count(*) from events where type like 'eval.%'").fetchone()[0]
    s2 = raw.execute("select count(*) from events where type='eval.scored'").fetchone()[0]
    print(f"  放大后：events={n2} eval.%={p2} eval.scored={s2}")
    repo.engine.dispose()
    repo = Repository(db_path=db)
    container = type("C", (), {"repo": repo, "collector": None, "browser": None,
                               "price_strategy": None})()
    base_method_ms = bench("list_events_by_type_prefix('eval.')（放大后）",
                           lambda: repo.list_events_by_type_prefix("eval."), n=5)
    for _ in range(3):
        dt_big, out_big, sqls_big = one_call()
    print(f"  list_evaluations（放大后）{dt_big:.1f} ms | SQL={len(sqls_big)} | "
          f"total={out_big['total']}")
    print(f"  增长比 {p2 / max(n_prefix, 1):.1f}×（eval.% {n_prefix} → {p2} 行）："
          f"方法耗时 {prefix_method_ms:.1f} → {base_method_ms:.1f} ms、"
          f"端点 {statistics.median(call_times):.1f} → {dt_big:.1f} ms")

    raw.close()
    os.chdir(REPO)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n[清理] scratch 目录已删除（DB 为一致快照副本，真实库未被写入）")


if __name__ == "__main__":
    main()
