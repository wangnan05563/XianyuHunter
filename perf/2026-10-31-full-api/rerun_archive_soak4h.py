"""归档 4 h soak：资源序列从 CSV 复算，引擎汇总由命令行传入

为什么不像 30 min 那轮（`rerun_archive_soak.py`）那样把数字写死：4 h 的判据是
「斜率是否随窗口变长而下降」，必须能从原始序列复算，而且要按 30 min 分段看衰减；
引擎的 TPS / 分位数由它自己汇总打印，从 stdout 抄进参数即可，不重复实现分位数。

用法（引擎跑完后）：
    python rerun_archive_soak4h.py --total 2190000 --wall 14401.2 \
        --p50 52.1 --p95 150.3 --p99 330.0
"""
import argparse
import csv
import json
import shutil
import statistics
from pathlib import Path

SRC = Path(r"D:\tmp\xh-perf\output")
DST = Path(__file__).resolve().parent / "results"
WINDOW = 1800          # 30 min 一段，与 30 min 那轮的窗口同宽，便于直接对比


def _slope(points):
    """最小二乘斜率（MB/h）；points 为 [(t_sec, mb)]"""
    n = len(points)
    mt = sum(p[0] for p in points) / n
    mr = sum(p[1] for p in points) / n
    num = sum((p[0] - mt) * (p[1] - mr) for p in points)
    den = sum((p[0] - mt) ** 2 for p in points)
    return (num / den) * 3600 if den else 0.0


def _window_stats(points, start, end):
    """窗口内的首末 / 斜率 / 峰值"""
    seg = [p for p in points if start <= p[0] < end]
    if len(seg) < 2:
        return None
    return {
        "start_mb": round(seg[0][1], 1),
        "end_mb": round(seg[-1][1], 1),
        "peak_mb": round(max(p[1] for p in seg), 1),
        "slope_mb_per_h": round(_slope(seg), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--wall", type=float, required=True)
    ap.add_argument("--p50", type=float, required=True)
    ap.add_argument("--p95", type=float, required=True)
    ap.add_argument("--p99", type=float, required=True)
    ap.add_argument("--errors", type=int, default=0)
    args = ap.parse_args()

    rows = []
    with (SRC / "sut_resource_soak.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": float(r["elapsed_s"]),
                "rss": float(r["rss_mb"]),
                "private": float(r["private_mb"]),
                "handles": int(r["handles"]),
            })
    # 关停后的 0 值行必须剔掉，否则斜率被稀释（REPORT §6.8 踩过）
    live = [r for r in rows if r["rss"] > 0]
    tps = args.total / args.wall
    rss_pts = [(r["t"], r["rss"]) for r in live]
    priv_pts = [(r["t"], r["private"]) for r in live]
    dur_h = (live[-1]["t"] - live[0]["t"]) / 3600

    segments = []
    for i in range(int(dur_h * 3600 // WINDOW) + 1):
        st = _window_stats(rss_pts, i * WINDOW, (i + 1) * WINDOW)
        if st:
            st["window"] = f"{i * WINDOW}-{(i + 1) * WINDOW}s"
            segments.append(st)

    third = len(live) // 3
    summary = {
        "note": "剔除 SSE 后的 4 h soak（12 并发 / 135 接口）；资源序列从 CSV 复算",
        "config": {"jmx": "mixed_biz_no_sse.jmx", "threads": 12,
                   "duration_s": 14400, "wall_sec": args.wall,
                   "samples": len(rows), "live_samples": len(live)},
        "engine": {"total": args.total, "errors": args.errors,
                   "error_rate_pct": round(args.errors / args.total * 100, 3) if args.total else 0,
                   "p50_ms": args.p50, "p95_ms": args.p95, "p99_ms": args.p99,
                   "tps": round(tps, 1)},
        "rss": {
            "start_mb": round(live[0]["rss"], 1),
            "end_mb": round(live[-1]["rss"], 1),
            "peak_mb": round(max(r["rss"] for r in live), 1),
            "slope_mb_per_h": round(_slope(rss_pts), 1),
            "per_tps_h_mb": round(_slope(rss_pts) / tps, 2),
            "head_third_mean_mb": round(statistics.fmean(r["rss"] for r in live[:third]), 1),
            "tail_third_mean_mb": round(statistics.fmean(r["rss"] for r in live[-third:]), 1),
        },
        "private": {
            "start_mb": round(live[0]["private"], 1),
            "end_mb": round(live[-1]["private"], 1),
            "peak_mb": round(max(r["private"] for r in live), 1),
            "slope_mb_per_h": round(_slope(priv_pts), 1),
        },
        "handles": {"start": live[0]["handles"], "end": live[-1]["handles"],
                    "peak": max(r["handles"] for r in live)},
        "segments_30min": segments,
        "compare_30min_run": {
            "note": "同一夹具的 30 min 那轮（results/rerun_soak_summary.json）",
            "rss_slope_mb_per_h": 219.7, "tps": 183.1, "per_tps_h_mb": 1.2,
        },
    }
    shutil.copy2(SRC / "sut_resource_soak.csv", DST / "rerun_soak4h_resource.csv")
    (DST / "rerun_soak4h_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("engine", "rss", "handles")},
                     ensure_ascii=False, indent=1))
    print(f"[segments] {len(segments)} 段；归档 rerun_soak4h_resource.csv + rerun_soak4h_summary.json")


if __name__ == "__main__":
    main()
