"""A1 变异验证：逐腿串行，红腿必须翻红 ≥1 例，绿腿（接错对象 / 只改 docstring）必须全绿

覆盖三类判据：
- 迁移行（旧库补索引）：删掉迁移行后 `test_migration_backfills_indexes_for_existing_db` 必须红
- ORM 声明（新建库建索引）：改错列后查询计划用例必须红
- 反向门禁（price_history.user_id / task_links.updated_at 刻意不建）：加回一个必须红

纪律同其它 mutation_run：基线全绿前置、只认 exit==1、ast.parse、锚点唯一、逐字节还原。
临时目录与 basetemp 都放 D 盘（本机 C 盘已满，写 C 会撞 disk full）。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DM = pathlib.Path("backend/xianyu_hunter/infra/db_models.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_perf_a1_indexes.py"]
BASETEMP = "D:/tmp/a1-mutation-basetemp"

LEGS = [
    ("L1 删 items 索引迁移行（旧库升级后仍无索引）", DM,
     '    _migrate_create_index(engine, "items", "ix_items_price", "price")\n', ""),
    ("L2 删 orders 复合索引迁移行", DM,
     '    _migrate_create_index(engine, "orders", "ix_orders_type_created", "type, created_at")\n', ""),
    ("L3 ORM 里 orders 索引改了列（新建库建出错误索引）", DM,
     '        Index("ix_orders_type_created", "type", "created_at"),',
     '        Index("ix_orders_type_created", "seller_id", "created_at"),'),
    ("L4 把「判定不落」的 price_history.user_id 加回来", DM,
     '        Index("ix_price_history_item_captured", "item_id", "captured_at"),',
     '        Index("ix_price_history_item_captured", "item_id", "captured_at"),\n'
     '        Index("ix_price_history_user", "user_id"),'),
]

# 应绿腿①：接错对象——重复声明 evaluations.seller_id 索引，不影响任何判据
GREEN_WRONG_TARGET = (
    DM,
    '        Index("ix_evaluations_created", "created_at"),',
    '        Index("ix_evaluations_created", "created_at"),\n'
    '        Index("ix_eval_seller_dup_probe", "seller_id"),',
)
# 应绿腿②：只改 docstring
GREEN_DOCSTRING = (
    DM,
    '    """初始化数据库（创建所有表 + 增量迁移缺失列/索引）"""',
    '    """初始化数据库（创建全部表，并增量迁移缺失的列与索引）"""',
)

FILES = [DM]


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
    env = {
        "TEMP": "D:/tmp/a1-mutation-temp",
        "TMP": "D:/tmp/a1-mutation-temp",
    }
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf", f"--basetemp={BASETEMP}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env={**__import__("os").environ, **env},
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


if __name__ == "__main__":
    sys.exit(main())
