"""控制实验：这台机器上回环 TCP connect 到底要不要 1 s？

背景：anticrawl_probe_cost.py 里连**自己刚 bind+listen 的端口**也报 timeout，
这在正常 Windows 上不可能（监听端口的 connect 由内核直接完成，不需要 accept）。
要么本机有安全软件接管回环，要么执行环境（sandbox）限制了 socket。
两种解释对 C4 的结论完全不同，所以先把它钉死。

判据矩阵（每个都单独测 3 次取中位）：
  A. 同进程 bind+listen 后 connect（不需要 accept）
  B. 同进程 bind+listen，且在另一个线程里 accept 后再 connect
  C. 另起一个真实 HTTP server 进程，再用 urllib 取一个 /
  D. connect 一个确定没监听的端口（对照组：期望 refused 或 timeout）
"""
from __future__ import annotations

import socket
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPEATS = 3


def ms(fn):
    runs = []
    outcome = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        try:
            fn()
            outcome.append("ok")
        except (TimeoutError, socket.timeout):
            outcome.append("timeout")
        except ConnectionRefusedError:
            outcome.append("refused")
        except OSError as exc:
            outcome.append(f"oserr:{type(exc).__name__}")
        runs.append((time.perf_counter() - t0) * 1000)
    return outcome[0], statistics.median(runs)


def case_a(timeout: float = 1.0):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        return ms(lambda: _conn(port, timeout))
    finally:
        srv.close()


def case_b(timeout: float = 1.0):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        return ms(lambda: _conn(port, timeout))
    finally:
        stop.set()
        t.join(timeout=1)
        srv.close()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"hi")

    def log_message(self, *a):
        pass


def case_c(timeout: float = 1.0):
    import urllib.request

    httpd = HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        def once():
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as r:
                r.read()
        return ms(once)
    finally:
        httpd.shutdown()
        th.join(timeout=2)
        httpd.server_close()


def case_d(timeout: float = 1.0):
    """找一个确认空闲的端口：先 bind(0) 拿到端口号再立刻关掉"""
    tmp = socket.socket()
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()
    return ms(lambda: _conn(port, timeout))


def _conn(port: int, timeout: float) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout):
        pass


def main() -> int:
    print(f"回环 connect 控制实验（每项 {REPEATS} 次取中位，timeout 上限见标签）")
    for label, fn in (
        ("A 同进程 bind+listen，不 accept", case_a),
        ("B 同进程 bind+listen 且后台 accept", case_b),
        ("C 真实 HTTP server + urllib GET", case_c),
        ("D 空闲端口（对照组）", case_d),
    ):
        outcome, med = fn(1.0)
        print(f"    {label}：{outcome:<8} 中位 {med:8.2f} ms")
    outcome, med = case_d(0.1)
    print(f"    D' 空闲端口 timeout=0.1：{outcome:<8} 中位 {med:8.2f} ms")
    print("\n判读：")
    print("  - A/B/C 都 ok 且只有 D 慢 → 「端口未开吃满 timeout」成立，C4 ① 是真降本")
    print("  - A/B/C 也 timeout → 本机/执行环境接管了回环 connect，"
          "压测里那 1 s 是本机特有的，不能推广到普通用户环境")
    print(f"  - python: {sys.version.split()[0]}  platform: {sys.platform}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
