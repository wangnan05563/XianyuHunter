"""E3 变异验证：逐腿串行，每腿必须翻红；负对照（改相邻的 404 文案）必须全绿"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

TARGET = pathlib.Path("backend/xianyu_hunter/web/routes/api_task_links.py")
PY = ".venv/Scripts/python.exe"
TEST = "tests/test_task_links_units.py::test_live_links_endpoint"

RAISE_SCHEDULER = '''        raise CodedHTTPException(
            status_code=503,
            code="scheduler_off",
            detail="实时搜索需要浏览器实例，请以 XH_WITH_SCHEDULER=1 模式启动",
        )
'''

RETURN_SCHEDULER = '''        return _live_unavailable_response(
            "scheduler_off",
            "实时搜索需要浏览器实例，请以 XH_WITH_SCHEDULER=1 模式启动",
        )
'''

LEGS = [
    ("L1 scheduler_off 退回 HTTP 503",
     RETURN_SCHEDULER, RAISE_SCHEDULER, "红"),
    ("L2 流内事件丢掉 code",
     '    event = {"stage": "error", "status": 503, "code": code, "detail": detail}',
     '    event = {"stage": "error", "status": 503, "detail": detail}',
     "红"),
    ("L3 错误流不再声明 text/event-stream",
     '    return StreamingResponse(_one_event(), media_type="text/event-stream")',
     '    return StreamingResponse(_one_event(), media_type="application/json")',
     "红"),
    ("负对照：改相邻 400 的 detail",
     '    if not container.browser:\n        return _live_unavailable_response("browser_unavailable", "浏览器实例未初始化，请重启服务")\n\n    keyword = task.get("keyword", "")\n    if not keyword:\n        raise HTTPException(status_code=400, detail="任务无关键词")',
     '    if not container.browser:\n        return _live_unavailable_response("browser_unavailable", "浏览器实例未初始化，请重启服务")\n\n    keyword = task.get("keyword", "")\n    if not keyword:\n        raise HTTPException(status_code=400, detail="缺少关键词")',
     "绿"),
]


def run_test() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", TEST, "-o", "addopts=", "-p", "no:cacheprovider",
         "-q", "--no-header", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"baseline sha256={digest}")

    code, out = run_test()
    print(f"\n=== 基线 → exit={code}（期望 0）")
    print("   ", [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln])
    if code != 0:
        print("基线不绿，变异无效。")
        return 1

    failures = 0
    for label, old, new, expect in LEGS:
        count = original.count(old)
        if count != 1:
            print(f"\n=== {label}: 锚点出现 {count} 次（不是唯一），本腿无效")
            failures += 1
            continue
        TARGET.write_text(original.replace(old, new), encoding="utf-8")
        try:
            code, out = run_test()
        finally:
            TARGET.write_text(original, encoding="utf-8")
        ok = (code != 0) if expect == "红" else (code == 0)
        failures += 0 if ok else 1
        print(f"\n=== {label} → exit={code}，期望{expect}，判定={'OK' if ok else '不成立'}")
        for ln in [x for x in out.splitlines() if " passed" in x or " failed" in x or x.startswith("FAILED")][-6:]:
            print("   ", ln)

    restored = TARGET.read_text(encoding="utf-8") == original
    same_hash = hashlib.sha256(TARGET.read_bytes()).hexdigest() == digest
    print(f"\nrestore: same={restored} sha256_match={same_hash}")
    if not (restored and same_hash):
        failures += 1
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
