"""C4 落地后的判据探针：CDP 关闭态下，那一秒的常数项是否真的没了

跑法（在项目根）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe perf/2026-10-31-full-api/c4_after_probe.py

OPTIMIZATIONS §C4 的「验证方式」写的是「CDP 关闭态下压该接口，确认不再出现 ~1 s 常数项」。
整接口级的数字要等同一 JMX 复跑，这里先把**常数项本身**钉住：同一个 `_check_cdp_available`
分别用落地后的预算与改动前的 1.0 s 各测若干次，再测完整 `evaluate()` 与 Cookie 库分支。

安全边界：不启动浏览器/隧道；不读真实 `data/browser-data`、真实 Chromium Cookie 库或
`data/cookies.json`（全部换成临时目录 + 自造假库）；socket 只连自建的本地端口。
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from xianyu_hunter.modules import login_strategy as ls  # noqa: E402
from xianyu_hunter.modules.login_strategy import LoginStrategySelector  # noqa: E402

REPEATS = 9
KEY = LoginStrategySelector.KEY_COOKIES


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def median_ms(fn) -> float:
    runs = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(runs)


def section_cdp() -> None:
    port = free_port()
    print(f"[1] CDP 关闭态探测（端口 {port} 确定无监听）")
    shipped = median_ms(lambda: LoginStrategySelector(cdp_target_port=port)._check_cdp_available())
    print(f"    落地后（CDP_PROBE_TIMEOUT={LoginStrategySelector.CDP_PROBE_TIMEOUT}）：中位 {shipped:7.2f} ms")
    original = LoginStrategySelector.CDP_PROBE_TIMEOUT
    LoginStrategySelector.CDP_PROBE_TIMEOUT = 1.0
    try:
        before = median_ms(lambda: LoginStrategySelector(cdp_target_port=port)._check_cdp_available())
    finally:
        LoginStrategySelector.CDP_PROBE_TIMEOUT = original
    print(f"    改动前（timeout=1.0）：中位 {before:7.2f} ms")
    print(f"    → 省 {before - shipped:.2f} ms；判定都是「不可用」：{before / shipped:.1f}×")

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    accepting_port = srv.getsockname()[1]

    def _accept_once() -> None:
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass

    threading.Thread(target=_accept_once, daemon=True).start()
    ok_ms = median_ms(lambda: LoginStrategySelector(cdp_target_port=accepting_port)._check_cdp_available())
    print(f"    真在 accept 的端口（模拟 CDP 已开）：中位 {ok_ms:7.2f} ms → 仍判可用 {ok_ms < LoginStrategySelector.CDP_PROBE_TIMEOUT * 1000}")
    srv.close()


def make_cookie_db(path: Path, rows: int, with_key: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
        batch = [
            ((".goofish.com" if i % 2 else ".taobao.com"),
             f"noise_{i}" if i % 500 else ("_m_h5_tk" if with_key else f"tk_{i}"), "v" * 32)
            for i in range(rows)
        ]
        conn.executemany("INSERT INTO cookies VALUES (?,?,?)", batch)


def section_cookies(work: Path) -> None:
    print("\n[2] Cookie 库分支（走真实代码路径，指向自造假库）")
    original_get_config = ls.get_config
    original_get_bd = ls.get_browser_data_dir
    ls.get_config = lambda: types.SimpleNamespace(browser=types.SimpleNamespace(user_data_dir="p"))
    cwd0 = Path.cwd()
    try:
        for rows, with_key in ((200, True), (5000, True), (5000, False)):
            root = work / f"rows{rows}_{int(with_key)}"
            db_dir = root / "Default" / "Network"
            db_dir.mkdir(parents=True, exist_ok=True)
            make_cookie_db(db_dir / "Cookies", rows, with_key)
            ls.get_browser_data_dir = lambda configured=None, r=root: r
            (work / "cwd").mkdir(exist_ok=True)
            os.chdir(work / "cwd")
            sel = LoginStrategySelector(cdp_target_port=free_port())
            ms = median_ms(lambda: sel._check_user_data_cookies())
            verdict = sel._check_user_data_cookies()
            print(f"    {rows} 行 关键名={'有' if with_key else '无'}：中位 {ms:7.2f} ms，判定 {verdict}")
    finally:
        os.chdir(cwd0)
        ls.get_config = original_get_config
        ls.get_browser_data_dir = original_get_bd


def section_evaluate(work: Path) -> None:
    print("\n[3] 完整 `evaluate()`（CDP 关闭 + 无 Cookie 库 + 策略 C 打桩）")
    sel = LoginStrategySelector(cdp_target_port=free_port())
    sel._check_browser_importable = lambda: False
    ls.get_config = lambda: types.SimpleNamespace(browser=types.SimpleNamespace(user_data_dir="none"))
    ls.get_browser_data_dir = lambda configured=None: work / "empty"
    cwd0 = Path.cwd()
    try:
        (work / "cwd2").mkdir(exist_ok=True)
        os.chdir(work / "cwd2")
        ms = median_ms(lambda: sel.evaluate())
        print(f"    中位 {ms:7.2f} ms（压测基线 avg 1 094.7 ms；接口级数字仍待同一 JMX 复跑）")
    finally:
        os.chdir(cwd0)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="c4_after_"))
    try:
        section_cdp()
        section_cookies(work)
        section_evaluate(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\n判定口径：[1] 的两行之差就是接口里那一秒常数项的下限；[2] 是 SQL 侧短路的量级；"
          "[3] 是「不算框架与鉴权」的 evaluate 地板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
