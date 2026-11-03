"""D1 变异验证：逐腿串行，红腿必须只认 pytest exit==1

规则（沿用上几轮的教训，写在这里以免下次又踩）：
- 严禁并发：一次只改一个文件、只跑一个 pytest 进程；上一腿没恢复绝不开始下一腿。
- 基线不绿 → 整轮无效，直接退出（变异红可能是基线本来就红）。
- 判定「红」只认 ``exit == 1``。2/3/4/5（用法错误、内部错、被杀、超时）说明这条腿**没跑到断言**，
  属 INVALID，立刻中止整轮并保留现场 —— 把 INVALID 当红是最容易自欺的地方。
- 锚点必须唯一（count==1），且写盘前先 ``ast.parse``：语法坏掉的腿同样会「红」，但那不是证据。
- 每条腿都说明「预期由哪个用例翻红」，事后核对确实是它，而不是别的用例偶然挂了。
- 收尾按 sha256 逐字节还原，并打印 git status 证明工作树没被留下变异。
- 负对照（改一句无断言的 docstring）必须全绿：证明红腿是被断言抓住的，不是「文件被改就红」。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import pathlib
import subprocess
import sys
import time

PY = ".venv/Scripts/python.exe"
LANE = pathlib.Path("backend/xianyu_hunter/web/slow_lane.py")
CONF = pathlib.Path("backend/xianyu_hunter/config.py")
TESTS = ["tests/test_slow_lane.py"]
PER_LEG_TIMEOUT = 420.0

# 逐腿：(标签, 文件, 原锚点, 替换, 预期, 预期翻红的用例)
LEGS: list[tuple[str, pathlib.Path, str, str, str, str]] = [
    # 首版只改构造（`CapacityLimiter(inf)`）时这条腿**全绿** —— 下面那句 `elif total_tokens != capacity`
    # 会在第二次取用时把容量夹回来，等于自愈。所以「不限容」必须连夹逼一起摘掉，否则腿是假的。
    ("L01 专用道不限容（含去掉容量夹逼）", LANE,
     "    if limiter is None:\n"
     "        limiter = anyio.to_thread.CapacityLimiter(capacity)\n"
     "        _lanes[loop] = limiter\n"
     "    elif limiter.total_tokens != capacity:\n"
     "        limiter.total_tokens = capacity",
     "    if limiter is None:\n"
     "        limiter = anyio.to_thread.CapacityLimiter(float(\"inf\"))\n"
     "        _lanes[loop] = limiter",
     "红", "test_lane_caps_inflight_and_queues：峰值在途 8 != cap 2"),
    ("L02 去掉 functools.wraps", LANE,
     "    @functools.wraps(func)",
     "    # (mutation) 不再 wraps",
     "红", "test_signature_still_resolvable + test_every_listed_route_is_wrapped 的 __wrapped__"),
    ("L03 投递时丢掉关键字参数", LANE,
     "        return await anyio.to_thread.run_sync(functools.partial(func, **kwargs),",
     "        return await anyio.to_thread.run_sync(func,",
     "红", "test_di_params_still_injected / test_real_endpoint_answers_through_lane"),
    ("L04 限流器改成跨循环单例（强引用 dict）", LANE,
     "    limiter = _lanes.get(loop)\n"
     "    if limiter is None:\n"
     "        limiter = anyio.to_thread.CapacityLimiter(capacity)\n"
     "        _lanes[loop] = limiter",
     "    limiter = _ONE.get('lane')\n"
     "    if limiter is None:\n"
     "        limiter = anyio.to_thread.CapacityLimiter(capacity)\n"
     "        _ONE['lane'] = limiter",
     "红", "test_limiter_is_per_event_loop（换循环仍拿到同一个限流器）"),
    ("L05 不清 cached_property", LANE,
     "        for cached in (\"is_coroutine_callable\", \"is_gen_callable\",\n"
     "                       \"is_async_gen_callable\", \"cache_key\"):\n"
     "            route.dependant.__dict__.pop(cached, None)",
     "        pass  # (mutation) 留着旧缓存",
     "红", "test_every_listed_route_is_wrapped 的 is_coroutine_callable 断言"),
    ("L06 不重建 route.app", LANE,
     "        route.app = request_response(route.get_route_handler())",
     "        pass  # (mutation) 留着旧的 ASGI app（闭包里 is_coroutine 仍是 False）",
     "红", "走专用道的请求用例全红：处理器协程被 run_in_threadpool 当成同步函数调用"),
    ("L07 名单失配不报错", LANE,
     "    missing = wanted - matched\n    if missing:",
     "    missing = wanted - matched\n    if False:",
     "红", "test_missing_listed_route_raises"),
    ("L08 去掉 capacity<=0 的关闭守卫", LANE,
     "    if capacity <= 0:\n        return []",
     "    if False:\n        return []",
     "红", "test_capacity_zero_disables_lane（关闭态仍在改写路由）"),
    ("L09 _normalize 不剥 /xianyu 前缀", LANE,
     "    return path[len(\"/xianyu\"):] if path.startswith(\"/xianyu/\") else path",
     "    return path",
     "红", "test_every_listed_route_is_wrapped：/xianyu 那份挂载没被改写（每键只剩 1 条）"),
    ("L10 从 SLOW_ROUTES 静默删一条", LANE,
     "    (\"GET\", \"/api/logs\"),",
     "    # (mutation) 静默删掉 /api/logs",
     "红", "test_slow_routes_are_pinned：名单数量与逐条成员"),
    ("L11 忘传 limiter（悄悄退回共享池）", LANE,
     "        return await anyio.to_thread.run_sync(functools.partial(func, **kwargs),\n"
     "                                              limiter=lane_limiter(capacity))",
     "        return await anyio.to_thread.run_sync(functools.partial(func, **kwargs))",
     "红", "test_lane_caps_inflight_and_queues：功能全对但隔离没了 —— 最关键的一条"),
    ("L12 配置默认值 4 改成 0（整体关闭）", CONF,
     "    slow_lane_tokens: int = 4",
     "    slow_lane_tokens: int = 0",
     "红", "test_every_listed_route_is_wrapped：生产应用按默认值没装专用道"),
    ("负对照：改 docstring 里无断言的一句说明", LANE,
     "**要解决的问题**", "**要应对的情形**",
     "绿", "（无）只改文档字符串措辞，所有用例仍应全绿"),
]

# L04 需要一个模块级容器，随这条腿一起注入，恢复时随原文一起还原
_L04_HELPER = "_ONE: dict = {}\n"


def run_pytest() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=PER_LEG_TIMEOUT,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def summarize(out: str) -> list[str]:
    keep = [ln for ln in out.splitlines()
            if (" passed" in ln or " failed" in ln or " error" in ln
                or ln.startswith("FAILED") or ln.startswith("ERROR")
                or "assert" in ln.strip()[:8])]
    return keep[-14:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑标签前缀匹配的腿（调试用）")
    args = ap.parse_args()

    targets = sorted({leg[1] for leg in LEGS})
    originals: dict[pathlib.Path, bytes] = {p: p.read_bytes() for p in targets}
    start_hash = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    print("baseline sha256:", {p.name: h[:16] for p, h in start_hash.items()})

    code, out = run_pytest()
    print(f"\n=== 基线 → exit={code}（期望 0）")
    for ln in summarize(out):
        print("   ", ln)
    if code != 0:
        print("基线不绿 → 本轮变异无效。先修基线。")
        return 1

    bad = 0
    for label, path, old, new, expect, why in LEGS:
        if args.only and not label.startswith(args.only):
            continue
        raw = originals[path]
        text = raw.decode("utf-8")
        anchor, repl = old, new
        # CRLF 文件（config.py）里的多行锚点要先换行归一化；单行锚点不受影响
        if "\r\n" in text and "\n" in anchor:
            anchor = anchor.replace("\n", "\r\n")
            repl = repl.replace("\n", "\r\n")
        n = text.count(anchor)
        if n != 1:
            print(f"\n=== {label}: 锚点出现 {n} 次（不是唯一）→ INVALID，中止整轮")
            return 3
        mutated = text.replace(anchor, repl)
        if path is LANE and label.startswith("L04"):
            mutated = mutated.replace(
                "\n_lanes: weakref", "\n" + _L04_HELPER + "_lanes: weakref", 1)
        try:
            ast.parse(mutated)
        except SyntaxError as e:
            print(f"\n=== {label}: 变异文本语法错误（{e}）→ INVALID，中止整轮")
            return 4
        t0 = time.perf_counter()
        path.write_bytes(mutated.encode("utf-8"))
        try:
            try:
                code, out = run_pytest()
            except subprocess.TimeoutExpired:
                print(f"\n=== {label}: pytest 超时 → INVALID，中止整轮")
                return 5
        finally:
            path.write_bytes(raw)      # 无条件恢复
        elapsed = time.perf_counter() - t0
        if expect == "红":
            ok = code == 1               # 只认 1；2/3/4/5 到不了这里就已中止
        else:
            ok = code == 0
        bad += 0 if ok else 1
        print(f"\n=== {label} → exit={code}，期望{expect}，判定={'OK' if ok else '不成立'}"
              f"（{elapsed:.0f}s）\n    预期用例：{why}")
        for ln in summarize(out):
            print("   ", ln)

    print("\n=== 还原核对")
    failures = 0 if bad == 0 else 1
    for p, raw in originals.items():
        now = p.read_bytes()
        same = hashlib.sha256(now).hexdigest() == start_hash[p]
        print(f"  {p.name}: sha256_match={same}")
        if not same:
            p.write_bytes(raw)
            print(f"    ↑ 不匹配，已按原始字节强制写回：{hashlib.sha256(p.read_bytes()).hexdigest() == start_hash[p]}")
            failures = 1
    status = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    print("\n=== git status --porcelain（应无本轮之外的新增脏项）")
    print(status.strip() or "(clean)")
    print("\nRESULT:", "ALL LEGS OK" if failures == 0 else f"{bad} 腿不成立 / 还原异常")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
