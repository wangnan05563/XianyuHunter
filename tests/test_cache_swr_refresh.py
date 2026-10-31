"""stale-while-revalidate 后台刷新回归测试 —— 对应性能测试报告 OPTIMIZATIONS B5

背景（修复前的两个静默失效，由 B1 落地时的实现复核 + `perf/2026-10-31-full-api/swr_probe.py` 取证）：
- 同步分支的 `_maybe_refresh_stale` docstring 写「启动守护线程异步刷新」，函数体却在调用
  线程里 inline 重算 → 「过期立即返回旧值」并不立即，探针实测 0.602 s ≈ 完整重算耗时。
- 异步分支用 `asyncio.run(...)`，而它只在已运行的事件循环里被调用 → 必然 RuntimeError，
  又被 `except Exception: pass` 吞掉 → 缓存永不刷新，值冻结在第一个旧值上。

这里锁定四条语义：过期调用不被重算阻塞、重算确实在后台写回缓存、同一 key 只起一次刷新
（惊群）、刷新失败保留旧值且名额被释放（下次仍能重试）。
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from xianyu_hunter.web import cache as cache_mod
from xianyu_hunter.web.cache import cached_ttl

REFCALC_SEC = 0.25


@pytest.fixture(autouse=True)
def _clean_cache_state():
    """每个用例前后清空全局缓存、在途表与刷新名额，避免用例间串味。"""
    def _reset():
        with cache_mod._cache_lock:
            cache_mod._cache_store.clear()
            cache_mod._flights.clear()
            cache_mod._flights_async.clear()
            cache_mod._refreshing.clear()
        cache_mod._bg_tasks.clear()
    _reset()
    yield
    _reset()


class _Gate:
    """可控制放行节奏的假重算体。

    `release` 未置位时：同步体阻塞等待、异步体让出循环轮询等待 —— 两者都能把「重算正在进行」
    这段时间拉长到可观测，但异步侧绝不能阻塞事件循环（否则后台任务永远排不上）。
    """

    def __init__(self) -> None:
        self.runs: list[int] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = False

    def _begin(self) -> None:
        self.entered.set()

    def _finish(self) -> int:
        if self.fail:
            raise RuntimeError("重算失败")
        self.runs.append(len(self.runs) + 1)
        return len(self.runs)

    def sync_body(self) -> int:
        self._begin()
        if not self.release.wait(5):
            raise AssertionError("后台刷新未在预期时间内放行")
        time.sleep(REFCALC_SEC)
        return self._finish()

    async def async_body(self) -> int:
        self._begin()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(REFCALC_SEC)
        return self._finish()


def _make_sync(gate: _Gate, tag: str):
    def body() -> int:
        return gate.sync_body()

    return cached_ttl(60, key_fn=lambda: tag, stale_while_revalidate=True)(body)


def _make_async(gate: _Gate, tag: str):
    async def body() -> int:
        return await gate.async_body()

    return cached_ttl(60, key_fn=lambda: tag, stale_while_revalidate=True)(body)


def _expire(tag: str) -> None:
    """把该 tag 的缓存条目时间戳挪到过去 —— 等价跨过 TTL，但测试不必真睡 ttl 秒。"""
    with cache_mod._cache_lock:
        for key, (_ts, value) in list(cache_mod._cache_store.items()):
            if len(key) == 2 and key[1] == tag:
                cache_mod._cache_store[key] = (time.monotonic() - 10_000, value)


def _read(tag: str):
    """直接读缓存里的值（不论是否过期），用于断言后台刷新是否写回。"""
    with cache_mod._cache_lock:
        for key, (_ts, value) in cache_mod._cache_store.items():
            if len(key) == 2 and key[1] == tag:
                return value
    return None


def _refreshing_has(tag: str) -> bool:
    with cache_mod._cache_lock:
        return any(len(key) == 2 and key[1] == tag for key in cache_mod._refreshing)


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


async def _await_until(predicate, timeout: float = 3.0) -> bool:
    """异步版轮询：等待期间必须让出事件循环，否则后台刷新任务永远排不上。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_sync_expired_call_returns_immediately_and_refreshes_in_background():
    gate = _Gate()
    gate.release.set()
    fn = _make_sync(gate, "swr:sync:blocking")
    assert fn() == 1                                    # 冷路径：必然重算

    gate.release.clear()
    gate.entered.clear()
    _expire("swr:sync:blocking")
    started = time.perf_counter()
    returned = fn()
    elapsed = time.perf_counter() - started

    assert returned == 1, "过期调用应返回旧值"
    assert elapsed < REFCALC_SEC / 3, f"过期调用被重算阻塞（{elapsed:.3f}s，重算需 {REFCALC_SEC}s）"
    assert _wait_until(gate.entered.is_set), "后台线程应已开始重算"
    gate.release.set()
    assert _wait_until(lambda: _read("swr:sync:blocking") == 2), "后台重算结果应写回缓存"


