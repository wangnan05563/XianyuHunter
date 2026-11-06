"""系统级 I/O 与调度水位采样（补 HANDOFF §2「系统级 I/O / 带宽水位」这一未覆盖项）

为什么不走 `Get-Counter` / WMI：本机第一次 `Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk`
要 30 s 以上（提供程序冷加载），5 s 一次的采样会被它整个拖死。psutil 读的是同一组内核
计数器，单次 <1 ms，可以放进 5 s 循环里。

口径：psutil 给的是**开机以来的累计值**，所以每行记录的是「本间隔内的速率」＝
累计值差分 ÷ 实测间隔（不是固定 5 s —— 采样本身有抖动，用实测 dt 才不失真）。
`disk_*_Bps` 与 `*_iops` 同源：字节数看带宽水位，次数看随机小 IO 占比。

为什么这些指标能回答 4 h 那类问题：SUT 是单进程 + SQLite(WAL)，如果磁盘队列或
写带宽成为瓶颈，`disk_write_Bps` 会与 TPS 同步走平而 `ctx_switches_s` 继续上升；
反之若两者都随 TPS 线性变化，说明 IO 有裕量、瓶颈在 CPU/GIL。

用法（与负载同时起，时长比负载多 100 s 以便覆盖收尾）：
    python rerun_sample_sysio.py 1900
"""
import csv
import sys
import time
from pathlib import Path

import psutil

OUT = Path(r"D:\tmp\xh-perf\output\sys_io.csv")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 1900
INTERVAL = 5


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prev_d = psutil.disk_io_counters()
    prev_n = psutil.net_io_counters()
    prev_c = psutil.cpu_stats()
    t0 = time.monotonic()
    pt = t0
    psutil.cpu_percent(interval=None)  # 丢弃第一次的占位值
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "elapsed_s", "disk_read_Bps", "disk_write_Bps",
                    "disk_read_iops", "disk_write_iops", "net_recv_Bps", "net_sent_Bps",
                    "cpu_pct", "ctx_switches_s", "interrupts_s"])
        while time.monotonic() - t0 < DURATION:
            time.sleep(INTERVAL)
            now = time.monotonic()
            dt = now - pt
            d = psutil.disk_io_counters()
            n = psutil.net_io_counters()
            c = psutil.cpu_stats()
            w.writerow([
                int(time.time()), round(now - t0, 1),
                round((d.read_bytes - prev_d.read_bytes) / dt),
                round((d.write_bytes - prev_d.write_bytes) / dt),
                round((d.read_count - prev_d.read_count) / dt, 1),
                round((d.write_count - prev_d.write_count) / dt, 1),
                round((n.bytes_recv - prev_n.bytes_recv) / dt),
                round((n.bytes_sent - prev_n.bytes_sent) / dt),
                psutil.cpu_percent(interval=None),
                round((c.ctx_switches - prev_c.ctx_switches) / dt),
                round((c.interrupts - prev_c.interrupts) / dt),
            ])
            f.flush()
            prev_d, prev_n, prev_c, pt = d, n, c, now


if __name__ == "__main__":
    main()
