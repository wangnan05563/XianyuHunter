"""soak 配置：只跑 mixed_biz 单档长跑（默认 30 min，与基线同档；4 h 传 14400）

**必须剔除 SSE 端点**：`GET /api/tasks/{id}/links/live` 在 E3 之后返回 200 +
`text/event-stream`（流不结束），而 python_replay 引擎不给流式响应设读超时 ——
实测该 sampler 会把线程挂到窗口结束（样本 max **1 632 s**），12 线程 × 135 sampler
的顺序轮转下 TPS 从 59.6 崩到 **7.3**，RSS/句柄序列同时被挂起连接污染。

时长从命令行读（默认 1800 s）：4 h 复跑用 `14400`，不要手改生成的 yml ——
那样下次重跑又会退回 30 min。
"""
import re
import sys
from pathlib import Path

import yaml

PERF = Path(r"D:\tmp\xh-perf")
REPO = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api")
SRC = REPO / "jmeter-config-v2.yml"
SSE_SAMPLER = "GET /api/tasks/t0134ee45/links/live"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 1800

# 从原 jmx 派生一份「SSE 已禁用」的副本：只把该 sampler 的 enabled 置 false，
# 其余 134 个一律不动 —— 保住与基线的同构性。
src_jmx = REPO / "jmx" / "mixed_biz.jmx"
dst_jmx = PERF / "jmeter" / "mixed_biz_no_sse.jmx"
text = src_jmx.read_text(encoding="utf-8", errors="replace")
patched, n = re.subn(
    rf'(<HTTPSamplerProxy[^>]*testname="{re.escape(SSE_SAMPLER)}" enabled=")true(")',
    r"\1false\2", text,
)
assert n == 1, f"SSE sampler 锚点命中 {n} 次，应为 1"
dst_jmx.parent.mkdir(parents=True, exist_ok=True)
dst_jmx.write_text(patched, encoding="utf-8")

cfg = yaml.safe_load(SRC.read_text(encoding="utf-8"))
cfg["sandbox"]["cwd"] = str(PERF / "sandbox")
cfg["sandbox"]["process_match"]["pid_file"] = str(PERF / "sut.pid")
cfg["target"]["auth"]["token_file"] = str(PERF / "token.txt")
for section, key in (("report", "output_dir"), ("results", "output_dir")):
    cfg[section][key] = str(PERF / "output")
cfg["logging"]["file"] = str(PERF / "output" / "perf_soak.log")

phases = [dict(p) for p in cfg["phases"] if p.get("name") == "soak_mixed"]
assert phases, "v2 配置里没有 soak_mixed"
for p in phases:
    p["duration_override"] = DURATION
    p["jmx"] = "jmeter/mixed_biz_no_sse.jmx"
cfg["phases"] = phases

out = PERF / "jmeter-config-soak.yml"
cfg.pop("__config_path__", None)
out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200),
               encoding="utf-8")
print(f"[soak] threads={phases[0].get('threads_override')} "
      f"duration={phases[0]['duration_override']}s，已禁用 SSE sampler → {dst_jmx.name}")
