"""C2 回归：`GET /api/auth/me` 不再为「顺手触发」付出每请求一次全量订单扫描

取证背景（`perf/2026-10-31-full-api/c2_me_probe.py` / `c2_ladder.py`）：
`/me` 稳态每请求打两条 SQL —— `SELECT * FROM users`（取展示名）与
`list_orders(limit=200)`（订单超时扫描）。而 `create_sqlite_engine` 用 StaticPool
全进程共享**一条** SQLite 连接（`infra/db_models.py:872`），所以这两条不只是 `/me`
自己的耗时，而是每请求抢一次全局连接。阶梯实测（同一沙箱、每级 96 请求）：

    c=1   /me 8.0 ms   vs 近乎空处理器的 POST /me/refresh 5.3 ms
    c=48  /me 233.5 ms vs 178.4 ms
    c=96  /me 297.1 ms vs 177.0 ms

本文件锁两件事：
1. 节流本身正确 —— 窗口内不重复扫、窗口外恢复、按用户分桶、默认不节流，
   且**首次调用仍会正常产出超时通知**（节流不能把功能关掉）。
2. 路由接线正确 —— `/me` 传了窗口、身份只解析一次（`verify_session` 持进程级锁，
   每请求两次串行化点是纯浪费）。
"""
from __future__ import annotations

import ast
import concurrent.futures
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from xianyu_hunter.web.routes import auth_query
from xianyu_hunter.web.services import notification_engine as eng

ROOT = Path(__file__).resolve().parent.parent
AUTH_QUERY = ROOT / "backend" / "xianyu_hunter" / "web" / "routes" / "auth_query.py"


@pytest.fixture(autouse=True)
def _clear_scan_gate():
    """节流窗口是进程级状态，逐例清零，避免用例间互相吃掉窗口"""
    eng.reset_scan_gate()
    yield
    eng.reset_scan_gate()


class _CountingRepo:
    """只记录 list_orders 调用次数的最小替身"""

    def __init__(self) -> None:
        self.scans: list[str | None] = []

    def list_orders(self, limit: int = 200, user_id: str | None = None):
        self.scans.append(user_id)
        return []


def _shim(repo) -> SimpleNamespace:
    return SimpleNamespace(repo=repo)


@pytest.fixture
def repo(tmp_path):
    """真实仓储（临时库），供需要落 orders/notifications 的两条功能用例"""
    from xianyu_hunter.infra.repository import Repository

    return Repository(db_path=str(tmp_path / "me_scan_throttle.db"))


# ==================== 1. 节流窗口 ====================
def test_first_call_scans_and_second_is_skipped_within_window():
    repo = _CountingRepo()
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=30.0)
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=30.0)
    assert repo.scans == ["alice"], "窗口内第二次不应再触碰 orders 表"


def test_default_min_interval_does_not_throttle():
    """默认不节流：显式调用方（测试、按需扫描）语义必须与改动前一致"""
    repo = _CountingRepo()
    eng.scan_order_timeouts(_shim(repo), user_id="alice")
    eng.scan_order_timeouts(_shim(repo), user_id="alice")
    assert repo.scans == ["alice", "alice"]


def test_window_expiry_restores_scan():
    repo = _CountingRepo()
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=0.02)
    time.sleep(0.05)
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=0.02)
    assert len(repo.scans) == 2, "超过窗口后必须恢复扫描，否则提醒会永久停摆"


def test_gate_is_per_user():
    """A 的请求不能把 B 的扫描窗口吃掉，否则 B 的超时提醒被别人的流量压住"""
    repo = _CountingRepo()
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=30.0)
    eng.scan_order_timeouts(_shim(repo), user_id="bob", min_interval=30.0)
    assert repo.scans == ["alice", "bob"]


def test_none_scope_has_its_own_bucket():
    """None（直通/全库）与具名用户分属不同桶：范围不同，不能互相顶掉"""
    repo = _CountingRepo()
    eng.scan_order_timeouts(_shim(repo), user_id=None, min_interval=30.0)
    eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=30.0)
    assert repo.scans == [None, "alice"]


def test_concurrent_first_burst_scans_once():
    """并发风暴里同用户只放行一次（窗口登记必须在扫描之前，否则 N 线程全进）"""
    repo = _CountingRepo()
    barrier = threading.Barrier(16)

    def _call(_ignored) -> None:
        barrier.wait()
        eng.scan_order_timeouts(_shim(repo), user_id="alice", min_interval=30.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_call, range(16)))
    assert len(repo.scans) == 1


# ==================== 2. 功能不能被节流关掉 ====================
def test_timeout_notification_still_produced_on_first_call(repo):
    """节流的正当理由是「5 min 阈值 vs 30 s 窗口」，不是少发通知"""
    from sqlalchemy import insert

    from xianyu_hunter.infra.db_models import OrderRow, _utcnow

    with repo.engine.begin() as conn:
        conn.execute(insert(OrderRow).values(
            id="o-throttle", item_id="i-throttle", price=9.9, status="pending_pay",
            created_at=_utcnow() - timedelta(minutes=8), user_id="carol",
        ))

    stats = eng.scan_and_notify(
        _shim(repo), auth_state={"logged_in": True}, user_id="carol",
        min_scan_interval=eng.ORDER_SCAN_MIN_INTERVAL_SEC,
    )
    assert stats["order_timeout"] == 1
    items = repo.list_notifications(user_id="carol")
    assert [i["dedup_key"] for i in items] == ["order_timeout:o-throttle:5min"]

    # 同一窗口内的第二个请求被跳过：通知不重复，但已经写好的那条还在
    second = eng.scan_and_notify(
        _shim(repo), auth_state={"logged_in": True}, user_id="carol",
        min_scan_interval=eng.ORDER_SCAN_MIN_INTERVAL_SEC,
    )
    assert second["order_timeout"] == 0
    assert len(repo.list_notifications(user_id="carol")) == 1


