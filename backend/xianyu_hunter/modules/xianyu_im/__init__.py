"""闲鱼 IM WebSocket 长连接模块（T05073 / O-01）

L1 骨架（协议评估见 docs/requirements/闲鱼IM-WebSocket协议评估-20261110.md）：

- client.py：传输抽象 + 连接状态机（指数退避重连、看门狗、连续失败降级）；
- service.py：与 FastAPI 生命周期的组装（启动/停止/事件发布/降级留痕）；
- protocol.py：真实闲鱼协议（sign + base64 + Protobuf）适配器接口，L2 阶段实现。

硬约束（降级契约）：
- enabled 默认 False，未显式开启时零副作用；
- 页面轮询是常备底座，IM 断开/降级不影响任何现有业务路径；
- 降级后不自动升回，避免抖动放大风控暴露面。
"""
