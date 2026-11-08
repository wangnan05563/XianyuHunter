"""A2 增长对照：事件表放大 5 倍后，等值取数与前缀取数的差距是否拉开（同进程 A/B）

OPTIMIZATIONS A2 的验证方式写的是「比对事件表行数翻倍后的 P95 —— 抗增长才是真收益」。
本脚本在 scratch 副本里把 `eval.*` 复制到 5 倍，然后在**同一进程、同一份数据**下
只切换取数口径（前缀 vs 等值），逐轮交替（前→后→前→后）以吃掉机器漂移：

- 前缀腿：把 `evaluations_common.load_eval_scored_events` 换成
  `repo.list_events_by_type_prefix("eval.", user_id=…)[0]`（改动前的真实取数）
- 等值腿：改动后的入口原样

两腿各测：事件读取阶段耗时 / `list_evaluations` 主人态 warm median / 每请求 SQL 条数。
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
SCRATCH = Path(r"D:/tmp/xh-a2-grow")
OUT_DIR = REPO / "perf" / "2026-10-31-full-api" / "results"
MULTIPLIER = 5
DATA_UID = ""  # main() 里由 resolve_data_owner() 填充（该 id 形如手机号，不写死进版本库）

SQL_LOG: list[str] = []


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


class FakeRequest:
    def __init__(self, user_id: str | None) -> None:
        self.state = type("S", (), {"user_id": user_id})()


NO_FILTER = dict(item_id=None, task_id=None, brand=None, min_score=None, max_score=None,
                 start_time=None, end_time=None, sold_filter="all", result_category=None,
                 min_price=None, max_price=None, include_out_of_range=False,
                 limit=200, offset=0, page_num=1, page_size=20)


def resolve_data_owner(db_path: str) -> str:
    """eval.* 事件的归属账号；从放大后的副本解析（等值腿用它，前缀腿也用同一个）"""
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "select user_id from events where type like 'eval.%' "
            "group by user_id order by count(*) desc limit 1"
        ).fetchone()[0]


def main() -> None:
    import json

    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    build_scaffold()
    os.chdir(SCRATCH)
    sys.path.insert(0, str(REPO / "backend"))

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        SQL_LOG.append(" ".join(str(statement).split())[:40])

    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes import evaluations_common as EC
    from xianyu_hunter.web.routes import evaluations_list as EL

    db = str(SCRATCH / "data" / "xianyu.db")
    DATA_UID = resolve_data_owner(db)
    raw = sqlite3.connect(db)
    base_prefix = raw.execute("select count(*) from events where type like 'eval.%'").fetchone()[0]
    cols = ("created_at, level, stage, type, task_id, item_id, request_id,"
            " user_id, message, payload, score_value")
    for k in range(MULTIPLIER - 1):
        raw.execute(f"""
            insert into events ({cols})
            select created_at, level, stage, type, task_id,
                   'dup{k}-' || coalesce(item_id, 'na'), request_id, user_id,
                   message, payload, score_value
            from events
            where type like 'eval.%' and coalesce(item_id, 'na') not like 'dup%'
        """)
    raw.commit()
    big_prefix = raw.execute("select count(*) from events where type like 'eval.%'").fetchone()[0]
    big_scored = raw.execute("select count(*) from events where type='eval.scored'").fetchone()[0]
    n_events = raw.execute("select count(*) from events").fetchone()[0]
    print(f"放大：eval.% {base_prefix} → {big_prefix}（{(big_prefix / base_prefix):.1f}×）、"
          f"eval.scored={big_scored}、events 总数={n_events}")
    raw.close()

    repo = Repository(db_path=db)
    container = type("C", (), {"repo": repo, "collector": None, "browser": None,
                               "price_strategy": None})()
    req = FakeRequest(DATA_UID)

    real_loader = EC.load_eval_scored_events

    def prefix_loader(r, user_id, limit=50000):
        return r.list_events_by_type_prefix("eval.", user_id=user_id)[0]

    def run_once(loader):
        EC.load_eval_scored_events = loader
        EL.load_eval_scored_events = loader
        SQL_LOG.clear()
        t0 = time.perf_counter()
        rows = loader(repo, DATA_UID)
        dt_read = (time.perf_counter() - t0) * 1000
        read_sql = len(SQL_LOG)
        EL._MARKET_STATS_CACHE.clear()
        times, sqls = [], []
        for _ in range(4):
            SQL_LOG.clear()
            t1 = time.perf_counter()
            out = EL.list_evaluations(req, container=container, **NO_FILTER)
            times.append((time.perf_counter() - t1) * 1000)
            sqls.append(len(SQL_LOG))
        return {"rows": len(rows), "read_ms": round(dt_read, 1), "read_sql": read_sql,
                "endpoint_median_ms": round(statistics.median(times[1:]), 1),
                "endpoint_ms": [round(t, 1) for t in times],
                "sql_warm": sorted(set(sqls[1:])), "total": out["total"]}

    results = {}
    try:
        for leg, loader in (("prefix(改动前)", prefix_loader), ("exact(改动后)", real_loader)):
            runs = [run_once(loader) for _ in range(2)]
            results[leg] = runs
            best = runs[-1]
            print(f"\n{leg}")
            for i, r in enumerate(runs):
                print(f"  第{i + 1}轮 读取 {r['read_ms']:7.1f} ms / {r['rows']} 行 / "
                      f"{r['read_sql']} 条 SQL | 端点 median {r['endpoint_median_ms']:7.1f} ms "
                      f"(冷 {r['endpoint_ms'][0]:.1f}) | SQL={r['sql_warm']} total={r['total']}")
    finally:
        EC.load_eval_scored_events = real_loader
        EL.load_eval_scored_events = real_loader

    report = {
        "events_total": n_events, "eval_prefix_rows": big_prefix,
        "eval_scored_rows": big_scored, "multiplier": MULTIPLIER,
        "legs": results,
    }
    dst = OUT_DIR / "a2_growth_5x.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    repo.engine.dispose()
    os.chdir(REPO)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"\n[写入] {dst.relative_to(REPO)} | scratch 已删除（真实库未被写入）")


if __name__ == "__main__":
    main()
