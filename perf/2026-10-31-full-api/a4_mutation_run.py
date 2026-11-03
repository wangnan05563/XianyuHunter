"""A4 变异验证：逐腿串行，每腿必须翻红 ≥1 例；负对照腿必须全绿

每腿都在写回后立即恢复原文件，最后校验 sha256 与起始一致（不允许带着变异退出）。
负对照改的是同一集合里**另一条**排除项（"dist"），fixture 不覆盖它 → 必须全绿，
用来证明红腿针对的是 `.venv-build` 本身，而不是「文件凡被改就红」。
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

API = pathlib.Path("backend/xianyu_hunter/web/routes/api_maintenance.py")
STARTUP = pathlib.Path("backend/xianyu_hunter/web/startup.py")
PY = ".venv/Scripts/python.exe"
TESTS = ["tests/test_maintenance_status_cost.py", "tests/test_maintenance_cleanup.py"]

NEW_LOG_BLOCK = '''            total_size = 0
            oldest = None
            for f in log_files:
                try:
                    st = f.stat()
                except OSError:
                    continue
                total_size += st.st_size
                oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            log_stats["total_size_mb"] = round(total_size / 1024 / 1024, 2)
            if oldest is not None:
                log_stats["oldest"] = datetime.fromtimestamp(oldest).strftime("%Y-%m-%d")
'''
LEGACY_LOG_BLOCK = '''            total_size = sum(f.stat().st_size for f in log_files if f.is_file())
            log_stats["total_size_mb"] = round(total_size / 1024 / 1024, 2)
            if log_files:
                oldest = min(f.stat().st_mtime for f in log_files if f.is_file())
                log_stats["oldest"] = datetime.fromtimestamp(oldest).strftime("%Y-%m-%d")
'''

WARM_LINE = '    await _warm_one("maintenance_status", asyncio.to_thread, maintenance_status, container=container)\n'

# (标签, 文件, 原锚点, 替换文本, 期望)
LEGS = [
    ("L1 去掉 .venv-build 排除", API, '    ".venv-build",\n', "", "红"),
    ("L2 日志统计退回每文件 3 次 stat", API, NEW_LOG_BLOCK, LEGACY_LOG_BLOCK, "红"),
    ("L3 TTL 写回 60", API, "@cached_ttl(300, key_fn=lambda container: \"maintenance_status\"",
     "@cached_ttl(60, key_fn=lambda container: \"maintenance_status\"", "红"),
    ("L4 去掉 maintenance_status 预热", STARTUP, WARM_LINE, "", "红"),
    ("L5 预热不走 to_thread（同步跑在事件循环上）", STARTUP,
     '"maintenance_status", asyncio.to_thread, maintenance_status',
     '"maintenance_status", maintenance_status', "红"),
    ("负对照：改同集合另一条排除项 dist", API, '    "dist", "build",', '    "distx", "build",', "绿"),
]


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


def main() -> int:
    originals = {
        p: p.read_text(encoding="utf-8") for p in (API, STARTUP)
    }
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    print("baseline sha256:", {p.name: h for p, h in hashes.items()})

    code, out = run_tests()
    print(f"\n=== 基线 → exit={code}（期望 0）")
    print("   ", [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln])
    if code != 0:
        print("基线不绿，变异无效；先修基线。")
        return 1

    failures = 0
    for label, path, old, new, expect in LEGS:
        src = originals[path]
        count = src.count(old)
        if count != 1:
            print(f"\n=== {label}: 锚点出现 {count} 次（不是唯一），本腿无效")
            failures += 1
            continue
        path.write_text(src.replace(old, new), encoding="utf-8")
        try:
            code, out = run_tests()
        finally:
            path.write_text(src, encoding="utf-8")
        headline = [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln]
        failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
        ok = (code != 0) if expect == "红" else (code == 0)
        failures += 0 if ok else 1
        print(f"\n=== {label} → exit={code}，期望{expect}，判定={'OK' if ok else '不成立'}")
        for ln in (headline + failed)[-10:]:
            print("   ", ln)

    for p, src in originals.items():
        now = hashlib.sha256(p.read_bytes()).hexdigest()
        same = p.read_text(encoding="utf-8") == src
        print(f"restore {p.name}: same={same} sha256_match={now == hashes[p]}")
        if not same:
            failures += 1
    print("\nMUT markers:", subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
