"""A/B 对照：把 `PRAGMA cache_size` 从 20 MB 降到 2 MB，RSS 增量塌多少

为什么要这个对照：B8 的「RSS 爬升」在 30 min 尺度上与慢泄漏不可区分，而
`tracemalloc` 在本应用上两次都不可用（见 HANDOFF §7）。`sys.getallocatedblocks()`
只能回答「Python 堆在不在涨」；要把剩余部分钉到具体来源，最省事的做法是**单变量
对照** —— 只改每个 SQLite 连接的页缓存上限，同一套夹具、同样 5 min、同样 12 并发。

两组都是冷启动后的同形状采样（60/120/180/240/300 s），但**压测起始时刻不同**
（B 组的采样在第一点就已是 threads=32，A 组要到第二点才涨到 30），所以判据取
「稳定段（t≥180）的 RSS 斜率」，并用各自引擎报告的 TPS 归一化。
"""
import json
from pathlib import Path

OUT = Path(r"D:\tmp\xh-perf\output")
GROUPS = {
    "A_cache_20mb": {
        "heap": OUT / "heap_series_A_cs20000.jsonl",
        "engine": {"total": 46048, "wall_sec": 301.5,
                   "p50_ms": 57.69, "p95_ms": 167.83, "p99_ms": 434.11},
    },
    "B_cache_2mb": {
        "heap": OUT / "heap_series_B_cs2000.jsonl",
        "engine": {"total": 34657, "wall_sec": 300.8,
                   "p50_ms": 71.1, "p95_ms": 226.54, "p99_ms": 572.63},
    },
}
STABLE_T = 180      # 线程池与连接池都已张开，之后才是稳态斜率


def _slope(points):
    """最小二乘斜率（MB/h），points 为 [(t, rss)]"""
    n = len(points)
    mt = sum(p[0] for p in points) / n
    mr = sum(p[1] for p in points) / n
    num = sum((p[0] - mt) * (p[1] - mr) for p in points)
    den = sum((p[0] - mt) ** 2 for p in points)
    return (num / den) * 3600 if den else 0.0


def main() -> None:
    summary = {}
    for name, cfg in GROUPS.items():
        rows = [json.loads(line) for line in cfg["heap"].read_text(encoding="utf-8").splitlines()
                if line.strip() and "error" not in line]
        tps = cfg["engine"]["total"] / cfg["engine"]["wall_sec"]
        stable = [(r["t"], r["rss_mb"]) for r in rows if r["t"] >= STABLE_T]
        first, last = rows[0], rows[-1]
        summary[name] = {
            "samples": len(rows),
            "tps": round(tps, 1),
            "rss_first": {"t": first["t"], "mb": first["rss_mb"]},
            "rss_last": {"t": last["t"], "mb": last["rss_mb"]},
            "rss_delta_all_mb": round(last["rss_mb"] - first["rss_mb"], 1),
            "rss_slope_stable_mb_per_h": round(_slope(stable), 1),
            "per_tps_h_mb": round(_slope(stable) / tps, 1),
            # Python 堆两个口径：block 数（pymalloc）与 GC 可见对象数
            "blocks_delta": last["blocks"] - first["blocks"],
            "objs_delta": last["objs"] - first["objs"],
            "threads_max": max(r["threads"] for r in rows),
            "engine": {k: v for k, v in cfg["engine"].items() if k != "wall_sec"},
        }
    a, b = summary["A_cache_20mb"], summary["B_cache_2mb"]
    summary["_ratio"] = {
        "note": "B/A，<1 表示小页缓存下增量更小",
        "rss_slope_stable": round(b["rss_slope_stable_mb_per_h"] / a["rss_slope_stable_mb_per_h"], 2),
        "per_tps_h": round(b["per_tps_h_mb"] / a["per_tps_h_mb"], 2),
        "tps": round(b["tps"] / a["tps"], 3),
        "blocks_delta": round(b["blocks_delta"] / a["blocks_delta"], 3),
        "objs_delta": round(b["objs_delta"] / a["objs_delta"], 3),
    }
    out = Path(r"D:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api"
               r"\results\rerun_heap_ab_summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
