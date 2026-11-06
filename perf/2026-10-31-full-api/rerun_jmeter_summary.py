"""把 JMeter 的 jtl 汇总成与回放引擎同口径的 JSON

为什么另写：原生 JMeter 的 `-l` 输出是 CSV（每请求一行），本身没有汇总；要与
`python_replay` 引擎的 TPS / p50 / p95 / p99 直接对比，必须用同一套算法 ——
TPS = 样本数 ÷（末时间戳 − 首时间戳），分位数按「排序后取整索引」，否则两套
工具的数字不可并列。

用法：
    python rerun_jmeter_summary.py D:/tmp/xh-perf/output/jmeter_light_t96.jtl
"""
import csv
import json
import sys
from pathlib import Path


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q / 100 * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 2)


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    span = (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 1000 or 1e-9
    elapsed = sorted(r["el"] for r in rows)
    errors = sum(1 for r in rows if not r["ok"])
    by_label: dict[str, list[dict]] = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)

    endpoints = []
    for label, items in by_label.items():
        vals = sorted(i["el"] for i in items)
        endpoints.append({
            "label": label, "count": len(items),
            "p50": _pct(vals, 50), "p95": _pct(vals, 95), "p99": _pct(vals, 99),
            "max": round(vals[-1], 2),
            "err": sum(1 for i in items if not i["ok"]),
            "codes": {c: sum(1 for i in items if i["code"] == c)
                      for c in {i["code"] for i in items}},
        })
    endpoints.sort(key=lambda e: -e["p99"])

    return {
        "total": total,
        "span_sec": round(span, 1),
        "tps": round(total / span, 1),
        "p50": _pct(elapsed, 50), "p95": _pct(elapsed, 95), "p99": _pct(elapsed, 99),
        "max": round(elapsed[-1], 2),
        "errors": errors,
        "error_rate_pct": round(errors / total * 100, 3) if total else 0,
        "endpoints": endpoints,
    }


def main() -> None:
    jtl = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else jtl.with_suffix(".json")
    rows = []
    with jtl.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "ts": float(r["timeStamp"]),
                "el": float(r["elapsed"]),
                "ok": r["success"] == "true",
                "label": r["label"],
                "code": r["responseCode"],
            })
    if not rows:
        raise SystemExit("jtl 为空")
    summary = summarize(rows)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total", "tps", "p50", "p95", "p99",
                                              "errors", "error_rate_pct")},
                     ensure_ascii=False, indent=1))
    print(f"[json] {out}")


if __name__ == "__main__":
    main()
