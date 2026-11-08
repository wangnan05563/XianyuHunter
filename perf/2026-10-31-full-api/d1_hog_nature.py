"""D1 取证 · 第三层：真实慢接口的「性质」定价（决定隔离名单收谁）

四臂实验（`d1_lane_probe.py`）给出两个结论：
- 88 个 **IO 型** hog 打满 40 令牌 → 廉价接口 p50 2 226/2 235 ms、窗口完成数 1 083 → 16（与压测同形）；
- 40 个 **CPU 型** hog 连令牌都没占满（峰值借用 9~13/40）就能把廉价接口推到 6.9~10.7 s ——
  红的原因不是「等线程」，是 GIL 与事件循环被拖住。

两者的药不同：令牌隔离对 IO 型是「别占坑」，对 CPU 型是「别同时转」。
所以落地前必须知道真实慢接口属于哪一类 —— 这决定隔离名单收哪几族、上限给多少。

主判据用最直接的那个量：**同一目标在无陪跑线程时的 CPU 时间 / wall 时间**。
忙等 → ≈1；`time.sleep`/系统调用/等待远端 → ≪1。
第二判据是常驻纯 Python 计数线程的速率比（目标运行期 / 空载基线），只作佐证 ——
本机实测它分辨力有限（忙等 0.54 vs sleep 0.72，差不到 1.4 倍），
原因是 5 ms 的 GIL 切换粒度让两个线程都拿到大致均分的时间片；
所以判读一律以 CPU/wall 为准，比值列出来是为了让「等待时 GIL 确实被让出」这件事有独立证据。

口径：走盘原语指向**真实仓库树**（/status、/snapshots 的实际成本来源，只读）；
DB 侧用生产库的一致性快照副本（sqlite backup API），表名与文本列按副本现场发现，
真实库文件从不被打开、也不写。不启动调度器/浏览器，不读取任何 cookie 文件。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sqlite3
import statistics
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(r"D:/tmp/xh-d1-hog")
OUT_DIR = REPO / "perf" / "2026-10-31-full-api" / "results"

CPU_ANCHOR_IS_FULL_SCALE = 0.596  # 本机忙等锚点的实测值；见下方 classify 的说明
IO_ANCHOR_IS_ZERO = 0.0
CPU_KIND_REL = 0.8     # 相对锚点 ≥0.8 → CPU 型（还抢 GIL）
IO_KIND_REL = 0.35     # 相对锚点 ≤0.35 → IO 型（只吃令牌）
SAMPLE_WINDOW = 1.0    # 每个目标采满多久：把 15.6 ms 的 CPU 时钟量化误差压到 <2%
ANCHOR_WINDOW = 0.05   # 锚点单次时长（会被循环采满 SAMPLE_WINDOW）


class Ticker:
    """一次性纯 Python 计数线程：目标运行期间 GIL 是否被让出，由它的速率佐证。"""

    def __init__(self) -> None:
        self.n = 0
        self._stop = False
        self._th = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop:
            self.n += 1

    def __enter__(self) -> "Ticker":
        self._stop = False
        self._th.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop = True
        self._th.join(timeout=2.0)

    def baseline(self, seconds: float = 0.20) -> float:
        a, t0 = self.n, time.perf_counter()
        time.sleep(seconds)
        return (self.n - a) / max(time.perf_counter() - t0, 1e-9)

    def rate_around(self, fn) -> tuple[object, float, float]:
        a, t0 = self.n, time.perf_counter()
        rv = fn()
        wall_ms = (time.perf_counter() - t0) * 1000.0
        return rv, wall_ms, (self.n - a) / max(wall_ms / 1000.0, 1e-9)


def measure(label: str, fn, iters: int) -> dict:
    """采满 SAMPLE_WINDOW 再算比值。

    本机 `time.process_time()` 以 15.6 ms（1/64 s）为步长 —— 所有读数都是它的整数倍。
    200 ms 的窗口只能容下 ~13 个格子，忙等锚点都会被量成 0.39；
    把窗口拉到 1 s，量化误差压到 <2%，cpu/wall 才有判据资格。
    """
    cpu_ratios, tick_ratios, per_call = [], [], []
    for _ in range(iters):
        # 相一：不启陪跑线程，process_time 就是目标自己的 CPU
        n = 0
        t0, c0 = time.perf_counter(), time.process_time()
        while time.perf_counter() - t0 < SAMPLE_WINDOW:
            fn()
            n += 1
        wall = time.perf_counter() - t0
        cpu = time.process_time() - c0
        cpu_ratios.append(cpu / wall if wall else None)
        per_call.append(wall * 1000.0 / max(n, 1))
    # 相二：带陪跑线程，取 ticker 速率比（佐证，同样采满 1 s）
    with Ticker() as tick:
        rate_base = tick.baseline()
        n = 0
        a, t0 = tick.n, time.perf_counter()
        while time.perf_counter() - t0 < SAMPLE_WINDOW:
            fn()
            n += 1
        wall2 = time.perf_counter() - t0
        rate_during = (tick.n - a) / max(wall2, 1e-9)
        tick_ratios.append(rate_during / rate_base)
    return {
        "label": label,
        "per_call_ms": round(statistics.median(per_call), 3),
        "cpu_over_wall": round(statistics.median(cpu_ratios), 3),
        "ticker_ratio": round(statistics.median(tick_ratios), 3),
    }


def classify(r: dict, anchors: dict) -> str:
    """按**锚点相对位置**分档，不用绝对阈值。

    本机 `time.process_time()` 走 `GetProcessTimes()`，实测 2 s 纯忙等只记 1.02 s
    （ratio 0.507；本探针忙等锚点 0.596）—— 满刻度不是 1，而是锚点本身。
    所以「是不是 CPU 型」只能相对锚点回答。
    """
    if r["label"].startswith("锚点"):
        return "锚点"
    c = r["cpu_over_wall"]
    if c is None:
        return "不可信"
    cpu_full = anchors.get("锚点·纯 Python 忙等", {}).get("cpu_over_wall") or CPU_ANCHOR_IS_FULL_SCALE
    rel = c / cpu_full
    if rel >= CPU_KIND_REL:
        return f"CPU 型（占令牌且抢 GIL，锚点的 {rel:.2f}×）"
    if rel <= IO_KIND_REL:
        return f"IO 型（占令牌、让 GIL，锚点的 {rel:.2f}×）"
    return f"混合型（锚点的 {rel:.2f}×）"


def discover_text_col(conn: sqlite3.Connection, table: str) -> str | None:
    # PRAGMA table_info 每行是 (cid, name, type, notnull, dflt_value, pk)
    for info in conn.execute(f'PRAGMA table_info("{table}")'):
        ctype = str(info[2]).upper()
        if "CHAR" in ctype or "TEXT" in ctype:
            return info[1]
    return None


def biggest_tables(conn: sqlite3.Connection, n: int = 4) -> list[tuple[str, int]]:
    names = [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    counts = [(t, conn.execute(f'select count(*) from "{t}"').fetchone()[0]) for t in names]
    return sorted(counts, key=lambda x: -x[1])[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=3)
    args = ap.parse_args()

    os.environ.setdefault("XH_WITH_SCHEDULER", "0")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "config").mkdir(parents=True)
    for f in ("config.yaml", "menu_registry.yaml", "eval.yaml", "param_rules.yaml"):
        src = REPO / "config" / f
        if src.exists():
            shutil.copy2(src, SCRATCH / "config" / f)
    (SCRATCH / "data").mkdir()
    with sqlite3.connect(str(REPO / "data" / "xianyu.db")) as s, \
            sqlite3.connect(str(SCRATCH / "data" / "xianyu.db")) as d:
        s.backup(d)
    db = str(SCRATCH / "data" / "xianyu.db")

    rows: list[dict] = []

    def add(name: str, fn) -> dict:
        r = measure(name, fn, args.iters)
        rows.append(r)
        print(f"  {name:<40} per_call={r['per_call_ms']:>9} ms  "
              f"cpu/wall={r['cpu_over_wall']}  ticker={r['ticker_ratio']}")
        return r

    print("=" * 92)
    print("[D1 性质定价] 先看两个锚点定标尺：忙等 cpu/wall≈1，sleep≈0")
    print("=" * 92)

    def busy_wait() -> int:
        end = time.perf_counter() + ANCHOR_WINDOW
        x = 0
        while time.perf_counter() < end:
            x += 1
        return x

    add("锚点·纯 Python 忙等", busy_wait)
    add("锚点·time.sleep 等待", lambda: time.sleep(ANCHOR_WINDOW))

    print("\n" + "=" * 92)
    print("[走盘族] maintenance/status、vector-admin/snapshots 的成本主体（真实仓库树，只读）")
    print("=" * 92)
    target_tree = REPO / "backend" / "xianyu_hunter"

    def rglob_scan() -> int:
        return sum(1 for _ in target_tree.rglob("*"))

    def dir_size_style() -> int:
        total = 0
        for root, dirs, files in os.walk(target_tree):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
        return total

    add("rglob 全树枚举（vector-admin 同形）", rglob_scan)
    add("_dir_size 同形遍历（含逐文件 stat）", dir_size_style)

    print("\n" + "=" * 92)
    print("[DB 族] logs/search 的 LIKE、db-admin export 的全表读")
    print("=" * 92)
    with sqlite3.connect(db) as conn:
        tops = biggest_tables(conn, 4)
        print(f"  快照里最大的表：{tops}")
        big_table, big_rows = tops[0]
        big_col = discover_text_col(conn, big_table)

    def like_scan() -> list:
        with sqlite3.connect(db) as c:
            return c.execute(
                f'select * from "{big_table}" where "{big_col}" like \'%a%\' '
                f'order by rowid desc limit 200').fetchall()

    def full_read() -> list:
        with sqlite3.connect(db) as c:
            return c.execute(f'select * from "{big_table}" limit 5000').fetchall()

    add(f"SQLite LIKE 全扫 {big_table}({big_rows} 行, {big_col})", like_scan)
    add(f"SQLite 顺序取 5000 行（{big_table}）", full_read)

    print("\n" + "=" * 92)
    print("[序列化族] 导出/列表接口取数之后还要做的纯 Python 功")
    print("=" * 92)

    def csv_dump() -> str:
        with sqlite3.connect(db) as c:
            cur = c.execute(f'select * from "{big_table}" limit 5000')
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur)
        return buf.getvalue()

    def json_dump() -> str:
        with sqlite3.connect(db) as c:
            cur = c.execute(f'select * from "{big_table}" limit 5000')
            cols = [d[0] for d in cur.description]
            return json.dumps([dict(zip(cols, r)) for r in cur], default=str)

    add("CSV 序列化 5000 行", csv_dump)
    add("JSON 序列化 5000 行（响应体同形功）", json_dump)

    anchors = {r["label"]: r for r in rows if r["label"].startswith("锚点")}
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "d1_hog_nature.json"
    out_path.write_text(json.dumps({"anchors": anchors, "targets": rows,
                                    "thresholds": {"cpu_rel": CPU_KIND_REL, "io_rel": IO_KIND_REL,
                                    "anchor_full_scale": CPU_ANCHOR_IS_FULL_SCALE}},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 92)
    print(f"[判读] 以忙等锚点为满刻度：相对 ≥{CPU_KIND_REL} → CPU 型；≤{IO_KIND_REL} → IO 型；其间 → 混合型")
    print("=" * 92)
    for r in rows:
        print(f"  {r['label']:<38} cpu/wall={r['cpu_over_wall']} → {classify(r, anchors)}")
    print(f"\n[落盘] {out_path}")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
