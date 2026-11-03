"""D1 取证 · 第二层：线程池隔离到底能不能兑现收益（四臂对照，一次性沙箱）

D1 原文的因果链是「慢同步接口吃满 anyio 的 40 个令牌 → 8.4 ms 的廉价接口排队等线程 → avg 2 096 ms、
窗口完成数 55 → 7」，并建议「给慢接口单独设 `anyio.to_thread.RunVars`/`CapacityLimiter`（如 4）」。
装机 anyio 4.13 **没有 RunVars**，而 `starlette.concurrency.run_in_threadpool` 调
`anyio.to_thread.run_sync(func)` 时**不传 limiter** —— 所以「按接口分道」在生产代码里没有现成落点，
能不能兑现必须先证明，不能拿形状成立当收益成立。

本沙箱把三个变量各自钉死，逐个臂只动一个：
- **等待可拆分**：中间件在事件循环里盖章 `t0`，同步处理器（工作线程）开工时回算 `queue_ms`。
  于是廉价接口的总耗时被拆成「等令牌 + 派发」和「自己算」两段 —— 归因争议就此终结。
- **hog 的性质**：IO 型（`time.sleep`，让出 GIL）与 CPU 型（忙等，抢 GIL）分开跑。
  令牌隔离理论上只治前者；若 CPU 型也能被「廉价接口变慢」，那说明根因是 GIL 而不是池子。
- **两个可动旋钮**：全局令牌数（40 → 128，等价于「不改代码只扩容」）和专用道容量（4 / 40）。

臂：A0 空载地板 / A1 40 个 IO hog 吃满共享池（复现饥饿）/ A2 同样 40 个 hog 走专用道 cap=4
（隔离是否兑现）/ A3 专用道 cap=40（负对照：证明起作用的是「上限」而不是「另开一个 limiter」）
/ A4 不隔离只把全局令牌抬到 128（扩容替代方案）/ A5 CPU 型 hog 吃满共享池 / A6 CPU 型 hog 走 cap=4。
每臂两轮，第二轮倒序跑，按同向判据发布。

只跑一个手写的最小 FastAPI 应用（不导入生产代码、不连数据库、不启动调度器/浏览器），
端口由系统分配，退出即删。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import anyio
import httpx

REPO = Path(__file__).resolve().parents[2]
# 沙箱目录带 PID：两次运行（或一次后台没退干净）并发时，先结束的那个会在收尾 rmtree，
# 把还在跑的另一个的应用源码和服务端日志一起删掉 —— 症状就是「server not ready + 日志为空」
SCRATCH = Path(REPO / "perf" / "2026-10-31-full-api" / f"_scratch_d1_lane_{os.getpid()}")
APP_FILE = "d1_lane_app.py"
WINDOW_SEC = 4.0
SETTLE_SEC = 1.5    # 臂间留口：上一臂的服务端仍在退出时，新绑端口会失败
CHEAP_CONC = 8
HOG_MS = 1000
ROUNDS = 2

ARMS = [
    # id, name, hog_path, hog_n, tokens, lane_tokens, hog_kind
    ("A0", "A0 空载地板", None, 0, 40, 4, "none"),
    ("A1", "A1 IOhog×40 吃满共享池", "/hog_io", 40, 40, 4, "io"),
    ("A2", "A2 IOhog×40 走专用道 cap=4", "/hog_lane", 40, 40, 4, "io"),
    ("A3", "A3 专用道 cap=40（负对照）", "/hog_lane", 40, 40, 40, "io"),
    ("A4", "A4 只扩容：全局令牌 128", "/hog_io", 40, 128, 4, "io"),
    ("A5", "A5 CPUhog×40 吃满共享池", "/hog_cpu", 40, 40, 4, "cpu"),
    ("A6", "A6 CPUhog×40 走专用道 cap=4", "/hog_lane", 40, 40, 4, "cpu"),
    # 96 并发保真臂：压测里廉价接口被抬到 2 s 的是 96 并发档，40 hog 只是中间态
    ("B1", "B1 IOhog×88 吃满共享池（96 档）", "/hog_io", 88, 40, 4, "io"),
    ("B2", "B2 IOhog×88 走专用道 cap=4", "/hog_lane", 88, 40, 4, "io"),
    ("B3", "B3 CPUhog×88 走专用道 cap=4", "/hog_lane", 88, 40, 4, "cpu"),
    # 生产形状臂：慢接口自带同步依赖 + 3000 行响应体（序列化也借共享池令牌）
    ("C0", "C0 大响应体hog×40 走共享池", "/hog_big", 40, 40, 4, "io"),
    ("C1", "C1 大响应体hog×40 走专用道 cap=4", "/hog_lane_big", 40, 40, 4, "io"),
]

APP_SRC = '''
"""D1 沙箱服务端：形状对齐生产（同步 def 处理器 → anyio 线程池），内容全是合成。"""
import functools
import os
import time

import anyio
import anyio.to_thread
from fastapi import Depends, FastAPI, Request

TOKENS = int(os.environ.get("D1_TOKENS", "40"))
LANE = int(os.environ.get("D1_LANE", "4"))

app = FastAPI()
_lane = anyio.to_thread.CapacityLimiter(LANE)
_state = {"hog_started": 0}


@app.middleware("http")
async def stamp(request, call_next):
    # 事件循环里盖章：处理器真正开工的时刻能反推出「等令牌」花了多久
    request.state.t0 = time.perf_counter()
    return await call_next(request)


def _cheap_body() -> dict:
    time.sleep(0.003)          # 代表「读配置 + 一条小 SQL」的等待
    return {"ok": True, "n": sum(range(50))}   # 一点点序列化/CPU


@app.get("/cheap")
def cheap(request: Request) -> dict:
    q_ms = (time.perf_counter() - request.state.t0) * 1000.0
    t = time.perf_counter()
    out = _cheap_body()
    out["queue_ms"] = round(q_ms, 3)
    out["work_ms"] = round((time.perf_counter() - t) * 1000.0, 3)
    return out


@app.get("/hog_io")
def hog_io(ms: int = 1000) -> dict:
    _state["hog_started"] += 1
    time.sleep(ms / 1000.0)     # IO/等待型：占令牌但让出 GIL
    return {"hog": "io"}


@app.get("/hog_cpu")
def hog_cpu(ms: int = 1000) -> dict:
    _state["hog_started"] += 1
    end = time.perf_counter() + ms / 1000.0   # CPU 型：占令牌且抢 GIL
    x = 0
    while time.perf_counter() < end:
        x += 1
    return {"hog": "cpu", "spin": x}


async def _lane_body(ms: int) -> dict:
    _state["hog_started"] += 1
    await anyio.to_thread.run_sync(time.sleep, ms / 1000.0, limiter=_lane)
    return {"hog": "lane"}


@app.get("/hog_lane")
async def hog_lane(ms: int = 1000) -> dict:
    return await _lane_body(ms)


def _dep() -> dict:
    time.sleep(0.001)             # 同步依赖：FastAPI 也会为它借一个共享池令牌
    return {"d": 1}


def _big_body(ms: int, n: int) -> dict:
    time.sleep(ms / 1000.0)
    return {"rows": [{"i": i, "v": f"row-{i}"} for i in range(n)]}


@app.get("/hog_big")
def hog_big(ms: int = 1000, rows: int = 3000, d: dict = Depends(_dep)) -> dict:
    return _big_body(ms, rows)     # 生产形状：占用共享池做重计算 + 大响应体


@app.get("/hog_lane_big")
async def hog_lane_big(ms: int = 1000, rows: int = 3000, d: dict = Depends(_dep)) -> dict:
    return await anyio.to_thread.run_sync(functools.partial(_big_body, ms, rows),
                                          limiter=_lane)   # 处理器进专用道


@app.get("/poolstat")
async def poolstat() -> dict:
    lim = anyio.to_thread.current_default_thread_limiter()
    return {
        "tokens": lim.total_tokens,
        "borrowed": lim.borrowed_tokens,
        "lane_borrowed": _lane.borrowed_tokens,
        "lane_capacity": _lane.total_tokens,
        "hog_started": _state["hog_started"],
    }


@app.on_event("startup")
async def apply_tokens() -> None:
    anyio.to_thread.current_default_thread_limiter().total_tokens = TOKENS
'''


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(port: int, tokens: int, lane: int, err_log=None) -> subprocess.Popen:
    env = {**os.environ, "D1_TOKENS": str(tokens), "D1_LANE": str(lane)}
    # 本机常驻 HTTP(S)_PROXY：留给服务端无妨，但绝不能让它参与本探针的计时回路
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    sink = open(err_log, "wb") if err_log else subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "d1_lane_app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
        cwd=str(SCRATCH), env=env, stdout=sink, stderr=subprocess.STDOUT,
    )
    proc._sink = sink          # 关掉它才拿得到完整报错
    return proc


def wait_ready(port: int, timeout: float = 45.0) -> bool:
    # 显式 trust_env=False：否则代理会接管 127.0.0.1，就绪探测与压测都打到别处去
    with httpx.Client(trust_env=False, timeout=2.0) as client:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if client.get(f"http://127.0.0.1:{port}/cheap").status_code == 200:
                    return True
            except Exception:
                time.sleep(0.2)
    return False


def kill_tree(proc: subprocess.Popen) -> None:
    """本机 venv 的 python.exe 是启动器，会再拉起 uv 的真实解释器作子进程（进程表实测可见）。
    proc.terminate() 只杀父，uvicorn 会孤儿化并继续占着端口，所以按整棵进程树收。"""
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        proc.kill()


def run_arm(arm) -> dict:
    arm_id, name, hog_path, hog_n, tokens, lane, kind = arm
    time.sleep(SETTLE_SEC)
    port = free_port()
    proc = start_server(port, tokens, lane, err_log=SCRATCH / f"srv_{arm_id}.log")
    result = {"arm_id": arm_id, "arm": name, "hog": hog_path, "hog_n": hog_n,
              "tokens": tokens, "lane": lane, "kind": kind}
    try:
        if not wait_ready(port):
            log_path = SCRATCH / f"srv_{arm_id}.log"
            tail = ""
            try:
                proc._sink.flush()
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-400:]
            except OSError:
                tail = f"<日志文件不可读: exists={log_path.exists()}>"
            result["error"] = (f"server not ready（alive={proc.poll() is None}"
                               f" rc={proc.poll()} app_src={(SCRATCH / APP_FILE).exists()}）"
                               f"服务端输出尾部: {tail!r}")
            return result
        base = f"http://127.0.0.1:{port}"

        async def drive():
            cheap_lat: list[float] = []
            cheap_q: list[float] = []
            cheap_work: list[float] = []
            cheap_err = {"n": 0}
            hog_done = {"n": 0}
            peak = {"borrowed": 0, "lane_borrowed": 0}
            limits = httpx.Limits(max_connections=hog_n + CHEAP_CONC + 8,
                                  max_keepalive_connections=hog_n + CHEAP_CONC + 8)
            async with httpx.AsyncClient(base_url=base, limits=limits,
                                         trust_env=False, timeout=30.0) as client:
                stop = time.perf_counter() + WINDOW_SEC

                async def cheap_loop():
                    while time.perf_counter() < stop:
                        t = time.perf_counter()
                        try:
                            r = await client.get("/cheap")
                            j = r.json()
                        except Exception:      # 单条网络错误计入 err，不让整臂崩掉
                            cheap_err["n"] += 1
                            continue
                        cheap_lat.append((time.perf_counter() - t) * 1000.0)
                        cheap_q.append(j["queue_ms"])
                        cheap_work.append(j["work_ms"])

                async def hog_loop():
                    while time.perf_counter() < stop and hog_path:
                        try:
                            await client.get(hog_path, params={"ms": HOG_MS})
                        except Exception:
                            continue
                        hog_done["n"] += 1

                async def poll_loop():
                    while time.perf_counter() < stop:
                        try:
                            j = (await client.get("/poolstat")).json()
                        except Exception:
                            await anyio.sleep(0.05)
                            continue
                        peak["borrowed"] = max(peak["borrowed"], j["borrowed"])
                        peak["lane_borrowed"] = max(peak["lane_borrowed"], j["lane_borrowed"])
                        await anyio.sleep(0.05)

                async with anyio.create_task_group() as tg:
                    for _ in range(CHEAP_CONC):
                        tg.start_soon(cheap_loop)
                    for _ in range(hog_n):
                        tg.start_soon(hog_loop)
                    tg.start_soon(poll_loop)
            return cheap_lat, cheap_q, cheap_work, hog_done["n"], peak, cheap_err["n"]

        cheap_lat, cheap_q, cheap_work, hog_done, peak, cheap_err_n = anyio.run(drive)

        def pct(xs, p):
            if not xs:
                return None
            s = sorted(xs)
            k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
            return round(s[k], 2)

        result.update({
            "cheap_completed": len(cheap_lat),
            "cheap_avg_ms": round(statistics.fmean(cheap_lat), 2) if cheap_lat else None,
            "cheap_p50_ms": pct(cheap_lat, 50),
            "cheap_p95_ms": pct(cheap_lat, 95),
            "queue_p50_ms": pct(cheap_q, 50),
            "queue_p95_ms": pct(cheap_q, 95),
            "work_p50_ms": pct(cheap_work, 50),
            "hog_completions": hog_done,
            "cheap_errors": cheap_err_n,
            "peak_borrowed": peak["borrowed"],
            "peak_lane_borrowed": peak["lane_borrowed"],
        })
    finally:
        kill_tree(proc)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        sink = getattr(proc, "_sink", None)
        if sink not in (None, subprocess.DEVNULL):
            try:
                sink.close()
            except OSError:
                pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "perf" / "2026-10-31-full-api" / "results"
                                         / "d1_lane_arms.json"))
    ap.add_argument("--arms", default="", help="逗号分隔的臂 id（默认全跑）")
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    args = ap.parse_args()
    picked = [a for a in ARMS if not args.arms or a[0] in {x.strip() for x in args.arms.split(",")}]
    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / APP_FILE).write_text(APP_SRC, encoding="utf-8", newline="\n")
    log_lines: list[str] = []

    def say(line: str = "") -> None:
        log_lines.append(line)
        # 控制台只打 ASCII 摘要（GBK 下中文会 UnicodeEncodeError，且崩点在落盘之后）
        print(line.encode("ascii", "backslashreplace").decode("ascii"))

    say("=" * 92)
    say("[D1 多臂对照] 廉价接口（同步 def，8 并发）在各臂下的表现；"
        "queue_ms = 等令牌+派发，work_ms = 处理器自己算")
    say("=" * 92)
    rows: list[dict] = []
    for rnd in range(args.rounds):
        arms = picked if rnd % 2 == 0 else list(reversed(picked))
        for arm in arms:
            r = run_arm(arm)
            r["round"] = rnd + 1
            rows.append(r)
            if "error" in r:
                say(f"  轮{rnd+1} {arm[1]:<30} ERROR {r['error']}")
                continue
            say(f"  轮{rnd+1} {arm[1]:<30} 完成={r['cheap_completed']:<5} "
                f"avg={r['cheap_avg_ms']:<8} p50={r['cheap_p50_ms']:<9} "
                f"p95={r['cheap_p95_ms']:<9} | queue_p50={r['queue_p50_ms']:<7} "
                f"queue_p95={r['queue_p95_ms']:<7} work_p50={r['work_p50_ms']:<6} | "
                f"hog完成={r['hog_completions']:<4} err={r['cheap_errors']} "
                f"峰值借用={r['peak_borrowed']}/{r['tokens']} 专用道={r['peak_lane_borrowed']}/{r['lane']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"\n[落盘] {out}")

    say("\n" + "=" * 92)
    say("[同向判读] 每臂逐轮并排：饥饿臂 vs 隔离臂 vs 扩容臂")
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["arm_id"], []).append(r)
    for arm in picked:
        rs = [x for x in by[arm[0]] if x.get("cheap_p50_ms") is not None]
        if not rs:
            continue
        say(f"  {arm[1]:<30} "
            f"p50={[x['cheap_p50_ms'] for x in rs]}  "
            f"p95={[x['cheap_p95_ms'] for x in rs]}  "
            f"queue_p95={[x['queue_p95_ms'] for x in rs]}  "
            f"work_p50={[x['work_p50_ms'] for x in rs]}  "
            f"完成数={[x['cheap_completed'] for x in rs]}")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    log_path = out.with_suffix(".log")
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    say(f"[判读表落盘] {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
