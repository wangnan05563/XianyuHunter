"""C2 取证三：`/api/auth/me` 隔离并发阶梯 + 每请求 SQL 次数

为什么要阶梯：探针一/二证明 `/me` 在安静服务端只有 ~10 ms，报告里的 152.6 ms 是
混合负载下的排队结果。`create_sqlite_engine` 用 **StaticPool 全进程共享单连接**
（`infra/db_models.py:872`），所以 `/me` 每请求的 SQL 往返次数会直接换算成
「占用那一条全局连接的时间」，在 48/96 并发下被排队放大 —— 这才是 C2 真正能动的部分。

判据：
- `[A]` 单请求 SQL 语句逐条列出（身份 = xh_token 会话 cookie，即真实控制台形态）
- `[B]` 并发阶梯 1/16/48/96 的 avg/p50/p95/p99 与缺陷率
结果落 `results/c2_ladder_<label>.json`，供改动前后对比（同一沙箱、同一阶梯）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(r"D:/code/otherProjects/17_xianyu")
SANDBOX = Path(r"D:/tmp/xh-c2-srv")
OUT_DIR = REPO / "perf" / "2026-10-31-full-api" / "results"
PROBE_UID = "135379001"

sys.path.insert(0, str(REPO / "perf" / "2026-10-31-full-api"))
from c2_server_probe import (  # noqa: E402
    build_sandbox, free_port, make_session_token, read_web_token, wait_healthz,
)


def one(port: int, headers: dict[str, str], path: str = "/api/auth/me",
        method: str = "GET") -> float:
    real_headers = {k: v for k, v in headers.items() if not k.startswith("_")}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=real_headers,
                                 method=method, data=b"" if method != "GET" else None)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return (time.perf_counter() - t0) * 1000, code


def ladder(port: int, headers: dict[str, str], conc: int, total: int) -> dict:
    lat: list[float] = []
    codes: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
        futs = [pool.submit(one, port, headers) for _ in range(total)]
        for f in concurrent.futures.as_completed(futs):
            ms, code = f.result()
            lat.append(ms)
            codes[str(code)] = codes.get(str(code), 0) + 1
    lat_sorted = sorted(lat)

    def q(p: float) -> float:
        return lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * p))]

    return {"concurrency": conc, "n": len(lat), "avg": round(statistics.mean(lat), 1),
            "p50": round(q(.5), 1), "p95": round(q(.95), 1), "p99": round(q(.99), 1),
            "max": round(max(lat), 1), "codes": codes,
            "defect_rate": round(sum(v for k, v in codes.items() if k[0] not in "23") / len(lat), 4)}


def sql_trace(port: int, headers: dict[str, str]) -> list[str]:
    """在同进程内重放 auth_me，用 SQLAlchemy 监听抓 SQL 序列"""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    sys.path.insert(0, str(REPO / "backend"))
    os.chdir(SANDBOX)
    captured: list[str] = []

    @event.listens_for(Engine, "before_cursor_execute")
    def _l(conn, cursor, statement, parameters, context, executemany):
        captured.append(" ".join(str(statement).split())[:80])

    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes import auth_query

    repo = Repository(db_path=str(SANDBOX / "data" / "xianyu.db"))
    container = type("C", (), {"repo": repo, "collector": None, "browser": None})()

    cookie_vals = dict(headers.get("_cookies", {}))
    auth_header = headers.get("Authorization", "")

    class Req:
        cookies = cookie_vals
        headers = {"authorization": auth_header} if auth_header else {}
        state = type("S", (), {"user_id": PROBE_UID})()
        # 已登录时 /me 会走 make_auth_response → _resolve_secure_policy(request)，
        # 需要 url.scheme / url.hostname
        url = type("U", (), {"scheme": "http", "hostname": "127.0.0.1", "path": "/api/auth/me",
                             "port": 8000})()

    # 先跑两次不计数：Repository 构造后头一次查询会触发惰性建表/迁移 PRAGMA，
    # 那些不属于 /me 的成本，计进来会把结论带偏（探针一就这么误判过一次）
    captured.clear()
    auth_query.auth_me(Req(), container=container)
    auth_query.auth_me(Req(), container=container)
    captured.clear()
    auth_query.auth_me(Req(), container=container)
    first = list(captured)
    captured.clear()
    for _ in range(3):
        auth_query.auth_me(Req(), container=container)
    return first, list(captured)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="before / after / after-r2 ...")
    ap.add_argument("--rebuild", action="store_true",
                    help="强制重建沙箱（每轮都从同一个初始状态起跑）")
    args = ap.parse_args()

    port = free_port()
    if args.rebuild or not (SANDBOX / "config" / "config.yaml").exists():
        # 为什么必须能强制重建：服务端启动时 token_renewer / cookie_rotator 会把
        # session 层标记失效并回写沙箱 cookie 文件，下一轮 /me 走的就是未登录分支。
        # 复用上一次跑过的沙箱会让「改动前后」比的是两条不同代码路径。
        port = build_sandbox()
    # 每轮都把 userinfo 的 fetched_at 拨到当下：AuthManager 的 USERINFO_TTL=300 s，
    # 若沿用上轮的旧时间戳，/me 会在阶梯中途判定缓存过期并触发后台 userinfo 刷新，
    # 把「刷新副作用」混进本轮延迟里（沙箱里 auth_helper 不存在，真实部署里则会起子进程）
    userinfo = SANDBOX / "data" / "auth_cache" / "userinfo.json"
    if userinfo.exists():
        info = json.loads(userinfo.read_text(encoding="utf-8"))
        info["fetched_at"] = time.time()
        userinfo.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env.pop("XH_WITH_SCHEDULER", None)
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(SANDBOX / f"_server_{args.label}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "xianyu_hunter.web.app:app", "--host", "127.0.0.1", "--port", str(port),
         "--app-dir", "backend"],
        cwd=str(SANDBOX), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    print(f"[{args.label}] port={port} pid={proc.pid}")
    results: dict = {"label": args.label, "port": port, "pid": proc.pid}
    try:
        # 启动预算放宽到 240 s：chatbot 子容器（本地 embedding 模型）在冷磁盘缓存下
        # 单步能吃掉 14 s，120 s 预算会误判成「服务端没起来」
        wait_healthz(port, proc, timeout=240)
        one(port, {})  # 触发 settings 生成 .env
        token = read_web_token()
        session_token = make_session_token()
        headers = {"Cookie": f"xh_token={session_token}"}
        headers["_cookies"] = {"xh_token": session_token}

        print("\n[A] 单请求 SQL 序列（xh_token 会话身份，已排除建表迁移噪声）")
        first, steady = sql_trace(None, headers)
        print(f"  预热后单次 {len(first)} 条：")
        for s in first:
            print(f"    · {s}")
        print(f"  之后 3 次共 {len(steady)} 条（稳态每请求 {len(steady) / 3:.1f} 条）：")
        for s in steady[:12]:
            print(f"    · {s}")
        results["sql_warm_single"] = first
        results["sql_steady_3x"] = steady

        print("\n[B] 并发阶梯（每级 96 个请求）")
        rows = []
        for conc in (1, 16, 48, 96):
            r = ladder(port, headers, conc, 96)
            rows.append(r)
            print(f"  c={r['concurrency']:<3} avg={r['avg']:8.1f} p50={r['p50']:8.1f} "
                  f"p95={r['p95']:8.1f} p99={r['p99']:8.1f} max={r['max']:8.1f} "
                  f"defect={r['defect_rate']:.4f} codes={r['codes']}")
        results["ladder"] = rows

        print("\n[C] 对照端点（同沙箱同阶梯）")
        for name, path, method in (
            ("POST /api/auth/me/refresh（处理器近乎为空）", "/api/auth/me/refresh", "POST"),
            ("GET /api/auth/__no_such__（无处理器，纯框架排队）", "/api/auth/__no_such__", "GET"),
        ):
            rows = []
            for conc in (1, 16, 48, 96):
                lat = []
                codes: dict[str, int] = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
                    futs = [pool.submit(one, port, {}, path, method) for _ in range(96)]
                    for f in concurrent.futures.as_completed(futs):
                        ms, code = f.result()
                        lat.append(ms)
                        codes[str(code)] = codes.get(str(code), 0) + 1
                print(f"  {name} c={conc:<3} avg={statistics.mean(lat):8.1f} "
                      f"p50={sorted(lat)[len(lat) // 2]:8.1f} p95={sorted(lat)[int(len(lat) * .95)]:8.1f} codes={codes}")
                rows.append({"concurrency": conc, "avg": round(statistics.mean(lat), 1),
                             "p50": round(sorted(lat)[len(lat) // 2], 1),
                             "p95": round(sorted(lat)[int(len(lat) * .95)], 1), "codes": codes})
            results.setdefault("control_ladder", {})[path] = rows

        # 服务端侧 SQL 计数：从日志里数 StaticPool 相关告警（仅记录，不作判据）
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"c2_ladder_{args.label}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[落盘] results/c2_ladder_{args.label}.json")    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=20)
        log.close()
        print(f"[清理] 服务端 pid={proc.pid} 已按 PID 终止")
        # 把「本轮服务端有没有起浏览器/子进程」这条判据落盘（下一轮 `--rebuild` 会
        # rmtree 整个沙箱，日志本身随之消失）。只落**计数**，不落原始日志：
        # 本仓库是公开仓，服务端 stdout 属于未脱敏材料，不进 results/
        srv_log = SANDBOX / f"_server_{args.label}.log"
        if srv_log.exists():
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            text = srv_log.read_text(encoding="utf-8", errors="replace").lower()
            side_effects = {
                kw: text.count(kw)
                for kw in ("auth_helper", "chromium", "playwright", "subprocess", "launch")
            }
            (OUT_DIR / f"c2_server_scan_{args.label}.json").write_text(
                json.dumps({"label": args.label, "mentions": side_effects,
                            "log_lines": text.count("\n")}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"[归档] results/c2_server_scan_{args.label}.json {side_effects}")
    os.chdir(REPO)


if __name__ == "__main__":
    main()
