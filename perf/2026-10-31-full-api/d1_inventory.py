"""D1 取证 · 第一层：线程池令牌的「需求侧」静态清点

D1 原文说「本项目大量 handler 是同步 `def`，FastAPI 一律丢进 anyio 线程池（默认 40 token）」。
决定这笔成本去向的是**形状**而不是耗时：只有同步 def 才占用线程令牌，async def 根本不进池。
所以先把两侧数清楚，再谈隔离：

- 需求侧：每个 `@router.*` 处理器是 sync 还是 async（AST 判定，不启动应用、不连数据库）；
- 分组：按路由文件归到功能族，看慢接口族（maintenance / logs / vector-admin / export）吃掉多少令牌；
- 关键对照：D1 用来证明「饥饿」的两条廉价接口（`notifier/quiet-hours`、
  `anticrawl/fingerprint`）是不是也走池子 —— 若它们是 async def，那它们的 2 s 与线程池无关，
  D1 的归因当场作废（同 C2/A2 的教训：先验形状，再信数字）。

只读，不启动浏览器/调度器，不连接任何数据库。
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from pathlib import Path

ROUTES_ROOT = Path("backend/xianyu_hunter/web")
METHODS = {"get", "post", "put", "patch", "delete"}

# D1 证据链里点名的接口：慢族（抢令牌的）与廉价族（被饿的）。
# 键必须含 method —— 同一路径常挂读写两个处理器（GET/POST /quiet-hours），
# 只按 (file, path) 建索引会被后写入的那条覆盖，读到的形状就不是压测打的那条。
SLOW_FILE_MARKERS = ("maintenance", "logs", "vector_admin", "db_admin")
CHEAP_ENDPOINTS = [
    ("api_notifier.py", "GET", "/quiet-hours"),
    ("api_anticrawl.py", "GET", "/fingerprint"),
    # 真实路径带 /freq 前缀；早先写成 "/delay" 会落到「未找到」分支，
    # 从而让判读少一条廉价样本 —— 清点脚本自己的错不能算成被测系统的形状。
    ("api_anticrawl.py", "GET", "/freq/delay"),
]


def route_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str] | None:
    """返回 (method, path)；不是路由处理器则返回 None。"""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        owner = dec.func.value
        if not isinstance(owner, ast.Name) or owner.id not in {"router", "api_router"}:
            continue
        method = dec.func.attr
        if method not in METHODS:
            continue
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return method, arg.value
        return method, "<no-literal-path>"
    return None


def collect(routes_root: Path) -> list[dict]:
    rows: list[dict] = []
    for py in sorted(routes_root.rglob("*.py")):
        if "middleware" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:  # 只认模块级处理器，嵌套闭包不参与计数
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            hit = route_decorator(node)
            if hit is None:
                continue
            method, path = hit
            rows.append({
                "file": py.name,
                "method": method.upper(),
                "path": path,
                "handler": node.name,
                "async": isinstance(node, ast.AsyncFunctionDef),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="仓库根目录")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    rows = collect(root / ROUTES_ROOT)

    total = len(rows)
    n_async = sum(1 for r in rows if r["async"])
    n_sync = total - n_async
    print("=" * 78)
    print("[1] 需求侧总量：多少请求会来抢 anyio 的 40 个线程令牌")
    print(f"  路由处理器总数 {total}   同步 def（占令牌） {n_sync}   async def（不占） {n_async}")
    print(f"  同步占比 {n_sync / total:.1%} —— 「一律丢进线程池」的说法只对 {n_sync} 个成立")

    print("\n" + "=" * 78)
    print("[2] 按文件分布（同步数 = 该族的令牌需求）")
    by_file: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_file[r["file"]][
            "async" if r["async"] else "sync"
        ] += 1
    for f, c in sorted(by_file.items(), key=lambda kv: -kv[1]["sync"])[:18]:
        print(f"  {f:<34} sync={c['sync']:<4} async={c['async']}")

    print("\n" + "=" * 78)
    print("[3] D1 证据链点名接口的形状（决定归因成立与否）")
    by_path: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_path[(r["file"], r["path"])].append(r)
    cheap_shape: dict[tuple[str, str], str] = {}
    for fname, _meth, path in CHEAP_ENDPOINTS:
        hits = by_path.get((fname, path)) or []
        if not hits:
            print(f"  [廉价] {fname}{path:<18} 未找到（可能挂在别的 router 上）")
            continue
        for r in sorted(hits, key=lambda x: x["method"]):
            lane = "async（不占令牌）" if r["async"] else "SYNC（占令牌）"
            print(f"  [廉价] {r['method']:<6} {path:<16} -> {lane:<20} handler={r['handler']}")
        # 压测打的是读侧 GET；无 GET 时退回该路径的第一条
        row = next((x for x in hits if x["method"] == "GET"), hits[0])
        cheap_shape[(fname, path)] = "async" if row["async"] else "sync"
    slow_sync = [r for r in rows
                 if not r["async"] and any(s in r["file"].lower() for s in SLOW_FILE_MARKERS)]
    print(f"  [慢族]  同步处理器 {len(slow_sync)} 个，见下：")
    for r in sorted(slow_sync, key=lambda x: (x["file"], x["path"]))[:40]:
        print(f"     {r['file']:<28} {r['method']:<6} {r['path']}")

    print("\n" + "=" * 78)
    print("[4] 判读")
    if not cheap_shape:
        print("  点名接口一条都没定位到，无法判形状 —— 先修清点脚本再谈归因。")
        return 1
    cheap_all_async = all(v == "async" for v in cheap_shape.values())
    if cheap_all_async:
        print("  廉价接口全是 async def → 它们不等线程令牌，D1 的「排队等线程」不成立，须改归因。")
    else:
        print("  至少一条廉价接口是同步 def → 它与慢接口抢同一个池，D1 的形状前提成立，")
        print("  但收益仍需隔离实验（d1_lane_probe.py）证明，不能由形状直接外推延迟数字。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
