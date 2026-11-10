"""闲鱼 IM 协议适配器接口（T05073 / O-01，L2 待实现）

本文件只定义契约，不提供真实协议实现。为什么：
1. 真实协议需要 wss 端点、注册报文、Protobuf schema、sign 签名 JS（Node.js
   子进程）与完整登录态 Cookie——见 docs/requirements/闲鱼IM-WebSocket协议评估-20261110.md
   §2/§5，这些资产要么来自上游逆向项目（cv-cat/XianYuApis、pyxianyu），
   要么必须在授权账号上抓包验证，无法在本机凭空写出；
2. L1 骨架的验收判据（重连退避、降级、事件留痕、开关默认关闭）全部可用
   FakeTransport 自测，不需要协议实现。

L2 实现者按下方 XianyuProtocolAdapter 契约接入，替换 service 的
transport_factory 即可，状态机与事件链路零改动。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xianyu_hunter.infra.yaml_config import XianyuImConfig
from xianyu_hunter.modules.xianyu_im.client import ImSessionConfig, ImTransport


class ProtocolNotImplemented(NotImplementedError):
    """L2 真实协议适配器尚未接入时由 factory 抛出，service 据此降级并留痕。"""


def build_transport_factory(
    config: XianyuImConfig,
    cookie_provider: Callable[[], dict[str, str]],
    uid_provider: Callable[[], str],
) -> Callable[[], ImTransport]:
    """构造真实协议传输工厂（L2 阶段实现）。

    参数：
    - config：心跳间隔等参数（yaml xianyu_im 段）；
    - cookie_provider：从 Cookie 三层管理（web/services/cookie_store.py）取完整
      登录态 Cookie（含 httpOnly 字段）；
    - uid_provider：取用户标识（Cookie `unb`，见 infra/db_models.py:720 注释）。

    L2 待办（缺一不可）：
    - wss 端点与握手 headers（Cookie / User-Agent / Origin）；
    - MTOP 注册接口签名（modules/mtop_signer.py 同源算法或逆向 JS）；
    - 注册/心跳/下行帧的 Protobuf schema 与编解码；
    - deviceId 生成（当前仓库无任何实现，需 utdid 算法或服务端下发）。
    """
    raise ProtocolNotImplemented(
        "真实闲鱼 IM 协议适配器属 L2 阶段（需授权账号抓包 + 协议资产），"
        "详见 docs/requirements/闲鱼IM-WebSocket协议评估-20261110.md §6"
    )


def session_config_from_yaml(config: XianyuImConfig) -> ImSessionConfig:
    """把 yaml 配置映射成状态机参数（唯一入口，保证两处字段不漂移）。"""
    return ImSessionConfig(
        reconnect_backoff_initial_sec=config.reconnect_backoff_initial_sec,
        reconnect_backoff_max_sec=config.reconnect_backoff_max_sec,
        reconnect_max_attempts=config.reconnect_max_attempts,
        stale_after_sec=float(config.stale_after_sec),
    )


def describe_frame(frame: Any) -> str:
    """把一帧协议数据压缩成可读摘要（L2 实现后用于消息事件 payload）。"""
    text = repr(frame)
    return text[:200]
