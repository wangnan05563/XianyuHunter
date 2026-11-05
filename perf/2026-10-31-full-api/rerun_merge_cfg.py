"""把轻组/写组/重读组三套配置合并成 aggregate 能读的一份（9 个阶段）"""
import json
from pathlib import Path

import yaml

PERF = Path(r"D:\tmp\xh-perf")
OUT = PERF / "output"

base = yaml.safe_load((PERF / "jmeter-config-rerun.yml").read_text(encoding="utf-8"))
heavy = yaml.safe_load((PERF / "jmeter-config-heavy.yml").read_text(encoding="utf-8"))
names = {p["name"] for p in base["phases"]}
for p in heavy["phases"]:
    if p["name"] not in names:
        base["phases"].append(p)
base["phases"].sort(key=lambda p: p["name"])
(PERF / "jmeter-config.yml").write_text(
    yaml.safe_dump(base, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")

WALL_HEAVY = [("baseline_heavy", 180.0), ("stress_heavy_t16", 60.3),
              ("stress_heavy_t64", 60.2)]
lines = []
for name, wall in WALL_HEAVY:
    lines.append(f"=== running phase: {name} ===")
    lines.append(json.dumps({"phase": name, "wall_sec": wall}, ensure_ascii=False, indent=2))
    lines.append("")
(OUT / "engine_heavy.log").write_text("\n".join(lines), encoding="utf-8")

print(f"[merge] 阶段 {len(base['phases'])} 个：{sorted(p['name'] for p in base['phases'])}")
