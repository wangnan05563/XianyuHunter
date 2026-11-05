"""压测用 SUT 启动器：把自己的 pid 写进 sut.pid，再原地跑 uvicorn。

为什么要这层包装：uvicorn CLI 进程与实际服务进程不一定同 pid，资源采样
（rss/private/handles）必须对着真正干活的那个进程；用 os.getpid() 写文件
就是服务进程本身。
"""
import os
import sys

import uvicorn

PID_FILE = r"D:\tmp\xh-perf\sut.pid"
with open(PID_FILE, "w", encoding="ascii") as f:
    f.write(str(os.getpid()))

uvicorn.run(
    "xianyu_hunter.web.app:app",
    host="127.0.0.1",
    port=8011,
    log_level="warning",
)
