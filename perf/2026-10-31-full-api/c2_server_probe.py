"""C2 取证二：真实 ASGI 服务器上的 `/api/auth/me` 服务端归因（隔离沙箱，用完即删）

探针一（`c2_me_probe.py`）结论：`auth_me` 处理器**直调**稳态只有 3.6~7.2 ms，
C2 原文把 152.6 ms 归给「重复文件解析 + 多余 SQL」在进程内复现不出来。
同场景 `POST /api/auth/me/refresh` HTTP 侧 p50 12.9 ms，而 `/me` p50 85.6 ms ——
差的 70 ms 不在业务代码里，就得在 ASGI 栈里找。

本探针把三件事分开量：
1. `/healthz`（免鉴权、无业务）= 传输 + 框架地板
2. `POST /me/refresh`（fire-and-forget）= 与基线同形的参照
3. `GET /me` 在「无凭据 / Bearer WEB_TOKEN / xh_token 会话 cookie」三种身份下的差异
   —— C2 原文怀疑的 `_resolve_current_user_id`（内含 `verify_session` 全局锁）只可能
   出现在后两种，若第一种就 80 ms，则该条前提不成立。

沙箱：独立端口 + 一次性 DB 快照 + 合成 cookie/userinfo，`XH_WITH_SCHEDULER` 不设
（调度器/浏览器不启动）。WEB_TOKEN 由沙箱自己的 `.env` 生成，脚本只在进程内读取、绝不打印。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SANDBOX = Path(r"D:/tmp/xh-c2-srv")
N = 30
PROBE_UID = "135379001"  # 合成 unb，非真实账号


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def build_sandbox() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "config").mkdir(parents=True)
    src_cfg = (REPO / "config" / "config.yaml").read_text(encoding="utf-8")
    patched = src_cfg.replace("auto_start: true", "auto_start: false")
    (SANDBOX / "config" / "config.yaml").write_text(patched, encoding="utf-8")
    for extra in ("menu_registry.yaml", "eval.yaml", "param_rules.yaml"):
        f = REPO / "config" / extra
        if f.exists():
            shutil.copy2(f, SANDBOX / "config" / extra)
    (SANDBOX / "data").mkdir()
    with sqlite3.connect(str(REPO / "data" / "xianyu.db")) as s, \
            sqlite3.connect(str(SANDBOX / "data" / "xianyu.db")) as d:
        s.backup(d)

    # 合成登录态：cookies_{uid}.json（假值）+ userinfo.json
    future = int(time.time()) + 30 * 86400
    cookies = []
    for name in ("unb", "cookie2", "_m_h5_tk", "_m_h5_tk_enc", "sgck", "cna",
                 "tracknick", "xlly", "t", "_tb_token_", "cookie17", "lgc"):
        val = (f"{int(time.time()) + 3600}_deadbeef" if name == "_m_h5_tk"
               else (PROBE_UID if name == "unb" else f"fake-{name}"))
        cookies.append({"name": name, "value": val, "domain": ".goofish.com",
                        "path": "/", "expires": future, "httpOnly": True, "secure": True})
    for i in range(30):
        cookies.append({"name": f"filler_{i}", "value": "x" * 64, "domain": ".goofish.com",
                        "path": "/", "expires": future, "secure": True})
    (SANDBOX / "data" / f"cookies_{PROBE_UID}.json").write_text(
        json.dumps({"exported_at": time.time(), "method": "probe",
                    "cookie_count": len(cookies), "cookies": cookies},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (SANDBOX / "data" / "auth_cache").mkdir()
    (SANDBOX / "data" / "auth_cache" / "userinfo.json").write_text(
        json.dumps({"logged_in": True, "user_id": PROBE_UID, "nick": "探针昵称",
                    "avatar_url": "", "fetched_at": time.time()}, ensure_ascii=False),
        encoding="utf-8")
    return free_port()


def wait_healthz(port: int, proc: subprocess.Popen, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务端提前退出 code={proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("等待 /healthz 超时")


def read_web_token() -> str:
    env = SANDBOX / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("WEB_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def timed(method: str, port: int, path: str, headers: dict[str, str], n: int) -> list[float]:
    out: list[float] = []
    url = f"http://127.0.0.1:{port}{path}"
    for _ in range(n):
        req = urllib.request.Request(url, method=method, headers=headers)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
        except urllib.error.HTTPError as e:
            e.read()
        out.append((time.perf_counter() - t0) * 1000)
    return out


def make_session_token() -> str:
    """在沙箱库里创建一个属于 PROBE_UID 的会话 token（不打印）"""
    sys.path.insert(0, str(REPO / "backend"))
    os.chdir(SANDBOX)
    from xianyu_hunter.web.services.user_manager import get_user_manager
    mgr = get_user_manager()
    cookies = json.loads(
        (SANDBOX / "data" / f"cookies_{PROBE_UID}.json").read_text(encoding="utf-8")
    )["cookies"]
    uid = mgr.identify_or_create(cookies)
    return mgr.issue_session(uid)


def report(name: str, ts: list[float]) -> None:
    ts_sorted = sorted(ts)

    def q(p: float) -> float:
        return ts_sorted[min(len(ts_sorted) - 1, int(len(ts_sorted) * p))]

    print(f"  {name:<42} n={len(ts):<4} avg={statistics.mean(ts):7.1f} "
          f"p50={q(.5):7.1f} p95={q(.95):7.1f} max={max(ts):7.1f}")


def main() -> None:
    port = build_sandbox()
    env = os.environ.copy()
    env.pop("XH_WITH_SCHEDULER", None)
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(SANDBOX / "_server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "xianyu_hunter.web.app:app", "--host", "127.0.0.1", "--port", str(port),
         "--app-dir", "backend"],
        cwd=str(SANDBOX), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    print(f"[sandbox] port={port} pid={proc.pid}")
    try:
        wait_healthz(port, proc)
        print("[sandbox] /healthz 就绪")

        # 触发一次鉴权路径，让 settings 生成 .env 里的 WEB_TOKEN
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/me", timeout=30).read()
        token = read_web_token()
        print(f"[sandbox] WEB_TOKEN 读取: {'成功' if token else '缺失'}")

        session_token = make_session_token()

        print("\n[1] 三种身份下的 GET /api/auth/me + 两个参照端点")
        report("GET /healthz（框架地板）", timed("GET", port, "/healthz", {}, N))
        report("GET /api/auth/me（无任何凭据）", timed("GET", port, "/api/auth/me", {}, N))
        report("GET /api/auth/me（Bearer WEB_TOKEN）",
               timed("GET", port, "/api/auth/me", {"Authorization": f"Bearer {token}"}, N))
        report("GET /api/auth/me（xh_token 会话 cookie）",
               timed("GET", port, "/api/auth/me", {"Cookie": f"xh_token={session_token}"}, N))
        report("POST /api/auth/me/refresh（参照）",
               timed("POST", port, "/api/auth/me/refresh", {}, N))
        report("GET /api/auth/cookie/health（同族参照）",
               timed("GET", port, "/api/auth/cookie/health", {}, N))

        print("\n[2] 同一连接内交替（排除一次性抖动）")
        for _ in range(3):
            report("me(无凭据)", timed("GET", port, "/api/auth/me", {}, 8))
            report("me/refresh", timed("POST", port, "/api/auth/me/refresh", {}, 8))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=20)
        log.close()
        print(f"\n[清理] 服务端 pid={proc.pid} 已按 PID 终止")
    os.chdir(REPO)


if __name__ == "__main__":
    main()
