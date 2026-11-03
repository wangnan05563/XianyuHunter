"""D1 取证 · 第三层：专用道容量到底要多少（按实测吞吐 × 占用时长算，不拍脑袋）

沙箱证明「cap=4 能把廉价接口从 2 s 拉回 30 ms」，但没回答另一半问题：**把慢接口关进 4 个槽位，
慢接口自己会不会开始排队？** 若专用道被自己堵死，D1 只是把饥饿从廉价接口挪到了慢接口。

算法用 Little's law：某接口的**平均在途数 = 吞吐 × 单次占用时长**。
- 慢族在途数之和 = 专用道需要容纳的并发；
- 专用道吞吐上限 = cap ÷ 平均占用时长（再与实测需求比较，超出即排队）。

占用时长取**基线（未饱和）档的 avg**：饱和档的 avg 里含排队，用它算会把需求估高（自证式）。
本脚本只读 results/agg_phases.json，不再打服务端。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "perf" / "2026-10-31-full-api" / "results"

# 入道名单的判据（与 slow_lane.py 的 SLOW_ROUTES 一一对应，改动须同步）
SLOW_KEYS = (
    "/api/anticrawl/strategy",
    "/api/vector-admin/snapshots",
    "/api/maintenance/status",
    "/api/maintenance/cache-scan",
    "/api/logs",
    "/api/config/raw",
    "/api/config/share",
    "/api/config/export",
    "/api/export/",
    "/api/db-admin/tables/",
)
# 原文点名但只有 422/400/404 样本（从未真正执行）的接口：不作为入道依据
NOT_EVIDENCED = (
    "/api/logs/search",
    "/api/logs/export",
    "/api/logs/request/",
    "/api/error-logs",
)


def is_slow(label: str) -> bool:
    # 表里的 label 形如 "GET /api/xxx"；只看路径部分
    path = label.split(" ", 1)[-1]
    return any(k in path for k in SLOW_KEYS)


def is_not_evidenced(label: str) -> bool:
    path = label.split(" ", 1)[-1]
    return any(path.startswith(n) or n in path for n in NOT_EVIDENCED)


def pricing_phase(name: str) -> str:
    """占用时长的取价档位：读轻→baseline_light，读重→baseline_heavy，写→baseline_write。
    同一族里饱和档（stress_*）的 avg 含排队，用它定价会循环论证。"""
    if "write" in name:
        return "baseline_write"
    if "heavy" in name:
        return "baseline_heavy"
    return "baseline_light"


def analyse(phase: dict, price: dict, cap: int, price_name: str) -> dict:
    """需求（吞吐）取自被分析档位，占用时长取自未饱和的定价档位。"""
    demand = {label: st.get("tps", 0.0) for label, st in phase["by_endpoint"].items()}

    def hold_of(label: str) -> float:
        src = price.get(label) or phase["by_endpoint"].get(label)
        return (src["avg"] / 1000.0) if src else 0.0

    slow = {k: v for k, v in demand.items() if is_slow(k) and not is_not_evidenced(k)}
    hold = {k: hold_of(k) for k in slow}
    inflight = {k: v * hold[k] for k, v in slow.items()}
    total_inflight = sum(inflight.values())
    offered_rps = sum(slow.values())
    mean_hold = (total_inflight / offered_rps) if offered_rps else 0.0
    lane_capacity = cap / mean_hold if mean_hold else float("inf")
    return {
        "priced_from": price_name,
        "slow_endpoints": len(slow),
        "slow_offered_rps": round(offered_rps, 2),
        "slow_mean_hold_ms": round(mean_hold * 1000.0, 1),
        "slow_inflight_needed": round(total_inflight, 2),
        "lane_cap": cap,
        "lane_throughput_ceiling_rps": round(lane_capacity, 2),
        "lane_saturates": offered_rps > lane_capacity,
        "worst_hold_ms": round(max([hold[k] * 1000.0 for k in slow], default=0.0), 1),
        "per_endpoint": {
            k: {"rps": round(v, 2), "hold_ms": round(hold[k] * 1000.0, 1),
                "inflight": round(inflight[k], 2)}
            for k, v in sorted(slow.items(), key=lambda kv: -kv[1] * hold[kv[0]])
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg", default=str(RESULTS / "agg_phases.json"))
    ap.add_argument("--cap", type=int, default=4)
    ap.add_argument("--out", default=str(RESULTS / "d1_lane_budget.json"))
    args = ap.parse_args()
    agg = json.loads(Path(args.agg).read_text(encoding="utf-8"))
    lines: list[str] = []

    def say(s: str = "") -> None:
        lines.append(s)
        print(s.encode("ascii", "backslashreplace").decode("ascii"))

    say("=" * 92)
    say(f"[D1 容量账] 专用道 cap={args.cap}；占用时长一律用基线档 avg（饱和档含排队，不能用）")
    say("=" * 92)
    out = {}
    for name, phase in agg.items():
        price_name = pricing_phase(name)
        price = agg.get(price_name, phase)["by_endpoint"]
        r = analyse(phase, price, args.cap, price_name)
        out[name] = r
        say(f"  {name:<20} 慢族接口={r['slow_endpoints']:<3} 需求={r['slow_offered_rps']:<6} rps  "
            f"平均占用={r['slow_mean_hold_ms']:<7} ms  在途需求={r['slow_inflight_needed']:<6}  "
            f"专用道上限={r['lane_throughput_ceiling_rps']:<7} rps  是否堵道={r['lane_saturates']}")
    say("")
    say("[判读] 在途需求 = Σ(吞吐 × 占用时长)。需求 < cap 说明 4 个槽位够用，慢族不会因限流而排队；")
    say("       专用道上限 > 实测需求 是同一件事的另一个方向。两个都成立才叫容量选对了。")
    say("")
    key = "stress_light_t96" if "stress_light_t96" in out else next(iter(out))
    say(f"[{key} 逐接口在途贡献]")
    for k, v in out[key]["per_endpoint"].items():
        say(f"   {k:<46} rps={v['rps']:<6} hold={v['hold_ms']:<8} ms 在途={v['inflight']}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"\n[落盘] {args.out}")
    log = Path(args.out).with_suffix(".log")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
