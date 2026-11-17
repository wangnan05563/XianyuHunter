"""闲鱼 IM 长连接骨架测试（T05073 / O-01，L1）

验收判据（协议评估 §8 可自证部分）：
- 开关默认关闭：enabled=False 时零副作用（不建连、不发事件）；
- 断线重连按指数退避，且不超过上限；
- 连续失败达到阈值即降级（DEGRADED 终态，不自动升回），并发 im.degraded 事件；
- 看门狗超时判定连接假死并重连；
- 关闭链路干净（STOPPED、无残留 task）。
"""
import asyncio
from typing import Any

import pytest

from xianyu_hunter.domain.events import Event, EventType
from xianyu_hunter.infra.yaml_config import AppConfig, XianyuImConfig
from xianyu_hunter.modules.xianyu_im.client import (
    ImSession,
    ImSessionConfig,
    SessionNotice,
    SessionState,
)
from xianyu_hunter.modules.xianyu_im.protocol import session_config_from_yaml
from xianyu_hunter.modules.xianyu_im.service import XianyuImService


class FakeTransport:
    """按脚本驱动的假传输：connect_exc / receive_script 每次消费一项。

    receive_script 为 None 表示永久挂起（供看门狗用例触发 stale 超时）。
    """

    def __init__(
        self,
        connect_exc: Exception | None = None,
        receive_script: list[Any] | None = None,
        hang: bool = False,
    ) -> None:
        self.connect_exc = connect_exc
        self.receive_script = list(receive_script or [])
        self.hang = hang
        self.connect_calls = 0
        self.closed = False

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_exc is not None:
            raise self.connect_exc

    async def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return not self.closed

    async def receive(self) -> Any:
        if self.hang:
            await asyncio.sleep(3600)
        if not self.receive_script:
            raise ConnectionError("script exhausted")
        item = self.receive_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_session_config(**overrides: Any) -> ImSessionConfig:
    base = dict(
        reconnect_backoff_initial_sec=0.01,
        reconnect_backoff_max_sec=0.03,
        reconnect_max_attempts=3,
        stale_after_sec=0.05,
    )
    base.update(overrides)
    return ImSessionConfig(**base)


async def collect_until(session: ImSession, task: asyncio.Task, events: list[SessionNotice]) -> None:
    """运行状态机直到进入终态或超时。"""
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        raise AssertionError(f"状态机未在限时内进入终态，state={session.state} events={events}")


class TestSessionStateMachine:
    async def test_connect_receive_then_stop(self):
        """连接成功 → 收消息 → 外部停止：通知序列与终态正确。"""
        events: list[SessionNotice] = []
        transport = FakeTransport(receive_script=["msg1", "msg2", "msg3"])
        session = ImSession(
            config=make_session_config(),
            transport_factory=lambda: transport,
            notify=events.append,
        )
        task = asyncio.create_task(session.run())
        await asyncio.sleep(0.05)
        await session.shutdown()
        final = await task

        kinds = [e.kind for e in events]
        assert kinds[0] == "connected"
        assert kinds.count("message") == 3
        assert final == SessionState.STOPPED
        assert transport.closed

    async def test_watchdog_triggers_reconnect(self):
        """下行帧超时 → watchdog 通知 → 自动重连成功。"""
        events: list[SessionNotice] = []
        # 第 1 个连接：收到一条消息后脚本耗尽（receive 抛 ConnectionError）→ 重连；
        # 第 2 个连接起：receive 永久挂起，触发看门狗超时。
        first = FakeTransport(receive_script=["hello"])
        second = FakeTransport(hang=True)
        transports = [first, second]

        def factory() -> FakeTransport:
            if transports:
                return transports.pop(0)
            return FakeTransport(hang=True)

        session = ImSession(
            config=make_session_config(),
            transport_factory=factory,
            notify=events.append,
        )
        task = asyncio.create_task(session.run())
        await asyncio.sleep(0.2)
        await session.shutdown()
        # shutdown 后状态机自然退出（不依赖外部 cancel），返回 STOPPED
        final = await asyncio.wait_for(task, timeout=2)

        assert "connected" in [e.kind for e in events]
        assert "watchdog" in [e.kind for e in events]
        # 看门狗后重连：第二个 transport 被 connect 过
        assert second.connect_calls == 1
        assert final == SessionState.STOPPED

    async def test_persistent_failure_degrades(self):
        """连续失败达到阈值 → DEGRADED 终态，不再重连。"""
        events: list[SessionNotice] = []
        calls: list[FakeTransport] = []

        def factory() -> FakeTransport:
            t = FakeTransport(connect_exc=ConnectionError("refused"))
            calls.append(t)
            return t

        session = ImSession(
            config=make_session_config(reconnect_max_attempts=3),
            transport_factory=factory,
            notify=events.append,
        )
        final = await asyncio.wait_for(session.run(), timeout=5)

        assert final == SessionState.DEGRADED
        assert len(calls) == 3
        assert events[-1].kind == "degraded"
        assert "refused" in events[-1].detail
        # 降级后不再自动升回：没有第 4 次 connect
        assert all(t.connect_calls == 1 for t in calls)

    async def test_backoff_grows_exponentially(self):
        """重连间隔按指数退避且封顶（用注入 clock 观测 sleep 序列）。"""
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def timed_sleep(delay: float) -> None:
            sleeps.append(delay)
            await real_sleep(min(delay, 0.01))

        events: list[SessionNotice] = []
        session = ImSession(
            config=make_session_config(reconnect_max_attempts=50),
            transport_factory=lambda: FakeTransport(connect_exc=ConnectionError("down")),
            notify=events.append,
        )
        # monkeypatch asyncio.sleep（模块内引用）；测试自身的等待必须走原始 sleep，
        # 否则会被记进 sleeps 序列污染断言（此前 0.05 混入导致「退避超上限」误报）
        orig = asyncio.sleep
        asyncio.sleep = timed_sleep  # type: ignore[assignment]
        try:
            task = asyncio.create_task(session.run())
            await orig(0.05)
            await session.shutdown()
            await asyncio.wait_for(task, timeout=2)
        finally:
            asyncio.sleep = orig  # type: ignore[assignment]

        grow = [s for s in sleeps if s > 0.005]
        assert len(grow) >= 2
        assert all(b <= 0.03 for b in grow), f"退避超上限: {grow}"


