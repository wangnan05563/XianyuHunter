"""C2 取证：`GET /api/auth/me` 逐阶段成本与每请求文件/SQL 次数（只读，绝不触碰真实登录态）

两个场景都要跑，因为报告里的 152.6 ms 基线来自压测沙箱（`setup_sandbox.py` 只复制
DB 副本、**不复制任何 cookie 文件**），而真实控制台用户是有 cookie 文件的：
- A 沙箱态：`data/` 内只有 DB，`has_cookie=False`
- B 登录态：合成 `cookies_probe_user.json`（关键名齐全）+ `auth_cache/userinfo.json`

计数口径：`Path.read_text` / `Path.exists` / `Path.stat` / `builtins.open` / SQL 语句。
隔离：`config/` 复制 + DB 用 sqlite backup API 取一致快照 → chdir 到 scratch；
CookieStore 的 `_COOKIE_JSON_DIR` 在 **import 时**求值，所以 chdir 必须早于 import。
真实 `data/cookies_*.json`、`browser-data/` 全程不读不写。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(r"D:/code/otherProjects/17_xianyu")
SCRATCH = Path(r"D:/tmp/xh-c2-probe")

FILE_OPS: dict[str, int] = {}
SQL_STATEMENTS: list[str] = []


def _bump(key: str) -> None:
    FILE_OPS[key] = FILE_OPS.get(key, 0) + 1


def install_counters() -> None:
    """在 import 业务模块之前挂好计数器，保证统计覆盖全链路"""
    orig_read_text = Path.read_text
    orig_exists = Path.exists
    orig_stat = Path.stat

    Path.read_text = lambda self, *a, **k: (_bump(f"read:{self.name}") or orig_read_text(self, *a, **k))
    Path.exists = lambda self, *a, **k: (_bump(f"exists:{self.name}") or orig_exists(self, *a, **k))
    Path.stat = lambda self, *a, **k: (_bump(f"stat:{self.name}") or orig_stat(self, *a, **k))

    conn_cls = sqlite3.Connection
    orig_cursor = conn_cls.cursor  # noqa: F841  sqlite3.Connection 属性只读，SQL 计数走 SQLAlchemy

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _count_sql(conn, cursor, statement, parameters, context, executemany):
        SQL_STATEMENTS.append(" ".join(str(statement).split())[:90])


def build_scaffold() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    (SCRATCH / "config").mkdir(parents=True)
    for f in ("config.yaml", "menu_registry.yaml", "eval.yaml", "param_rules.yaml"):
        src = REPO / "config" / f
        if src.exists():
            shutil.copy2(src, SCRATCH / "config" / f)
    (SCRATCH / "data").mkdir()
    # DB：一致性快照（orders 表当前 0 行，与压测沙箱同形）
    src_db = REPO / "data" / "xianyu.db"
    dst_db = SCRATCH / "data" / "xianyu.db"
    with sqlite3.connect(str(src_db)) as s, sqlite3.connect(str(dst_db)) as d:
        s.backup(d)


def make_synthetic_cookies(uid: str) -> None:
    """写一份合成 Cookie 文件（关键名齐全，值全为假数据）"""
    import json as _json

    future = int(time.time()) + 30 * 86400
    cookies = []
    for name in (
        "unb", "cookie2", "_m_h5_tk", "_m_h5_tk_enc", "sgck", "cna",
        "tracknick", "xlly", "t", "_tb_token_", "cookie17", "lgc",
    ):
        cookies.append({
            "name": name,
            # _m_h5_tk 需带内嵌时间戳才不被判过期
            "value": (f"{int(time.time()) + 3600 * 4}_abcdef0123456789"
                      if name == "_m_h5_tk" else f"probe-{name}"),
            "domain": ".goofish.com",
            "path": "/",
            "expires": future,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        })
    for i in range(30):
        cookies.append({
            "name": f"filler_{i}", "value": "x" * 64, "domain": ".goofish.com",
            "path": "/", "expires": future, "httpOnly": False, "secure": True,
        })
    payload = {
        "exported_at": time.time(), "method": "probe", "cookie_count": len(cookies),
        "cookies": cookies,
    }
    path = SCRATCH / "data" / f"cookies_{uid}.json"
    path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_userinfo(uid: str) -> None:
    import json as _json

    d = SCRATCH / "data" / "auth_cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / "userinfo.json").write_text(_json.dumps({
        "logged_in": True, "user_id": uid, "nick": "探针昵称",
        "avatar_url": "", "fetched_at": time.time(),
    }, ensure_ascii=False), encoding="utf-8")


class FakeRequest:
    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = cookies or {}
        self.headers: dict[str, str] = {}
        self.state = type("S", (), {"user_id": None})()
        self.query_params: dict = {}


def stage(name: str, fn, n: int = 5) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(ts)
    print(f"    {name:<38} {med:7.1f} ms  (min {min(ts):6.1f} / max {max(ts):7.1f})")
    return med


def main() -> None:
    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    build_scaffold()
    os.chdir(SCRATCH)
    install_counters()

    sys.path.insert(0, str(REPO / "backend"))
    from xianyu_hunter.infra.repository import Repository
    from xianyu_hunter.web.routes import auth_query
    from xianyu_hunter.web.services.cookie_store import get_cookie_store

    repo = Repository(db_path=str(SCRATCH / "data" / "xianyu.db"))
    container = type("C", (), {"repo": repo, "collector": None, "browser": None})()

    probe_uid = "probe_user"

    def one_me(req: FakeRequest):
        FILE_OPS.clear()
        SQL_STATEMENTS.clear()
        t0 = time.perf_counter()
        out = auth_query.auth_me(req, container=container)
        dt = (time.perf_counter() - t0) * 1000
        return dt, out, dict(FILE_OPS), list(SQL_STATEMENTS)

    for label, req in (
        ("A 沙箱态（无任何 cookie 文件，与压测沙箱同形）", FakeRequest()),
        ("B 登录态（合成 cookies_probe_user.json + userinfo.json）", FakeRequest()),
    ):
        if label.startswith("B"):
            make_synthetic_cookies(probe_uid)
            make_userinfo(probe_uid)
            get_cookie_store().invalidate_cache("default")
        print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
        runs = [one_me(req) for _ in range(6)]
        for i, (dt, out, ops, sqls) in enumerate(runs):
            reads = {k: v for k, v in ops.items() if k.startswith("read:")}
            exists = sum(v for k, v in ops.items() if k.startswith("exists:"))
            stats_ = sum(v for k, v in ops.items() if k.startswith("stat:"))
            print(f"  第{i + 1}次 {dt:7.1f} ms | read_text={sum(reads.values())} "
                  f"exists={exists} stat={stats_} SQL={len(sqls)} | "
                  f"logged_in={out.get('logged_in')} uid={out.get('user_id')!r}")
            if i in (0, len(runs) - 1):
                print(f"     读文件明细: {reads}")
                print(f"     SQL: {sqls}")

    print(f"\n{'=' * 78}\n阶段拆分（B 登录态）\n{'=' * 78}")
    req = FakeRequest()
    stage("_resolve_current_user_id(无 token)", lambda: auth_query._resolve_current_user_id(req))
    stage("_check_cookies(default)", lambda: auth_query._check_cookies("default"))
    stage("get_auth_manager().get_userinfo()", lambda: auth_query.get_auth_manager().get_userinfo())
    store = get_cookie_store()
    stage("_uid_from_cookies(store, default)", lambda: auth_query._uid_from_cookies(store, "default"))
    stage("_account_name_from_cookies", lambda: auth_query._account_name_from_cookies(store, "default"))
    stage("_load_cookie_data_for_health", lambda: auth_query._load_cookie_data_for_health(store, "default"))
    stage("_sync_nick_to_users_table(default, nick)",
          lambda: auth_query._sync_nick_to_users_table("default", "探针昵称"))
    stage("_sync_nick_to_users_table(probe_user, nick)",
          lambda: auth_query._sync_nick_to_users_table(probe_uid, "探针昵称"))
    stage("scan_and_notify(auth_state=logged_in)",
          lambda: auth_query.scan_and_notify(container, auth_state={"logged_in": True}, user_id=None))
    stage("store.invalidate_cache + has_valid_cookies",
          lambda: (store.invalidate_cache("default"), store.has_valid_cookies(user_id="default")))

    print(f"\n{'=' * 78}\n对照：cookie 文件大小与单次解析成本\n{'=' * 78}")
    p = SCRATCH / f"data/cookies_{probe_uid}.json".replace("data/", "data/")
    for f in sorted((SCRATCH / "data").glob("cookies_*.json")):
        raw = f.read_bytes()
        import json as _json
        t0 = time.perf_counter()
        for _ in range(5):
            _json.loads(raw)
        print(f"  {f.name}: {len(raw)} B  json.loads median "
              f"{((time.perf_counter() - t0) * 1000 / 5):.3f} ms  (内存态)")
        del p
    t0 = time.perf_counter()
    for _ in range(5):
        store.invalidate_cache(probe_uid)
        store._read_json(user_id=probe_uid)
    print(f"  invalidate_cache + _read_json(读盘+解析) median "
          f"{((time.perf_counter() - t0) * 1000 / 5):.1f} ms")

    os.chdir(REPO)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n[清理] scratch 目录已删除（合成假数据，无真实凭据）")


if __name__ == "__main__":
    main()
