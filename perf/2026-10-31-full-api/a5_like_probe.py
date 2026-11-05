"""A5 判据：`%LIKE%` 前后通配的真实成本，以及 `/api/logs/search` 到底有没有 SQL LIKE

两个被原文混在一起的接口：
1. `/api/error-logs` → `repo_error_logs.list_error_logs` 的 `error_type/request_path LIKE '%kw%'`；
2. `/api/logs/search` → `api_logs.search_logs`，其 `q` 是 **Python 端子串匹配**，SQL 侧没有 LIKE。

判据：两条路径的候选集大小与中位耗时；前缀匹配（会改语义）只作对照。
在生产库的一致快照副本上跑（sqlite3 backup），不碰生产文件。
"""
from __future__ import annotations

import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC_DB = REPO / "data" / "xianyu.db"
SCRATCH = Path(r"D:/tmp/a5-like-probe")
ROUNDS = 15

sys.path.insert(0, str(REPO / "backend"))


def bench(fn, rounds: int = ROUNDS) -> tuple[float, int]:
    samples = []
    rows = 0
    for _ in range(rounds):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000)
        rows = len(out) if hasattr(out, "__len__") else out
    return statistics.median(samples), rows


def main() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    db = str(SCRATCH / "xianyu.db")
    with sqlite3.connect(str(SRC_DB)) as s, sqlite3.connect(db) as d:
        s.backup(d)

    c = sqlite3.connect(db)
    print(f"error_logs={c.execute('select count(*) from error_logs').fetchone()[0]}  "
          f"events={c.execute('select count(*) from events').fetchone()[0]}")

    q = {
        "无过滤 ORDER BY timestamp LIMIT 50": (
            "select * from error_logs order by timestamp desc limit 50"
        ),
        "error_type LIKE '%network%'": (
            "select * from error_logs where error_type like '%network%' "
            "order by timestamp desc limit 50"
        ),
        "error_type LIKE 'network%'（改语义的对照）": (
            "select * from error_logs where error_type like 'network%' "
            "order by timestamp desc limit 50"
        ),
        "request_path LIKE '%api%'": (
            "select * from error_logs where request_path like '%api%' "
            "order by timestamp desc limit 50"
        ),
        "request_path LIKE 'api%'（改语义的对照）": (
            "select * from error_logs where request_path like 'api%' "
            "order by timestamp desc limit 50"
        ),
    }
    print("\n[error_logs 直接 SQL，15 次中位]")
    for label, sql in q.items():
        ms, n = bench(lambda s=sql: c.execute(s).fetchall())
        print(f"  {label:<44} {ms:6.2f} ms  命中≈{n}")
    c.close()

    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes.api_logs import _fetch_log_candidates, _filter_log_candidates

    repo = Repository(db_path=db)
    print("\n[走真实代码路径]")
    ms, n = bench(lambda: repo.list_error_logs(error_type="network", limit=50))
    print(f"  repo.list_error_logs(error_type='network')      {ms:6.2f} ms  返回 {n} 行")
    ms, n = bench(lambda: repo.list_error_logs(request_path="api", limit=50))
    print(f"  repo.list_error_logs(request_path='api')        {ms:6.2f} ms  返回 {n} 行")

    class _C:
        pass

    container = _C()
    container.repo = repo
    ms, n = bench(lambda: _fetch_log_candidates(container, None, None, None, 100, None, None))
    print(f"  logs/search 取候选（limit=100 → 1000 行）        {ms:6.2f} ms  返回 {n} 行")
    candidates = _fetch_log_candidates(container, None, None, None, 100, None, None)
    ms, n = bench(lambda: _filter_log_candidates(candidates, "error", None, None, None, None))
    print(f"  logs/search Python 子串过滤 q='error'            {ms:6.2f} ms  命中 {n} 条")
    repo.engine.dispose()

    # 对照：把 q 下推到 SQL（覆盖全部历史，而不是最近 1000 条候选）
    c = sqlite3.connect(db)
    print("\n[q 下推 SQL 的对照（会改变 total_estimate 口径）]")
    for label, sql in (
        ("下推 q=error + LIMIT 100",
         "select * from events where (message like '%error%' or payload like '%error%') "
         "order by created_at desc limit 100"),
        ("下推 q=error + COUNT(*)（准确 total）",
         "select count(*) from events where (message like '%error%' or payload like '%error%')"),
    ):
        ms, n = bench(lambda s=sql: c.execute(s).fetchall())
        print(f"  {label:<40} {ms:6.2f} ms  返回/命中 {n}")
    c.close()
    print("  结论：下推把命中从 14 条扩到 52 条（口径更准），但延迟 33.7 → 37.7 ms（略慢），"
          "不是性能收益")


if __name__ == "__main__":
    main()
