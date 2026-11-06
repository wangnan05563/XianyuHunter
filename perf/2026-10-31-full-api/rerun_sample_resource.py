"""SUT 侧 RSS / 句柄采样：每 5 s 一条，写 CSV

为什么自己写：当前技能版回放引擎没有 `resource_monitor` 的实现（HANDOFF §7），
跑 soak 时拿不到内存序列；基线那 30 min 的 +75.9 MB/h 就是这样采出来的。
"""
import csv
import os
import time
from pathlib import Path

import psutil

PID_FILE = Path(r"D:\tmp\xh-perf\sut.pid")
OUT = Path(r"D:\tmp\xh-perf\output\sut_resource_soak.csv")
DURATION = 1860          # 稍长于 1800 s 负载，覆盖到关停前后
INTERVAL = 5


def main() -> None:
    pid = int(PID_FILE.read_text().strip())
    proc = psutil.Process(pid)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "elapsed_s", "rss_mb", "private_mb", "handles", "cpu_percent"])
        while time.time() - t0 < DURATION:
            try:
                mi = proc.memory_info()
                rss = round(mi.rss / 1048576, 1)
                private = round(getattr(mi, "private", mi.rss) / 1048576, 1)
                handles = proc.num_handles() if hasattr(proc, "num_handles") else 0
                cpu = proc.cpu_percent(interval=None)
            except Exception:
                break
            w.writerow([round(time.time(), 1), round(time.time() - t0, 1), rss, private,
                        handles, cpu])
            f.flush()
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
