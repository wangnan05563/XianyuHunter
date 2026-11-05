"""B4 取证：进程内 TTL 缓存到底驻留了什么、能长多大、过期的是不是不落地回收

原文（OPTIMIZATIONS B4 / CONCLUSION ③）只说「`_cache_store` 无容量上限/淘汰」，
并把长跑 RSS 斜率 +75.9 MB/h 的一半嫌疑记在它头上。动手加 cap 之前要先量清四件事，
否则很容易把「上限」加错地方：

1. **谁在写**：全仓只有 8 处 `@cached_ttl`（AST 清点，不靠记忆），key 模板决定键基数上限；
2. **一条多大**：全量 GET 扫一遍后逐条深量（`sys.getsizeof` + 递归 + id 去重，
   共享子对象只算一次 —— 不这么算会把同一份 list 反复计入而虚高）；
3. **过期的是不是还占着**：`_try_get_cache` 只在读时判 TTL，过期项**从不删除**，
   只有同 key 再次写入才覆盖 —— 所以「读不到的死值」才是真正无上限的那部分；
4. **键基数上限**：参数化站点的取值域（`task_id` 来自 DB 计数、metric/bins/range 来自
   前端实际发出的查询参数），估「跑久了」会驻留多少条、多少 MB。

沙箱：`setup_sandbox.build_sandbox()` 的一致快照副本（sqlite backup API）+ chdir，
调度器不启动（不设 `XH_WITH_SCHEDULER`）。只发 GET，绝不发写请求。
令牌只在进程内读取用于请求头，**绝不打印**；键名与值内容也不打印，只报字节量与字段名形状。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(r"D:/code/otherProjects/17_xianyu")
PERF = REPO / "perf" / "2026-10-31-full-api"
SANDBOX = Path(r"D:/tmp/xh-perf-b4/sandbox")
MB = 1024 * 1024


# ------------------------------------------------------------------ 深量字节
def deep_bytes(obj, seen: set[int]) -> int:
    """容器自身 + 递归成员的 sizeof；同一对象只算一次（id 去重）"""
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    total = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            total += deep_bytes(k, seen) + deep_bytes(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            total += deep_bytes(v, seen)
    return total


# ------------------------------------------------------------------ ① 静态清点
def census_cache_sites() -> list[dict]:
    """AST 找出所有 @cached_ttl(...) 站点：模块、限定名、ttl、SWR、key 模板形状"""
    root = REPO / "backend" / "xianyu_hunter"
    out: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call)
                        and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "cached_ttl"):
                    continue
                ttl = dec.args[0].value if dec.args else None
                kw = {k.arg: k.value for k in dec.keywords}
                keyfn = kw.get("key_fn")
                template = None
                if isinstance(keyfn, ast.Lambda):
                    # 把 f-string 的常量与占位拼成一个可读模板（只为看清键基数来自哪些参数）
                    parts = []
                    body = keyfn.body
                    if isinstance(body, ast.Constant):
                        parts.append(str(body.value))
                    for el in getattr(body, "values", []):
                        if isinstance(el, ast.Constant):
                            parts.append(str(el.value))
                        elif isinstance(el, ast.FormattedValue):
                            parts.append("%s")
                        elif isinstance(el, ast.JoinedStr):
                            parts.append("<f>")
                        else:
                            parts.append(f"<{type(el).__name__}>")
                    template = "".join(parts) or "<expr>"
                elif keyfn is not None:
                    template = "<key_fn 非常量>"
                out.append({
                    "file": str(path.relative_to(root)).replace("\\", "/"),
                    "qualname": node.name,
                    "ttl_seconds": ttl,
                    "swr": bool(kw.get("stale_while_revalidate")
                                and getattr(kw["stale_while_revalidate"], "value", False)),
                    "key_template": template if template is not None else "<args>",
                })
    return out


# ------------------------------------------------------------------ ② 请求混合
def read_labels() -> list[str]:
    """压测里真实打过的 GET 端点（含查询串），来自 results/per_endpoint.json 的 label

    只跳过 SSE/流式形态：它们会一直 yield 到超时，把清点拖死。**导出类不跳** ——
    「导出会不会进缓存」正是 B4 要证伪的一项，让条目曲线自己回答。
    """
    data = json.loads((PERF / "results" / "per_endpoint.json").read_text(encoding="utf-8"))
    labels = {e["label"][4:] for e in data if e["label"].startswith("GET ")}
    skipped = {l for l in labels if any(h in l for h in ("/live", "/stream", "/events", "/sse",
                                                         "/progress"))}
    return sorted(labels - skipped), sorted(skipped)


def boot_app():
    """在沙箱目录里创建 app（调度器不起），返回 (app, web_token)"""
    sys.path.insert(0, str(REPO / "backend"))
    os.environ.pop("XH_WITH_SCHEDULER", None)
    os.chdir(SANDBOX)
    from xianyu_hunter.config import get_settings
    from xianyu_hunter.web.app import create_app

    return create_app(), get_settings().web_token


def sweep(app, token: str) -> dict:
    """逐条发 GET，每发一条记一次缓存快照（条目数 + 深量字节），得到增长曲线"""
    import asyncio

    import httpx

    from xianyu_hunter.web import cache as C

    labels, skipped = read_labels()

    async def run() -> dict:
        seen_status: dict[str, int] = defaultdict(int)
        curve: list[dict] = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://s",
                                     timeout=20.0,
                                     headers={"Authorization": f"Bearer {token}"}) as client:
            for i, path in enumerate(labels):
                try:
                    r = await client.get(path)
                    seen_status[str(r.status_code)] += 1
                except Exception as e:                       # 端点自身的失败不该拖垮清点
                    seen_status[f"exc:{type(e).__name__}"] += 1
                with C._cache_lock:
                    n = len(C._cache_store)
                    if i == 0 or i == len(labels) - 1 or (curve and n != curve[-1]["entries"]):
                        seen: set[int] = set()
                        total = sum(deep_bytes(v, seen) for (_, v) in C._cache_store.values())
                        curve.append({"i": i, "path": path, "entries": n, "bytes": total})
                await asyncio.sleep(0)
        return {"labels": len(labels), "skipped_streaming": skipped,
                "codes": dict(seen_status), "curve": curve}

    return asyncio.run(run())


# ------------------------------------------------------------------ ③ 驻留分布
def residency(app_for_ttl: dict[str, int]) -> dict:
    """把每条缓存项按「限定名 × 新鲜/过期」拆开计价，并给出单条最大项与形状"""
    from xianyu_hunter.web import cache as C

    now = time.monotonic()
    rows: dict[str, dict] = {}
    with C._cache_lock:
        items = list(C._cache_store.items())
    for key, (ts, value) in items:
        qual = key[0] if isinstance(key, tuple) and key else str(key)
        ttl = app_for_ttl.get(qual.split(".")[-1], 0)
        age = now - ts
        seen: set[int] = set()
        size = deep_bytes(value, seen)
        r = rows.setdefault(qual, {
            "qualname": qual, "entries": 0, "bytes": 0, "max_bytes": 0, "max_key": None,
            "expired_entries": 0, "expired_bytes": 0, "ttl_seconds": ttl, "objects": 0,
        })
        r["entries"] += 1
        r["bytes"] += size
        r["objects"] += len(seen)
        if size > r["max_bytes"]:
            r["max_bytes"], r["max_key"] = size, (key[1] if len(key) > 1 else "")
        if ttl and age > ttl:
            r["expired_entries"] += 1
            r["expired_bytes"] += size
    return {"by_qualname": sorted(rows.values(), key=lambda r: -r["bytes"]),
            "total_entries": len(items),
            "total_bytes": sum(r["bytes"] for r in rows.values())}


def expired_reclaimed_by_rescanning(ttl_seconds: int, wait_seconds: float) -> dict:
    """直接证明「过期项不会被回收」：写一条 → 活到 TTL 外 → 读一次（miss）→ 看条目还在不在"""
    from xianyu_hunter.web import cache as C

    key = ("b4_probe.dead_key", "dead")
    payload = {"rows": [{"a": i, "b": "x" * 64} for i in range(200)]}
    C._set_cache(key, payload)
    before = len(C._cache_store)
    # 把时间戳人为推到 TTL 之外，避免用例真的睡 ttl
    with C._cache_lock:
        ts, v = C._cache_store[key]
        C._cache_store[key] = (time.monotonic() - (ttl_seconds + wait_seconds), v)
    hit, _ = C._try_get_cache(key, ttl_seconds)
    still = key in C._cache_store
    seen: set[int] = set()
    retained = deep_bytes(C._cache_store[key][1], seen) if still else 0
    C._cache_store.pop(key, None)
    return {"hit_after_expiry": hit, "entry_still_present": still,
            "retained_bytes": retained, "entries_before": before,
            "entries_after_pop": len(C._cache_store)}


# ------------------------------------------------------------------ ④ 键基数上限
def key_ceiling(sites: list[dict]) -> dict:
    """参数化站点的取值域：task 数来自 DB（只读快照），metric/bins/range 来自前端源码"""
    out: dict = {}
    db = SANDBOX / "data" / "xianyu.db"
    tasks = 0
    if db.exists():
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            tasks = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
        finally:
            con.close()
    out["tasks_in_sandbox_db"] = tasks

    fe = REPO / "frontend" / "src"
    domains: dict[str, set[str]] = defaultdict(set)
    for path in list(fe.rglob("*.ts")) + list(fe.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in ("sold", "on_sale", "all", "price", "count", "30", "90", "24", "72"):
            if f"'{token}'" in text or f'"{token}"' in text:
                domains[token].add(path.name)
    out["frontend_param_tokens"] = {k: len(v) for k, v in sorted(domains.items())}
    out["parameterized_sites"] = [s for s in sites if s["key_template"] not in (None, "<args>")]
    # 上限口径：带 task_id 的两个站点，每个 task 各有一组 metric/bins 取值
    out["ceiling_note"] = ("trend:{metric}:{range}:{task} 与 hist:{bins}:{task} 的键基数"
                          f"随 tasks 线性增长（沙箱 tasks={tasks}）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完保留沙箱目录（默认删除）")
    ap.add_argument("--ttl", type=int, default=60, help="过期不回收演示用的 TTL（秒）")
    args = ap.parse_args()

    print("[1] 沙箱搭建（sqlite 一致性快照副本；生产 data/ 从不被打开）")
    sys.path.insert(0, str(PERF))
    import setup_sandbox

    setup_sandbox.SANDBOX = SANDBOX
    setup_sandbox.OUT = SANDBOX / "output"
    setup_sandbox.build_sandbox()

    sites = census_cache_sites()
    print(f"[2] @cached_ttl 站点 = {len(sites)} 处")
    for s in sites:
        print(f"    ttl={str(s['ttl_seconds']):>4} swr={int(s['swr'])} "
              f"key={s['key_template']!r:<48} {s['file']}#{s['qualname']}")

    print("[3] 全量 GET 扫一遍（只发 GET；不启动调度器）")
    app, _tok = boot_app()
    res = sweep(app, _tok)
    print(f"    标签 {res['labels']} 条（流式/导出类跳过 {len(res['skipped_streaming'])} 条），"
          f"状态码分布 {res['codes']}")
    curve = res["curve"]
    print(f"    缓存条目曲线：起 {curve[0]['entries'] if curve else 0} 条 → "
          f"止 {curve[-1]['entries'] if curve else 0} 条，"
          f"深量字节 {curve[-1]['bytes'] / MB if curve else 0:.2f} MB")
    for c in curve[1:]:
        if c["entries"] != curve[0]["entries"]:
            print(f"      +{c['entries'] - curve[0]['entries']:>3} 条 @ {c['path'][:60]} "
                  f"→ {c['bytes'] / MB:.2f} MB")
            break
    biggest = max(curve, key=lambda c: c["bytes"]) if curve else None
    if biggest:
        print(f"    最大驻留点：{biggest['bytes'] / MB:.2f} MB @ {biggest['path'][:60]}")

    ttl_map = {s["qualname"]: (s["ttl_seconds"] or 0) for s in sites}
    print("[4] 驻留分布（按限定名 × 过期与否）")
    r = residency(ttl_map)
    print(f"    合计 {r['total_entries']} 条 / {r['total_bytes'] / MB:.2f} MB")
    for row in r["by_qualname"]:
        if not row["bytes"]:
            continue
        print(f"    {row['qualname']:<34} 条={row['entries']:<3} "
              f"{row['bytes'] / 1024:>9.1f} KB  单条最大={row['max_bytes'] / 1024:>8.1f} KB"
              f"  过期仍占={row['expired_entries']} 条/{row['expired_bytes'] / 1024:.1f} KB"
              f"  (ttl={row['ttl_seconds']})  key={str(row['max_key'])[:28]}")

    print("[5] 过期不回收：写一条 → 推到 TTL 外 → 读（miss）→ 条目是否还在")
    d = expired_reclaimed_by_rescanning(args.ttl, 1.0)
    print(f"    hit_after_expiry={d['hit_after_expiry']} "
          f"entry_still_present={d['entry_still_present']} "
          f"retained_bytes={d['retained_bytes'] / 1024:.1f} KB")

    print("[6] 键基数上限")
    ceil = key_ceiling(sites)
    print(f"    沙箱 tasks={ceil['tasks_in_sandbox_db']}；参数化站点 "
          f"{len(ceil['parameterized_sites'])} 处 → {ceil['ceiling_note']}")
    print(f"    前端出现过的取值 token：{ceil['frontend_param_tokens']}")

    out = PERF / "results" / "b4_cache_census.json"
    out.write_text(json.dumps({
        "sites": sites, "sweep": {"labels": res["labels"], "codes": res["codes"],
                                  "curve": curve},
        "residency": r, "expiry_not_reclaimed": d, "ceiling": ceil,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n产物 → {out}")

    if not args.keep:
        import shutil

        shutil.rmtree(SANDBOX, ignore_errors=True)
        print("沙箱已删除")
    else:
        print(f"沙箱保留在 {SANDBOX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
