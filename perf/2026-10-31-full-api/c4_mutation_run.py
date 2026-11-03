"""C4 变异验证：逐腿串行，每腿必须翻红 ≥1 例；负对照腿必须全绿"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

TARGET = pathlib.Path("backend/xianyu_hunter/modules/login_strategy.py")
BASELINE = pathlib.Path("D:/tmp/login_strategy.baseline.py")
PY = ".venv/Scripts/python.exe"

NEW_SQL_BODY = '''            key_names = sorted(self.KEY_COOKIES)
            placeholders = ",".join(["?"] * len(key_names))
            with sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True) as conn:
                hit = conn.execute(
                    "SELECT 1 FROM cookies "
                    f"WHERE (host_key LIKE ? OR host_key LIKE ?) AND name IN ({placeholders}) "
                    "LIMIT 1",
                    ("%goofish%", "%taobao%", *key_names),
                ).fetchone()
                return hit is not None
'''
LEGACY_SQL_BODY = '''            with sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True) as conn:
                rows = conn.execute(
                    "SELECT name FROM cookies WHERE host_key LIKE ? OR host_key LIKE ?",
                    ("%goofish%", "%taobao%"),
                ).fetchall()
                names = {n for (n,) in rows}
                return bool(self.KEY_COOKIES & names)
'''

LEGS = [
    ("L1 探测超时写回 1.0", "timeout=self.CDP_PROBE_TIMEOUT", "timeout=1.0"),
    ("L2 SQL 分支整体退回原句", NEW_SQL_BODY, LEGACY_SQL_BODY),
    ("L3 去掉 name 下推",
     'f"WHERE (host_key LIKE ? OR host_key LIKE ?) AND name IN ({placeholders}) "',
     'f"WHERE (host_key LIKE ? OR host_key LIKE ?) "'),
    ("L4 host 图案接错对象（taobao 写成第二个 goofish）",
     '("%goofish%", "%taobao%", *key_names),',
     '("%goofish%", "%goofish%", *key_names),'),
]


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "tests/test_anticrawl_strategy_cost.py",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


def main() -> int:
    original = BASELINE.read_text(encoding="utf-8")
    assert TARGET.read_text(encoding="utf-8") == original, "基线文件与备份不一致，先修复再跑"
    print(f"baseline sha256={hashlib.sha256(TARGET.read_bytes()).hexdigest()}")
    for label, old, new in LEGS:
        assert original.count(old) == 1, f"{label}: 锚点出现 {original.count(old)} 次，不是唯一"
        TARGET.write_text(original.replace(old, new), encoding="utf-8")
        try:
            code, out = run_tests()
        finally:
            TARGET.write_text(original, encoding="utf-8")
        lines = [ln for ln in out.splitlines() if ln.startswith("FAILED") or " passed" in ln or " failed" in ln]
        print(f"\n=== {label} → exit={code}")
        for ln in lines[-12:]:
            print("   ", ln)
    # 负对照：改另一处 socket 超时（infra/browser.py），本文件用例应全绿
    other = pathlib.Path("backend/xianyu_hunter/infra/browser.py")
    other_src = other.read_text(encoding="utf-8")
    anchor = '("127.0.0.1", _CDP_PORT), timeout=0.5'
    assert other_src.count(anchor) == 1
    other.write_text(other_src.replace(anchor, '("127.0.0.1", _CDP_PORT), timeout=1.0'), encoding="utf-8")
    try:
        code, out = run_tests()
    finally:
        other.write_text(other_src, encoding="utf-8")
    print(f"\n=== 负对照（改 browser.py 的超时）→ exit={code}（期望 0=全绿）")
    print("   ", [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln])
    after = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"\nrestore check: sha256={after} match={after == hashlib.sha256(TARGET.read_bytes()).hexdigest()}")
    print(f"browser.py restored={other.read_text(encoding='utf-8') == other_src}")
    print("MUT markers:", subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
