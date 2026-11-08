"""B4 取证二：键基数能不能被「参数取值」无界撑大，撑大之后驻留多少字节

取证一（`b4_cache_census.py`）实测：把压测里真实打过的 106 条 GET 全扫一遍，
`_cache_store` 只留下 **7 条 / 0.02 MB**，单条最大 7.9 KB。也就是说原文 B4 的
「无容量上限 ⇒ 内存峰值/RSS 斜率的嫌疑」在**值体积**这个维度上不成立。

但那条结论只覆盖了「键不变」的情形。本探针问的是另一半：**键从哪来、能长多少**——
八个缓存站点里有四个的 key 由请求参数拼成（`trend:{metric}:{range}:{task}`、
`hist:{bins}:{task}`、`kpi:{range_days}`、`eval_funnel:{range_days}`），而路由签名是
`metric: str = "events"` / `range_hours: int = 24` —— **没有 Enum、没有 pattern**，
所以每个新取值都造一个新键，且过期项永不回收。于是两问：

A. **随数据增长**：按 task 逐个打一遍（真实 task id），量「每键驻留多少字节」，
   再用实测均值线性外推到 tasks 全量与 tasks×metrics×ranges 的组合上限；
B. **随参数滥用**：拿 200 个没人用过的 `metric` 取值打一遍，看条目是不是真的照样长
   （这一条同时是「缓存可被外部当内存放大器」的判据）。

同时用 psutil 记进程 RSS 的实测增量，与深量字节对照 ——  walker 算少了还是算多了，
决定外推该不该打折。沙箱同取证一（sqlite 一致性快照副本 + chdir，只发 GET，
令牌只在进程内用于请求头、绝不打印）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "perf" / "2026-10-31-full-api"
SANDBOX = Path(r"D:/tmp/xh-perf-b4b/sandbox")
MB = 1024 * 1024
sys.path.insert(0, str(PERF))

import b4_cache_census as C1  # noqa: E402  复用深量与启动件

CACHED_ENDPOINTS = [
    "/api/stats/trend", "/api/prices/histogram", "/api/stats/business-kpi",
    "/api/stats/eval-funnel", "/api/stats/overview", "/api/stats/today",
    "/api/vector-admin/status", "/api/maintenance/status",
]


def rss_mb() -> float:
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / MB


def store_state() -> tuple[int, int, dict]:
    """当前驻留：条目数、深量总字节、按限定名分组的条目数"""
    from xianyu_hunter.web import cache as C

    with C._cache_lock:
        items = list(C._cache_store.items())
        seen: set[int] = set()
        total = sum(C1.deep_bytes(v, seen) for (_, v) in items)
        by: dict[str, int] = {}
        for key, _ in items:
            qual = key[0] if isinstance(key, tuple) and key else str(key)
            by[qual] = by.get(qual, 0) + 1
    return len(items), total, by


async def hit(client, path: str) -> int:
    try:
        return (await client.get(path)).status_code
    except Exception:
        return 0


def task_ids(limit: int) -> list[str]:
    db = SANDBOX / "data" / "xianyu.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in con.execute("SELECT id FROM tasks ORDER BY id").fetchall()]
    finally:
        con.close()
    return rows[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=60, help="A 段真实打的 task 数（外推基数）")
    ap.add_argument("--mint", type=int, default=200, help="B 段伪造的 metric 取值个数")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    import asyncio

    import httpx

    print("[1] 沙箱搭建")
    import setup_sandbox

    setup_sandbox.SANDBOX = SANDBOX
    setup_sandbox.OUT = SANDBOX / "output"
    setup_sandbox.build_sandbox()

    app, token = C1.boot_app()
    rss0 = rss_mb()
    n0, b0, _ = store_state()
    print(f"[2] 起点：条目 {n0}，深量 {b0 / 1024:.1f} KB，RSS {rss0:.1f} MB")

    async def run() -> dict:
        out: dict = {}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://s",
                                     timeout=30.0,
                                     headers={"Authorization": f"Bearer {token}"}) as client:
            # A 段前置：把 8 个缓存站点各打一次（含取证一漏掉的 /api/maintenance/status）
            for path in CACHED_ENDPOINTS:
                await hit(client, path)
            nA0, bA0, byA = store_state()
            out["baseline_sweep"] = {"entries": nA0, "bytes": bA0, "by_qualname": byA}
            print(f"[3] 八站点各一次后：条目 {nA0}，深量 {bA0 / 1024:.1f} KB")
            for q, n in sorted(byA.items(), key=lambda kv: -kv[1]):
                print(f"      {q:<34} 条={n}")

            # —— A：按真实 task 逐个打
            ids = task_ids(args.tasks)
            t0 = time.perf_counter()
            codes: dict[str, int] = {}
            for tid in ids:
                for path in (f"/api/stats/trend?metric=events&range_hours=24&task_id={tid}",
                             f"/api/prices/histogram?bins=0&task_id={tid}"):
                    code = str(await hit(client, path))
                    codes[code] = codes.get(code, 0) + 1
            nA1, bA1, _ = store_state()
            per_task_bytes = (bA1 - bA0) / max(len(ids), 1)
            per_task_entries = (nA1 - nA0) / max(len(ids), 1)
            out["per_task"] = {
                "tasks_fired": len(ids), "elapsed_sec": round(time.perf_counter() - t0, 1),
                "entries_added": nA1 - nA0, "bytes_added": bA1 - bA0,
                "mean_bytes_per_task_pair": per_task_bytes,
                "mean_entries_per_task": per_task_entries,
                "codes": codes,
            }
            print(f"[4] A 段：{len(ids)} 个真实 task × 2 个参数化站点 → "
                  f"+{nA1 - nA0} 条 / +{(bA1 - bA0) / 1024:.1f} KB "
                  f"（每 task {per_task_entries:.1f} 条、{per_task_bytes / 1024:.1f} KB，"
                  f"{time.perf_counter() - t0:.1f}s）")

            # —— B：伪造 metric 取值（键无界的直接证明）
            nB0, bB0, _ = store_state()
            t1 = time.perf_counter()
            for i in range(args.mint):
                await hit(client, f"/api/stats/trend?metric=probe{i}&range_hours=24")
            nB1, bB1, _ = store_state()
            out["minted"] = {
                "requests": args.mint, "elapsed_sec": round(time.perf_counter() - t1, 1),
                "entries_added": nB1 - nB0, "bytes_added": bB1 - bB0,
                "mean_bytes_per_minted_entry": (bB1 - bB0) / max(nB1 - nB0, 1),
            }
            print(f"[5] B 段：{args.mint} 个没人用过的 metric → "
                  f"+{nB1 - nB0} 条 / +{(bB1 - bB0) / 1024:.1f} KB"
                  f"（单条 {(bB1 - bB0) / max(nB1 - nB0, 1) / 1024:.2f} KB）")
        return out

    res = asyncio.run(run())
    n_end, b_end, by_end = store_state()
    rss1 = rss_mb()
    res["final"] = {"entries": n_end, "bytes": b_end, "rss_before_mb": round(rss0, 1),
                    "rss_after_mb": round(rss1, 1), "rss_delta_mb": round(rss1 - rss0, 1),
                    "deep_delta_mb": round((b_end - b0) / MB, 3),
                    "by_qualname": by_end}
    print(f"[6] 终点：条目 {n_end}，深量 {b_end / MB:.2f} MB；"
          f"RSS {rss0:.1f} → {rss1:.1f} MB（Δ {rss1 - rss0:+.1f} MB，深量口径 Δ "
          f"{(b_end - b0) / MB:.2f} MB）")

    total_tasks = len(task_ids(10 ** 9))
    per_task = res["per_task"]["mean_bytes_per_task_pair"]
    res["extrapolation"] = {
        "tasks_in_db": total_tasks,
        "bytes_if_every_task_hit_twice": int(per_task * total_tasks),
        "mb_if_every_task_hit_twice": round(per_task * total_tasks / MB, 1),
        "note": ("均值取自 A 段实测（真实 task × {trend, histogram}）；"
                 "前端还会切 7 个 metric × 3 档 range，乘上去即组合上限"),
    }
    print(f"[7] 外推：沙箱共 {total_tasks} 个 task；若每个 task 都被打过一遍 → "
          f"{per_task * total_tasks / MB:.1f} MB")

    out_path = PERF / "results" / "b4_key_ceiling.json"
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n产物 → {out_path}")

    if not args.keep:
        import shutil

        shutil.rmtree(SANDBOX, ignore_errors=True)
        print("沙箱已删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
