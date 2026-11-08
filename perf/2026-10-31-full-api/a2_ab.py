"""A2 A/B：`GET /api/evaluations` 主人态单次调用的耗时与每请求 SQL 往返（前/后各跑两轮）

判据只取**确定量**（同 C2 的纪律）：
- 每请求 SQL 往返条数（LIKE 前缀 + 被丢弃的 COUNT → 等值、无 COUNT）
- 事件读取阶段的行数（1867 → 377）
- 主人态直调处理器的 median（同进程、同 DB 副本、同参数，只有一支代码之差）

身份口径是这条探针的关键：`middleware/auth.py:123` 在 WEB_TOKEN 直通时把
`request.state.user_id` 置为 `"default"`，而真实库里 eval.* 全部属于另一个账号
（其 user_id 形如手机号，故不写死在本脚本里 —— `main()` 从快照副本现场解析）
→ **压测基线跑的是 0 行分支**（探针二实测 median 4.8 ms / total=0）。
本脚本对三种身份都取值，避免再拿空结果集的延迟当性能证据。

隔离：只读 DB 副本（sqlite backup API）+ config 副本，不写真实库、不启动调度器/浏览器、
不读取任何 cookie 文件。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/xh-a2-ab")
OUT_DIR = REPO / "perf" / "2026-10-31-full-api" / "results"
SQL_LOG: list[str] = []


def resolve_data_owner(db_path: str) -> str:
    """eval.* 事件的归属账号（持有数据的那一个），从快照副本现场解析

    不写死：这个 user_id 形如手机号，属于账号个人信息，不该进版本库。
    """
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "select user_id from events where type like 'eval.%' "
            "group by user_id order by count(*) desc limit 1"
        ).fetchone()[0]


DATA_UID = ""  # main() 里由 resolve_data_owner() 填充


def install_sql_counter() -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        SQL_LOG.append(" ".join(str(statement).split())[:70])


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


def main() -> None:
    global DATA_UID
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=("before", "after"))
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--iters", type=int, default=7)
    args = ap.parse_args()

    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    build_scaffold()
    os.chdir(SCRATCH)
    install_sql_counter()
    sys.path.insert(0, str(REPO / "backend"))
    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes import evaluations_common as EC
    from xianyu_hunter.web.routes import evaluations_list as EL

    db = str(SCRATCH / "data" / "xianyu.db")
    DATA_UID = resolve_data_owner(db)
    repo = Repository(db_path=db)
    container = type("C", (), {"repo": repo, "collector": None, "browser": None,
                               "price_strategy": None})()

    report: dict = {"label": args.label, "round": args.round, "iters": args.iters,
                    "identity": {}}
    for name, uid in (("sandbox_default", "default"), ("owner", DATA_UID)):
        req = FakeRequest(uid)
        EL._MARKET_STATS_CACHE.clear()
        times, sql_counts, totals = [], [], []
        for i in range(args.iters + 1):  # 第 0 次为冷（市场统计未命中），不计入
            SQL_LOG.clear()
            t0 = time.perf_counter()
            out = EL.list_evaluations(req, container=container, **NO_FILTER)
            times.append((time.perf_counter() - t0) * 1000)
            sql_counts.append(len(SQL_LOG))
            totals.append(out["total"])
        report["identity"][name] = {
            "cold_ms": round(times[0], 1),
            "median_ms": round(statistics.median(times[1:]), 1),
            "min_ms": round(min(times[1:]), 1),
            "max_ms": round(max(times[1:]), 1),
            "sql_warm": sorted(set(sql_counts[1:])),
            "total": totals[-1],
        }
        print(f"  {name:<14} cold {times[0]:8.1f} ms | warm median "
              f"{statistics.median(times[1:]):8.1f} ms "
              f"(min {min(times[1:]):6.1f} / max {max(times[1:]):6.1f}) | "
              f"SQL={sorted(set(sql_counts[1:]))} total={totals[-1]}")

    # 事件读取阶段单独定价（不含 enrich），这是本次改动直接命中的那一段
    # before：路由调 list_events_by_type_prefix('eval.') → 1867 行 + 一次被丢弃的 COUNT
    # after ：路由调 evaluations_common.load_eval_scored_events → 等值 377 行、无 COUNT
    def read_rows():
        if hasattr(EC, "load_eval_scored_events"):
            return EC.load_eval_scored_events(repo, DATA_UID), "list_events_by_type(等值)"
        return repo.list_events_by_type_prefix("eval.", user_id=DATA_UID)[0], "prefix(LIKE)"

    t0 = time.perf_counter()
    rows, method = read_rows()
    dt_read = (time.perf_counter() - t0) * 1000
    report["read_stage"] = {"rows": len(rows), "ms": round(dt_read, 1), "method": method}
    print(f"  事件读取阶段（{method}）：{len(rows)} 行 / {dt_read:.1f} ms")

    repo.engine.dispose()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / f"a2_ab_{args.label}_r{args.round}.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chdir(REPO)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"  [写入] {dst.relative_to(REPO)}  | scratch 已删除（真实库未被写入）")


if __name__ == "__main__":
    main()
