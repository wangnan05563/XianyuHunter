"""重读组（reads_heavy，45+1 接口）复跑配置：baseline + 16/64 两档。

为什么补这一组：D4 的接口级判据（`db-admin/tables/items/export` P99 1 827.7 → ≤600 ms）
与 A1/A2 的几个重接口都在这一组，上一轮只跑了轻组与写组，那几个数字仍然悬空。
"""
from pathlib import Path

import yaml

PERF = Path(r"D:\tmp\xh-perf")
SRC = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\jmeter-config-v2.yml")
V1 = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\jmeter-config.yml")
KEEP = ["baseline_heavy", "stress_heavy_t16", "stress_heavy_t64"]

cfg = yaml.safe_load(SRC.read_text(encoding="utf-8"))
# 重读组的阶梯（t2..t64）只在 v1 里保留：v2 那份只留下了 v2 轮实际跑过的 10 个阶段
v1_phases = {p["name"]: p for p in
             yaml.safe_load(V1.read_text(encoding="utf-8"))["phases"]}
have = {p["name"] for p in cfg["phases"]}
for name in KEEP:
    if name not in have and name in v1_phases:
        cfg["phases"].append(dict(v1_phases[name]))
# 基线那一轮（v2）的 baseline_heavy 实跑 182 s；这里对齐到 180 s，避免时长口径差
for p in cfg["phases"]:
    if p["name"] == "baseline_heavy":
        p["duration_override"] = 180
cfg["sandbox"]["cwd"] = str(PERF / "sandbox")
cfg["sandbox"]["process_match"]["pid_file"] = str(PERF / "sut.pid")
cfg["resource_monitor"]["match"]["pid_file"] = str(PERF / "sut.pid")
cfg["resource_monitor"]["output_csv"] = str(PERF / "output" / "sut_resource_heavy.csv")
cfg["target"]["auth"]["token_file"] = str(PERF / "token.txt")
for section, key in (("report", "output_dir"), ("results", "output_dir")):
    cfg[section][key] = str(PERF / "output")
cfg["logging"]["file"] = str(PERF / "output" / "perf_heavy.log")

before = len(cfg["phases"])
cfg["phases"] = [p for p in cfg["phases"] if p.get("name") in KEEP]
assert len(cfg["phases"]) == len(KEEP), f"阶段过滤异常：{len(cfg['phases'])}/{before}"

out = PERF / "jmeter-config-heavy.yml"
cfg.pop("__config_path__", None)
out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200),
               encoding="utf-8")
total = sum(int(p.get("duration_override") or 60) for p in cfg["phases"])
print(f"[heavy] 阶段 {len(cfg['phases'])}/{before}，预计 {total / 60:.1f} 分钟")
for p in cfg["phases"]:
    print(f"  {p['name']:<20} threads={p.get('threads_override')} dur={p.get('duration_override')}s")
print(f"[heavy] 写出 {out}")
