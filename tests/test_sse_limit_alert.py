"""E2 回归：SSE 连接超限必须在服务端留告警

超限时客户端只收到一条流内 error 事件，服务端此前没有任何记录 —— 运维侧看不到
「容量已经打满」。本文件锁三件事：
1. 超限时恰好打一条 WARNING，且带上限值；
2. 未超限时不打；
3. 释放槽位后可以重新获取（计数不漂移）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xianyu_hunter.web.routes import sse_stream as S


@pytest.fixture
def warn_log(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(S, "logger", SimpleNamespace(
        warning=lambda msg: calls.append(msg),
        info=lambda msg: None,
        debug=lambda msg: None,
    ))
    # 模块级 Lock 会绑定首次 acquire 的事件循环，测试里换一个干净的
    monkeypatch.setattr(S, "_sse_gate", asyncio.Lock())
    return calls


def test_limit_rejection_warns_with_cap(monkeypatch, warn_log: list[str]) -> None:
    monkeypatch.setattr(S, "_sse_connection_count", S._MAX_SSE_CONNECTIONS)

    async def _flow() -> bool:
        return await S._acquire_sse_slot()

    assert asyncio.run(_flow()) is False
    assert len(warn_log) == 1, f"超限应恰好打一条告警，实际 {len(warn_log)} 条"
    assert str(S._MAX_SSE_CONNECTIONS) in warn_log[0]
    assert "SSE" in warn_log[0]


def test_below_limit_does_not_warn(monkeypatch, warn_log: list[str]) -> None:
    monkeypatch.setattr(S, "_sse_connection_count", 0)

    async def _flow() -> bool:
        ok = await S._acquire_sse_slot()
        await S._release_sse_slot()
        return ok

    assert asyncio.run(_flow()) is True
    assert warn_log == []


def test_slot_can_be_reacquired_after_release(monkeypatch, warn_log: list[str]) -> None:
    monkeypatch.setattr(S, "_sse_connection_count", S._MAX_SSE_CONNECTIONS)

    async def _flow() -> tuple[bool, bool]:
        first = await S._acquire_sse_slot()
        await S._release_sse_slot()
        second = await S._acquire_sse_slot()
        await S._release_sse_slot()
        return first, second

    first, second = asyncio.run(_flow())
    assert first is False
    assert second is True, "释放后槽位没有归还，计数漂移"
    assert len(warn_log) == 1