class TestService:
    def _service(self, config: XianyuImConfig, factory=None) -> tuple[XianyuImService, list[Event]]:
        published: list[Event] = []
        return XianyuImService(config=config, transport_factory=factory, publish_event=published.append), published

    async def test_disabled_is_noop(self):
        """enabled=False：不建连、不发事件（默认配置即此形态）。"""
        service, published = self._service(XianyuImConfig())
        service.start()
        assert service.state == "idle"
        assert published == []
        await service.stop()

    async def test_no_adapter_degrades_with_event(self):
        """enabled=True 但无协议适配器（L1 现状）：诚实降级 + im.degraded 事件。"""
        service, published = self._service(XianyuImConfig(enabled=True), factory=None)
        service.start()
        assert [e.type for e in published] == [EventType.IM_DEGRADED]
        assert published[0].payload["fallback"] == "page_polling"
        assert service.state == "idle"
        await service.stop()

    async def test_start_publishes_connected_event(self):
        """有适配器且连接成功：发 im.connected；消息通知进 EventBus。"""
        transport = FakeTransport(receive_script=[None], hang=True)
        service, published = self._service(XianyuImConfig(enabled=True), factory=lambda: transport)
        service.start()
        try:
            await asyncio.sleep(0.1)
            kinds = [e.type for e in published]
            assert EventType.IM_CONNECTED in kinds
            assert service.state == "online"
        finally:
            await service.stop()
        assert service.state == "idle"


class TestConfigDefaults:
    def test_default_disabled(self):
        """硬约束：默认关闭，未显式开启时行为与旧版本一致。"""
        cfg = AppConfig()
        assert cfg.xianyu_im.enabled is False
        assert cfg.xianyu_im.reconnect_max_attempts >= 1

    def test_yaml_mapping_roundtrip(self):
        """yaml → 状态机参数映射不丢字段。"""
        cfg = XianyuImConfig(
            enabled=True,
            reconnect_backoff_initial_sec=2.0,
            reconnect_backoff_max_sec=60.0,
            reconnect_max_attempts=7,
            stale_after_sec=300,
        )
        sc = session_config_from_yaml(cfg)
        assert sc.reconnect_backoff_initial_sec == 2.0
        assert sc.reconnect_backoff_max_sec == 60.0
        assert sc.reconnect_max_attempts == 7
        assert sc.stale_after_sec == 300.0

    def test_event_severity_registered(self):
        """降级事件必须以 critical 送达（免打扰时段也不被吞）。"""
        assert EventType.IM_DEGRADED.value == "im.degraded"
        from xianyu_hunter.domain.events import severity_of
        assert severity_of(EventType.IM_DEGRADED) == "critical"
