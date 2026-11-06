"""归档干净 soak 的**资源序列与汇总**；33 万行原始样本不入库

取舍理由：330 k 行 `.jsonl` 本身约 33 MB，而它的价值只在「可复算 P50/P95」——
那些数字已由引擎汇总进 summary；真正不可再生的证据是**RSS/句柄序列**（引擎没有
resource_monitor，只能靠外部采样），所以只归档这两样。
"""
import gzip
import json
import shutil
from pathlib import Path

SRC = Path(r"D:\tmp\xh-perf\output")
DST = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\results")

shutil.copy2(SRC / "sut_resource_soak.csv", DST / "rerun_soak_resource_r1.csv")
print("[csv] rerun_soak_resource_r1.csv")

summary = {
    "note": "剔除 SSE（links/live）后的 30 min soak；上一轮被 SSE 挂起污染，已作废",
    "config": {"jmx": "mixed_biz_no_sse.jmx", "threads": 12, "duration_s": 1800,
               "wall_sec": 1801.8},
    "engine": {"total": 329975, "errors": 92802, "error_rate_pct": 28.124,
               "p50_ms": 50.93, "p95_ms": 144.35, "p99_ms": 314.35,
               "tps": round(329975 / 1801.8, 1)},
    "baseline_v2": {"total": 107324, "wall_sec": 1801.4, "p50_ms": 198.3,
                    "p95_ms": 545.0, "p99_ms": 1194.5, "tps": 59.6},
    "rss": {"start_mb": 202.5, "end_mb": 387.9, "peak_mb": 419.5,
            "slope_mb_per_h": 219.7,
            "per_tps_h_mb": round(219.7 / round(329975 / 1801.8, 1), 2)},
    "rss_baseline": {"start_mb": 346.8, "end_mb": 378.4, "slope_mb_per_h": 75.9,
                     "per_tps_h_mb": round(75.9 / 59.6, 2)},
    "handles": {"start": 424, "end": 634, "peak": 653, "baseline": "627~631 恒定"},
    "polluted_r1": {"note": "首次 soak：links/live 的 SSE 流挂死引擎线程，已作废",
                    "total": 13139, "tps": 7.3, "max_ms": 1632802.3,
                    "rss_slope_mb_per_h": 34.1},
}
(DST / "rerun_soak_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print("[json] rerun_soak_summary.json")
