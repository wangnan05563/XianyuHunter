"""A/B 探针：重接口（tasks/options）占并发时，廉价接口是否被拖慢。

为什么需要它：4 h 长跑里 tasks/options 的 SQL 只要 2.24 ms 而接口 p50 554 ms
（差 250×），差额只能是排队；但「排队在 anyio 共享线程池」与「排队在 GIL/SQLite
连接池」两种解释会得出不同结论 —— 前者该用 D1 慢车道，后者该调连接池。
本探针只做纯测量、不改产品代码。

用法（SUT 已在 8011 监听）：
    python xh_ab_probe.py
"""
import re
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8011"
ENV = r"D:\tmp\xh-perf\sandbox\.env"
TOK = re.search(r"^WEB_TOKEN=(.+)$", open(ENV, encoding="utf-8").read(), re.M).group(1).strip()
HDR = {"Authorization": "Bearer " + TOK}

CHEAP = "/api/config"
HEAVY = "/api/tasks/options"


def get(path, timeout=180):
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(BASE + path, headers=HDR)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return (time.perf_counter() - t0) * 1000
    except Exception as e:
        return (time.perf_counter() - t0) * 1000 + 1e6  # 失败样本推到队尾，不污染分位


def pct(v, q):
    v = sorted(v)
    if not v:
        return 0.0
    return v[min(len(v) - 1, max(0, round(q / 100 * (len(v) - 1))))]


def run(heavy_workers, cheap_workers, n, threads=8):
    """heavy/cheap 各自起独立线程池，同时开跑。"""
    out = {"heavy": [], "cheap": []}
    lock = threading.Lock()

    def work(path, bucket):
        local = [get(path) for _ in range(n)]
        with lock:
            out[bucket].extend(local)

    jobs = []
    with ThreadPoolExecutor(max_workers=heavy_workers + cheap_workers + 2) as ex:
        for _ in range(heavy_workers):
            jobs.append(ex.submit(work, HEAVY, "heavy"))
        for _ in range(cheap_workers):
            jobs.append(ex.submit(work, CHEAP, "cheap"))
        for j in jobs:
            j.result()
    return out


def main():
    print("== 单发基线 ==")
    for p in (CHEAP, HEAVY):
        ts = [get(p) for _ in range(5)]
        print(f"  {p:22s} p50={statistics.median(ts):8.1f}  all={[round(x, 1) for x in ts]}")

    print("\n== A 组：8 并发全部打廉价接口（对照）==")
    a = run(heavy_workers=0, cheap_workers=8, n=12)
    print(f"  cheap p50={pct(a['cheap'], 50):8.1f} p95={pct(a['cheap'], 95):8.1f} n={len(a['cheap'])}")

    print("\n== B1 组：8 并发打重接口，同时 1 并发打廉价 ==")
    b1 = run(heavy_workers=8, cheap_workers=1, n=12)
    print(f"  cheap p50={pct(b1['cheap'], 50):8.1f} p95={pct(b1['cheap'], 95):8.1f} n={len(b1['cheap'])}")
    print(f"  heavy p50={pct(b1['heavy'], 50):8.1f} p95={pct(b1['heavy'], 95):8.1f} n={len(b1['heavy'])}")

    print("\n== B2 组：4 并发打重接口，同时 4 并发打廉价 ==")
    b2 = run(heavy_workers=4, cheap_workers=4, n=12)
    print(f"  cheap p50={pct(b2['cheap'], 50):8.1f} p95={pct(b2['cheap'], 95):8.1f} n={len(b2['cheap'])}")
    print(f"  heavy p50={pct(b2['heavy'], 50):8.1f} p95={pct(b2['heavy'], 95):8.1f} n={len(b2['heavy'])}")

    print("\n[判读] 若 B1/B2 的 cheap p95 显著高于 A 组 -> 重接口会拖慢无关接口（池/资源争用坐实）")


if __name__ == "__main__":
    main()
