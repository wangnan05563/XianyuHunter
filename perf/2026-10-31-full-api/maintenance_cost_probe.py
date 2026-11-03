"""A4 判据探针：`GET /api/maintenance/status` 的 1.6 s 到底由哪几段构成

跑法（在项目根）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe perf/2026-10-31-full-api/maintenance_cost_probe.py

OPTIMIZATIONS §A4 的证据是「单发探针 1.6 s」以及 v1 负载轮里 P95 = 60 033 ms。要给降本
定优先级，得先把这 1.6 s 拆成：6 张表的 `SELECT COUNT(*)` / 日志目录 glob / 增量扫描
`_cache_scan.snapshot(budget=0.5)` / `__pycache__` 遍历，各自占多少。

安全边界：
- 真实库只以 ``mode=ro`` 打开（SQLAlchemy URI 同样带 uri=true），不写任何一行；
- 日志目录、browser-data、`__pycache__` 只 stat/遍历，不删不改；
- 不启动浏览器/隧道/调度器；`_cache_scan` 是本进程内的实例，与正在运行的服务互不影响。
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from xianyu_hunter.web.routes import api_maintenance as am  # noqa: E402

DB = ROOT / "data" / "xianyu.db"
TABLES = ("tasks", "items", "task_links", "events", "orders", "notifications")
REPEATS = 3


def timed(label: str, fn, repeats: int = REPEATS) -> float:
    runs = []
    result = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        runs.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(runs)
    print(f"    {label:<52} 中位 {med:8.1f} ms  all={[round(r) for r in runs]}")
    return med


def ro_engine():
    return create_engine(f"sqlite:///file:{DB.as_posix()}?mode=ro&uri=true")


def section_counts() -> None:
    print("[1] 6 张表的 SELECT COUNT(*)（库 mode=ro，120 MB）")
    engine = ro_engine()
    with engine.connect() as conn:
        for table in TABLES:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            timed(f"{table} count={n}", lambda t=table: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
        timed("db_size st_size", lambda: am._data_dir().joinpath("xianyu.db").stat().st_size)
    engine.dispose()

    def all_counts():
        eng = ro_engine()
        try:
            with eng.connect() as c:
                for t in TABLES:
                    c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        finally:
            eng.dispose()

    timed("全部 6 张（含一次新连接）", all_counts)


def section_logs() -> None:
    print("[2] 日志目录统计")
    log_dir = am._log_dir()
    print(f"    目录：{log_dir}（存在={log_dir.exists()}）")
    if not log_dir.exists():
        print("    → 跳过：目录不存在")
        return

    def two_pass():
        files = list(log_dir.glob("*.log*"))
        total = sum(f.stat().st_size for f in files if f.is_file())
        oldest = min(f.stat().st_mtime for f in files if f.is_file())
        return total, oldest

    def one_pass():
        total = 0
        oldest = None
        for f in log_dir.glob("*.log*"):
            try:
                st = f.stat()
            except OSError:
                continue
            total += st.st_size
            oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
        return total, oldest

    a = timed("现状：glob + 每个文件 stat×2", two_pass)
    b = timed("合并：glob + 每个文件 stat×1", one_pass)
    print(f"    → 合并省 {a - b:.1f} ms（文件数 {len(list(log_dir.glob('*.log*')))}）")


def section_scan() -> None:
    print("[3] 浏览器数据目录（增量扫描，预算 0.5 s）")
    bd = am._browser_data_dir()
    print(f"    目录：{bd}")
    if bd is None:
        return
    am._cache_scan.invalidate(bd)

    def one_snapshot():
        return am._cache_scan.snapshot(bd, budget=am.STATUS_SCAN_BUDGET_SEC)

    timed("冷启动首片（推进扫描状态机）", one_snapshot, repeats=1)
    timed("扫描进行中再取一片", one_snapshot)
    timed("browser_data_exists 判定", lambda: bd.exists() and any(bd.iterdir()))


def section_pycache() -> None:
    print("[4] __pycache__ 遍历（与清理范围同源）")
    timed("_iter_pycache_dirs + _walk_dir 全量", lambda: [am._walk_dir(p) for p in am._iter_pycache_dirs()])


def section_full() -> None:
    print("[5] 完整 maintenance_status（bypass_cache=True，强制重算）")
    container = SimpleNamespace(repo=SimpleNamespace(engine=ro_engine()))
    try:
        first = timed("冷（首片扫描 + 全部统计）", lambda: am.maintenance_status(container=container, bypass_cache=True), repeats=1)
        warm = timed("热（扫描已推进）", lambda: am.maintenance_status(container=container, bypass_cache=True))
        result = am.maintenance_status(container=container, bypass_cache=True)
        print(f"    db={result['db']}")
        print(f"    logs={result['logs']}")
        print(f"    cache.scan={result['cache']['browser_data_scan']}")
        print(f"    cache.mb={result['cache']['browser_data_mb']} pycache={result['cache']['pycache_mb']} MB/{result['cache']['pycache_count']} 目录")
        print(f"    → 冷 {first:.0f} ms / 热 {warm:.0f} ms；其中扫描预算是 {am.STATUS_SCAN_BUDGET_SEC * 1000:.0f} ms")
    finally:
        container.repo.engine.dispose()


def main() -> int:
    print(f"库：{DB}（{DB.stat().st_size / 1024 / 1024:.1f} MB）")
    section_counts()
    section_logs()
    section_scan()
    section_pycache()
    section_full()
    print("\n口径：接口的 TTL 是 300 s + SWR（A4 落地后）；这里的「热」仍是强制重算，因此是上界。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