def test_sync_repeated_expired_calls_trigger_only_one_refresh():
    gate = _Gate()
    gate.release.set()
    fn = _make_sync(gate, "swr:sync:thundering")
    fn()
    gate.release.clear()
    gate.entered.clear()
    _expire("swr:sync:thundering")

    for _ in range(5):
        assert fn() == 1

    assert _wait_until(gate.entered.is_set)
    gate.release.set()
    # 名额释放即后台线程跑完；此时重算次数应恰为「冷 1 次 + 后台 1 次」
    assert _wait_until(lambda: not _refreshing_has("swr:sync:thundering"))
    assert gate.runs == [1, 2], f"5 次过期调用只应触发 1 次刷新，实际 {gate.runs}"


def test_sync_refresh_failure_keeps_stale_value_and_releases_claim():
    gate = _Gate()
    gate.release.set()
    fn = _make_sync(gate, "swr:sync:fail")
    assert fn() == 1
    _expire("swr:sync:fail")
    gate.fail = True
    gate.entered.clear()

    assert fn() == 1
    assert _wait_until(lambda: not _refreshing_has("swr:sync:fail"))
    assert _read("swr:sync:fail") == 1, "刷新失败应保留旧值（不缓存错误结果）"

    gate.fail = False
    gate.entered.clear()
    _expire("swr:sync:fail")
    assert fn() == 1
    assert _wait_until(lambda: _read("swr:sync:fail") == 2), "名额释放后应能重试并写回新值"


def test_sync_bypass_cache_still_returns_fresh_value_under_swr():
    gate = _Gate()
    gate.release.set()
    fn = _make_sync(gate, "swr:sync:bypass")
    assert fn() == 1
    _expire("swr:sync:bypass")
    assert fn(bypass_cache=True) == 2, "强制刷新不应被 SWR 的旧值快路径截走"


def test_async_expired_call_refreshes_instead_of_freezing():
    """B5 核心回归：修复前 `asyncio.run` 在运行中的循环里必然失败 → 值永久冻结。"""

    async def scenario() -> None:
        gate = _Gate()
        gate.release.set()
        fn = _make_async(gate, "swr:async:freeze")
        first = await fn()
        assert first == 1

        gate.release.clear()
        gate.entered.clear()
        _expire("swr:async:freeze")
        vals = [await fn() for _ in range(5)]           # 全部立即返回旧值
        assert vals == [1] * 5
        assert await _await_until(gate.entered.is_set), "后台刷新任务应被排上事件循环"
        gate.release.set()
        assert await _await_until(lambda: _read("swr:async:freeze") == 2), "刷新任务应写回新值"
        assert not cache_mod._bg_tasks, "完成的任务应被摘掉，不长期持有引用"

    asyncio.run(scenario())


def test_async_refresh_failure_releases_claim_for_retry():
    async def scenario() -> None:
        gate = _Gate()
        gate.release.set()
        fn = _make_async(gate, "swr:async:fail")
        assert await fn() == 1
        _expire("swr:async:fail")

        gate.fail = True
        gate.entered.clear()
        assert await fn() == 1
        assert await _await_until(lambda: not _refreshing_has("swr:async:fail"))
        assert _read("swr:async:fail") == 1, "刷新失败应保留旧值"

        gate.fail = False
        gate.entered.clear()
        _expire("swr:async:fail")
        assert await fn() == 1
        assert await _await_until(lambda: _read("swr:async:fail") == 2), "名额释放后应能重试"

    asyncio.run(scenario())
