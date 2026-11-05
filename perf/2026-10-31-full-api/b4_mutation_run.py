"""B4 变异验证 · 缓存容量上限的每一条守卫是否真被用例覆盖

对象（§B4 落地的三处改动）：
- `backend/xianyu_hunter/web/cache.py`：`_cache_store` 改 LRUDict、`configure_cache_max_entries`
- `backend/xianyu_hunter/web/app.py`：装配期套用 `cache_max_entries`
- `backend/xianyu_hunter/infra/lru.py`：B4 现在依赖的「读即续命」语义（原语本身未改）

判据：tests/test_cache_capacity.py + 相邻三个缓存用例族

纪律（沿用 D1/D2，写死在这里）：
- 逐条串行，绝不并发；基线不绿则整轮无效；
- 只有 pytest exit==1 算「红」，2/3/4/5 一律判 INVALID 并中止；
- 锚点必须唯一（count==1），变异文本先过 ast.parse；
- 含一条「接错对象」腿（改成语义等价的写法，应判绿）和一条负对照（只改 docstring，应判绿）；
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
CACHE = REPO / "backend" / "xianyu_hunter" / "web" / "cache.py"
APP = REPO / "backend" / "xianyu_hunter" / "web" / "app.py"
LRU = REPO / "backend" / "xianyu_hunter" / "infra" / "lru.py"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
TESTS = [
    "tests/test_cache_capacity.py",
    "tests/test_cache_swr_refresh.py",
    "tests/test_cache_single_flight.py",
    "tests/test_order_stats_cache_invalidation.py",
]
PER_LEG_TIMEOUT = 600

# (标签, 文件, 原锚点, 变异文本, 期望, 预期红的用例)
LEGS = [
    ("M01 写入绕过 LRU（等于回到无上限的普通 dict）", CACHE,
     "        _cache_store[cache_key] = (time.monotonic(), value)",
     "        dict.__setitem__(_cache_store, cache_key, (time.monotonic(), value))",
     "红", "test_store_evicts_least_recently_used_entry / test_cached_ttl_entry_count_stays_bounded：只写不驱逐"),

    ("M02 上限套用变成空操作", CACHE,
     """    if max_entries <= 0:
        logger.warning("cache_max_entries=%s 非法（需 >=1），沿用 %s", max_entries, _cache_store.maxsize)
        return
    with _cache_lock:
        _cache_store.maxsize = max_entries
""",
     """    return
""",
     "红", "test_store_evicts… / test_cached_ttl_entry_count_stays_bounded：上限不生效"),

    ("M03 读命中不续命（LRU 退化为 FIFO）", LRU,
     """        value = super().__getitem__(key)
        # 命中后提到队尾，标记为最近访问
        self.move_to_end(key)
        return value
""",
     """        value = super().__getitem__(key)
        return value
""",
     "红", "test_stale_value_is_protected_by_recency_on_read：stale 旧值被后来的写入挤掉"),

    ("M04 非正上限放行（<=0 也照样套用）", CACHE,
     "    if max_entries <= 0:",
     "    if max_entries < 0:",
     "红", "test_non_positive_cap_is_rejected：maxsize 被写成 0"),

    ("M05 装配期漏接（create_app 不再套用配置）", APP,
     "    configure_cache_max_entries(get_settings().cache_max_entries)",
     "    _ = get_settings().cache_max_entries",
     "红", "test_create_app_applies_configured_cap：calls 为空"),

    ("M06 接错对象：前缀失效改成等价写法（不该影响任何用例）", CACHE,
     "            if len(key) == 2 and isinstance(key[1], str) and key[1].startswith(prefixes)",
     "            if len(key) == 2 and isinstance(key[1], str) and any(\n"
     "                key[1].startswith(prefix) for prefix in prefixes\n"
     "            )",
     "绿", "（无）语义完全等价，所有用例应仍全绿"),

    ("负对照：只改 docstring 措辞", CACHE,
     "由 create_app 在装配阶段调用一次", "由 create_app 在装配阶段调用一次（装配期）",
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
    args = ap.parse_args()

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
    restore_bad = 0
    for p, raw in originals.items():
        now = p.read_bytes()
        same = hashlib.sha256(now).hexdigest() == start_hash[p]
        print(f"  {p.name}: sha256_match={same}")
        if not same:
            p.write_bytes(raw)
            print(f"    ↑ 不匹配，已按原始字节强制写回："
                  f"{hashlib.sha256(p.read_bytes()).hexdigest() == start_hash[p]}")
            restore_bad = 1
    status = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True, cwd=str(REPO)).stdout
    print("\n=== git status --porcelain（应无本轮之外的新增脏项）")
    print(status.strip() or "(clean)")
    print(f"\nRESULT: {bad} 腿不成立 / 还原{'正常' if restore_bad == 0 else '异常'}")
    return 0 if (bad == 0 and restore_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
