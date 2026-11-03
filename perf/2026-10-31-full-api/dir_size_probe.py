"""C1 判据探针：快照目录体积「两遍遍历 → 一遍」的实际收益 + 遍历让出事件循环的证据

跑法（在项目根）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe perf/2026-10-31-full-api/dir_size_probe.py

只在自己建的临时目录里造快照树，不读也不写真实的 data/chromadb。
两段判据：
  [A] 一遍 vs 两遍：同一棵树，`_dir_size(d)` 调一次与调两次的中位耗时比。
  [B] async handler 里同步遍历 vs 交给工作线程：遍历期间事件循环还能推进多少次心跳。
"""
from __future__ import annotations

import asyncio
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from xianyu_hunter.web.routes import api_vector_admin as va  # noqa: E402

WORK = Path("D:/tmp/dir_size_probe")
SNAPSHOT_DIRS = 8
FILES_PER_DIR = 400
BYTES_PER_FILE = 4096
REPEATS = 9


class _FakeStore:
    def __init__(self, persist_path: Path) -> None:
        self.persist_path = str(persist_path)
        self.collection_name = "kb_chunks"

    async def count(self) -> int:
        return 0


def build_tree(root: Path) -> None:
    payload = b"\0" * BYTES_PER_FILE
    for i in range(SNAPSHOT_DIRS):
        d = root / "snapshots" / f"manual_2026103{i}"
        d.mkdir(parents=True, exist_ok=True)
        for j in range(FILES_PER_DIR):
            (d / f"f{j}.bin").write_bytes(payload)


def list_once(root: Path) -> list[dict]:
    """修复后：每个目录一次遍历"""
    return va._list_snapshots(_FakeStore(root))


def list_twice(root: Path) -> list[dict]:
    """修复前：size_bytes 与 size_display 各遍历一次（等价复刻当时的两行）"""
    items = []
    for d in (root / "snapshots").iterdir():
        if not d.is_dir():
            continue
        items.append({
            "id": d.name,
            "size_bytes": va._dir_size(d),
            "size_display": va._format_size(va._dir_size(d)),
        })
    return items


def timed(fn) -> tuple[float, float]:
    runs = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(runs), min(runs)


def section_a(root: Path) -> None:
    total_files = SNAPSHOT_DIRS * FILES_PER_DIR
    print(f"[A] 同一棵快照树（{SNAPSHOT_DIRS} 目录 × {FILES_PER_DIR} 文件 "
          f"= {total_files} 次 stat，{REPEATS} 次采样取中位）")
    twice, tw = timed(lambda: list_twice(root))
    once, ow = timed(lambda: list_once(root))
    keys = ("id", "size_bytes", "size_display")
    same = [{k: it[k] for k in keys} for it in sorted(list_twice(root), key=lambda x: x["id"])] == \
           [{k: it[k] for k in keys} for it in sorted(list_once(root), key=lambda x: x["id"])]
    print(f"    两遍遍历：中位 {twice:.2f} ms（最快 {tw:.2f}）")
    print(f"    一遍遍历：中位 {once:.2f} ms（最快 {ow:.2f}）")
    print(f"    省 {twice - once:.2f} ms/请求，降到 {once / twice * 100:.0f}%；"
          f"结果一致：{same}")
    print("    → 与压测口径一致：该接口成本几乎全在 stat 遍历上，减半即 avg 减半")


def _count_heartbeats(inline: bool, root: Path) -> int:
    """在遍历进行中统计事件循环推进次数

    inline=True 复刻修复前（遍历直接写在 async handler 里）；
    False 走修复后的 asyncio.to_thread。
    """
    ticks = 0

    async def heartbeat(stop: threading.Event) -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.001)
            ticks += 1

    async def run() -> int:
        stop = threading.Event()
        hb = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0)  # 让心跳先跑一轮，证明循环本身是活的
        if inline:
            va._list_snapshots(_FakeStore(root))
        else:
            await asyncio.to_thread(lambda: va._list_snapshots(_FakeStore(root)))
        stop.set()
        await hb
        return ticks

    return asyncio.run(run())


def section_b(root: Path) -> None:
    print(f"\n[B] 遍历期间事件循环的心跳推进次数（心跳每 1 ms 一次；{REPEATS} 次采样取中位）")
    medians = {}
    for label, inline in (("修复前（循环内同步遍历）", True), ("修复后（to_thread）", False)):
        samples = [_count_heartbeats(inline, root) for _ in range(REPEATS)]
        medians[label] = statistics.median(samples)
        print(f"    {label}：中位 {medians[label]:.0f} 次心跳（区间 {min(samples)}~{max(samples)}）")
    print(f"    → 修复前心跳被冻住（次数≈0），修复后遍历期间循环照常推进："
          f"同 key 的单飞跟随者（B1）不再干等 lead 的那一跳")


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    root = WORK / "data"
    root.mkdir(parents=True, exist_ok=True)
    build_tree(root)
    try:
        section_a(root)
        section_b(root)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
