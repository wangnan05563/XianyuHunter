"""业务通知规则引擎（P1-4）

设计原则：
- 零侵入：所有检测都通过现有 API 端点返回路径"顺手"触发，不引入新的 cron / 定时器
- 幂等：每条业务事件用 dedup_key 唯一标识，重复调用只刷新不堆积
- 升级：超时订单从 5min → 15min → 30min 用不同 dedup_key，让用户能感受到"事情在变严重"

当前实现：
1. OrderTimeoutRule：扫 orders 表，发现待支付（pending_pay）超过阈值就告警
2. AuthDownRule：检测登录态从"已登录" → "未登录"时告警（首次 + 重复提醒分级）

调用入口：scan_and_notify() → 聚合所有规则
`list_orders` 已不再顺手触发（H-05），现在唯一的顺手触发点是 `GET /api/auth/me`
——它是控制台每个页面加载的必经调用。为了不把这个扫描变成"每请求一条 SQL"，
`/me` 传入 `min_scan_interval`（见 ORDER_SCAN_MIN_INTERVAL_SEC）：同一用户在窗口内
只做一次全量扫描。节流放在请求路径而不是另起 cron，正是本模块「零侵入」原则的延续。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from loguru import logger

from xianyu_hunter.container import Container
from xianyu_hunter.domain.notification import NotificationCategory
from xianyu_hunter.domain.order import PENDING_STATUSES
from xianyu_hunter.infra.db_models import _utcnow
from xianyu_hunter.web.utils import to_datetime

# 订单超时阈值（分钟 → 等级）
# 为什么不直接 30min 一个阈值：分级别让用户感受到紧迫感，
# 也避免 30min 才提醒（可能已超时关闭）
ORDER_TIMEOUT_RULES: list[tuple[int, str, str]] = [
    (5, "warn", "5min"),
    (15, "warn", "15min"),
    (30, "err", "30min"),
]

# 顺手触发的扫描窗口（秒）。取 30 s 的判据：最松的一档阈值是 5 min，
# 30 s 窗口最多让一条超时提醒晚 30 s 出现（1% 的阈值跨度），却能把
# 「每个 /me 请求一条 list_orders(limit=200)」压成「每用户每 30 s 一条」。
ORDER_SCAN_MIN_INTERVAL_SEC = 30.0

# 各用户上轮扫描时刻（monotonic）。锁只为保护这个字典本身，
# 不用 `dict.setdefault` 之类的原子写法是因为读取与写回必须成对比较。
_last_scan_at: dict[str, float] = {}
_scan_gate_lock = threading.Lock()


def _scan_gate_allowed(key: str, min_interval: float) -> bool:
    """判断本轮扫描是否放行，并在放行时登记时刻（min_interval<=0 恒放行）"""
    if min_interval <= 0:
        return True
    now = time.monotonic()
    with _scan_gate_lock:
        last = _last_scan_at.get(key)
        if last is not None and now - last < min_interval:
            return False
        _last_scan_at[key] = now
    return True


def reset_scan_gate() -> None:
    """清空节流窗口（供测试与其他显式触发方使用）"""
    with _scan_gate_lock:
        _last_scan_at.clear()


def scan_order_timeouts(
    container: Container,
    user_id: str | None = None,
    min_interval: float = 0.0,
) -> int:
    """扫描超时订单，按阈值生成/更新业务通知。

    user_id（T02618）：扫描范围与通知归属同一个值——只扫这个用户的订单，
    生成的通知也归他；None = 全库扫描且归属由仓储兜底 default。
    与路由侧 `owner_user_id` 同源，否则「扫描按 A、通知落 default、列表按 A 过滤」
    三方错位，A 永远看不到自己的超时提醒。

    min_interval > 0 时按用户节流：窗口内的重复调用直接返回 0，不再触碰 orders 表
    （None 归入 "*" 一档）。默认 0 = 每次都扫，保持显式调用方（测试、按需扫描端点）
    的原有语义不变。

    Returns: 新增/刷新的通知数（仅统计本轮实际触发的）。
    """
    if not _scan_gate_allowed(user_id or "*", min_interval):
        return 0
    orders = container.repo.list_orders(limit=200, user_id=user_id) or []
    now = _utcnow()
    triggered = 0
    for o in orders:
        status = o.get("status")
        if status not in PENDING_STATUSES:
            continue
        created_at = to_datetime(o.get("created_at"))
        if created_at is None:
            continue
        age_min = (now - created_at).total_seconds() / 60
        # 按阈值从大到小匹配，确保"最严重级别"胜出
        # 为什么要从大到小而不是从小到大：5min 触发过 → 15min 才触发时
        # 我们用 15min 的 dedup_key，5min 的旧通知被替换为新级别
        for thr_min, level, level_tag in sorted(ORDER_TIMEOUT_RULES, key=lambda x: -x[0]):
            if age_min >= thr_min:
                order_id = o.get("id")
                amount = o.get("price") or 0
                title = f"订单 #{order_id} 已等待支付 {level_tag}"
                message = (
                    f"订单 #{order_id}（¥{amount:.2f}）已超过 {thr_min} 分钟未支付，"
                    f"系统已暂停自动重试。请尽快人工接管或确认是否需要放弃。"
                )
                link = f"/orders?ref={order_id}"
                dedup_key = f"order_timeout:{order_id}:{level_tag}"
                # reset_read=True：超时从 5min 升到 15min 时让用户重新看到
                container.repo.add_notification(
                    level=level,
                    category=NotificationCategory.ORDER.value,
                    title=title,
                    message=message,
                    link=link,
                    dedup_key=dedup_key,
                    reset_read=True,
                    user_id=user_id,
                )
                triggered += 1
                break  # 一笔订单只触发最严重级别
    return triggered


def _scan_auth_down(
    container: Container, auth_state: dict[str, Any], user_id: str | None = None
) -> int:
    """登录态掉线告警（被动触发：auth_state 由调用方注入）

    auth_state 形如 {"logged_in": bool, "user_id": str|None, ...}
    触发条件：上次是已登录 + 这次是未登录 → 创建"登录态失效"通知
    user_id（T02618）：通知归属；None 由仓储兜底 default。
    """
    if not auth_state or auth_state.get("logged_in"):
        return 0
    # 借力现有 auth 模块的 userinfo.json 状态：拿不到 user_id 就说明真的没登过
    # 避免每个匿名用户首次访问都生成一条"登录态失效"噪音
    from pathlib import Path

    from xianyu_hunter.paths import get_data_dir
    userinfo_path = get_data_dir() / "userinfo.json"
    last_user_id: str | None = None
    if userinfo_path.exists():
        try:
            import json
            data = json.loads(userinfo_path.read_text(encoding="utf-8"))
            last_user_id = data.get("user_id")
        except (json.JSONDecodeError, OSError):
            pass
    if not last_user_id:
        return 0  # 从未登录过 → 不告警

    # 用稳定的 dedup_key：登录态失效事件按 user_id + 半天窗口归一
    # 半天窗口：避免反复刷；半天后又失效 → 重新生成一条
    half_day_key = _utcnow().strftime("%Y%m%d_%H") + (
        "_PM" if _utcnow().hour >= 12 else "_AM"
    )
    dedup_key = f"auth_down:{last_user_id}:{half_day_key}"
    title = "闲鱼登录态已失效"
    message = (
        f"账号 {last_user_id} 的登录态已掉线，请尽快重新扫码登录。"
        f"系统在登录恢复前不会执行抢单。"
    )
    link = "/dashboard#login"
    container.repo.add_notification(
        level="err",
        category=NotificationCategory.AUTH.value,
        title=title,
        message=message,
        link=link,
        dedup_key=dedup_key,
        reset_read=True,
        user_id=user_id,
    )
    return 1


def scan_and_notify(
    container: Container,
    auth_state: dict[str, Any] | None = None,
    user_id: str | None = None,
    min_scan_interval: float = 0.0,
) -> dict[str, int]:
    """聚合入口：执行所有规则，返回各类触发数

    Args:
        auth_state: 当前请求的登录态（可选）。None 表示不检测登录掉线。
        user_id: 本轮扫描与通知的归属用户（T02618），由请求侧传
            `web.utils.owner_user_id(request)`——与 /api/notifications 读写侧同一个值，
            语义是"范围 = 归属"：只扫这个用户名下的订单，生成的通知也归他。
            原先既不传范围也不传归属，等于"用 A 的请求给全体订单生成归属 default 的
            通知"，两头都不对：会话用户在自己的通知中心看不到本该属于他的提醒。
            None 时由仓储层兜底 default（单用户安装的行为完全不变）。
        min_scan_interval: 透传给订单超时扫描的节流窗口（秒）。高频请求路径
            （`GET /api/auth/me`）传 `ORDER_SCAN_MIN_INTERVAL_SEC`，显式触发方传 0。
    """
    stats: dict[str, int] = {}
    try:
        stats["order_timeout"] = scan_order_timeouts(
            container, user_id=user_id, min_interval=min_scan_interval
        )
    except Exception as e:  # noqa: BLE001
        # 通知规则失败不能让业务 API 跟着 500
        logger.warning(f"[notify] 订单超时扫描失败: {e}")
        stats["order_timeout"] = 0
    if auth_state is not None:
        try:
            stats["auth_down"] = _scan_auth_down(container, auth_state, user_id=user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[notify] 登录态扫描失败: {e}")
            stats["auth_down"] = 0
    return stats
