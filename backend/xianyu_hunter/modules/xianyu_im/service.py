"""闲鱼 IM 服务组装：生命周期 + 事件发布 + 降级留痕（T05073 / O-01，L1 骨架）

与 EventBus 的关系（复用现有推送链路，前端 SSE 零改动）：
- 连接状态事件（connected/disconnected/degraded）走 bus.publish_nowait →
  NotifierHub 落 events 表 → SSE 3s 轮询自动推给前端时间线；
- 消息级事件仅 publish 不落库（量大），订阅方（L3 阶段的 Buyer 订阅器）
  自行决定业务处理。

启动挂点：web/startup.py _start_all_schedulers（EventBus 之后）；关闭挂点：
_on_shutdown（EventBus 停止之前 cancel）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from xianyu_hunter.domain.events import Event, EventType
from xianyu_hunter.infra.yaml_config import XianyuImConfig
from xianyu_hunter.modules.xianyu_im.client import (
    ImSession,
    ImTransport,
    SessionNotice,
    SessionState,
)
from xianyu_hunter.modules.xianyu_im.protocol import session_config_from_yaml


class XianyuImService:
    """IM 长连接服务：enabled=False 时 start() 直接返回，零副作用。"""

    def __init__(
        self,
        config: XianyuImConfig,
        transport_factory: Callable[[], ImTransport] | None,
        publish_event: Callable[[Event], None],
    ) -> None:
        self._config = config
        self._transport_factory = transport_factory
        self._publish = publish_event
        self._session: ImSession | None = None
        self._task: asyncio.Task[SessionState] | None = None

    @property
    def state(self) -> str:
        if self._session is None:
            return SessionState.IDLE.value
        return self._session.state.value

    def _on_notice(self, notice: SessionNotice) -> None:
        """状态机通知 → 领域事件 + 日志（降级契约：事件与日志双留痕）。"""
        if notice.kind == "connected":
            logger.info("[xianyu-im] IM 连接已建立")
            self._publish(Event(type=EventType.IM_CONNECTED, payload={"detail": notice.detail}))
        elif notice.kind == "disconnected":
            logger.warning(f"[xianyu-im] IM 连接断开，进入重连：{notice.detail}")
            self._publish(Event(type=EventType.IM_DISCONNECTED, payload={"detail": notice.detail}))
        elif notice.kind == "watchdog":
            logger.warning(f"[xianyu-im] {notice.detail}")
        elif notice.kind == "degraded":
            logger.error(f"[xianyu-im] 已降级到页面轮询：{notice.detail}")
            self._publish(Event(
                type=EventType.IM_DEGRADED,
                payload={"detail": notice.detail, "fallback": "page_polling"},
            ))
        elif notice.kind == "message":
            logger.debug("[xianyu-im] 收到 IM 消息（L3 阶段接入业务分发）")
            self._publish(Event(type=EventType.IM_MESSAGE_RECEIVED, payload={"preview": notice.detail}))

    async def start(self) -> None:
        """按配置启动长连接；任何一步不可用都走降级留痕，绝不抛出到启动链路。"""
        if not self._config.enabled:
            logger.info("[xianyu-im] xianyu_im.enabled=false，IM 长连接未启用（页面轮询照常）")
            return
        if self._transport_factory is None:
            # 诚实降级：L1 骨架没有真实协议适配器，明确告知而不是假装在线
            logger.warning("[xianyu-im] 无协议适配器（L2 未实现），降级到页面轮询")
            self._publish(Event(
                type=EventType.IM_DEGRADED,
                payload={"detail": "协议适配器未实现（L2 待接入）", "fallback": "page_polling"},
            ))
            return

        self._session = ImSession(
            config=session_config_from_yaml(self._config),
            transport_factory=self._transport_factory,
            notify=self._on_notice,
        )
        self._task = asyncio.create_task(self._session.run(), name="xianyu-im-session")
        logger.info("[xianyu-im] IM 长连接已启动")

    async def stop(self) -> None:
        """停止长连接并等待状态机退出（幂等；shutdown 链路调用）。"""
        if self._session is not None:
            await self._session.shutdown()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._session = None
        logger.info("[xianyu-im] IM 长连接已停止")


def create_service(
    container: Any,
    transport_factory: Callable[[], ImTransport] | None = None,
) -> XianyuImService:
    """从 DI 容器组装服务（web/startup.py 调用）。

    transport_factory 默认 None（L1 骨架无真实协议）；L2 接入后由
    protocol.build_transport_factory 构造传入。
    """
    return XianyuImService(
        config=container.config.xianyu_im,
        transport_factory=transport_factory,
        publish_event=lambda event: container.event_bus.publish_nowait(event),
    )
