"""D1 慢接口专用道（`web/slow_lane.py`）的行为测试

判据来自 `perf/2026-10-31-full-api/` 的沙箱实验（`d1_lane_probe.py`），这里只做**装机后**
的静态与行为核对，不复现压测数字：

1. 名单里每条路由都被改写，且改的是「投递方式」而不是「处理器对象」—— 生产代码与十余处
   测试仍按同步函数直接调用这些处理器；
2. 对外契约不变：openapi 逐字节相同，DI（Path/Query/Body/Request/同步依赖）照常注入；
3. 专用道真的限住在途数（不限容 = 没隔离，沙箱里是负对照），且廉价接口不再排在慢接口后面；
4. 限流器按事件循环键控（TestClient 每例一个新循环，模块级单例会绑死在第一个循环上）；
5. 名单失配必须报错，容量 <=0 必须整体关闭；
6. 名单成员逐条钉住 —— 隔离范围被静默缩小是这类改动最典型的退化方式，必须比「跑得更慢」先响。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time

import httpx
import pytest
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from xianyu_hunter.web import slow_lane as sl

LANE_PATH = "/api/config/raw"          # 真名单里的路径，用于合成应用
LANE_KEY = ("GET", LANE_PATH)

# 名单快照。`apply_slow_lanes` 的失配报错只拦「名单里有、路由里没」，
# 拦不住「路由还在、名单被删了一条」—— 后者会让隔离范围悄悄缩水而全绿。
# 所以这里把成员逐条钉住：删条/改路径必须在这里翻红。
PINNED_SLOW_ROUTES = {
    ("GET", "/api/anticrawl/strategy"),
    ("GET", "/api/config/raw"),
    ("GET", "/api/config/share"),
    ("GET", "/api/config/export"),
    ("GET", "/api/prompts"),
    ("GET", "/api/templates"),
    ("GET", "/api/maintenance/status"),
    ("GET", "/api/maintenance/cache-scan"),
    ("GET", "/api/logs"),
    ("GET", "/api/vector-admin/snapshots"),
    ("GET", "/api/db-admin/tables/{table}/export"),
    ("GET", "/api/export/{dataset}"),
    ("POST", "/api/maintenance/cache"),
    ("POST", "/api/maintenance/database"),
    ("POST", "/api/maintenance/logs"),
    ("DELETE", "/api/vector-admin/snapshots/{snapshot_id}"),
}

# 明确判定「不入道」的对照：压测里从未真正执行（只有 422/400/404 样本）或实测占用低于判据
NOT_IN_LANE = {
    ("GET", "/api/logs/search"),                        # 全是 422 样本，无占用数据
    ("GET", "/api/error-logs"),                          # 同上
    ("GET", "/api/db-admin/tables/{table}/rows"),        # 34.2 ms < 90 ms 判据
}


class _Body(BaseModel):
    n: int = 1


def _zoo_app() -> tuple[FastAPI, dict]:
    """形状齐全的最小应用：查询参数、Request、同步依赖（入道），外加一个 POST 请求体。"""
    seen: dict = {}

    def _dep() -> dict:
        return {"from": "dep"}

    app = FastAPI()

    def lane_handler(q: str = Query("x"), request: Request = None,  # type: ignore[assignment]
                     dep: dict = Depends(_dep)) -> dict:
        seen.update(q=q, method=request.method, dep=dep)
        return {"ok": True, "echo": seen}

    def posted(body: _Body) -> dict:
        return {"n": body.n}

    def cheap_handler() -> dict:
        return {"cheap": True}

    app.add_api_route(LANE_PATH, lane_handler, methods=["GET"])
    app.add_api_route("/api/prompts", posted, methods=["POST"])
    app.add_api_route("/api/cheap", cheap_handler, methods=["GET"])
    return app, seen


def _route(app: FastAPI, path: str) -> APIRoute:
    hits = [r for r in app.routes if isinstance(r, APIRoute) and r.path == path]
    assert hits, f"合成应用里找不到 {path}"
    return hits[0]


def _counters() -> dict:
    return {"inflight": 0, "peak": 0, "done": 0, "samples": [], "lock": threading.Lock()}


def _tracked(st: dict, hold_ms: float):
    """带在途计数的处理器：计数在处理器内部做，取样不依赖外部轮询。"""
    def handler() -> dict:
        with st["lock"]:
            st["inflight"] += 1
            st["peak"] = max(st["peak"], st["inflight"])
            st["samples"].append(st["inflight"])
        time.sleep(hold_ms / 1000.0)
        with st["lock"]:
            st["inflight"] -= 1
            st["done"] += 1
        return {"ok": True}
    return handler


async def _fire(app: FastAPI, spec: list[tuple[str, int]]) -> list[int]:
    """同一个事件循环里并发打满，返回每条请求的状态码。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30.0) as client:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(client.get(path)) for path, n in spec for _ in range(n)]
    return [t.result().status_code for t in tasks]


