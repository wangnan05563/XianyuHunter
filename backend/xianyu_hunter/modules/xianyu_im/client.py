"""闲鱼 IM WebSocket 客户端：传输抽象 + 连接状态机（T05073 / O-01，L1 骨架）

职责边界（协议评估 §6 分级路线）：
- 本模块只负责「与协议无关」的长连接生命周期：连接 → 在线 → 断线重连
  （指数退避）→ 连续失败降级 → 停止；
- 真实闲鱼协议（握手/注册/心跳帧/Protobuf 编解码/sign 签名）由 ImTransport
  的 L2 实现承载，本模块不感知协议细节，可用假传输完全自测。

降级语义（协议评估 §7 硬约束）：
- 连续 reconnect_max_attempts 次重连失败 → state=DEGRADED 并发 IM_DEGRADED
  事件后**停止重连**；降级后不允许自动升回（由人工确认或显式重启）；
- 看门狗：receive() 超过 stale_after_sec 无下行帧即判定连接假死，主动断开
  走重连——防止「TCP 在、消息不来」的假在线。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from loguru import logger


class SessionState(str, Enum):
    """连接状态机的稳定态（过渡态用 CONNECTING/RECONNECTING 表达）"""
    IDLE = "idle"
    CONNECTING = "connecting"
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass
class SessionNotice:
    """状态机对外通知（service 层转成领域事件 + 日志）"""
    kind: str            # connected / disconnected / degraded / message / watchdog
    detail: str = ""
    message: Any = None  # kind="message" 时承载协议解析出的消息体


@runtime_checkable
class ImTransport(Protocol):
    """闲鱼 IM 传输抽象。

    L2 阶段由真实协议适配器实现（wss 握手 + 注册 + 心跳 + Protobuf 解码）；
    测试与骨架阶段用 FakeTransport 驱动状态机。receive() 阻塞直到下一帧，
    连接断开/解析失败时抛异常（状态机据此走重连分支）。
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def receive(self) -> Any: ...


@dataclass
class ImSessionConfig:
    """状态机参数（从 yaml_config.XianyuImConfig 映射，便于独立测试）"""
    reconnect_backoff_initial_sec: float = 1.0
    reconnect_backoff_max_sec: float = 30.0
    reconnect_max_attempts: int = 5
    stale_after_sec: float = 180.0


@dataclass
class ImSession:
    """与协议无关的 IM 长连接状态机。

    notify 回调在状态变化时被同步调用（service 层据此发事件/写日志）；
    transport_factory 每次重连调用一次，允许适配器按需重建握手凭据。
    """

    config: ImSessionConfig
    transport_factory: Callable[[], ImTransport]
    notify: Callable[[SessionNotice], None]
    clock: Callable[[], float] = field(default=time.monotonic)
    state: SessionState = SessionState.IDLE
    _stop_requested: bool = field(default=False, repr=False)
    _transport: ImTransport | None = field(default=None, repr=False)

    def _emit(self, kind: str, detail: str = "", message: Any = None) -> None:
        self.notify(SessionNotice(kind=kind, detail=detail, message=message))

    def _set_state(self, state: SessionState) -> None:
        self.state = state

    def request_stop(self) -> None:
        """外部请求停止（shutdown / 用户手动关停），幂等。"""
        self._stop_requested = True

    async def _close_transport(self) -> None:
        if self._transport is None:
            return
        try:
            await self._transport.close()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[xianyu-im] 关闭旧传输异常（忽略）: {e}")
        finally:
            self._transport = None

    async def run(self) -> SessionState:
        """状态机主循环：连接 → 在线 → 断线重连 → 降级/停止。

        返回最终状态；DEGRADED 与 STOPPED 都是终态（不自动升回）。
        """
        backoff = self.config.reconnect_backoff_initial_sec
        failures = 0
        self._set_state(SessionState.CONNECTING)

        while not self._stop_requested:
            transport = self.transport_factory()
            self._transport = transport
            try:
                await transport.connect()
            except Exception as e:  # noqa: BLE001
                await self._close_transport()
                failures += 1
                if failures >= self.config.reconnect_max_attempts:
                    return self._degrade(f"连续 {failures} 次连接失败：{e}")
                self._set_state(SessionState.RECONNECTING)
                self._emit("disconnected", detail=f"连接失败（第 {failures} 次）：{e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.config.reconnect_backoff_max_sec)
                continue

            failures = 0
            backoff = self.config.reconnect_backoff_initial_sec
            self._set_state(SessionState.ONLINE)
            self._emit("connected")

            # 在线循环：收帧 + 看门狗（stale 超时主动断开重连）
            action, detail = await self._receive_loop(transport)
            if action == "stop":
                self._set_state(SessionState.STOPPED)
                return self.state
            if action == "degrade":
                return self._degrade(detail)

            # 假死/接收中断 → 指数退避后重连
            self._set_state(SessionState.RECONNECTING)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.config.reconnect_backoff_max_sec)

        await self._close_transport()
        self._set_state(SessionState.STOPPED)
        return self.state

    async def _receive_loop(self, transport: ImTransport) -> tuple[str, str]:
        """在线收帧循环：返回 (下一步动作, 原因)，动作 ∈ {stop, degrade, reconnect}。

        CancelledError 必须在清理后重新抛出（S7497）：吞掉取消会让调用方的
        task.cancel() 表现成「正常返回」，取消信号就此丢失。
        """
        failures = 0
        while not self._stop_requested:
            try:
                message = await asyncio.wait_for(
                    transport.receive(), timeout=self.config.stale_after_sec,
                )
            except asyncio.TimeoutError:
                await self._close_transport()
                self._emit(
                    "watchdog",
                    detail=f"超过 {self.config.stale_after_sec:.0f}s 未收到下行帧，判定连接假死",
                )
                return "reconnect", "watchdog"
            except asyncio.CancelledError:
                await self._close_transport()
                self._set_state(SessionState.STOPPED)
                raise
            except Exception as e:  # noqa: BLE001
                await self._close_transport()
                failures += 1
                self._emit("disconnected", detail=f"接收中断：{e}")
                if failures >= self.config.reconnect_max_attempts:
                    return "degrade", f"连续 {failures} 次接收失败：{e}"
                return "reconnect", "receive-error"

            self._emit("message", message=message)
        return "stop", "stop-requested"

    def _degrade(self, reason: str) -> SessionState:
        """连续失败超阈值：降级并停止重连（硬约束：不自动升回）。"""
        self._set_state(SessionState.DEGRADED)
        self._emit("degraded", detail=reason)
        logger.warning(f"[xianyu-im] 已降级到页面轮询（{reason}）；不再自动重连")
        return self.state

    async def shutdown(self) -> None:
        """请求停止并等待传输关闭（幂等）。"""
        self.request_stop()
        transport = self._transport
        if transport is not None:
            try:
                await transport.close()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[xianyu-im] shutdown 关闭传输异常（忽略）: {e}")
