"""SUT 堆采样器：判 B8 的 RSS 爬升是「Python 对象涨」还是「分配器/页缓存涨」

为什么不用 tracemalloc（两次实测都不可用，见 HANDOFF §7）：
- `start(25)` 在进程启动时就开：实测 6 分钟不监听 8011、WS 冲到 966 MB；
- `start(10)` 延迟 90 s 再开：同样不监听（采样序列显示 t=207 s 时 traced 才 76 MB
  而进程 WS 已 1 034 MB）—— 因为那时 uvicorn 才走到 import 应用，每次 import 分配
  都要记 10 帧回溯，启动路径被拖成分钟级。
归因只需要回答「Python 堆在不在涨」，用两个几乎零开销的口径就够了：
- `sys.getallocatedblocks()`：pymalloc 当前持有的块数，O(1)、不记栈帧；
- `gc.get_objects()` 按类型分组：O(存活对象)，每 60 s 一次，能指出是哪类对象在涨。
"""
import gc
import json
import os
import sys
import threading
import time
from collections import Counter

import psutil
import uvicorn

PID_FILE = r"D:\tmp\xh-perf\sut.pid"
OUT_FILE = r"D:\tmp\xh-perf\output\heap_series.jsonl"
INTERVAL = 60
TOP = 8

with open(PID_FILE, "w", encoding="ascii") as f:
    f.write(str(os.getpid()))

_t0 = time.time()
_prev_types: Counter | None = None
_proc = psutil.Process(os.getpid())


def _sample() -> None:
    global _prev_types
    for _ in range(60):                      # 等 uvicorn 起完，避免把 import 期算进斜率
        time.sleep(1)
    while True:
        try:
            mi = _proc.memory_info()
            objs = gc.get_objects()
            types = Counter(type(o).__name__ for o in objs)
            row = {
                "t": round(time.time() - _t0, 1),
                "rss_mb": round(mi.rss / 1048576, 1),
                "private_mb": round(getattr(mi, "private", mi.rss) / 1048576, 1),
                "blocks": sys.getallocatedblocks(),
                "objs": len(objs),
                "threads": threading.active_count(),
                "gc": list(gc.get_count()),
                "top": [],
            }
            if _prev_types is not None:
                deltas = ((k, types[k] - _prev_types.get(k, 0)) for k in types)
                row["top"] = [
                    {"type": k, "d": d}
                    for k, d in sorted(deltas, key=lambda x: -x[1])[:TOP] if d > 0
                ]
            _prev_types = types
            with open(OUT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:                      # 采样失败不能拖垮服务
            with open(OUT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time() - _t0, 1),
                                    "error": str(exc)}) + "\n")
        time.sleep(INTERVAL)


threading.Thread(target=_sample, name="heap-sampler", daemon=True).start()

uvicorn.run("xianyu_hunter.web.app:app", host="127.0.0.1", port=8011, log_level="warning")