# ---------------------------------------------------------------- 静态契约

def test_slow_routes_are_pinned() -> None:
    """名单逐条钉住：静默删条、改路径、加错项都必须翻红。"""
    assert set(sl.SLOW_ROUTES) == PINNED_SLOW_ROUTES
    assert len(sl.SLOW_ROUTES) == len(PINNED_SLOW_ROUTES) == 16
    for key in NOT_IN_LANE:
        assert key not in sl.SLOW_ROUTES, f"{key} 属「判定不入道」，加进名单要有实测占用依据"


def test_every_listed_route_is_wrapped() -> None:
    """生产应用里名单的每条路由（含 /xianyu 前缀副本）都必须是协程处理器且能追回原函数。

    这里刻意**不用** `sl._normalize` 找挂载：借用被测函数自己的归一化，
    「不剥 /xianyu」这类变异就会把两份挂载错认成同一份，测试跟着变异一起变绿。
    """
    from xianyu_hunter.web.app import app

    for method, path in sl.SLOW_ROUTES:
        routes = [
            route for prefix in ("", "/xianyu")
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == prefix + path
            and method in (route.methods or set())
        ]
        assert len(routes) == 2, f"{method} {path} 应有 /api 与 /xianyu/api 两份挂载，实得 {len(routes)}"
        for route in routes:
            assert asyncio.iscoroutinefunction(route.dependant.call), f"{method} {path} 未改写成走专用道"
            # 路由闭包读的是 cached_property，不是 dependant.call 本身：缓存没清就仍按同步投递
            assert route.dependant.is_coroutine_callable is True
            assert getattr(route.dependant.call, "__wrapped__", None) is not None


def test_handler_objects_untouched() -> None:
    """处理器模块级名字仍是同步函数：startup 与十余处测试直接按同步函数调用它们。"""
    from xianyu_hunter.web.routes import api_config, api_maintenance

    assert not asyncio.iscoroutinefunction(api_config.get_raw_yaml)
    assert not asyncio.iscoroutinefunction(api_maintenance.cleanup_cache)


def test_openapi_identical_before_and_after() -> None:
    """改写只动投递方式：openapi 必须逐字节不变（对外契约的判据）。"""
    app, _ = _zoo_app()
    before = json.dumps(app.openapi(), sort_keys=True, ensure_ascii=False)
    sl.apply_slow_lanes(app, capacity=4, allowlist={LANE_KEY, ("POST", "/api/prompts")})
    app.openapi_schema = None          # 强制重算，确认不是拿缓存比缓存
    after = json.dumps(app.openapi(), sort_keys=True, ensure_ascii=False)
    assert after == before


def test_signature_still_resolvable() -> None:
    """`functools.wraps` 的意义：任何按签名/函数名内省路由处理器的代码仍看到原函数。"""
    app, _ = _zoo_app()
    original = _route(app, LANE_PATH).dependant.call
    before = str(inspect.signature(original))
    assert original.__name__ == "lane_handler"

    sl.apply_slow_lanes(app, capacity=4, allowlist={LANE_KEY})
    wrapped = _route(app, LANE_PATH).dependant.call
    assert str(inspect.signature(wrapped)) == before
    assert wrapped.__name__ == "lane_handler"
    assert wrapped.__wrapped__ is original


def test_missing_listed_route_raises() -> None:
    app = FastAPI()
    app.add_api_route("/api/nothing", lambda: {}, methods=["GET"])
    with pytest.raises(RuntimeError, match=LANE_PATH):
        sl.apply_slow_lanes(app, capacity=4, allowlist={LANE_KEY})


def test_capacity_zero_disables_lane() -> None:
    app, _ = _zoo_app()
    assert sl.apply_slow_lanes(app, capacity=0) == []
    assert not asyncio.iscoroutinefunction(_route(app, LANE_PATH).dependant.call)


# ---------------------------------------------------------------- 行为

