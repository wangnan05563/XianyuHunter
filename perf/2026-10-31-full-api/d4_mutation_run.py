"""D4 变异验证：逐腿串行，红腿必须翻红 ≥1 例，绿腿必须全绿

判据集 = tests/test_export.py（D4 新增的 7 例 + 原有导出契约）。
纪律同其它 *_mutation_run.py：只认 pytest exit==1 为翻红；基线必须全绿；
锚点唯一 + ast.parse；每条腿无条件按原始字节恢复并 sha256 核对。
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EX = pathlib.Path("backend/xianyu_hunter/web/routes/api_export.py")
LG = pathlib.Path("backend/xianyu_hunter/web/routes/api_logs.py")
DB = pathlib.Path("backend/xianyu_hunter/web/routes/api_db_admin.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_export.py"]
BASETEMP = "D:/tmp/d4-mutation-basetemp"

LEGS = [
    ("L1 CSV 只在末尾发一次（先拼满）", EX,
     "        if buf.tell() >= _CSV_CHUNK_CHARS:",
     "        if False:"),
    ("L2 CSV 丢掉 BOM", EX,
     '    buf.write("\\ufeff")',
     '    buf.write("")'),
    ("L3 缺失列写成 None 字面量", EX,
     '    return "" if v is None else v',
     '    return row.get(col, "None")'),
    ("L4 日志 CSV 不再截断 message", LG,
     '            (e.get("message") or "")[:500],',
     '            (e.get("message") or ""),'),
    ("L5 日志文件不做通用换行转换（\\r\\n 原样留下）", LG,
     "            raw, encoding=\"utf-8\", errors=\"replace\", newline=None",
     "            raw, encoding=\"utf-8\", errors=\"replace\", newline=\"\""),
    ("L6 日志文件一次读完再发", LG,
     "            while True:\n"
     "                text = f.read(_LOG_FILE_BLOCK_CHARS)\n"
     "                if not text:\n"
     "                    break\n"
     "                yield text",
     "            text = f.read()\n"
     "            if text:\n"
     "                yield text"),
    ("L7 日志 CSV 只在末尾发一次（先拼满）", LG,
     "        if buf.tell() >= _LOG_CSV_CHUNK_CHARS:",
     "        if False:"),
    ("L8 db-admin 表导出退回逐行 yield", DB,
     "    buf: list[str] = []\n"
     "    size = 0\n"
     "    for piece in pieces:\n"
     "        buf.append(piece)\n"
     "        size += len(piece)\n"
     "        if size >= _EXPORT_CHUNK_CHARS:\n"
     "            yield \"\".join(buf)\n"
     "            buf.clear()\n"
     "            size = 0\n"
     "    if buf:\n"
     "        yield \"\".join(buf)",
     "    yield from pieces"),
]

# 应绿腿：改了个看似相关其实不影响任何判据的地方
GREEN_WRONG_TARGET = (
    EX,
    "_CSV_CHUNK_CHARS = 256 * 1024",
    "_CSV_CHUNK_CHARS = 128 * 1024",
)
# 应绿腿：只改 docstring 措辞
GREEN_DOCSTRING = (
    EX,
    "    \"\"\"逐块生成 CSV 文本，避免把整份导出内容先拼进内存",
    "    \"\"\"逐块生成 CSV 文本（导出时不再整份驻留）",
)

FILES = [EX, LG, DB]


def read_raw(p: pathlib.Path) -> str:
    with open(REPO_ROOT / p, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_raw(p: pathlib.Path, text: str) -> None:
    with open(REPO_ROOT / p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def to_eol(text: str, src: str) -> str:
    if "\r\n" in src:
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def run_tests() -> tuple[int, str]:
    # C 盘无可用空间，pytest 的临时目录必须落在 D 盘且预先存在：
    # 缺目录时 fixture 的 TemporaryDirectory 会直接 setup 失败（表现为一堆 error）
    pathlib.Path("D:/tmp/d4-mutation-temp").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TEMP": "D:/tmp/d4-mutation-temp", "TMP": "D:/tmp/d4-mutation-temp"}
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf", f"--basetemp={BASETEMP}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env,
    )
    return proc.returncode, proc.stdout


def _summary(out: str) -> list[str]:
    return [ln for ln in out.splitlines()
            if ln.startswith(("FAILED", "ERROR")) or " passed" in ln or " failed" in ln][-12:]


def _leg(target: pathlib.Path, originals: dict, old: str, new: str) -> tuple[int, str]:
    src = originals[target]
    old_r, new_r = to_eol(old, src), to_eol(new, src)
    n = src.count(old_r)
    assert n == 1, f"锚点出现 {n} 次，不是唯一：{target.name} :: {old[:70]!r}"
    mutated = src.replace(old_r, new_r)
    ast.parse(mutated)
    write_raw(target, mutated)
    assert read_raw(target) != src, f"{target.name} 写入后回读仍与原文相同，变异未生效"
    try:
        return run_tests()
    finally:
        write_raw(target, src)


def _run() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    originals = {p: read_raw(p) for p in FILES}
    baseline_sha = {p: hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in FILES}
    for p in FILES:
        eol = "CRLF" if "\r\n" in originals[p] else "LF"
        print(f"基线 {p.name:<16} {eol} sha256={baseline_sha[p][:12]}…")

    code, out = run_tests()
    print(f"\n=== 基线（未改动）→ exit={code}（期望 0=全绿）")
    for ln in _summary(out):
        print("   ", ln)
    if code != 0:
        print("!!! 基线不绿，本轮变异无效，中止")
        return 3

    results = []
    for label, target, old, new in LEGS:
        code, out = _leg(target, originals, old, new)
        if code == 1:
            verdict = "OK（翻红）"
        elif code == 0:
            verdict = "!!! 全绿：这条腿没有判据"
        else:
            verdict = f"!!! 无效：pytest exit={code}（非用例失败）"
        results.append((label, code, verdict))
        print(f"\n=== {label} → exit={code} {verdict}")
        for ln in _summary(out):
            print("   ", ln)
        if code not in (0, 1):
            for ln in out.splitlines()[-30:]:
                print("    |", ln)

    green_ok = True
    for label, leg in (("应绿-接错对象", GREEN_WRONG_TARGET), ("应绿-只改 docstring", GREEN_DOCSTRING)):
        code, out = _leg(leg[0], originals, leg[1], leg[2])
        print(f"\n=== {label} → exit={code}（期望 0=全绿）")
        for ln in _summary(out):
            print("   ", ln)
        green_ok = green_ok and code == 0

    print("\n=== 还原校验")
    restored_ok = True
    for p in FILES:
        now = hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest()
        same = now == baseline_sha[p]
        restored_ok = restored_ok and same
        print(f"  {p.name:<16} sha256={now[:12]}… 逐字节相同={same}")

    bad = [r for r in results if r[1] != 1]
    print("\n=== 判定")
    print(f"  有效翻红腿数 = {len(results) - len(bad)}/{len(results)}")
    for label, code, verdict in bad:
        print(f"    不合格（exit={code}）：{label}")
    print(f"  应绿腿全绿 = {green_ok}")
    print(f"  逐字节还原 = {restored_ok}")
    return 0 if (not bad and green_ok and restored_ok) else 1


LOG_PATH = REPO_ROOT / "perf/2026-10-31-full-api/results/d4_mutation_run.log"


def main() -> int:
    """日志直接写文件：经 PowerShell 管道（Tee/Out-File）会缓冲到进程退出，
    中途被杀就只剩半份进度，排查时看不出卡在哪条腿。"""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
    saved = sys.stdout
    sys.stdout = log
    try:
        return _run()
    finally:
        sys.stdout = saved
        log.close()


if __name__ == "__main__":
    sys.exit(main())