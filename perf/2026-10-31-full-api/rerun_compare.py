"""同一套 JMX 复跑 vs 2026-10-31 基线：逐阶段 + 逐接口对照

为什么用 `agg_phases.json` 的 `by_endpoint` 而不是 `per_endpoint.json`：
后者把基线全部 10 个阶段（含 t2~t64 阶梯与 30 min 长跑）混在一起算 avg，
而复跑只跑了 6 个阶段 —— 混算口径不可比。同名阶段的 by_endpoint 才是同构。

判据口径：
- 逐阶段 overall（n / tps / avg / p50 / p95 / p99 / 缺陷率）直接可比；
- 逐接口按 label 交配，输出 avg 与 p95 的降幅排序；
- 只统计两边样本数都 ≥20 的接口，避免长尾噪声。
"""
import json
from pathlib import Path

BASE = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\results\agg_phases.json")
RERUN = Path(r"D:\tmp\xh-perf\output\agg_phases.json")
OUT = Path(r"D:\tmp\xh-perf\output\rerun_vs_baseline.json")

PHASES = ["baseline_heavy", "baseline_light", "stress_heavy_t16", "stress_heavy_t64",
          "stress_light_t16", "stress_light_t48", "stress_light_t96",
          "baseline_write", "stress_write_t16"]
MIN_N = 20

base = json.loads(BASE.read_text(encoding="utf-8"))
rerun = json.loads(RERUN.read_text(encoding="utf-8"))

result = {"phases": {}, "endpoints": {}}

print(f"{'阶段':<20}{'指标':>8}{'基线':>10}{'复跑':>10}{'变化':>10}")
for phase in PHASES:
    if phase not in base or phase not in rerun:
        print(f"{phase:<20} 缺一侧，跳过")
        continue
    bo, ro = base[phase]["overall"], rerun[phase]["overall"]
    rows = {}
    for key in ("n", "tps", "avg", "p50", "p95", "p99", "max", "defect_pct", "c4_pct"):
        b, r = bo.get(key), ro.get(key)
        rows[key] = {"base": b, "rerun": r}
        if key in ("tps", "defect_pct", "c4_pct", "n"):
            delta = f"{(r - b) / b * 100:+.1f}%" if b else "n/a"
        else:
            delta = f"{(r - b) / b * 100:+.1f}%" if b else "n/a"
        if key in ("n", "tps", "avg", "p95", "p99", "defect_pct"):
            print(f"{phase:<20}{key:>8}{b:>10.1f}{r:>10.1f}{delta:>10}")
    result["phases"][phase] = rows

    bep, rep = base[phase].get("by_endpoint", {}), rerun[phase].get("by_endpoint", {})
    common = [k for k in bep if k in rep and bep[k].get("n", 0) >= MIN_N
              and rep[k].get("n", 0) >= MIN_N]
    eps = []
    for label in common:
        b, r = bep[label], rep[label]
        b_avg, r_avg = b.get("avg") or 0, r.get("avg") or 0
        b_p95, r_p95 = b.get("p95") or 0, r.get("p95") or 0
        eps.append({
            "label": label,
            "n_base": b.get("n"), "n_rerun": r.get("n"),
            "avg_base": b_avg, "avg_rerun": r_avg,
            "p95_base": b_p95, "p95_rerun": r_p95,
            "avg_delta_pct": round((r_avg - b_avg) / b_avg * 100, 1) if b_avg else None,
            "p95_delta_pct": round((r_p95 - b_p95) / b_p95 * 100, 1) if b_p95 else None,
        })
    eps.sort(key=lambda e: (e["avg_delta_pct"] if e["avg_delta_pct"] is not None else 0))
    result["endpoints"][phase] = eps

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

print("\n=== 逐接口 avg 变化最大的 12 条（baseline_light）")
for e in result["endpoints"].get("baseline_light", [])[:12]:
    print(f"  {e['label']:<48} {e['avg_base']:>8.1f} → {e['avg_rerun']:>8.1f} ms "
          f"({e['avg_delta_pct']:>+7.1f}%)  n={e['n_base']}/{e['n_rerun']}")

print("\n=== 逐接口 avg 反而变慢的（baseline_light）")
slower = [e for e in result["endpoints"].get("baseline_light", [])
          if e["avg_delta_pct"] is not None and e["avg_delta_pct"] > 20]
for e in slower[-10:]:
    print(f"  {e['label']:<48} {e['avg_base']:>8.1f} → {e['avg_rerun']:>8.1f} ms "
          f"({e['avg_delta_pct']:>+7.1f}%)")

print(f"\n=== 写组逐接口（baseline_write，avg 降幅前 8）")
for e in result["endpoints"].get("baseline_write", [])[:8]:
    print(f"  {e['label']:<48} {e['avg_base']:>8.1f} → {e['avg_rerun']:>8.1f} ms "
          f"({e['avg_delta_pct']:>+7.1f}%)  n={e['n_base']}/{e['n_rerun']}")

print(f"\n结果写入 {OUT}")
