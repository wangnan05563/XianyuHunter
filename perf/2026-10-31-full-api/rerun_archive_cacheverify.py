"""归档缓存验证轮的 2 个阶段样本（逐轮唯一命名，不覆盖基线）"""
import gzip
import json
import shutil
from pathlib import Path

SRC = Path(r"D:\tmp\xh-perf\output")
DST = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\results")

for phase, wall in (("stress_heavy_t16", 60.5), ("stress_heavy_t64", 60.3)):
    with (SRC / f"{phase}.jsonl").open("rb") as f_in, \
            gzip.open(DST / f"rerun_cacheverify_{phase}.jsonl.gz", "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    print(f"[gz] rerun_cacheverify_{phase}.jsonl.gz")

summary = {
    "note": "category-comparison 挂 60 s 缓存后的重读并发档（2 阶段，60 s 各）",
    "wall_sec": {"stress_heavy_t16": 60.5, "stress_heavy_t64": 60.3},
    "endpoint": "GET /api/prices/category-comparison",
    "before": {"stress_heavy_t16": {"avg": 2433.7, "p99": 3299.2, "n": 233},
               "stress_heavy_t64": {"avg": 3436.4, "p99": 5268.7, "n": 236}},
    "after": {"stress_heavy_t16": {"avg": 103.2, "p50": 92.3, "p95": 211.1, "p99": 325.3, "max": 479.7},
              "stress_heavy_t64": {"avg": 372.4, "p50": 351.6, "p95": 654.7, "p99": 712.4, "max": 721.3}},
    "overall": {"stress_heavy_t16": {"n": 10651, "avg": 89.3, "p50": 79.5, "p95": 166.2,
                                     "p99": 251.1, "max": 603.9},
                "stress_heavy_t64": {"n": 11058, "avg": 345.0, "p50": 322.5, "p95": 585.8,
                                     "p99": 720.8, "max": 947.5}},
    "before_overall": {"stress_heavy_t16": {"n": 10136, "avg": 94.1, "p50": 33.9, "p95": 121.1,
                                            "p99": 2637.4},
                       "stress_heavy_t64": {"n": 10147, "avg": 375.9, "p50": 295.5, "p95": 620.5,
                                            "p99": 3640.1}},
}
(DST / "rerun_cacheverify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print("[json] rerun_cacheverify_summary.json")
