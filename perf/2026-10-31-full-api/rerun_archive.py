"""把本次复跑的产物按「逐轮唯一」命名归档进仓库 results/。

只归档能支撑结论的文件：逐请求原始样本（gzip-9，与基线同口径）、聚合 JSON、
对照表，以及实跑墙钟（引擎日志本身被 .gitignore 忽略，转录成 JSON 才留得住）。
"""
import gzip
import json
import shutil
from pathlib import Path

SRC = Path(r"D:\tmp\xh-perf\output")
DST = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\results")

PHASES = ["baseline_heavy", "stress_heavy_t16", "stress_heavy_t64",
          "baseline_light", "stress_light_t16", "stress_light_t48",
          "stress_light_t96", "baseline_write", "stress_write_t16"]
PLAIN = {
    "agg_phases.json": "rerun_agg_phases.json",
    "per_endpoint.json": "rerun_per_endpoint.json",
    "endpoint_table.md": "rerun_endpoint_table.md",
    "ladder_table.md": "rerun_ladder_table.md",
    "rerun_vs_baseline.json": "rerun_vs_baseline.json",
}

for phase in PHASES:
    src = SRC / f"{phase}.jsonl"
    dst = DST / f"rerun_{phase}.jsonl.gz"
    with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    print(f"[gz] {dst.name}: {src.stat().st_size / 1e6:.2f} MB → {dst.stat().st_size / 1e6:.2f} MB")

for src_name, dst_name in PLAIN.items():
    shutil.copy2(SRC / src_name, DST / dst_name)
    print(f"[cp] {dst_name}")

WALL = {"baseline_heavy": 180.0, "stress_heavy_t16": 60.3, "stress_heavy_t64": 60.2,
        "baseline_light": 180.1, "stress_light_t16": 60.0, "stress_light_t48": 60.2,
        "stress_light_t96": 60.2, "baseline_write": 180.0, "stress_write_t16": 60.1}
(DST / "rerun_engine_wall.json").write_text(
    json.dumps({"note": "engine_rerun.log 的逐阶段实测墙钟（*.log 按 .gitignore 不入库）",
                "wall_sec": WALL}, ensure_ascii=False, indent=1), encoding="utf-8")
print("[cp] rerun_engine_wall.json")
