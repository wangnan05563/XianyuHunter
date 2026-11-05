"""D3 前提清点：`writes_idem` 组里到底有多少样本真的落到 SQLite

数据源是 2026-10-31 那轮的逐接口聚合（`results/agg_phases.json`），不重新压测。
为什么要先清点：REPORT 表 C 已注明写组 4xx 率 61.5% 是「夹具口径」——合成 query/body
没补齐必填参数，400/404/422 在 FastAPI 校验层就返回了，一次库都没碰。D3 原文的
「批量 UPSERT」只对**真写**有意义，所以先按响应码把 26 个接口拆开，再算各自的
耗时份额（n × avg / Σ(n × avg)），否则会把优化预算花在不写库的请求上。

判据输出：
1. 真写 / 夹具 4xx 的样本占比与耗时占比；
2. 真写接口逐条 avg / p95 / 平均耗时份额；
3. 真写组内「单条写」与「批量写」的形态（人工判读，脚本只给数据）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "perf/2026-10-31-full-api/results"
PHASES = ("baseline_write", "stress_write_t4", "stress_write_t8", "stress_write_t16")


def _load() -> dict[str, Any]:
    return json.loads((RESULTS / "agg_phases.json").read_text(encoding="utf-8"))


def _split(by_endpoint: dict[str, dict[str, Any]]) -> tuple[list, list]:
    real, fixture = [], []
    for label, s in by_endpoint.items():
        codes = s.get("codes") or {}
        ok = codes.get("200", 0)
        total = sum(codes.values()) or s.get("n", 0) or 1
        row = {
            "label": label,
            "n": s.get("n", 0),
            "avg": s.get("avg", 0.0),
            "p95": s.get("p95", 0.0),
            "ok_share": ok / total,
            "codes": codes,
            "work_ms": s.get("n", 0) * s.get("avg", 0.0),
        }
        (real if row["ok_share"] >= 0.5 else fixture).append(row)
    return real, fixture


def _share(rows: list[dict[str, Any]], denom: float) -> list[dict[str, Any]]:
    for r in rows:
        r["cost_share"] = (r["work_ms"] / denom) if denom else 0.0
    return rows


def main() -> None:
    data = _load()
    out: dict[str, Any] = {"phases": {}}
    for phase in PHASES:
        p = data.get(phase)
        if not p:
            continue
        by_ep = p.get("by_endpoint") or {}
        total_work = sum(s.get("n", 0) * s.get("avg", 0.0) for s in by_ep.values())
        real, fixture = _split(by_ep)
        _share(real, total_work)
        _share(fixture, total_work)
        real.sort(key=lambda r: r["cost_share"], reverse=True)
        fixture.sort(key=lambda r: r["cost_share"], reverse=True)

        n_total = sum(s.get("n", 0) for s in by_ep.values())
        n_real = sum(r["n"] for r in real)
        n_fixture = sum(r["n"] for r in fixture)
        out["phases"][phase] = {
            "threads": p.get("threads"),
            "overall": p.get("overall"),
            "n_total": n_total,
            "n_real": n_real,
            "n_fixture": n_fixture,
            "real_sample_share": n_real / n_total if n_total else 0.0,
            "real_cost_share": sum(r["cost_share"] for r in real),
            "real": real,
            "fixture": fixture,
        }

    (RESULTS / "d3_write_inventory.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    t16 = out["phases"].get("stress_write_t16")
    if not t16:
        raise SystemExit("缺少 stress_write_t16")
    print(
        f"[stress_write_t16] 样本 {t16['n_total']} = 真写 {t16['n_real']} "
        f"({t16['real_sample_share']:.1%}) + 夹具4xx {t16['n_fixture']} "
        f"({1 - t16['real_sample_share']:.1%})"
    )
    print(f"  耗时份额：真写 {t16['real_cost_share']:.1%}")
    print(f"\n  {'真写接口':<52}{'n':>6}{'avg':>9}{'p95':>9}{'份额':>8}")
    for r in t16["real"][:15]:
        print(f"  {r['label']:<52}{r['n']:>6}{r['avg']:>9.1f}{r['p95']:>9.1f}"
              f"{r['cost_share']:>8.1%}")
    print(f"\n  {'夹具 4xx（不写库）':<50}{'n':>6}{'avg':>9}{'p95':>9}{'份额':>8}")
    for r in t16["fixture"][:8]:
        print(f"  {r['label']:<52}{r['n']:>6}{r['avg']:>9.1f}{r['p95']:>9.1f}"
              f"{r['cost_share']:>8.1%}")
    print(f"\n结果写入 {RESULTS / 'd3_write_inventory.json'}")


if __name__ == "__main__":
    main()
