"""价格行情缓存（category-comparison）的变异验证

判据集 = tests/test_price_category_comparison_cache.py（5 例）。
纪律同其它 *_mutation_run.py：只认 pytest exit==1 为翻红、基线必须全绿、
锚点唯一 + ast.parse、逐字节恢复并 sha256 核对。
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PD = pathlib.Path("backend/xianyu_hunter/web/routes/price_dashboard.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_price_category_comparison_cache.py"]
BASETEMP = "D:/tmp/cmp-mutation-basetemp"
LOG_PATH = REPO_ROOT / "perf/2026-10-31-full-api/results/price_cmp_mutation_run.log"

LEGS = [
    ("L1 去掉 @cached_ttl（退回每次全表扫）", PD,
     "@cached_ttl(\n"
     "    60,\n"
     "    key_fn=lambda sort_by, order, limit, range_days, container:\n"
     "        f\"cmp:{sort_by}:{order}:{limit}:{range_days}\",\n"
     ")\n"
     "def _compute_category_comparison(",
     "def _compute_category_comparison("),
    ("L2 缓存 key 漏掉 range_days", PD,
     '        f"cmp:{sort_by}:{order}:{limit}:{range_days}",',
     '        f"cmp:{sort_by}:{order}:{limit}",'),
    ("L3 缓存 key 漏掉 limit", PD,
     '        f"cmp:{sort_by}:{order}:{limit}:{range_days}",',
     '        f"cmp:{sort_by}:{order}:{range_days}",'),
    ("L4 归一化挪进计算函数（非法 sort_by 各占一个 key）", PD,
     "    # 归一化放在路由层而不是计算函数里：非法 sort_by/order 先折成合法值，\n"
     "    # 否则 `sort_by=bogus` 会各自占一个缓存 key，把命中率稀释掉\n"
     "    sort_by, order = _normalize_comparison_params(sort_by, order)\n"
     "    return _compute_category_comparison(sort_by, order, limit, range_days, container)",
     "    return _compute_category_comparison(sort_by, order, limit, range_days, container)"),
]

# 应绿腿：改 TTL 数值（判据只认「有没有缓存 / key 对不对」，与时长无关）
GREEN_WRONG_TARGET = (PD, "@cached_ttl(\n    60,", "@cached_ttl(\n    120,")
# 应绿腿：只改 docstring 措辞
GREEN_DOCSTRING = (
    PD,
    '    """多品类横向对比的核心计算（被 60 s 缓存包裹）',
    '    """多品类横向对比的核心计算（60 s 内复用）',
)

FILES = [PD]


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
    pathlib.Path("D:/tmp/cmp-mutation-temp").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TEMP": "D:/tmp/cmp-mutation-temp", "TMP": "D:/tmp/cmp-mutation-temp"}
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
        print(f"基线 {p.name:<20} {eol} sha256={baseline_sha[p][:12]}…")

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
            for ln in out.splitlines()[-25:]:
                print("    |", ln)

    green_ok = True
    for label, leg in (("应绿-改 TTL 数值", GREEN_WRONG_TARGET), ("应绿-只改 docstring", GREEN_DOCSTRING)):
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
        print(f"  {p.name:<20} sha256={now[:12]}… 逐字节相同={same}")

    bad = [r for r in results if r[1] != 1]
    print("\n=== 判定")
    print(f"  有效翻红腿数 = {len(results) - len(bad)}/{len(results)}")
    for label, code, verdict in bad:
        print(f"    不合格（exit={code}）：{label}")
    print(f"  应绿腿全绿 = {green_ok}")
    print(f"  逐字节还原 = {restored_ok}")
    return 0 if (not bad and green_ok and restored_ok) else 1


def main() -> int:
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
