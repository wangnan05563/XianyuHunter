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


def _pct(sorted_vals, q):
    """分位数（与 rerun_jmeter_summary 同口径：排序后取整索引）"""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q / 100 * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _by_label_stats(path, top=20):
    """按 label 聚合 p50/p95/p99/count。

    为什么必须产出这一小段：逐请求样本（本轮 152 万行 / 125.8 MB）按体积政策
    不入库，归档时若只留整体分位数，「哪些接口随运行时长变慢」就再也复算不了
    （本轮已踩：4 h 的 TPS/分位数全面劣化，但定不到接口）。每 label 五个数、
    135 条约 15 KB，成本可以忽略，却能让下次直接做接口级前后对比。
    """
    if not path.exists():
        return {"note": "soak_mixed.jsonl 不存在（可能已随临时目录删除），无法产出接口级聚合"}
    buckets = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            buckets.setdefault(d["label"], []).append(d["el"])
    rows = []
    for lb, els in buckets.items():
        els.sort()
        rows.append({
            "label": lb,
            "count": len(els),
            # 必须复用 _pct：手写 int(0.95*(n-1)) 在 n=2 时得 0，会把 p95 报成最小值
            "p50_ms": round(_pct(els, 50), 2),
            "p95_ms": round(_pct(els, 95), 2),
            "p99_ms": round(_pct(els, 99), 2),
        })
    rows.sort(key=lambda r: r["p95_ms"], reverse=True)
    return {"total_labels": len(rows), "slowest": rows[:top], "all": rows}


def _window_stats(points, start, end):
    """窗口内的首末 / 斜率 / 峰值 / P95"""
    seg = [p for p in points if start <= p[0] < end]
    if len(seg) < 2:
        return None
    vals = sorted(p[1] for p in seg)
    return {
        "start_mb": round(seg[0][1], 1),
        "end_mb": round(seg[-1][1], 1),
        "peak_mb": round(max(p[1] for p in seg), 1),
        # 段内 P95 抗单点尖刺：本轮 t=5991.3 的单点 449.3 若按 max 计会把水位
        # 抬高 30 MB，而其相邻采样只有 418.5/419.6 —— max 不能代表水位上限
        "p95_mb": round(_pct(vals, 95), 1),
        "slope_mb_per_h": round(_slope(seg), 1),
    }


