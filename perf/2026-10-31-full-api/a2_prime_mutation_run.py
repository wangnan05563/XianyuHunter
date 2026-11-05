"""A2′ 变异验证：逐腿串行，红腿必须翻红 ≥1 例，绿腿（接错对象 / 只改 docstring）必须全绿

覆盖 A2′ 实际改动的两处：
- `_COMPACT_BRAND_ALIASES` 预计算（别名不压缩就匹配不到 "SteamDeck" 这类无分隔符标题）
- 品牌比对按 canonical 组求交集（退化成「任意别名 + 任意别名」会误判跨组组合）

纪律同 a2_mutation_run.py：基线必须全绿；只认 exit==1 为红；每条腿逐字节还原并核对 sha256；
锚点唯一 + ast.parse；变异写入后回读确认真的落盘。本脚本运行期间不得并发任何替换源文件的操作。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CU = pathlib.Path("backend/xianyu_hunter/modules/collector_utils.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_brand_display_normalize.py"]

ALIAS_PRECOMPUTE = '''_COMPACT_BRAND_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (canonical, tuple(_compact_text(alias) for alias in aliases))
    for canonical, aliases in _BRAND_ALIASES
)'''

LEGS = [
    ("L1 别名表退回未压缩（带分隔符的别名匹配不到无分隔符标题）", CU,
     ALIAS_PRECOMPUTE,
     "_COMPACT_BRAND_ALIASES = _BRAND_ALIASES"),
    ("L2 组交集退化成「任意别名 + 任意别名」（跨组误判）", CU,
     "    return bool(_alias_groups_in_text(compact_raw_candidate) "
     "& _alias_groups_in_text(compact_title))",
     "    return bool(_alias_groups_in_text(compact_raw_candidate) "
     "or _alias_groups_in_text(compact_title))"),
]

# 应绿腿①：接错对象——往通用后缀表加一个用例里不存在的成员，看似相关但完全不影响判据
GREEN_WRONG_TARGET = (
    CU,
    '    "电脑", "手机", "配件", "小店", "店铺", "店",',
    '    "电脑", "手机", "配件", "小店", "店铺", "店", "旗舰店2",',
)
# 应绿腿②：只改 docstring 措辞
GREEN_DOCSTRING = (
    CU,
    '    """压缩文本用于宽松匹配：去掉空白/常见分隔符并转小写。"""',
    '    """压缩文本用于宽松匹配：去掉空白与常见分隔符，并转小写。"""',
)

FILES = [CU]


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
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
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
            verdict = f"!!! 无效：pytest exit={code}（非用例失败；打印原始输出尾部）"
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
