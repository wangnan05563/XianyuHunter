"""复核 D1 慢车道名单：有没有「单发 ≥90 ms」却没入道的接口

判据来自 slow_lane.py 的注释：(a) 同步 def 且单发 ≥90 ms，或 (b) 结构性长任务。
用本轮复跑的 baseline_heavy（1 线程）当单发成本来源。
"""
import json
from pathlib import Path

RESULTS = Path(r"d:\code\otherProjects\XianyuHunter\perf\2026-10-31-full-api\results")
RERUN = json.loads((RESULTS / "rerun_agg_phases.json").read_text(encoding="utf-8"))

IN_LANE = {
    ("GET", "/api/anticrawl/strategy"), ("GET", "/api/config/raw"),
    ("GET", "/api/config/share"), ("GET", "/api/config/export"),
    ("GET", "/api/prompts"), ("GET", "/api/templates"),
    ("GET", "/api/maintenance/status"), ("GET", "/api/maintenance/cache-scan"),
    ("GET", "/api/logs"), ("GET", "/api/vector-admin/snapshots"),
    ("GET", "/api/db-admin/tables/{table}/export"), ("GET", "/api/export/{dataset}"),
    ("POST", "/api/maintenance/cache"), ("POST", "/api/maintenance/database"),
    ("POST", "/api/maintenance/logs"), ("DELETE", "/api/vector-admin/snapshots/{snapshot_id}"),
}

by_ep = RERUN["baseline_heavy"]["by_endpoint"]
rows = []
for label, s in by_ep.items():
    method, _, path = label.partition(" ")
    key = (method, path)
    if key in IN_LANE:
        continue
    avg = s.get("avg") or 0
    if avg >= 90:
        rows.append((label, s.get("n"), avg, s.get("p95"), s.get("p99"), s.get("codes")))

rows.sort(key=lambda r: r[2], reverse=True)
print(f"baseline_heavy 里「单发 ≥90 ms 且不在慢车道名单」的接口：{len(rows)} 个")
print(f"  {'接口':<50}{'n':>5}{'avg':>9}{'p95':>9}{'p99':>9}")
for label, n, avg, p95, p99, codes in rows:
    print(f"  {label:<50}{n:>5}{avg:>9.1f}{p95:>9.1f}{p99:>9.1f}")

# 对照：名单内接口在当前 baseline_heavy 的单发
print("\n名单内接口的当前单发（用于判断是否还够判据）：")
for label, s in sorted(by_ep.items(), key=lambda kv: -(kv[1].get("avg") or 0)):
    method, _, path = label.partition(" ")
    if (method, path) in IN_LANE:
        print(f"  {label:<50}{s.get('n'):>5}{s.get('avg'):>9.1f}{s.get('p99'):>9.1f}")
