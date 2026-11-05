"""D4 判据：导出链路的「先拼满再发」vs「边生成边发」

被测三处（落地前后都由同一探针直接调用真实函数，不在探针里替换实现）：
1. `api_export._build_csv_response`：774 行（真实 items 全量）/ 50 000 行（真实行循环放大）；
2. `api_logs.export_logs(format=csv)`：1 000 / 10 000 行（路由上的 limit 上限）；
3. `api_logs._export_log_file`：2 MB / 40 MB 日志文件。

口径：
- `peak_delta`：tracemalloc 峰值增量，**在被测行数据构造完成之后**才开始追踪，
  因此只反映「序列化 → 传输」这段新增分配，不把 rows 本身算进来；
- `ms_first` / `ms_total`：产出第一块与消费完的墙钟。旧实现首块≈总耗时，
  说明首字节被整份数据拖住；新实现首块应是总耗时的一小部分；
- `out_mb`：真实输出字节数。用 `peak_delta / out_mb` 判断峰值是否随输出线性增长
  （线性增长＝仍然整份驻留，与绝对大小无关）。

判据：
- 旧实现：peak_delta 随 out_mb 同比放大，比值明显 > 1；
- 新实现：peak_delta 与 out_mb 脱钩（≈ 一个发送块），ms_first ≪ ms_total。

只读 data/xianyu.db（URI mode=ro），不写生产文件；日志文件写在 D:/tmp 下。
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import sqlite3
import statistics
import sys
import time
import tracemalloc
import types
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
SRC_DB = REPO / "data" / "xianyu.db"
SCRATCH = Path(r"D:/tmp/d4-export-probe")
ROUNDS = 3

sys.path.insert(0, str(REPO / "backend"))

ITEM_COLS = [
    "id", "task_id", "title", "price", "publish_time", "region",
    "seller_id", "want_cnt", "view_cnt", "first_seen", "last_seen",
]
EVENT_COLS = [
    "id", "created_at", "level", "stage", "task_id", "item_id", "message", "payload",
]

# 复刻真实日志行的形态：足够长，40 MB 只需 20 万行左右
_LOG_LINE = (
    "2026-11-06 12:00:00.123 | INFO     | [req=req-20261106120000123-abcdef] | "
    "xianyu_hunter.collector.runner:run:1234 - 采集完成 items=20 new=3 sold=1 elapsed=812ms\n"
)


def _load_rows(sql: str, cols: list[str], target: int) -> list[dict[str, Any]]:
    """按只读 URI 打开真实库，取模板行并循环放大到 target 行"""
    con = sqlite3.connect(f"file:{SRC_DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        base = [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()
    if not base:
        raise SystemExit(f"模板为空：{sql}")
    return [dict(base[i % len(base)]) for i in range(target)]


def _make_log_file(path: Path, target_bytes: int) -> None:
    chunk = _LOG_LINE * 200
    with path.open("w", encoding="utf-8", newline="") as f:
        written = 0
        while written < target_bytes:
            f.write(chunk)
            written += len(chunk.encode("utf-8"))


def _drain(resp: Any, t0: float) -> tuple[int, float]:
    """消费 StreamingResponse，返回 (字节数, 首块毫秒)

    StreamingResponse 对同步迭代器会用 iterate_in_threadpool 包装，
    所以这里两种形态都要支持。
    """
    total = 0
    first_ms: float | None = None
    it = resp.body_iterator
    if hasattr(it, "__aiter__"):
        async def _run() -> None:
            nonlocal total, first_ms
            async for chunk in it:
                if first_ms is None:
                    first_ms = (time.perf_counter() - t0) * 1000
                total += len(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)

        asyncio.run(_run())
    else:
        for chunk in it:
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
            total += len(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    if first_ms is None:
        first_ms = (time.perf_counter() - t0) * 1000
    return total, first_ms


def measure(factory: Callable[[], Any], trace: bool = True) -> dict[str, float]:
    """测一次「构造响应 + 完整消费」的峰值增量与耗时

    trace=False 另测一遍墙钟：tracemalloc 会放大分配成本，绝对耗时不能直接
    与压测的 P99 比较，只有同口径的 no-trace 数字才贴近真实。
    """
    gc.collect()
    if trace:
        tracemalloc.start()
    base = 0
    try:
        if trace:
            base, _ = tracemalloc.get_traced_memory()
        t0 = time.perf_counter()
        resp = factory()
        out_bytes, first_ms = _drain(resp, t0)
        total_ms = (time.perf_counter() - t0) * 1000
        peak = tracemalloc.get_traced_memory()[1] if trace else 0
    finally:
        if trace:
            tracemalloc.stop()
    return {
        "ms_first": first_ms,
        "ms_total": total_ms,
        "peak_mb": (peak - base) / 1048576,
        "out_mb": out_bytes / 1048576,
    }


def _agg(samples: list[dict[str, float]]) -> dict[str, float]:
    out = {k: statistics.median([s[k] for s in samples]) for k in samples[0]}
    # 峰值随输出同比放大则比值恒定；比值随输出缩小才是「脱钩」
    out["peak_per_out"] = out["peak_mb"] / out["out_mb"] if out["out_mb"] else 0.0
    out["first_ratio"] = out["ms_first"] / out["ms_total"] if out["ms_total"] else 0.0
    return {k: round(v, 4) for k, v in out.items()}


def _sample(factory: Callable[[], Any], rounds: int) -> dict[str, Any]:
    """同一 case 跑两遍：带 tracemalloc 取峰值，不带它取真实墙钟"""
    samples = [measure(factory) for _ in range(rounds)]
    fast = [measure(factory, trace=False) for _ in range(rounds)]
    agg = _agg(samples)
    agg["ms_first_real"] = round(statistics.median([s["ms_first"] for s in fast]), 4)
    agg["ms_total_real"] = round(statistics.median([s["ms_total"] for s in fast]), 4)
    return {"samples": samples, "real": fast, "agg": agg}


def _csv_factory(rows: list[dict[str, Any]], columns: list[str], dataset: str) -> Callable[[], Any]:
    from xianyu_hunter.web.routes.api_export import _build_csv_response

    return lambda: _build_csv_response(rows, columns, dataset)


def _logs_factory(rows: list[dict[str, Any]], limit: int) -> Callable[[], Any]:
    from xianyu_hunter.web.routes import api_logs

    class _Repo:
        def list_events(self, **_: Any) -> list[dict[str, Any]]:
            return rows

    container = types.SimpleNamespace(repo=_Repo())
    request = types.SimpleNamespace(state=types.SimpleNamespace(user_id=None))
    return lambda: api_logs.export_logs(
        request=request,
        format="csv",
        q=None,
        level=None,
        tag=None,
        task_id=None,
        stage=None,
        start=None,
        end=None,
        limit=limit,
        container=container,
    )


def _logfile_factory() -> Callable[[], Any]:
    from xianyu_hunter.web.routes.api_logs import _export_log_file

    return _export_log_file


def _prepare_probe_db() -> Path:
    """把真实库备份一份到 D 盘：db-admin 导出走 engine，绝不能对着生产文件跑"""
    db = SCRATCH / "d4-probe.db"
    src = sqlite3.connect(f"file:{SRC_DB.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(db))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return db


def _db_admin_factory(db: Path, table: str) -> tuple[Callable[[], Any], Any]:
    from xianyu_hunter.infra.db_models import create_sqlite_engine
    from xianyu_hunter.web.routes import api_db_admin

    engine = create_sqlite_engine(str(db))
    container = types.SimpleNamespace(repo=types.SimpleNamespace(engine=engine))
    return (
        lambda: api_db_admin.export_rows(table=table, format="csv", container=container),
        engine,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=str(REPO / "perf/2026-10-31-full-api/results/d4_export_probe.json"),
    )
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    args = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)

    from xianyu_hunter.web.routes.api_export import _DATASET_COLUMNS

    items_small = _load_rows(
        f"select {', '.join(ITEM_COLS)} from items order by id limit 774", ITEM_COLS, 774
    )
    items_big = _load_rows(
        f"select {', '.join(ITEM_COLS)} from items order by id limit 774", ITEM_COLS, 50_000
    )
    events_all = _load_rows(
        f"select {', '.join(EVENT_COLS)} from events order by id limit 10000", EVENT_COLS, 10_000
    )

    cwd = os.getcwd()
    cases: list[tuple[str, Callable[[], Any], dict[str, Any]]] = [
        ("export_items_774", _csv_factory(items_small, _DATASET_COLUMNS["items"], "items"),
         {"rows": 774}),
        ("export_items_50000", _csv_factory(items_big, _DATASET_COLUMNS["items"], "items"),
         {"rows": 50_000}),
        ("export_logs_csv_1000", _logs_factory(events_all[:1000], 1000), {"rows": 1_000}),
        ("export_logs_csv_10000", _logs_factory(events_all, 10_000), {"rows": 10_000}),
    ]

    results: dict[str, Any] = {"cases": {}}
    for name, factory, meta in cases:
        results["cases"][name] = {"meta": meta, **_sample(factory, args.rounds)}

    # 日志文件必须「写一份、测一份」：若先把 40 MB 覆盖上去，2 MB 的 case 会读到 40 MB
    os.chdir(SCRATCH)
    try:
        # stderr 留空文件：覆盖 join 语义里「空内容也是一个元素」的边界
        _make_log_file(SCRATCH / "run.stderr.log", 0)
        for name, size_mb in (("export_logfile_2mb", 2), ("export_logfile_40mb", 40)):
            _make_log_file(SCRATCH / "run.stdout.log", size_mb * 1024 * 1024)
            results["cases"][name] = {
                "meta": {"file_bytes": size_mb * 1024 * 1024},
                **_sample(_logfile_factory(), args.rounds),
            }
    finally:
        os.chdir(cwd)

    # db-admin 表导出：原文 D4 的主证据端点（items 774 行 / events 18 403 行）
    probe_db = _prepare_probe_db()
    engines = []
    try:
        for name, table in (
            ("db_admin_export_items", "items"),
            ("db_admin_export_events", "events"),
        ):
            factory, engine = _db_admin_factory(probe_db, table)
            engines.append(engine)
            results["cases"][name] = {
                "meta": {"table": table},
                **_sample(factory, args.rounds),
            }
    finally:
        for engine in engines:
            engine.dispose()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{args.rounds} 轮中位]  峰值增量口径：构造 rows 之后才开始 tracemalloc")
    print(f"  {'case':<24}{'out MB':>9}{'peak MB':>10}{'peak/out':>10}"
          f"{'first ms':>10}{'total ms':>10}{'first/total':>12}"
          f"{'real first':>11}{'real total':>11}")
    for name, r in results["cases"].items():
        a = r["agg"]
        print(f"  {name:<24}{a['out_mb']:>9.2f}{a['peak_mb']:>10.2f}{a['peak_per_out']:>10.2f}"
              f"{a['ms_first']:>10.2f}{a['ms_total']:>10.2f}{a['first_ratio']:>12.2f}"
              f"{a['ms_first_real']:>11.2f}{a['ms_total_real']:>11.2f}")
    print(f"\n结果写入 {out}")


if __name__ == "__main__":
    main()
