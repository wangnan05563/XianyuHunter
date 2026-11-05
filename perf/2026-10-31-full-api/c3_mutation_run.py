"""C3 残余变异验证：逐腿串行，红腿必须翻红 ≥1 例，绿腿（接错对象 / 只改 docstring）必须全绿

覆盖 `api_templates` 模板文件指纹 memo 的三处关键行为：
- 指纹参与命中判定（不看指纹就会把外部编辑当命中）
- `_save_user` 写盘后配对作废（同尺寸 + 同一 mtime tick 时指纹判定失效）
- 返回浅拷贝列表（直接返回缓存对象会让调用方 append 污染 memo）

纪律同 a2_prime_mutation_run.py：基线必须全绿；只认 exit==1 为红；锚点唯一 + ast.parse；
逐字节还原并核对 sha256；运行期间不得并发替换源文件。

注意：本机 `C:\\pyfix_tmp` 下 pytest 的 basetemp 偶发无法创建子目录（环境问题，与代码无关），
所以这里显式给 `--basetemp`，让基线与变异腿都跑在可控目录上。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AT = pathlib.Path("backend/xianyu_hunter/web/routes/api_templates.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = ["tests/test_templates_memo.py"]
BASETEMP = "D:/tmp/c3-mutation-basetemp"

LEGS = [
    ("L1 指纹不参与命中判定（外部编辑被当命中）", AT,
     "    if hit is not None and hit[0] == fingerprint:",
     "    if hit is not None:"),
    ("L2 写盘后不作废 memo（同尺寸 + 同 tick 读到旧值）", AT,
     """    # 写站点配对作废：同尺寸 + 同一 mtime tick 的改写，单靠文件指纹识别不到
    _bump_epoch(USER_FILE)
    _MEMO.pop(USER_FILE, None)
""", ""),
    ("L3 直接返回缓存对象（调用方 append 污染 memo）", AT,
     "        return list(hit[1])\n    data = parse()",
     "        return hit[1]\n    data = parse()"),
]

# 应绿腿①：接错对象——调换异常元组的顺序（语义完全等价，用例不该认它）
GREEN_WRONG_TARGET = (
    AT,
    "        except (json.JSONDecodeError, OSError):",
    "        except (OSError, json.JSONDecodeError):",
)
# 应绿腿②：只改 docstring 措辞
GREEN_DOCSTRING = (
    AT,
    '    """从 YAML 加载预置模板列表"""',
    '    """从 YAML 加载预置模板列表（命中指纹 memo 时不重新解析）"""',
)

FILES = [AT]


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
         "-q", "--no-header", "-rf", f"--basetemp={BASETEMP}"],
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
        print(f"基线 {p.name:<24} {eol} sha256={baseline_sha[p][:12]}…")

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
        print(f"  {p.name:<24} sha256={now[:12]}… 逐字节相同={same}")

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
