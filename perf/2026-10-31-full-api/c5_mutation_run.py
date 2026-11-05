"""C5 变异验证：逐腿串行，红腿必须翻红 ≥1 例，绿腿必须全绿"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PD = pathlib.Path("backend/xianyu_hunter/web/routes/price_dashboard.py")
EL = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_list.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_evaluations_market_stats_batch.py"]
BASETEMP = "D:/tmp/c5-mutation-basetemp"

LEGS = [
    ("L1 评估列表退回逐 task 循环（N+1 回来了）", EL,
     "            stats_map.update(_compute_sold_range_batch(conn, real_ids, range_days))",
     "            for tid in real_ids:\n"
     "                stats_map[tid] = _compute_sold_range(conn, tid, range_days)"),
    ("L2 每个任务套用第一个任务的价格区间（各 task 串味）", PD,
     "    for tid, prices in typed.items():\n        tr = ranges.get(tid)",
     '    for tid, prices in typed.items():\n        tr = ranges.get("t0000")'),
    ("L3 批量回退取数丢掉 range_days 过滤（范围外样本混入）", PD,
     '    stmt = select(ItemRow.task_id, ItemRow.price).where(ItemRow.task_id.in_(task_ids))\n'
     "    if range_days > 0:\n"
     "        cutoff = _utcnow() - timedelta(days=range_days)\n"
     "        # publish_time 为 NULL 的样本也保留（同 _filter_category_prices_by_range 逻辑）\n"
     "        stmt = stmt.where((ItemRow.publish_time >= cutoff) | (ItemRow.publish_time.is_(None)))",
     '    stmt = select(ItemRow.task_id, ItemRow.price).where(ItemRow.task_id.in_(task_ids))\n'
     "    if range_days > 0:\n"
     "        cutoff = _utcnow() - timedelta(days=range_days)\n"
     "        stmt = stmt.where(ItemRow.publish_time.is_(None))"),
    ("L4 回退判定反向（样本不足的任务反而不查回退）", PD,
     "    fallback = [tid for tid in task_ids if len(sold.get(tid, ([], 0))[0]) < _MIN_SOLD_SAMPLES]",
     "    fallback = [tid for tid in task_ids if len(sold.get(tid, ([], 0))[0]) >= _MIN_SOLD_SAMPLES]"),
]

GREEN_WRONG_TARGET = (
    PD,
    "        select(TaskRow.id, TaskRow.min_price, TaskRow.max_price)\n"
    "        .where(TaskRow.id.in_(task_ids))",
    "        select(TaskRow.id, TaskRow.min_price, TaskRow.max_price, TaskRow.status)\n"
    "        .where(TaskRow.id.in_(task_ids))",
)
GREEN_DOCSTRING = (
    PD,
    '    """一次取回多个任务的已售价格样本',
    '    """一次取回多个任务的已售价格样本（批量路径）',
)

FILES = [PD, EL]


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
    env = {**os.environ, "TEMP": "D:/tmp/c5-mutation-temp", "TMP": "D:/tmp/c5-mutation-temp"}
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


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    originals = {p: read_raw(p) for p in FILES}
    baseline_sha = {p: hashlib.sha256(originals[p].encode("utf-8")).hexdigest() for p in FILES}
    for p in FILES:
        eol = "CRLF" if "\r\n" in originals[p] else "LF"
        print(f"基线 {p.name:<28} {eol} sha256={baseline_sha[p][:12]}…")

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
        print(f"  {p.name:<28} sha256={now[:12]}… 逐字节相同={same}")

    bad = [r for r in results if r[1] != 1]
    print("\n=== 判定")
    print(f"  有效翻红腿数 = {len(results) - len(bad)}/{len(results)}")
    for label, code, verdict in bad:
        print(f"    不合格（exit={code}）：{label}")
    print(f"  应绿腿全绿 = {green_ok}")
    print(f"  逐字节还原 = {restored_ok}")
    return 0 if (not bad and green_ok and restored_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
