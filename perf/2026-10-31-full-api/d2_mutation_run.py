"""D2 变异验证 · 锁作用域改动的每一条守卫是否真被用例覆盖

对象：`backend/xianyu_hunter/web/services/cache_scan.py`（§D2 落地的改动）
判据：tests/test_maintenance_cache_scan.py + 相邻三个 maintenance 用例族

纪律（沿用上一轮，写死在这里）：
- 逐条串行，绝不并发；基线不绿则整轮无效；
- 只有 pytest exit==1 算「红」，2/3/4/5（收集错误、内部错、超时）一律判 INVALID 并中止；
- 锚点必须唯一（count==1），变异文本先过 ast.parse；
- 含一条「接错对象」腿（改了个看似相关但其实不管用的地方，应判绿）和一条负对照
  （只改 docstring 措辞，应判绿）—— 缺了这两条，全红只能说明脚本自嗨；
- 每条腿无条件按原始字节恢复，结束时逐文件 sha256 核对。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
CS = REPO / "backend" / "xianyu_hunter" / "web" / "services" / "cache_scan.py"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
TESTS = [
    "tests/test_maintenance_cache_scan.py",
    "tests/test_maintenance_status_cost.py",
    "tests/test_maintenance_cleanup.py",
    "tests/test_slow_lane.py",
]
# test_slow_lane.py 是 D1 的判据，不参与本条（cache_scan）的裁决；机器被外部负载占住时
# 那条「在途峰值取样」用例会自己抖（见 results/d2_mutation_negctl.log 里基线不绿那一轮）。
# 用 --drop-slow-lane 把它从跑批里摘掉，其余判据一条不少；默认仍带上做邻域守卫。
PER_LEG_TIMEOUT = 420

# (标签, 文件, 原锚点, 变异文本, 期望, 预期红的用例)
LEGS = [
    ("M01 时间片请回锁内（等于回到落地前）", CS,
     """            scan.advancing = True
        try:
            self._advance(scan, self._budget if budget is None else budget)
        finally:
            with self._lock:
                scan.advancing = False
""",
     """            scan.advancing = True
            self._advance(scan, self._budget if budget is None else budget)
            scan.advancing = False
""",
     "红", "test_late_caller… / test_invalidate_is_not_blocked… / test_other_path_is_not_blocked…"),

    ("M02 去掉同键单推进者守卫", CS,
     "            if not advance or scan.done or scan.advancing:",
     "            if not advance or scan.done:",
     "红", "test_late_caller…：slice_keys 会出现两条同一路径的片（并发重复计数）"),

    ("M03 推进者标记置了不清（名额永久占用）", CS,
     """        finally:
            with self._lock:
                scan.advancing = False
""",
     """        finally:
            with self._lock:
                pass
""",
     "红", "test_budget_cut…_resumes_without_double_counting：第二片永远进不去，state 卡在 scanning"),

    ("M04 名额改成全局而不是按键", CS,
     "            if not advance or scan.done or scan.advancing:",
     "            if not advance or scan.done or any(s.advancing for s in self._scans.values()):",
     "红", "test_other_path_is_not_blocked_by_one_paths_slice：别的目录也被一个名额挡住"),

    ("M05 去掉 TTL 过期重建", CS,
     "        elif scan.done and time.time() - (scan.finished_at or 0) > self._ttl:",
     "        elif False:",
     "红", "test_done_result_is_reused_until_ttl_then_rescanned：过期后不再重扫，终值停在旧数"),

    ("M06 在飞那一次不回读进度表", CS,
     """            current = self._scans.get(key)
            return self._describe(key, current if current is not None else _Scan(stack=[key]))
""",
     """            return self._describe(key, scan)
""",
     "红", "test_inflight_slice_does_not_resurrect_invalidated_progress：删除前的旧数字被回给用户"),

    ("M07 接错对象：守卫加在 invalidate 上（不该影响任何用例）", CS,
     """        with self._lock:
            self._scans.pop(str(path), None)
""",
     """        with self._lock:
            scan = self._scans.get(str(path))
            if scan is not None and scan.advancing:
                scan.stack.clear()
            self._scans.pop(str(path), None)
