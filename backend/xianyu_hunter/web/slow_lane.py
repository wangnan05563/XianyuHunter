"""慢同步接口的线程池专用道（OPTIMIZATIONS §D1 落地）

**要解决的问题**：本项目 231/277 个路由处理器是同步 `def`，FastAPI 一律用
`starlette.concurrency.run_in_threadpool` 把它们投进 anyio 的**默认线程池（40 个令牌）**，
且调用时不传 `limiter` —— 于是「占用 1 s 的导出」和「占用 8 ms 的读配置」排同一条队。
压测里 96 并发档 `GET /api/notifier/quiet-hours`（本身 8.4 ms）avg 被抬到 2 096.8 ms、
60 s 窗口完成样本 55 → 7，就是这个形状。沙箱（`perf/2026-10-31-full-api/d1_lane_probe.py`）
把延迟拆成 `queue_ms`（等令牌+派发）与 `work_ms`（处理器自己算）后证实：饥饿臂
`queue_p95 ≈ 974 ms` 而 `work_p50` 恒为 ~4 ms —— 慢的是排队，不是算。

**为什么是「专用道」而不是「把 40 调大」**：同一沙箱里两个替代方案都更差 ——
只抬全局令牌到 128（A4）时廉价接口 p50 反而 49.1 ms（空载 21.7 ms），hog 越多借用越深；
另开一条不限容的 limiter（A3，负对照）等于没隔离，p50 113.5 ms。真正起作用的变量是
**上限**：慢族走 `CapacityLimiter(4)` 后廉价接口回到 34~60 ms（A2/B2/C1 三轮同向）。

**容量 4 的依据**（不是拍脑袋）：`d1_lane_budget.py` 按 Little's law 用实测吞吐 × 基线占用
算慢族在途需求，最坏的 96 并发档为 2.14 个在途，4 个槽位的吞吐上限 9.16 rps > 实测需求
4.9 rps —— 既够用，又不会把慢族自己堵死。

**为什么用「换 `dependant.call` 再重建路由」而不是给处理器加装饰器**：装机 anyio 4.13 没有
D1 原文建议的 `anyio.to_thread.RunVars`，而 `run_in_threadpool` 又不接受 per-route limiter，
所以公开 API 里没有落点。剩下的两种写法里，装饰器会把处理器变成协程函数 —— 本项目的
`startup.py` 与十余处测试直接按同步函数调用这些处理器（`maintenance_status`、`cleanup_*`、
`list_logs`、`export_rows`…），会连带崩掉。集中式改写在路由注册之后动 `dependant.call`，
处理器对象本身不变，签名/openapi/直接调用全部保持原样。代价是依赖 FastAPI 内部结构，
因此这里逐条校验改写是否生效，并由 `tests/test_slow_lane.py` 断言「在途数真的被限住」——
一旦上游改版把机制悄悄失效，测试翻红而不是静默退化。

**不在名单里的**：`async def` 处理器（根本不进线程池）、写侧接口（瓶颈是 WAL 单写者，归 D3）、
以及压测里只有 422/400/404 样本、从未真正执行过的接口（`logs/search`、`logs/export`、
`logs/request/{id}`、`error-logs` 族）—— 没有实测占用就不限流。判据见 `SLOW_ROUTES` 的注释。
"""
from __future__ import annotations

import asyncio
import functools
import weakref
from typing import Any, Callable

import anyio
import anyio.to_thread
from fastapi import FastAPI
# 必须用 FastAPI 自己那份 request_response：它在 Starlette 版本之上多塞了
# scope["fastapi_inner_astack"] / ["fastapi_function_astack"]，而路由处理器里对此有断言。
# 误用 starlette.routing.request_response 会让每个请求撞 AssertionError（装机 0.136.3 实测）。
from fastapi.routing import APIRoute, request_response

from xianyu_hunter.config import get_settings

# 入道名单：(HTTP 方法, 注册路径模板)。路径按 `route.path` 原样匹配（不含 /xianyu 副本，
# 那份由 _normalize 去掉前缀后同样命中）。每条后面的数字是实测单次占用（ms）：
# 基线档 avg 来自 results/endpoint_table.md，长跑档来自 results/soak_table.md，
# 96 并发档来自 results/agg_phases.json。判据：同步 def 且
#   (a) 读接口实测占用 >= 90 ms，或 (b) 结构性长任务（全表清理/全量导出/递归删目录）。
SLOW_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/api/anticrawl/strategy"),              # 1094.7（C4 后关闭态约 190~250，仍最重）
    ("GET", "/api/config/raw"),                       # 132.4
    ("GET", "/api/config/share"),                     # 104.7
    ("GET", "/api/config/export"),                     # 107.5 → 长跑 288.4
    ("GET", "/api/prompts"),                           # 117.9
    ("GET", "/api/templates"),                         # 115.8（C3 降本后须复核是否仍够判据）
    ("GET", "/api/maintenance/status"),                # 冷算 188/170（A4），TTL 命中时 ~1 ms
    ("GET", "/api/maintenance/cache-scan"),            # 94.1 → 长跑 287.7 → 96 并发 1822.8
    ("GET", "/api/logs"),                              # 91.3
    ("GET", "/api/vector-admin/snapshots"),            # 233.8（含快照目录遍历）
    ("GET", "/api/db-admin/tables/{table}/export"),    # 35.0 → 长跑 558.2（P99 1827.7）
    ("GET", "/api/export/{dataset}"),                  # 16.2 → 长跑 134.4，上限 5 万行整份拼接
    ("POST", "/api/maintenance/cache"),                # 判据 (b)：全库缓存清理
    ("POST", "/api/maintenance/database"),             # 判据 (b)：VACUUM/整表删除级
    ("POST", "/api/maintenance/logs"),                 # 判据 (b)：日志批量删除级
    ("DELETE", "/api/vector-admin/snapshots/{snapshot_id}"),  # 判据 (b)：递归删目录（同形遍历实测 244 ms）
})

