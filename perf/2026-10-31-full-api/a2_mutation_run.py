"""A2 变异验证：逐腿串行，每腿必须翻红 ≥1 例；负对照腿必须全绿

覆盖三处被改动的东西：
- `repo_events.list_events_by_type` 的等值语义、单趟 SQL（不附发 COUNT）、user 隔离
- `evaluations_common.load_eval_scored_events` 与三个只读 GET 的接线
- `evaluations_batch._collect_evaluated_ids` 刻意保留的宽口径（改窄要翻红）

换行处理同 C2：这些文件在 Windows 工作树里是 CRLF，读写一律 `newline=""`，
锚点里的 `\\n` 在写入前按实际行尾换算，否则还原后 sha256 必然不一致。

纪律：本脚本运行期间**不得并发**任何会替换源文件的操作（C2 那轮就是这么作废的）。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(r"D:/code/otherProjects/17_xianyu")
REPO_EVENTS = pathlib.Path("backend/xianyu_hunter/infra/repo_events.py")
EC = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_common.py")
EL = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_list.py")
ED = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_distribution.py")
EF = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_feedback.py")
EB = pathlib.Path("backend/xianyu_hunter/web/routes/evaluations_batch.py")
PY = str(REPO_ROOT / ".venv/Scripts/python.exe")
TESTS = [
    "tests/test_eval_events_query_pushdown.py",
    "tests/test_evaluations_sold_filter.py",
    "tests/test_evaluations_brand_facets.py",
    "tests/test_evaluations_price_filter.py",
    "tests/test_enrich_eval_overwrite.py",
    "tests/test_auto_collect_stats_endpoint.py",
]

# 基线 = 「A2 已落地」那一版；任何残留状态都会在第一条腿之前中止
EXPECTED_SHA = {
    REPO_EVENTS: "fc7f22a9facb02fef7f25d4c6a52f934834347c3dc0c6ce381b7c61169b8bb8d",
    EC: "8afe0e37ff2672d5c097b97101dc183eb379972731c2143301fa3b6f7ff3d217",
    EL: "a10c8b2aa041222bd9488b292ed817602057fb8ef83b39ddde10568b6b157614",
    ED: "a358f6daa249c2f6f8eeb4970edccd62b32a629e1cec5a988f92b777488a8d79",
    EF: "4c9f2a04588eb237cc6c1c9e18ece42adc2d24ac6072157ca8666f00eb5694aa",
    EB: "d4313decd21c68cf1a358462117ade38cb71dd96e681ab46553de5031c4221e4",
}

EQ_CALL = "rows = load_eval_scored_events(container.repo, user_id)\n\n    min_price, max_price"
BRANDS_CALL = "rows = load_eval_scored_events(container.repo, user_id)\n    item_ids: set[str] = set()"
LEGS = [
    ("L1 等值退回前缀 LIKE（eval.scored_extra 会被顺带读进来）", REPO_EVENTS,
     "stmt = select(EventRow).where(EventRow.type == type_value)",
     'stmt = select(EventRow).where(EventRow.type.like(f"{type_value}%"))'),
    ("L2 把 COUNT 加回来并改回元组返回（只读端点白付一趟覆盖索引扫）", REPO_EVENTS,
     "            stmt = stmt.order_by(EventRow.created_at.desc()).limit(limit).offset(offset)\n"
     "            return [self._row_to_dict(r) for r in conn.execute(stmt).all()]\n\n"
     "    def list_events_by_type_prefix(",
     "            stmt = stmt.order_by(EventRow.created_at.desc()).limit(limit).offset(offset)\n"
     "            rows = [self._row_to_dict(r) for r in conn.execute(stmt).all()]\n"
     "            count_stmt = select(func.count()).select_from(EventRow).where(\n"
     "                EventRow.type == type_value\n"
     "            )\n"
     "            total = int(conn.execute(count_stmt).scalar() or 0)\n"
     "            return rows, total\n\n"
     "    def list_events_by_type_prefix("),
    ("L3 等值查询丢掉 user_id 隔离（跨账号读别人的评估记录）", REPO_EVENTS,
     "            stmt = select(EventRow).where(EventRow.type == type_value)\n"
     "            if task_id:\n"
     "                stmt = stmt.where(EventRow.task_id == task_id)\n"
     "            if user_id is not None:\n"
     "                stmt = stmt.where(EventRow.user_id == user_id)\n",
     "            stmt = select(EventRow).where(EventRow.type == type_value)\n"
     "            if task_id:\n"
     "                stmt = stmt.where(EventRow.task_id == task_id)\n"),
    ("L4 共享入口把精确类型换成前缀（列表端点直接 0 行）", EC,
     'return repo.list_events_by_type(_EVAL_SCORED_TYPE, limit=limit, user_id=user_id)',
     'return repo.list_events_by_type(_EVAL_TYPE_PREFIX, limit=limit, user_id=user_id)'),
    ("L5a /api/evaluations 回退到前缀取数", EL, EQ_CALL,
     'rows = container.repo.list_events_by_type_prefix("eval.", user_id=user_id)[0]\n\n'
     "    min_price, max_price"),
    ("L5b /evaluations/brands 回退到前缀取数", EL, BRANDS_CALL,
     'rows, _ = container.repo.list_events_by_type_prefix("eval.", user_id=user_id)\n'
     "    item_ids: set[str] = set()"),
    ("L6 distribution 回退到前缀取数", ED,
     "events = load_eval_scored_events(container.repo, user_id)",
     'events = container.repo.list_events_by_type_prefix("eval.", user_id=user_id)[0]'),
    ("L7 feedback 回退到前缀取数（多付一条被丢弃的 COUNT）", EF,
     "rows = load_eval_scored_events(container.repo, user_id)",
     'rows = container.repo.list_events_by_type_prefix(_EVAL_SCORED_TYPE, user_id=user_id)[0]'),
    ("L8 POST 宽口径被「顺手统一」成等值（batch 的已评估范围会漏判）", EB,
     "eval_events, _ = container.repo.list_events_by_type_prefix(\n"
     "        type_prefix=_EVAL_TYPE_PREFIX, user_id=user_id,\n"
     "    )",
     "eval_events = container.repo.list_events_by_type(\n"
     "        _EVAL_SCORED_TYPE, user_id=user_id,\n"
     "    )"),
]

# 负对照：只改等值方法的 limit 默认值（50 万行以内行为不变），用例不该认它
NEG = (REPO_EVENTS, "        limit: int = 50000,\n        offset: int = 0,\n        user_id: str | None = None,\n    ) -> list[dict]:",
       "        limit: int = 500000,\n        offset: int = 0,\n        user_id: str | None = None,\n    ) -> list[dict]:")

FILES = list(EXPECTED_SHA)


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
    # 变异文本必须还能解析：第一轮 L5b 的锚点少了一行缩进、拼出孤立的 `)`，pytest 直接
    # 以 exit=2（收集期 SyntaxError）「翻红」—— 那证明的是文件坏了，不是用例认这行改动。
    ast.parse(mutated)
    write_raw(target, mutated)
    # stillDiffers：写完立刻回读，证明这腿真的在磁盘上生效。没有这一步，「腿全绿」可能
    # 只是因为并发进程把我刚写的变异覆盖回去了（C2 那轮的负对照就是这么废的）。
    assert read_raw(target) != src, f"{target.name} 写入后回读仍与原文相同，变异未生效"
    try:
        return run_tests()
    finally:
        write_raw(target, src)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    originals = {p: read_raw(p) for p in FILES}
    baseline_sha = {p: hashlib.sha256(originals[p].encode("utf-8")).hexdigest() for p in FILES}
    for p, want in EXPECTED_SHA.items():
        got = baseline_sha[p]
        if got != want:
            print(f"!!! 基线不符，中止：{p.name}\n    期望 {want}\n    实际 {got}")
            return 2
    for p in FILES:
        eol = "CRLF" if "\r\n" in originals[p] else "LF"
        print(f"基线 {p.name:<28} {eol} sha256={baseline_sha[p][:12]}…")

    # 基线必须是全绿的：绿不了的基线，任何「翻红」都不说明这条腿的判据存在
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
        # 只有 exit=1（跑到了用例且有失败）才算「这条腿有判据」；2/3/4 是收集期或内部错误，
        # 文件坏掉也能造成「红」，那种红不证明任何事。
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

    ntarget, nold, nnew = NEG
    code, out = _leg(ntarget, originals, nold, nnew)
    print(f"\n=== 负对照（等值方法 limit 50000 → 500000）→ exit={code}（期望 0=全绿）")
    for ln in _summary(out):
        print("   ", ln)
    if code != 0:
        print("    ---- 原始输出尾部 ----")
        for ln in out.splitlines()[-40:]:
            print("    ", ln)
    neg_ok = code == 0

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
    print(f"  负对照全绿 = {neg_ok}")
    print(f"  逐字节还原 = {restored_ok}")
    return 0 if (not bad and neg_ok and restored_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
