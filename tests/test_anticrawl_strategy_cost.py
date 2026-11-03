"""C4：`GET /api/anticrawl/strategy` 的两个常数项（CDP 探测超时 + Chromium Cookie 库判定）

压测基线 avg **1 094.7 ms**（`perf/2026-10-31-full-api/REPORT.md` 表 A）。两个探针把它拆开：

- `perf/2026-10-31-full-api/loopback_connect_control.py`：本机对回环上**没监听的端口**不发
  RST 而是静默丢包 —— `create_connection(timeout=1.0)` 吃满 ~1 010 ms，`timeout=0.1` 只需
  ~109 ms；真正在 accept 的端口 0.76 ms 完成握手。所以「CDP 未开」这一最常见形态每次都付满
  超时，这就是那一秒的来源。
- `perf/2026-10-31-full-api/anticrawl_probe_cost.py`：Cookie 库原先 `SELECT name ... LIKE`
  全表取回再在 Python 侧求交集，5 000 行 8.24 ms；把关键名交给 SQL 并 `LIMIT 1` 是 3.44 ms，
  布尔结果逐形状相同。

所以这里只钉两件事：**探测预算是个小常数**、**Cookie 判定语义与改写前逐形状等价**。
C4 的 ②（探测结果短 TTL 缓存）刻意没有落地，因此这里也没有它的用例 —— 理由见
`OPTIMIZATIONS.md` §C4 落地记录。

安全边界（每条用例都满足）：不启动浏览器/隧道，不读真实 `data/browser-data` 与真实 Chromium
Cookie 库，不碰仓库里的 `data/*.json` —— 路径走 `monkeypatch.chdir(tmp_path)` 加模块级
`get_config` / `get_browser_data_dir` 打桩，socket 只碰自造的端口或确定空闲的端口。
"""
from __future__ import annotations

import socket
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from xianyu_hunter.modules import login_strategy as ls
from xianyu_hunter.modules.login_strategy import LoginStrategySelector

# 改写前的原句（逐字抄自改动前的实现）——等价性判据的参照实现，只在本文件里用
LEGACY_SQL = "SELECT name FROM cookies WHERE host_key LIKE ? OR host_key LIKE ?"


def legacy_check(cookie_db: Path) -> bool:
    """原实现的布尔结果：全表取回 name，Python 侧求交集"""
    if not cookie_db.exists():
        return False
    try:
        with sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True) as conn:
            rows = conn.execute(LEGACY_SQL, ("%goofish%", "%taobao%")).fetchall()
            return bool(LoginStrategySelector.KEY_COOKIES & {n for (n,) in rows})
    except Exception:
        return False


def make_cookie_db(path: Path, rows: list[tuple[str, str]]) -> None:
    """按 Chromium `Network/Cookies` 的形状造假库（只建判定用到的两列）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
        conn.executemany("INSERT INTO cookies VALUES (?,?,?)", [(h, n, "v") for h, n in rows])


@pytest.fixture
def browser_dir(tmp_path, monkeypatch) -> Path:
    """把策略 B 的两个外部依赖（配置 + browser-data 根）钉到 tmp_path

    `chdir` 是必需的：`_check_user_data_cookies` 先读 cwd 相对的 `data/cookies.json`，
    而仓库根下真有 `data/` 目录（真实登录 Cookie）。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ls, "get_config", lambda: SimpleNamespace(browser=SimpleNamespace(user_data_dir="bd"))
    )
    monkeypatch.setattr(ls, "get_browser_data_dir", lambda configured=None: tmp_path / "bd")
    return tmp_path / "bd" / "Default" / "Network"


def free_port() -> int:
    """取一个确定没人监听的端口（bind 0 再立刻关闭）"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    return port


def offline_selector() -> LoginStrategySelector:
    """构造一个「CDP 肯定没开」的选择器，让 `_check_cdp_available` 固定返回 False"""
    return LoginStrategySelector(cdp_target_port=free_port())


# ==================== ① CDP 探测预算 ====================
def test_probe_passes_the_class_constant(monkeypatch):
    """探测必须取 `CDP_PROBE_TIMEOUT`，而不是把字面量写死在调用处"""
    recorded: dict = {}

    class _FakeSocketModule:
        @staticmethod
        def create_connection(address, timeout=None):
            recorded["address"] = address
            recorded["timeout"] = timeout
            raise OSError("refused")

    monkeypatch.setattr(ls, "socket", _FakeSocketModule)
    assert LoginStrategySelector(cdp_target_port=12345)._check_cdp_available() is False
    assert recorded["address"] == ("127.0.0.1", 12345)
    assert recorded["timeout"] == LoginStrategySelector.CDP_PROBE_TIMEOUT


def test_probe_budget_is_subsecond():
    """那一秒的常数项就钉在这里：预算必须是亚秒级（判据见被测模块注释）"""
    assert LoginStrategySelector.CDP_PROBE_TIMEOUT <= 0.2


def test_closed_port_probe_fails_fast():
    """端口没监听时整次探测不越过预算量级（本机改回 1.0 s 实测 ~1 010 ms，必红）"""
    port = free_port()
    t0 = time.perf_counter()
    assert LoginStrategySelector(cdp_target_port=port)._check_cdp_available() is False
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"探测耗时 {elapsed * 1000:.0f} ms，那一秒的常数项回来了"


def test_accepting_listener_is_still_detected():
    """降本不能把真开着的 CDP 判成不可用 —— 亚毫秒握手要吃得下"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(5)
    accepted: list = []

    def _accept() -> None:
        try:
            conn, _ = srv.accept()
            accepted.append(conn)
        except OSError:
            accepted.append(None)

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    try:
        t0 = time.perf_counter()
        available = LoginStrategySelector(cdp_target_port=srv.getsockname()[1])._check_cdp_available()
        elapsed = time.perf_counter() - t0
        assert available is True, "0.1 s 预算把真实在 accept 的 CDP 端口判成了不可用"
        assert elapsed < LoginStrategySelector.CDP_PROBE_TIMEOUT
    finally:
        thread.join(timeout=5)
        for conn in accepted:
            if conn is not None:
                conn.close()
        srv.close()