""",
     "绿", "（无）这句看着像在管在飞的片，其实动的是即将被丢弃的对象，所有用例应仍全绿"),

    ("负对照：只改 docstring 措辞", CS,
     "秒级返回「统计中 x 项」", "秒级返回「统计中 x 项」进度",
     "绿", "（无）只改文档字符串措辞，所有用例仍应全绿"),
]


def run_pytest() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=PER_LEG_TIMEOUT, cwd=str(REPO),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def summarize(out: str) -> list[str]:
    keep = [ln for ln in out.splitlines()
            if (" passed" in ln or " failed" in ln or " error" in ln
                or ln.startswith("FAILED") or ln.startswith("ERROR")
                or ln.lstrip().startswith("E   ")
                or "assert" in ln.strip()[:8])]
    return keep[-14:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑标签前缀匹配的腿（调试用）")
    ap.add_argument("--drop-slow-lane", action="store_true",
                    help="把 tests/test_slow_lane.py 从跑批里摘掉（外部负载导致那条时序用例自己抖时用）")
    args = ap.parse_args()
    if args.drop_slow_lane:
        TESTS[:] = [t for t in TESTS if not t.endswith("test_slow_lane.py")]
        print("跑批用例：", ", ".join(TESTS), "（已摘掉 test_slow_lane.py）")

    targets = sorted({leg[1] for leg in LEGS})
    originals: dict[pathlib.Path, bytes] = {p: p.read_bytes() for p in targets}
    start_hash = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    print("baseline sha256:", {p.name: h[:16] for p, h in start_hash.items()})

    code, out = run_pytest()
    print(f"\n=== 基线 → exit={code}（期望 0）")
    for ln in summarize(out):
        print("   ", ln)
    if code != 0:
        print("基线不绿 → 本轮变异无效。先修基线。")
        return 1

    bad = 0
    for label, path, old, new, expect, why in LEGS:
        if args.only and not label.startswith(args.only):
            continue
        raw = originals[path]
        text = raw.decode("utf-8")
        anchor, repl = old, new
        # CRLF 文件里的多行锚点要先换行归一化；单行锚点不受影响
        if "\r\n" in text and "\n" in anchor:
            anchor = anchor.replace("\n", "\r\n")
            repl = repl.replace("\n", "\r\n")
        n = text.count(anchor)
        if n != 1:
            print(f"\n=== {label}: 锚点出现 {n} 次（不是唯一）→ INVALID，中止整轮")
            return 3
        mutated = text.replace(anchor, repl)
        try:
            ast.parse(mutated)
        except SyntaxError as e:
            print(f"\n=== {label}: 变异文本语法错误（{e}）→ INVALID，中止整轮")
            return 4
        t0 = time.perf_counter()
        path.write_bytes(mutated.encode("utf-8"))
        try:
            try:
                code, out = run_pytest()
            except subprocess.TimeoutExpired:
                print(f"\n=== {label}: pytest 超时 → INVALID，中止整轮")
                return 5
        finally:
            path.write_bytes(raw)      # 无条件恢复
        elapsed = time.perf_counter() - t0
        ok = (code == 1) if expect == "红" else (code == 0)
        bad += 0 if ok else 1
        print(f"\n=== {label} → exit={code}，期望{expect}，判定={'OK' if ok else '不成立'}"
              f"（{elapsed:.0f}s）\n    预期用例：{why}")
        for ln in summarize(out):
            print("   ", ln)

    print("\n=== 还原核对")
    failures = 0 if bad == 0 else 1
    for p, raw in originals.items():
        now = p.read_bytes()
        same = hashlib.sha256(now).hexdigest() == start_hash[p]
        print(f"  {p.name}: sha256_match={same}")
        if not same:
            p.write_bytes(raw)
            print(f"    ↑ 不匹配，已按原始字节强制写回："
                  f"{hashlib.sha256(p.read_bytes()).hexdigest() == start_hash[p]}")
            failures = 1
    status = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True, cwd=str(REPO)).stdout
    print("\n=== git status --porcelain（应无本轮之外的新增脏项）")
    print(status.strip() or "(clean)")
    print(f"\nRESULT: {bad} 腿不成立 / 还原{'正常' if failures == 0 else '异常'}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
