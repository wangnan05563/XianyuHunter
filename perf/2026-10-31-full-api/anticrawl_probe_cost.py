"""C4 判据探针：`/api/anticrawl/strategy` 那 1 094.7 ms 到底花在哪

跑法（在项目根）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe perf/2026-10-31-full-api/anticrawl_probe_cost.py

先测再改。OPTIMIZATIONS C4 原文把 ~1 s 归给「CDP 端口未开时 socket.create_connection
吃满 timeout=1.0」，但 Windows 回环上连一个**没监听**的端口是立刻 RST（毫秒级），
真正能吃满超时的是「有东西在监听但不完成握手」或被安全软件静默丢包。这一段必须实测。

安全边界：
- 不启动浏览器、不启动隧道、不读写真实 Chromium Cookie 库（全部指向临时目录）
- 真实 LOCALAPPDATA 用 monkeypatch 环境变量的方式绕开，只读自己造的假库
- socket 探测只 connect，不发任何数据；「有监听」这一路用自建的本地 socket
"""
from __future__ import annotations

import sqlite3
import statistics
import socket
import sys
import tempfile
import time
from pathlib import Path

REPEATS = 7
KEY_COOKIES = {"_m_h5_tk", "_m_h5_tk_enc", "unb", "sgcookie", "cookie2", "lg2"}


def median_ms(fn, repeats: int = REPEATS) -> float:
    runs = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(runs)


# ==================== 1. socket 探测的真实成本 ====================
def probe_connect(port: int, timeout: float) -> tuple[str, float]:
    """返回 (结果标签, 耗时 ms)：refused / open / timeout 三种形态分开测"""
    t0 = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            outcome = "open"
    except socket.timeout:
        outcome = "timeout"
    except ConnectionRefusedError:
        outcome = "refused"
    except OSError:
        outcome = "other-oserr"
    return outcome, (time.perf_counter() - t0) * 1000


def section_socket() -> None:
    print("[1] socket 探测（timeout=1.0 是现状；0.1 是 C4 建议值）")
    for label, port in (("CDP 默认端口 9222", 9222), ("空闲端口 19222", 19222)):
        for timeout in (1.0, 0.1):
            outcome, ms = probe_connect(port, timeout)
            print(f"    {label} timeout={timeout:<4} → {outcome:<12} {ms:7.2f} ms")

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    open_port = srv.getsockname()[1]
    outcome, ms = median_and(probe_connect, open_port, 1.0)
    print(f"    自建监听端口（模拟 CDP 已开）→ {outcome} {ms:7.2f} ms")
    srv.close()

    # 占住 backlog 但不 accept：这是「连得上但握手不完成」的极端形态吗？
    slow = socket.socket()
    slow.bind(("127.0.0.1", 0))
    slow.listen(1)
    slow_port = slow.getsockname()[1]
    filler = []
    try:
        for _ in range(4):
            s = socket.create_connection(("127.0.0.1", slow_port), timeout=0.2)
            filler.append(s)
    except OSError:
        pass
    outcome, ms = probe_connect(slow_port, 1.0)
    print(f"    监听但从不 accept（backlog 已满）→ {outcome} {ms:7.2f} ms")
    for s in filler:
        s.close()
    slow.close()
    print("    → 判据：若回环上 refused 是毫秒级，则「端口未开吃满 1 s」不成立，"
          "1 s 常数项另有出处")


def median_and(fn, *a) -> tuple[str, float]:
    """fn 返回 (label, ms)；标签取出现最多的一个，耗时取全部样本的中位"""
    samples = [fn(*a) for _ in range(3)]
    labels = [s[0] for s in samples]
    top = max(set(labels), key=lambda item: labels.count(item))
    return top, statistics.median([s[1] for s in samples])


