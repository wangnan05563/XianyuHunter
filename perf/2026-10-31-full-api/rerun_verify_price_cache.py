"""只跑重读组两个档，验证 category-comparison 缓存的效果

前置：`setup_sandbox.py` 已建沙箱、`run_sut.py` 已起常驻 SUT、token 已写。
只跑 `stress_heavy_t16` 与 `stress_heavy_t64`（各 60 s）—— 判据是
`category-comparison` 的 avg/P99 是否从「16 并发 2 433.7 ms」掉下来。
"""
from pathlib import Path

import yaml

PERF = Path(r"D:\tmp\xh-perf")
SRC = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\jmeter-config-v2.yml")
V1 = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\jmeter-config.yml")
KEEP = ["stress_heavy_t16", "stress_heavy_t64"]

cfg = yaml.safe_load(SRC.read_text(encoding="utf-8"))
v1_phases = {p["name"]: p for p in
             yaml.safe_load(V1.read_text(encoding="utf-8"))["phases"]}
have = {p["name"] for p in cfg["phases"]}
for name in KEEP:
    if name not in have and name in v1_phases:
        cfg["phases"].append(dict(v1_phases[name]))

cfg["sandbox"]["cwd"] = str(PERF / "sandbox")
cfg["sandbox"]["process_match"]["pid_file"] = str(PERF / "sut.pid")
cfg["target"]["auth"]["token_file"] = str(PERF / "token.txt")
for section, key in (("report", "output_dir"), ("results", "output_dir")):
    cfg[section][key] = str(PERF / "output")
cfg["logging"]["file"] = str(PERF / "output" / "perf_cache_verify.log")

cfg["phases"] = [p for p in cfg["phases"] if p.get("name") in KEEP]
assert len(cfg["phases"]) == len(KEEP), f"阶段过滤异常：{len(cfg['phases'])}"

out = PERF / "jmeter-config-cache-verify.yml"
cfg.pop("__config_path__", None)
out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200),
               encoding="utf-8")
print(f"[verify] {len(cfg['phases'])} 个阶段，2 分钟负载 → {out}")