def test_di_params_still_injected() -> None:
    """走专用道后 Query/Request/同步依赖仍要逐项送到处理器。"""
    app, seen = _zoo_app()
    sl.apply_slow_lanes(app, capacity=4, allowlist={LANE_KEY})
    r = TestClient(app).get(f"{LANE_PATH}?q=zz")
    assert r.status_code == 200, r.text
    assert seen == {"q": "zz", "method": "GET", "dep": {"from": "dep"}}
    assert r.json()["echo"]["q"] == "zz"


def test_body_params_still_injected() -> None:
    app, _ = _zoo_app()
    sl.apply_slow_lanes(app, capacity=4, allowlist={("POST", "/api/prompts")})
    r = TestClient(app).post("/api/prompts", json={"n": 7})
    assert r.status_code == 200, r.text
    assert r.json() == {"n": 7}


def test_raw_response_passes_through() -> None:
    """返回 Response 对象的处理器（导出族如此）不能被二次包装。"""
    app = FastAPI()

    def h() -> JSONResponse:
        return JSONResponse({"rows": [{"i": 1}]})

    app.add_api_route(LANE_PATH, h, methods=["GET"])
    sl.apply_slow_lanes(app, capacity=2, allowlist={LANE_KEY})
    assert TestClient(app).get(LANE_PATH).json() == {"rows": [{"i": 1}]}


def test_lane_caps_inflight_and_queues() -> None:
    """专用道硬上限判据：在途数 == cap（既没漏限，也没退化成串行）；多出的部分确实排队。"""
    capacity, requests, hold_ms = 2, 8, 50.0
    st = _counters()
    app = FastAPI()
    app.add_api_route(LANE_PATH, _tracked(st, hold_ms), methods=["GET"])
    sl.apply_slow_lanes(app, capacity=capacity, allowlist={LANE_KEY})

    t0 = time.perf_counter()
    codes = asyncio.run(_fire(app, [(LANE_PATH, requests)]))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert codes == [200] * requests
    assert st["done"] == requests and len(st["samples"]) == requests   # 存在性：样本真取到了
    assert st["peak"] == capacity, f"峰值在途 {st['peak']} != cap {capacity}"
    rounds = -(-requests // capacity)
    assert elapsed_ms >= rounds * hold_ms * 0.6, f"未见排队（{elapsed_ms:.0f} ms）"


def test_cheap_route_not_blocked_by_slow_lane() -> None:
    """隔离的本体判据：慢族被限在 cap 内时，未入道的接口仍能并发在途。"""
    capacity, slow_n, cheap_n = 2, 6, 8
    slow, cheap = _counters(), _counters()
    app = FastAPI()
    app.add_api_route(LANE_PATH, _tracked(slow, 60.0), methods=["GET"])
    app.add_api_route("/api/cheap", _tracked(cheap, 30.0), methods=["GET"])
    sl.apply_slow_lanes(app, capacity=capacity, allowlist={LANE_KEY})

    codes = asyncio.run(_fire(app, [(LANE_PATH, slow_n), ("/api/cheap", cheap_n)]))

    assert codes == [200] * (slow_n + cheap_n)
    assert slow["peak"] == capacity
    assert cheap["done"] == cheap_n
    assert cheap["peak"] > capacity, (
        f"廉价接口被专用道挤到 {cheap['peak']} 并发，隔离没生效")


def test_limiter_is_per_event_loop() -> None:
    """限流器按循环键控：换一个事件循环必须是另一个限流器（单例会绑死第一个循环）。"""
    async def probe():
        return sl.lane_limiter(3), sl.lane_limiter(3)

    a1, a2 = asyncio.run(probe())
    b1, b2 = asyncio.run(probe())
    assert a1 is a2 and b1 is b2
    assert a1 is not b1, "限流器跨循环复用了"
    assert a1.total_tokens == 3


def test_current_lane_limiter_needs_loop() -> None:
    assert sl.current_lane_limiter() is None      # 不在事件循环里 → 不谎报


def test_real_endpoint_answers_through_lane() -> None:
    """真应用的端到端核对：入道路由在 TestClient 下仍正常应答（认证走 cookie）。"""
    from xianyu_hunter.config import get_settings
    from xianyu_hunter.web.app import app

    r = TestClient(app).get("/api/config/raw", cookies={"xh_token": get_settings().web_token})
    assert r.status_code == 200, r.text