def test_auth_down_rule_is_not_throttled(repo, tmp_path, monkeypatch):
    """只节流订单扫描；掉线告警本身按半天窗口去重，成本低且时效重要"""
    import xianyu_hunter.paths as paths

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "userinfo.json").write_text('{"user_id": "dave"}', encoding="utf-8")
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)

    container = _shim(repo)
    stats = eng.scan_and_notify(
        container, auth_state={"logged_in": False}, user_id="dave",
        min_scan_interval=eng.ORDER_SCAN_MIN_INTERVAL_SEC,
    )
    assert stats["auth_down"] == 1, "订单扫描被节流不应连带关掉掉线告警"


# ==================== 3. 路由接线（静态守卫） ====================
def _auth_me_tree() -> ast.FunctionDef:
    tree = ast.parse(AUTH_QUERY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "auth_me":
            return node
    raise AssertionError("auth_query.py 里找不到 auth_me")


def test_me_resolves_current_user_id_only_once():
    """C2-①：`/me` 里 `_resolve_current_user_id` 只允许出现一次

    原先 :336 与 :406 各调一次，而 `verify_session` 全程持 UserManager 的进程级
    `_lock`（锁范围盖住查库）——同一请求里两次抢同一把全局锁纯属白付。
    """
    calls = [
        n for n in ast.walk(_auth_me_tree())
        if isinstance(n, ast.Call)
        and (isinstance(n.func, ast.Name) and n.func.id == "_resolve_current_user_id")
    ]
    assert len(calls) == 1, f"应只解析一次身份，实际 {len(calls)} 次"


def test_me_passes_scan_throttle_window():
    """C2-⑤：`/me` 必须显式带上节流窗口，且用的是模块常量而非散落字面量"""
    scan_calls = [
        n for n in ast.walk(_auth_me_tree())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "scan_and_notify"
    ]
    assert len(scan_calls) == 1
    kwargs = {kw.arg for kw in scan_calls[0].keywords}
    assert "min_scan_interval" in kwargs, "/me 未把节流窗口传给扫描，每请求一条全量 SQL"
    values = [kw.value for kw in scan_calls[0].keywords if kw.arg == "min_scan_interval"]
    assert any(
        isinstance(v, ast.Name) and v.id == "ORDER_SCAN_MIN_INTERVAL_SEC" for v in values
    ), "窗口应引用 notification_engine 的常量，避免路由与引擎各写一份数值"


def test_me_reuses_uid_for_notification_owner(monkeypatch):
    """行为层：身份解析一次，且同一个值既用于读 cookie 也用于通知归属"""
    seen: dict = {}
    calls: list = []

    class _Req:
        cookies = {"xh_token": "tok"}
        headers: dict = {}
        state = SimpleNamespace(user_id=None)

    def _fake_resolve(request):
        calls.append(request)
        return "frank"

    monkeypatch.setattr(auth_query, "_resolve_current_user_id", _fake_resolve)
    monkeypatch.setattr(auth_query, "_check_cookies", lambda user_id="default": False)
    monkeypatch.setattr(
        auth_query, "get_auth_manager",
        lambda: SimpleNamespace(
            USERINFO_TTL=300.0,
            get_userinfo=lambda: {"logged_in": False, "user_id": "", "nick": "",
                                  "avatar_url": "", "fetched_at": 0},
            get_refresh_status=lambda: {"state": "never", "error": "", "at": 0.0},
            trigger_refresh_userinfo_async=lambda delay=0.0: None,
        ),
    )

    def _fake_scan(container, auth_state=None, user_id=None, min_scan_interval=0.0):
        seen["user_id"] = user_id
        seen["min_scan_interval"] = min_scan_interval
        return {}

    monkeypatch.setattr(auth_query, "scan_and_notify", _fake_scan)

    auth_query.auth_me(_Req(), container=None)

    assert len(calls) == 1, "一次请求解析了两次身份"
    assert seen["user_id"] == "frank"
    assert seen["min_scan_interval"] == eng.ORDER_SCAN_MIN_INTERVAL_SEC


def test_owner_scope_preserved_when_no_session(monkeypatch):
    """无会话身份时仍回退 owner_user_id（T02618 口径不能被节流改动）"""
    seen: dict = {}

    class _Req:
        cookies: dict = {}
        headers: dict = {}
        state = SimpleNamespace(user_id="grace")

    monkeypatch.setattr(auth_query, "_resolve_current_user_id", lambda request: None)
    monkeypatch.setattr(auth_query, "_check_cookies", lambda user_id="default": False)
    monkeypatch.setattr(
        auth_query, "get_auth_manager",
        lambda: SimpleNamespace(
            USERINFO_TTL=300.0,
            get_userinfo=lambda: {"logged_in": False, "user_id": "", "nick": "",
                                  "avatar_url": "", "fetched_at": 0},
            get_refresh_status=lambda: {"state": "never", "error": "", "at": 0.0},
            trigger_refresh_userinfo_async=lambda delay=0.0: None,
        ),
    )
    monkeypatch.setattr(
        auth_query, "scan_and_notify",
        lambda container, auth_state=None, user_id=None, **_: seen.update(user_id=user_id),
    )

    auth_query.auth_me(_Req(), container=None)
    assert seen["user_id"] == "grace"
