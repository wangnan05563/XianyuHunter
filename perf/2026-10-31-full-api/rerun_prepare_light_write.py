"""把仓库里的 jmeter-config-v2.yml 改成本次复跑的工作区，并只保留关键阶段。

为什么用**已入库的 v2** 而不是重新生成：重新生成的 scenarios 会跟着当前 openapi
（273 端点）走，端点集与基线（272）不再同构，前后两轮就不可比了。v2 是基线那一轮
的唯一参数来源，改的只是路径与阶段子集。
"""
from pathlib import Path

import yaml

PERF = Path(r"D:\tmp\xh-perf")
SRC = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\jmeter-config-v2.yml")

KEEP = [
    "baseline_light",       # 单发基线：单接口成本（A2/A2′/C5/D4）
    "stress_light_t16",     # 并发拐点前
    "stress_light_t48",     # 并发拐点后（D1/D2/B1/B2）
    "stress_light_t96",     # 客户端上限档
    "baseline_write",       # 写单发（D3）
    "stress_write_t16",     # 写饱和档（D3）
]

cfg = yaml.safe_load(SRC.read_text(encoding="utf-8"))
cfg["__config_path__"] = str(PERF / "jmeter-config-rerun.yml")

sandbox = cfg["sandbox"]
sandbox["cwd"] = str(PERF / "sandbox")
sandbox["process_match"]["pid_file"] = str(PERF / "sut.pid")

mon = cfg["resource_monitor"]
mon["match"]["pid_file"] = str(PERF / "sut.pid")
mon["output_csv"] = str(PERF / "output" / "sut_resource_rerun.csv")

cfg["target"]["auth"]["token_file"] = str(PERF / "token.txt")

for section, key in (("report", "output_dir"), ("results", "output_dir")):
    cfg[section][key] = str(PERF / "output")
cfg["logging"]["file"] = str(PERF / "output" / "perf_rerun.log")

before = len(cfg["phases"])
cfg["phases"] = [p for p in cfg["phases"] if p.get("name") in KEEP]
assert len(cfg["phases"]) == len(KEEP), f"阶段过滤异常：{len(cfg['phases'])}/{before}"

out = PERF / "jmeter-config-rerun.yml"
data = {k: v for k, v in cfg.items() if k != "__config_path__"}
out.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8"
)

total = sum(int(p.get("duration_override") or 60) for p in cfg["phases"])
print(f"[rerun] 阶段 {len(cfg['phases'])}/{before}，预计负载轮 {total / 60:.1f} 分钟")
for p in cfg["phases"]:
    print(f"  {p['name']:<20} threads={p.get('threads_override')} "
          f"dur={p.get('duration_override')}s jmx={p['jmx']}")
print(f"[rerun] 写出 {out}")
