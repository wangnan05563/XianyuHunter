"""从 soak 的 RSS 序列算斜率（对照基线 +75.9 MB/h），并区分「爬升」与「预热平台」"""
import csv
import statistics
from pathlib import Path

CSV = Path(r"D:\tmp\xh-perf\output\sut_resource_soak.csv")


def load():
    rows = []
    with CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": float(r["elapsed_s"]),
                "rss": float(r["rss_mb"]),
                "private": float(r["private_mb"]),
                "handles": int(r["handles"]),
            })
    return rows


def slope_mb_per_h(xs, ys):
    """最小二乘斜率（MB/h）：样本等间隔，直接按 t 秒换算"""
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return (num / den) * 3600 if den else 0.0


def main() -> None:
    rows = load()
    if len(rows) < 10:
        print(f"样本太少（{len(rows)}），无法判定")
        return
    # 去掉关停后的 0 值行（基线那次就是被 0 值行稀释出了「回落到 350 MB」的假象）
    live = [r for r in rows if r["rss"] > 0]
    dur = (live[-1]["t"] - live[0]["t"]) / 3600

    print(f"样本 {len(rows)}（存活 {len(live)}），窗口 {dur:.2f} h")
    print(f"RSS    {live[0]['rss']:.1f} → {live[-1]['rss']:.1f} MB，"
          f"峰值 {max(r['rss'] for r in live):.1f}，斜率 {slope_mb_per_h([r['t'] for r in live], [r['rss'] for r in live]):+.1f} MB/h")
    print(f"私有内存 {live[0]['private']:.1f} → {live[-1]['private']:.1f} MB，"
          f"斜率 {slope_mb_per_h([r['t'] for r in live], [r['private'] for r in live]):+.1f} MB/h")
    print(f"句柄 {live[0]['handles']} → {live[-1]['handles']}，"
          f"峰值 {max(r['handles'] for r in live)}")

    # 分窗：前 1/3 与后 1/3 的均值差，区分「预热平台」与「持续爬升」
    third = len(live) // 3
    head = statistics.fmean(r["rss"] for r in live[:third])
    tail = statistics.fmean(r["rss"] for r in live[-third:])
    print(f"前 1/3 均值 {head:.1f} MB → 后 1/3 均值 {tail:.1f} MB（差 {tail - head:+.1f}）")
    print(f"基线（2026-10-31 30 min）：RSS 346.8 → 378.4 MB，+75.9 MB/h")


if __name__ == "__main__":
    main()