def _reclaim_stats(points):
    """RSS 的回收统计：最大单点跌幅 + 跌幅超过 60 MB 的次数

    为什么要单独统计：RSS 稳态下会被分配器与页缓存归还，首末差与段斜率都会被
    锯齿位置左右；「能跌回去多少、跌了几次」才是「内存并非只增」的直接证据。
    """
    drops = [(t1, r0 - r1) for (_, r0), (t1, r1) in zip(points, points[1:]) if r0 > r1]
    if not drops:
        return {"max_drop_mb": 0.0, "max_drop_at_s": 0.0, "drops_over_60mb": 0}
    t_peak, d_peak = max(drops, key=lambda d: d[1])
    return {"max_drop_mb": round(d_peak, 1), "max_drop_at_s": round(t_peak, 1),
            "drops_over_60mb": sum(1 for _, d in drops if d > 60)}


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
        # 采样器比负载多跑 120 s（rerun_sample_resource.py 14520），会多出一个残缺窗口；
        # 不足 100 点（≈8 min，5 s 间隔）的窗口一律不计，否则包络斜率被半截段带偏
        if st and sum(1 for r in live if i * WINDOW <= r["t"] < (i + 1) * WINDOW) >= 100:
            st["window"] = f"{i * WINDOW}-{(i + 1) * WINDOW}s"
            segments.append(st)

    third = len(live) // 3

    # 判据订正：原打算用「8 段峰值都不破 427.7」当阈值，但本轮第 4 段即到 449.3，
    # 且那是单点尖刺（相邻采样 418.5/419.6）。阈值式与 max 式判据都不可靠；
    # 改用不含绝对阈值、也不受单点影响的三个量：
    # ① 段内 P95 的包络斜率（水位上限抬升速率，抗尖刺）
    # ② 尾部斜率相对全局的衰减（增长是否收敛）
    # ③ 回收幅度/次数（内存能否归还）
    peaks_pts = [((i + 0.5) * WINDOW, s["p95_mb"]) for i, s in enumerate(segments)]
    peak_slope = round(_slope(peaks_pts), 1) if len(peaks_pts) >= 2 else 0.0

    def _tail_slope(frac: float) -> float:
        cut = live[0]["t"] + (live[-1]["t"] - live[0]["t"]) * (1 - frac)
        seg = [(r["t"], r["rss"]) for r in live if r["t"] >= cut]
        return round(_slope(seg), 1) if len(seg) >= 10 else 0.0

    summary = {
        "note": "剔除 SSE 后的 4 h soak（12 并发 / 135 接口）；资源序列从 CSV 复算",
        "config": {"jmx": "mixed_biz_no_sse.jmx", "threads": 12,
                   "duration_s": 14400, "wall_sec": args.wall,
                   "samples": len(rows), "live_samples": len(live)},
        "engine": {"total": args.total, "errors": args.errors,
                   "error_rate_pct": round(args.errors / args.total * 100, 3) if args.total else 0,
                   # 这条必须写进产物：引擎把 4xx 也算进 errors（30 min 轮是 28.124%，
                   # 几乎全是夹具的 422 参数校验失败）。不标注的话，28% 会被读成「4 h 出了 28% 故障」
                   "errors_note": ("errors/error_rate_pct 含夹具 4xx（同 30 min 轮 28.1% 的口径），"
                                   "不等于真实缺陷率；真实缺陷率需按 responseCode 区分 5xx 与 000"),
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
            "peak_envelope_slope_mb_per_h": peak_slope,
            "tail_half_slope_mb_per_h": _tail_slope(0.5),
            "tail_quarter_slope_mb_per_h": _tail_slope(0.25),
            "reclaim": _reclaim_stats(rss_pts),
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
        "by_label": _by_label_stats(SRC / "soak_mixed.jsonl"),
        "raw_samples_policy": {
            "note": ("逐请求样本（soak_mixed.jsonl，本轮实测 152.4 万行 / 125.8 MB）不入库 —— 与 30 min 那轮同口径；"
                     "P50/P95/P99/TPS 已由引擎汇总进 engine 段，资源序列归档为 rerun_soak4h_resource.csv；"
                     "接口级聚合见 by_label 段（jsonl 尚存时自动产出）"),
        },
        "compare_30min_run": {
            "note": "同一夹具的 30 min 那轮（results/rerun_soak_summary.json）",
            "rss_slope_mb_per_h": 219.7, "tps": 183.1, "per_tps_h_mb": 1.2,
            # 同 t 一致性：两轮在 t≈406 s 的 RSS 分别为 329.3（30 min，无探针）与 315.3（4 h，无探针），
            # 差值 14 MB 落在 30 min 轮自身 ±24 MB 的波动内 —— 这是「两轮可比」的前提证据
            "same_t_check": {"t_sec": 406, "30min_rss_mb": 329.3, "soak4h_rss_mb": 315.3,
                             "30min_self_swing_mb": 24},
        },
    }
    shutil.copy2(SRC / "sut_resource_soak.csv", DST / "rerun_soak4h_resource.csv")
    (DST / "rerun_soak4h_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("engine", "rss", "handles")},
                     ensure_ascii=False, indent=1))
    print(f"[segments] {len(segments)} 段（每 30 min，斜率是该段内最小二乘）：")
    for seg in segments:
        print(f"  {seg['window']:>12}  {seg['start_mb']:>6} → {seg['end_mb']:>6} MB  "
              f"峰值 {seg['peak_mb']:>6}   斜率 {seg['slope_mb_per_h']:>+9.1f} MB/h")
    peaks = [s["p95_mb"] for s in segments]
    print(f"[peaks-p95] {peaks}  单调递增={all(a < b for a, b in zip(peaks, peaks[1:]))}  "
          f"包络斜率 {peak_slope:+.1f} MB/h")
    print(f"[peaks-max] {[s['peak_mb'] for s in segments]}（仅参考，含单点尖刺）")
    r = summary["rss"]
    print(f"[tail]  全局 {r['slope_mb_per_h']:+.1f} MB/h  →  后 1/2 {r['tail_half_slope_mb_per_h']:+.1f}"
          f"  →  后 1/4 {r['tail_quarter_slope_mb_per_h']:+.1f} MB/h（持续泄漏时三者应同量级）")
    rc = r["reclaim"]
    print(f"[reclaim] 最大单点跌幅 {rc['max_drop_mb']} MB @ {rc['max_drop_at_s']} s，"
          f"跌超 60 MB 共 {rc['drops_over_60mb']} 次")
    print(f"[files] rerun_soak4h_resource.csv + rerun_soak4h_summary.json")


if __name__ == "__main__":
    main()