# ==================== 2. Chromium Cookie 库扫描的成本 ====================
def make_cookie_db(path: Path, rows: int) -> None:
    """按 Chromium 的形状造假库：host_key 前有点号，name 分布稀疏

    rows 里只有极少数命中 KEY_COOKIES，其余是同 host 的无关 cookie
    （真实浏览器 profile 往往有上千行）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE cookies ("
            "creation_time INTEGER, host_key TEXT, name TEXT, value TEXT,"
            "path TEXT, expires_utc INTEGER, is_secure INTEGER,"
            "is_httponly INTEGER, last_access_utc INTEGER,"
            "has_expires INTEGER, is_persistent INTEGER, priority INTEGER,"
            "same_site_policy INTEGER, source_scheme INTEGER, source_port INTEGER,"
            "is_same_site INTEGER, has_known_stored_value INTEGER)"
        )
        conn.execute("CREATE INDEX on_cookies ON cookies (host_key, name)")
        hosts = [".goofish.com", ".taobao.com", "www.goofish.com", ".alicdn.com",
                 ".alipay.com", "s.taobao.com", ".example.com"]
        batch = []
        for i in range(rows):
            host = hosts[i % len(hosts)]
            if i % 500 == 0:
                name = "_m_h5_tk"
            else:
                name = f"noise_{i}"
            batch.append((i, host, name, "v" * 32, "/", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)


LIKE_SQL = "SELECT name FROM cookies WHERE host_key LIKE ? OR host_key LIKE ?"
NAMES_SQL = (
    "SELECT 1 FROM cookies WHERE (host_key LIKE ? OR host_key LIKE ?) "
    f"AND name IN ({','.join('?' for _ in KEY_COOKIES)}) LIMIT 1"
)


def scan_cost(db: Path, rows: int) -> None:
    print(f"\n[2] Chromium Cookie 库扫描（{rows} 行，只读打开自己造的假库）")

    def open_and_like():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            rs = conn.execute(LIKE_SQL, ("%goofish%", "%taobao%")).fetchall()
            return bool(KEY_COOKIES & {n for (n,) in rs})

    def open_and_names():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            return conn.execute(
                NAMES_SQL, ("%goofish%", "%taobao%", *sorted(KEY_COOKIES))
            ).fetchone() is not None

    like_ms = median_ms(open_and_like)
    names_ms = median_ms(open_and_names)
    same = open_and_like() == open_and_names()
    print(f"    现状 SELECT name + LIKE 全表扫 + Python 侧求交集：中位 {like_ms:.2f} ms")
    print(f"    改为 LIKE + name IN(...) + LIMIT 1（DB 侧短路）：中位 {names_ms:.2f} ms")
    print(f"    布尔结果一致：{same}；省 {like_ms - names_ms:.2f} ms")
    print("    → 若这一项本身就远小于 1 s，C4 的 ③（精确匹配）不是主要矛盾，"
          "别为了它改动检测语义")


def section_json_read() -> None:
    print("\n[3] 策略 B 的 JSON 分支（`Path('data')/'cookies.json'`，随进程 cwd 而变）")
    work = Path(tempfile.mkdtemp(prefix="ac_probe_"))
    big = work / "cookies.json"
    import json
    big.write_text(json.dumps({"cookies": [{"name": f"n{i}", "value": "x" * 8}
                                           for i in range(3000)]}), encoding="utf-8")
    print(f"    3000 条 cookie 的 JSON（{big.stat().st_size / 1024:.0f} KB）"
          f"读+解析：中位 {median_ms(lambda: json.loads(big.read_text(encoding='utf-8'))):.2f} ms")
    print("    → 真实 data/cookies.json 只有几十条，这一项可以忽略")
    import shutil
    shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ac_probe_db_"))
    try:
        section_socket()
        for rows in (200, 5000):
            db = work / f"cookies_{rows}.sqlite"
            make_cookie_db(db, rows)
            scan_cost(db, rows)
        section_json_read()
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    print("\n结论口径：把上面三段的中位数相加，与压测 avg 1 094.7 ms 对比，"
          "差值就是「socket 之外还没被解释的常数项」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
