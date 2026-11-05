"""把本次复跑的实跑墙钟补成 engine_rerun.log，并备好 aggregate 的入口配置。

为什么需要它：`aggregate.py` 的 `engine_wall_times()` 从 `output/engine*.log` 里
解析每阶段实跑的 `"wall_sec"`；缺这份日志就退回配置时长，而那次踩过的坑正是
「配置改了、实跑没改」导致 TPS 被系统性低估。本次 6 个阶段的实跑墙钟由引擎
自己在阶段结束时打印，这里原样转录。
"""
import json
import shutil
from pathlib import Path

PERF = Path(r"D:\tmp\xh-perf")
OUT = PERF / "output"

WALLS = [
    ("baseline_write", 180.0),
    ("baseline_light", 180.1),
    ("stress_light_t16", 60.0),
    ("stress_light_t48", 60.2),
    ("stress_light_t96", 60.2),
    ("stress_write_t16", 60.1),
]

lines = []
for name, wall in WALLS:
    lines.append(f"=== running phase: {name} ===")
    lines.append(json.dumps({"phase": name, "wall_sec": wall}, ensure_ascii=False, indent=2))
    lines.append("")
(OUT / "engine_rerun.log").write_text("\n".join(lines), encoding="utf-8")

shutil.copy2(PERF / "jmeter-config-rerun.yml", PERF / "jmeter-config.yml")
print(f"[prep] engine_rerun.log 与 jmeter-config.yml 就绪（{len(WALLS)} 个阶段）")