# 循环 -> 专用道限流器。按循环键控而不是模块级单例：TestClient 每个用例开一个新 portal
# （新事件循环），单例的限流器会绑在第一个循环上，之后每个用例都在错误的循环里等待。
_lanes: weakref.WeakKeyDictionary[Any, anyio.to_thread.CapacityLimiter] = weakref.WeakKeyDictionary()


def lane_limiter(capacity: int) -> anyio.to_thread.CapacityLimiter:
    """取当前事件循环的专用道限流器（首用即建，容量变化时同步）。"""
    loop = asyncio.get_running_loop()
    limiter = _lanes.get(loop)
    if limiter is None:
        limiter = anyio.to_thread.CapacityLimiter(capacity)
        _lanes[loop] = limiter
    elif limiter.total_tokens != capacity:
        limiter.total_tokens = capacity
    return limiter


def current_lane_limiter() -> anyio.to_thread.CapacityLimiter | None:
    """供测试/观测读取当前循环的专用道；未建立时返回 None。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return _lanes.get(loop)


def _lane_call(func: Callable[..., Any], capacity: int) -> Callable[..., Any]:
    """把同步处理器变成「经专用道投递」的协程函数。

    FastAPI 对协程处理器直接 `await dependant.call(**values)`，全关键字调用，所以这里收
    `**kwargs` 就够；`functools.wraps` 保住 `__wrapped__`，`inspect.signature` 仍能从它
    还原原签名（现有测试里有按签名断言的用法）。
    """
    @functools.wraps(func)
    async def wrapper(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(func, **kwargs),
                                              limiter=lane_limiter(capacity))
    return wrapper


def _normalize(path: str) -> str:
    """/xianyu 前缀是同一批路由的第二份挂载（SPA 发布形态），剥掉后一起命中名单。"""
    return path[len("/xianyu"):] if path.startswith("/xianyu/") else path


def apply_slow_lanes(
    app: FastAPI,
    capacity: int | None = None,
    allowlist: frozenset[tuple[str, str]] | None = None,
) -> list[str]:
    """把 `allowlist`（默认 `SLOW_ROUTES`）里的同步处理器改写为走专用道；返回被改写的路由描述。

    - `capacity` 为 None 时取配置 `slow_lane_tokens`；<=0 视为关闭，原样返回空列表。
    - 名单里有一条没匹配上就抛错：路由改名/删除时必须立刻暴露，不能让隔离静默失效。
    """
    if capacity is None:
        capacity = get_settings().slow_lane_tokens
    if capacity <= 0:
        return []
    wanted = SLOW_ROUTES if allowlist is None else allowlist

    matched: set[tuple[str, str]] = set()
    rewritten: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.dependant.call is None:
            continue
        methods = (route.methods or set()) - {"HEAD", "OPTIONS"}
        keys = [(m, _normalize(route.path)) for m in methods]
        if not any(k in wanted for k in keys):
            continue
        call = route.dependant.call
        if asyncio.iscoroutinefunction(call):
            continue          # 已经进不了线程池的处理器，限流只会白排队
        route.dependant.call = _lane_call(call, capacity)
        # is_coroutine_callable / cache_key 是 cached_property，值已在构造路由时算过，
        # 不清掉缓存就还是旧处理器（同步、按共享池投递）
        for cached in ("is_coroutine_callable", "is_gen_callable",
                       "is_async_gen_callable", "cache_key"):
            route.dependant.__dict__.pop(cached, None)
        # 路由的 ASGI app 在构造时已把 is_coroutine 烧进闭包，必须重建才生效
        route.app = request_response(route.get_route_handler())
        matched.update(keys)
        rewritten.append(f"{','.join(sorted(methods))} {route.path}")

    missing = wanted - matched
    if missing:
        raise RuntimeError(
            "慢接口专用道名单失配（路由已改名/移除？）: "
            + ", ".join(f"{m} {p}" for m, p in sorted(missing))
        )
    return rewritten
