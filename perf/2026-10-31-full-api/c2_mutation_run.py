"""C2 变异验证：逐腿串行，每腿必须翻红 ≥1 例；负对照腿必须全绿

覆盖两件被改动的东西：
- `notification_engine` 的节流闸门（登记时机、默认值、分桶、聚合入口透传）
- `auth_query` 的路由接线（窗口常量透传、身份只解析一次）

负对照腿改的是**同一个文件里相邻但不被测**的掉线告警 dedup_key —— 用来证明
用例认的是节流这几行，而不是「文件里只要有改动就红」。

换行处理：这两个文件在 Windows 工作树里是 **CRLF**。若用 `read_text()/write_text()`
往返会把整份文件刷成 LF，还原后 sha256 必然不一致（还会把 git 里的行尾改脏）。
因此读写一律 `newline=""`（不做换行翻译），锚点里的 `\n` 在写入前按实际行尾换算。
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

NE = pathlib.Path("backend/xianyu_hunter/web/services/notification_engine.py")
AQ = pathlib.Path("backend/xianyu_hunter/web/routes/auth_query.py")
PY = ".venv/Scripts/python.exe"
TESTS = ["tests/test_auth_me_scan_throttle.py", "tests/test_regress_notification_user_scope.py"]

# (label, 目标文件, 旧锚点, 新文本)
LEGS = [
    ("L1 闸门登记挪到扫描之后（窗口内照样查库）", NE,
     '    if not _scan_gate_allowed(user_id or "*", min_interval):\n'
     '        return 0\n'
     '    orders = container.repo.list_orders(limit=200, user_id=user_id) or []\n',
     '    orders = container.repo.list_orders(limit=200, user_id=user_id) or []\n'
     '    if not _scan_gate_allowed(user_id or "*", min_interval):\n'
     '        return 0\n'),
    ("L2 默认窗口写成 30 s（显式调用方被静默节流）", NE,
     "    user_id: str | None = None,\n    min_interval: float = 0.0,\n) -> int:",
     "    user_id: str | None = None,\n    min_interval: float = 30.0,\n) -> int:"),
    ("L3 分桶塌成一个（A 的流量压住 B 的提醒）", NE,
     '    if not _scan_gate_allowed(user_id or "*", min_interval):',
     '    if not _scan_gate_allowed("*", min_interval):'),
    ("L4 聚合入口不透传窗口", NE,
     "            container, user_id=user_id, min_interval=min_scan_interval\n",
     "            container, user_id=user_id\n"),
    ("L5a 路由不传窗口（每请求一条全量扫描回来了）", AQ,
     "        user_id=current_uid or owner_user_id(request),\n"
     "        min_scan_interval=ORDER_SCAN_MIN_INTERVAL_SEC,\n",
     "        user_id=current_uid or owner_user_id(request),\n"),
    ("L5b 路由第二次解析身份", AQ,
     "        user_id=current_uid or owner_user_id(request),",
     "        user_id=_resolve_current_user_id(request) or owner_user_id(request),"),
]

# 负对照：同文件相邻代码，用例不该认它
NEG = (NE,
       '    dedup_key = f"auth_down:{last_user_id}:{half_day_key}"',
       '    dedup_key = f"auth_down_v2:{last_user_id}:{half_day_key}"')


# 起跑前置校验：这两个文件必须正是「已落地 C2」的那一版。
# 上一轮变异跑到一半时我并发地换过这两个文件（做 A/B 阶梯），负对照腿因此不可信；
# 把基线 sha 钉在这里，任何残留状态都会在第一条腿之前直接中止。
EXPECTED_SHA = {
    NE: "aa284ca5d7c526a07a359dac44870992e8c2e2f7ca583d16210a334bd4cb95b8",
    AQ: "419c39892c8c1476b6425dd284f4d45a0a65f27ac85eb9d5f006a84d37ba0d6c",
}


def read_raw(p: pathlib.Path) -> str:
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_raw(p: pathlib.Path, text: str) -> None:
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def to_eol(text: str, src: str) -> str:
    """锚点按目标文件真实行尾换算（源文件是 CRLF 时 \n → \r\n）"""
    if "\r\n" in src:
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


def _summary(out: str) -> list[str]:
    return [ln for ln in out.splitlines()
            if ln.startswith(("FAILED", "ERROR")) or " passed" in ln or " failed" in ln][-10:]


def _leg(target: pathlib.Path, originals: dict, old: str, new: str) -> tuple[int, str]:
    src = originals[target]
    old_r, new_r = to_eol(old, src), to_eol(new, src)
    assert src.count(old_r) == 1, f"锚点出现 {src.count(old_r)} 次，不是唯一：{old[:60]!r}"
    write_raw(target, src.replace(old_r, new_r))
    try:
        return run_tests()
    finally:
        write_raw(target, src)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    originals = {p: read_raw(p) for p in (NE, AQ)}
    baseline_sha = {p: hashlib.sha256(originals[p].encode("utf-8")).hexdigest() for p in (NE, AQ)}
    for p, want in EXPECTED_SHA.items():
        got = baseline_sha[p]
        if got != want:
            print(f"!!! 基线不符，中止：{p.name}\n    期望 {want}\n    实际 {got}\n"
                  f"    （文件里可能有变异残留或被其他操作换过）")
            return 2
    for p, src in originals.items():
        print(f"基线 {p.name}: {'CRLF' if chr(13)+chr(10) in src else 'LF'} sha256={baseline_sha[p][:16]}…")

    results = []
    for label, target, old, new in LEGS:
        code, out = _leg(target, originals, old, new)
        verdict = "OK（翻红）" if code != 0 else "!!! 全绿：这条腿没有判据"
        results.append((label, code, verdict))
        print(f"\n=== {label} → exit={code} {verdict}")
        for ln in _summary(out):
            print("   ", ln)

    ntarget, nold, nnew = NEG
    code, out = _leg(ntarget, originals, nold, nnew)
    print(f"\n=== 负对照（同文件掉线 dedup_key）→ exit={code}（期望 0=全绿）")
    for ln in _summary(out):
        print("   ", ln)
    if code != 0:
        # 上一轮这一腿红过，但 _summary 什么都没抓到（说明 pytest 没跑到汇总行就崩了）。
        # 不把原始输出摊开就没法区分「用例真不认这条改动」和「腿被外部干扰打断」。
        print("    ---- 原始输出尾部 ----")
        for ln in out.splitlines()[-40:]:
            print("    ", ln)
    neg_ok = code == 0

    print("\n=== 还原校验")
    restored_ok = True
    for p in (NE, AQ):
        now = hashlib.sha256(p.read_bytes()).hexdigest()
        same = now == baseline_sha[p]
        restored_ok = restored_ok and same
        print(f"  {p.name}: sha256={now[:16]}… 与基线逐字节相同={same}")

    bad = [r for r in results if r[1] == 0]
    print("\n=== 判定")
    print(f"  翻红腿数 = {len(results) - len(bad)}/{len(results)}")
    print(f"  负对照全绿 = {neg_ok}")
    print(f"  逐字节还原 = {restored_ok}")
    return 0 if (not bad and neg_ok and restored_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
