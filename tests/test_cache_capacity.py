"""读缓存容量上限回归测试 —— 对应性能线 OPTIMIZATIONS B4

背景（`perf/2026-10-31-full-api/b4_cache_census.py` + `b4_key_ceiling_probe.py` 取证）：
原文担心的是「`_cache_store` 无容量上限 ⇒ 内存/RSS 爬升」，但按值体积实测这条不成立
—— 全量扫一遍所有带缓存的 GET 接口只留下 7 条 / 0.02 MB，单条最深 7.9 KB。
真实的增长维度是**键基数**：key_fn 直接拼了查询参数，而路由声明不约束参数
（`metric: str`、`range_hours: int`、`task_id: str | None`），于是「每个 task 打一遍」
= 每 task 2.0 条 / 8.8 KB，随便传个没见过的 metric 也能各占一条 0.63 KB，且永不自清。

所以这里锁的是「兜底上限确实生效、且不破坏既有语义」四件事：
1. 超过上限时按 LRU 驱逐最久未用的条目，条目数恒定在上限内；
2. 读命中（含 stale 读）会续命 —— 否则正常流量会把 stale-while-revalidate 的旧值挤掉，
   把它从「立即返回旧值」退化成一整次重算（A4 实测该重算 170~190 ms）；
3. 淘汰后同 key 再来是干净的 miss（重算 + 写回），不会拿到别人的值；
4. 既有两个失效入口（全清 / 按前缀）在 LRUDict 上行为不变。
"""
from __future__ import annotations

import time

import pytest

from xianyu_hunter.web import cache as cache_mod
from xianyu_hunter.web.cache import cached_ttl, configure_cache_max_entries

STALE_KEY = ("maintenance_status", "maintenance_status")


def _reset_cache_state() -> None:
    with cache_mod._cache_lock:
        cache_mod._cache_store.clear()
        cache_mod._cache_store.maxsize = cache_mod._DEFAULT_MAX_ENTRIES
        cache_mod._flights.clear()
        cache_mod._flights_async.clear()
        cache_mod._refreshing.clear()
    cache_mod._bg_tasks.clear()


@pytest.fixture(autouse=True)
def _isolate_cache() -> None:
    _reset_cache_state()
    yield
    _reset_cache_state()


def _expire(cache_key: tuple) -> None:
    """把某条缓存的时间戳拨到过去，模拟 TTL 已过。"""
    with cache_mod._cache_lock:
        _ts, value = cache_mod._cache_store[cache_key]
        cache_mod._cache_store[cache_key] = (time.monotonic() - 10_000, value)


def test_store_evicts_least_recently_used_entry() -> None:
    configure_cache_max_entries(3)
    for i in range(3):
        cache_mod._set_cache(("f", str(i)), i)

    # 读一次 0 号：它不该再是最久未用的那条
    assert cache_mod._try_get_cache(("f", "0"), 60) == (True, 0)
    cache_mod._set_cache(("f", "3"), 3)

    keys = list(cache_mod._cache_store)
    assert len(keys) == 3
    assert ("f", "0") in keys
    assert ("f", "1") not in keys, "最久未用的 1 号应被驱逐"
    assert ("f", "3") in keys


def test_cached_ttl_entry_count_stays_bounded() -> None:
    calls: list[int] = []

    @cached_ttl(60, key_fn=lambda bucket: str(bucket))
    def load(bucket: int) -> dict[str, int]:
        calls.append(bucket)
        return {"bucket": bucket}

    configure_cache_max_entries(5)
    for i in range(20):
        assert load(bucket=i)["bucket"] == i

    assert len(calls) == 20
    assert len(cache_mod._cache_store) == 5
    # 最近写入的那条还在缓存里：命中不再重算
    assert load(bucket=19)["bucket"] == 19
    assert calls.count(19) == 1


def test_entry_evicted_misses_cleanly_and_recomputes() -> None:
    calls: list[int] = []

    @cached_ttl(60, key_fn=lambda bucket: str(bucket))
    def load(bucket: int) -> int:
        calls.append(bucket)
        return bucket

    configure_cache_max_entries(2)
    assert [load(bucket=i) for i in range(3)] == [0, 1, 2]
    assert {key[1] for key in cache_mod._cache_store} == {"1", "2"}

    assert load(bucket=0) == 0
    assert calls.count(0) == 2


def test_stale_value_is_protected_by_recency_on_read() -> None:
    """stale-while-revalidate 的旧值只在「被读过」时才续命——这正是正常流量下的形态。"""
    configure_cache_max_entries(3)
    cache_mod._set_cache(STALE_KEY, "old")
    cache_mod._set_cache(("f", "1"), 1)
    cache_mod._set_cache(("f", "2"), 2)
    _expire(STALE_KEY)

    # 一次过期请求的完整路径：TTL 读（miss）→ 取 stale 值
    assert cache_mod._try_get_cache(STALE_KEY, 300) == (False, None)
    assert cache_mod._try_get_stale(STALE_KEY) == "old"

    # 再来一条新 key 触发淘汰：被挤掉的应是没被读过的 1 号，而不是刚读过旧值的 stale key
    cache_mod._set_cache(("f", "3"), 3)
    assert STALE_KEY in cache_mod._cache_store
    assert ("f", "1") not in cache_mod._cache_store


def test_existing_invalidation_paths_still_work() -> None:
    configure_cache_max_entries(50)
    cache_mod._set_cache(("g", "trend:orders:24:all"), {})
    cache_mod._set_cache(("g", "kpi:7"), {})
    cache_mod._set_cache(("h", ("args",), ()), {})

    assert cache_mod.invalidate_cache_prefixes("kpi:") == 1
    assert ("g", "kpi:7") not in cache_mod._cache_store
    assert ("g", "trend:orders:24:all") in cache_mod._cache_store
    assert ("h", ("args",), ()) in cache_mod._cache_store

    cache_mod.invalidate_stats_cache()
    assert not cache_mod._cache_store


def test_non_positive_cap_is_rejected() -> None:
    configure_cache_max_entries(7)
    configure_cache_max_entries(0)
    configure_cache_max_entries(-1)
    assert cache_mod._cache_store.maxsize == 7


def test_default_setting_matches_module_default() -> None:
    from xianyu_hunter.config import Settings

    assert Settings().cache_max_entries == cache_mod._DEFAULT_MAX_ENTRIES


def test_create_app_applies_configured_cap(monkeypatch) -> None:
    """装配期必须真的把配置接到缓存字典上——漏接这行，前面所有用例照样全绿。"""
    from xianyu_hunter.config import get_settings
    from xianyu_hunter.web.app import create_app

    calls: list[int] = []
    monkeypatch.setattr(cache_mod, "configure_cache_max_entries", calls.append)
    create_app()

    assert calls == [get_settings().cache_max_entries]