def test_probe_failure_degrades_to_next_strategy(browser_dir, monkeypatch):
    """探测失败不能往上抛：`evaluate()` 要一路回落到扫码登录

    策略 C 打桩成 False：本机 `LOCALAPPDATA` 下真有 Edge 的 Cookie 文件，`_check_browser_importable`
    只看文件在不在就会返回 True，那样 `evaluate()` 停在策略 C，与「回落到扫码」无关，
    用例就变成对环境的断言了。
    """
    sel = offline_selector()
    monkeypatch.setattr(sel, "_check_browser_importable", lambda: False)
    evaluation = sel.evaluate()
    assert evaluation.details["cdp_available"] is False
    assert evaluation.recommended == ls.LoginStrategy.QR_SCAN


# ==================== ③ Cookie 判定与改写前等价 ====================
# 每格 = (表内容, 期望布尔)。形状覆盖：host 过滤、name 过滤、只命中一半、关键名在末尾、空表
SHAPE_CASES = [
    pytest.param([(".goofish.com", "noise")], False, id="goofish-仅无关名"),
    pytest.param([(".taobao.com", "_m_h5_tk")], True, id="taobao-命中"),
    pytest.param([(".alicdn.com", "_m_h5_tk")], False, id="关键名但host不匹配"),
    pytest.param([(".goofish.com", "unb")], True, id="goofish-命中另一个关键名"),
    pytest.param([], False, id="空表"),
    pytest.param(
        [(".goofish.com", f"noise_{i}") for i in range(500)] + [(".goofish.com", "cookie2")],
        True,
        id="关键名排在最后",
    ),
    pytest.param([("www.goofish.com", "sgcookie"), (".example.com", "unb")], True, id="子域host-命中"),
    pytest.param([(".example.com", "lg2")], False, id="关键名但host不含goofish/taobao"),
]


@pytest.mark.parametrize("rows, expected", SHAPE_CASES)
def test_sqlite_branch_matches_legacy(rows, expected, browser_dir):
    db = browser_dir / "Cookies"
    make_cookie_db(db, rows)
    assert offline_selector()._check_user_data_cookies() is expected
    # 双写：期望值必须与原实现的判定一致，防止「用例跟着新语义一起改错」
    assert legacy_check(db) is expected


def test_query_pushes_names_and_stops_at_first_hit(browser_dir, monkeypatch):
    """SQL 侧短路的直接判据：语句带 `name IN` 与 `LIMIT 1`，且不再是原句"""
    db = browser_dir / "Cookies"
    make_cookie_db(
        db, [(".goofish.com", f"noise_{i}") for i in range(300)] + [(".goofish.com", "unb")]
    )
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    # 实现里是函数内 `import sqlite3`，取到的是同一个模块对象，属性替换在调用时生效
    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    assert offline_selector()._check_user_data_cookies() is True

    selects = [
        s for s in statements if s.strip().upper().startswith("SELECT") and "cookies" in s.lower()
    ]
    assert selects, f"没看到对 cookies 表的查询：{statements}"
    stmt = " ".join(selects[-1].upper().split())
    assert "LIMIT 1" in stmt
    assert "NAME IN" in stmt
    assert LEGACY_SQL.upper() not in stmt


def test_missing_db_is_false(browser_dir):
    assert offline_selector()._check_user_data_cookies() is False


def test_db_without_cookies_table_is_false(browser_dir):
    make_cookie_db(browser_dir / "Cookies", [(".goofish.com", "unb")])
    db = browser_dir / "Cookies"
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE cookies")
    assert offline_selector()._check_user_data_cookies() is False
    assert legacy_check(db) is False


def test_not_a_sqlite_file_is_false(browser_dir):
    browser_dir.mkdir(parents=True, exist_ok=True)
    (browser_dir / "Cookies").write_bytes(b"this is not sqlite")
    assert offline_selector()._check_user_data_cookies() is False


def test_json_branch_short_circuits_before_sqlite(browser_dir, tmp_path):
    """JSON 是主数据源：命中它就返回，不该再去开 Cookie 库（顺序也是判据之一）"""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "cookies.json").write_text(
        '{"cookies": [{"name": "unb", "value": "1"}]}', encoding="utf-8"
    )
    # browser-data 下什么都没有：True 只能来自 JSON 分支
    assert (browser_dir / "Cookies").exists() is False
    assert offline_selector()._check_user_data_cookies() is True


def test_key_cookie_names_are_the_contract(browser_dir):
    """关键名集合本身没被改动顺手换窄（少一个名 = 少一类命中）"""
    assert LoginStrategySelector.KEY_COOKIES == {
        "_m_h5_tk",
        "_m_h5_tk_enc",
        "unb",
        "sgcookie",
        "cookie2",
        "lg2",
    }
