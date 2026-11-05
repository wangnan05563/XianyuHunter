"""D2 取证 · cache_scan 那把进程级 RLock 到底串行了什么

OPTIMIZATIONS §D2 原文说：`CacheScanService.snapshot()` 对**所有路径**互斥，
「改为按扫描路径分锁」即可让 `/cache-scan` 与 `/maintenance/status` 不再互相阻塞。
先把四件事分开量，否则很容易把一个「听着对」的改法落下去却买不到东西：

- **调用点清点**（静态）：三个调用点（/status、/cache-scan、清理预览）传的到底是几个路径？
  若只有一个路径，「按路径分锁」在真实负载里等价于没改 —— 串行的不是路径，是**预算片**。
- **锁的持有形状**：`_advance()` 在锁内走完整个预算片，于是迟到的调用排的不是「CPU 的队」，
  是「前一个预算片的墙钟」。这条才是 96 并发下 avg 1 822.8 ms、60 s 窗口只完成 6 次的机制。
- **候选 A（推进不持锁 + 同键单推进者）**：迟到者立刻回当前进度。省掉等待，但代价要量 ——
  「来的人手」被浪费了：一波 K 个到达只干出一片，扫完需要的波数会变成 K 倍。
- **候选 B（待扫目录当共享队列）**：锁只护「取一个目录」和「记一笔」，目录内遍历在锁外。
  人手不浪费，也没人排队。顺带都解决了原文关心的跨路径并行，但机制都不是「分锁」。
  B 能不能真的并行加速另说（遍历是 GIL 敏感的），探针按「每秒推进的目录数」直接量。

预算片必须**真的被走完**才看得到锁的墙钟：所以先一次性测出全树耗时，再取其 1/20 作预算，
让「一片 ≈ 几个目录」，波数才有分辨率（对应生产里 0.5 s 预算打在 426 MB profile 上）。
两个候选都验「终值与一次性遍历逐字段相同」与「并发推进不重复计数」，否则只是把等待换成了错数。

合成树跑在带 PID 的临时目录里（固定名会被并发探针互相 rmtree —— 上一轮的事故），
不读任何真实浏览器目录、不读 cookie 文件、不涉及任何账号标识。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import threading
import time
from pathlib import Path

from xianyu_hunter.web.services.cache_scan import CacheScanService, _Scan

REPO = Path(__file__).resolve().parents[2]

DIRS = 60
FILES_PER_DIR = 300
FILE_BYTES = 1024


def build_tree(root: Path, dirs: int = DIRS) -> dict:
    """造一棵 `dirs` 个目录 × 每目录 FILES_PER_DIR 个文件的合成树，返回一次性遍历的终值。"""
    total = 0
    files = 0
    for d in range(dirs):
        sub = root / f"d{d}"
        sub.mkdir(parents=True, exist_ok=True)
        for f in range(FILES_PER_DIR):
            (sub / f"f{f}.bin").write_bytes(b"\0" * FILE_BYTES)
            total += FILE_BYTES
            files += 1
    return {
        "mb": round(total / 1024 / 1024, 2),
        "files": files,
        # 根下 dirs 个目录项 + 每个目录 FILES_PER_DIR 个文件项；根自身不计入条目
        "entries_total": dirs + files,
    }


class _CountedCurrent(CacheScanService):
    """行为与生产实现完全一致，只是多记一个「这片真干了活」的计数 —— 用于算人手利用率。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.slices = 0
        self.waiters_short_circuited = 0

    def _advance(self, scan: _Scan, budget: float) -> None:
        self.slices += 1
        super()._advance(scan, budget)


class _NonBlockingAdvance(CacheScanService):
    """候选 A：锁只护状态表与快照读取，预算片在锁外走，同键同时只有一个推进者。

    继承生产类，复用 `_advance` / `_describe`，因此这里测的是「谁在等」的形状变了，
    不是把遍历换成别的算法。
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._advancing: set[str] = set()
        self.slices = 0
        self.waiters_short_circuited = 0

    def snapshot(self, path: Path, *, budget: float | None = None, advance: bool = True) -> dict:
        key = str(path)
        with self._lock:
            scan = self._scans.get(key)
            stale = (
                scan is not None
                and scan.done
                and time.time() - (scan.finished_at or 0) > self._ttl
            )
            # 推进者还在场上时不重建：否则会把正在写的那个对象从表里摘掉
            if stale and key not in self._advancing:
                scan = _Scan(stack=[key])
                self._scans[key] = scan
            if scan is None:
                scan = _Scan(stack=[key])
                self._scans[key] = scan
            if not advance or scan.done:
                return self._describe(key, scan)
            if key in self._advancing:
                self.waiters_short_circuited += 1
                return self._describe(key, scan)
            self._advancing.add(key)
        try:
            self.slices += 1
            self._advance(scan, self._budget if budget is None else budget)
        finally:
            with self._lock:
                self._advancing.discard(key)
        with self._lock:
            return self._describe(key, scan)


class _WorkSharingAdvance(CacheScanService):
    """候选 B：待扫目录＝共享队列，锁只护取活与计数，目录内遍历在锁外，多个调用方一起推进。

    两条不变量（B 必须守住，否则只是把「串行」改成「并发错乱」）：
    - 一个目录只会被一个调用方取走（pop 在锁内），因此条目不会重复计数；
    - 只有「队列空 且 没有调用方正走在目录里」才算 done，
      否则最后一个目录还没展开的子目录会被漏掉，而状态已经写成 done。
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.slices = 0
        self.waiters_short_circuited = 0

    def snapshot(self, path: Path, *, budget: float | None = None, advance: bool = True) -> dict:
        key = str(path)
        with self._lock:
            scan = self._scans.get(key)
            stale = (
                scan is not None
                and scan.done
                and time.time() - (scan.finished_at or 0) > self._ttl
            )
            if scan is None or stale:
                scan = _Scan(stack=[key])
                self._scans[key] = scan
            if not advance or scan.done:
                return self._describe(key, scan)
        self._share(scan, self._budget if budget is None else budget)
        with self._lock:
            return self._describe(key, scan)

    def _share(self, scan: _Scan, budget: float) -> None:
        self.slices += 1
        deadline = time.monotonic() + max(budget, 0.0)
        while time.monotonic() < deadline:
            with self._lock:
                if not scan.stack:
                    if getattr(scan, "active", 0) == 0 and not scan.done:
                        scan.finished_at = time.time()
                    return
                current = scan.stack.pop()
                scan.active = getattr(scan, "active", 0) + 1
            subdirs: list[str] = []
            n_entries = n_files = n_bytes = 0
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        n_entries += 1
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                n_bytes += entry.stat(follow_symlinks=False).st_size
                                n_files += 1
                        except OSError:
                            continue
            except OSError:
                pass
            finally:
                with self._lock:
                    scan.active -= 1
            with self._lock:
                scan.scanned_entries += n_entries
                scan.files += n_files
                scan.total_bytes += n_bytes
                scan.stack.extend(subdirs)
                if not scan.stack and scan.active == 0 and not scan.done:
                    scan.finished_at = time.time()


def run_concurrent(service, roots: list[Path], per_root: int, budget: float) -> dict:
    """K 个线程同时发 snapshot()，记录每次调用的墙钟、终态、干了活的手数与「被立刻放回」的次数。"""
    lat: list[float] = []
    states: list[str] = []
    lock = threading.Lock()
    plan = [roots[i % len(roots)] for i in range(per_root * len(roots))]
    gate = threading.Barrier(len(plan))
    slices_before = getattr(service, "slices", 0)

    def worker(p: Path) -> None:
        gate.wait()
        t0 = time.perf_counter()
        snap = service.snapshot(p, budget=budget)
        dt = (time.perf_counter() - t0) * 1000.0
        with lock:
            lat.append(dt)
            states.append(snap["state"])

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(p,)) for p in plan]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = (time.perf_counter() - t0) * 1000.0

    ordered = sorted(lat)
    return {
        "calls": len(lat),
        "slices_executed": getattr(service, "slices", 0) - slices_before,
        "total_wall_ms": round(wall, 1),
        "avg_ms": round(statistics.fmean(lat), 1),
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        "max_ms": round(ordered[-1], 1),
        "done_states": states.count("done"),
        "scanning_states": states.count("scanning"),
        "short_circuited": getattr(service, "waiters_short_circuited", 0),
    }


def one_shot_ms(root: Path) -> float:
    """一次性全扫（预算给足）的墙钟 —— 用来把预算片定成它的 1/N。"""
    t0 = time.perf_counter()
    CacheScanService().snapshot(root, budget=600.0)
    return (time.perf_counter() - t0) * 1000.0


def run_waves_until_done(factory, root: Path, k: int, budget: float, max_sec: float) -> dict:
    """按「一波 K 个并发到达」反复推进直到看到 done —— 波数是整数，不受 sleep 调度噪声支配。

    这条同时回答两个相反方向的问题：
    - 每波墙钟（用户等多久）；
    - 到 done 要几波、总墙钟多少（改法有没有把「扫完」这件事拖慢，即浪费人手）。
    """
    service = factory()
    wave_wall: list[float] = []
    calls = slices = 0
    t0 = time.perf_counter()
    finished = False
    while True:
        res = run_concurrent(service, [root], k, budget)
        wave_wall.append(res["total_wall_ms"])
        calls += res["calls"]
        slices += res["slices_executed"]
        if res["done_states"] > 0:
            finished = True
            break
        if time.perf_counter() - t0 > max_sec:
            break
    wall = (time.perf_counter() - t0) * 1000.0
    ordered = sorted(wave_wall)
    return {
        "callers_per_wave": k,
        "waves_to_done": len(wave_wall),
        "reached_done": finished,
        "wall_to_done_ms": round(wall, 1),
        "wave_avg_ms": round(statistics.fmean(wave_wall), 1),
        "wave_p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        "calls": calls,
        "slices_executed": slices,
        "hands_used_ratio": round(slices / calls, 2) if calls else 0,
    }


def final_values(service, root: Path, budget: float = 5.0) -> dict:
    """反复推进到 done，取终值 —— 用来对正确性做逐字段比较。"""
    for _ in range(2000):
        snap = service.snapshot(root, budget=budget)
        if snap["state"] == "done":
            return {k: snap[k] for k in ("mb", "files", "scanned_entries", "state")}
    raise AssertionError("2000 片仍未扫完")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=8, help="同路径并发到达数（一波的人数）")
    ap.add_argument("--slice-div", type=int, default=20,
                    help="预算片 = 一次性全扫耗时 / 该值（越大片越小、波数越多）")
    ap.add_argument("--budget", type=float, default=0.0, help="手动指定预算片（秒），0=自动")
    ap.add_argument("--rounds", type=int, default=2, help="重复整轮的轮数（判方向是否同向）")
    args = ap.parse_args()

    scratch = REPO / "perf" / "2026-10-31-full-api" / f"_scratch_d2_lock_{os.getpid()}"
    out: dict = {"k": args.k, "tree": None, "rounds": []}
    if scratch.exists():
        shutil.rmtree(scratch)
    try:
        root = scratch / "tree"
        root.mkdir(parents=True)
        ref = build_tree(root)
        root2 = scratch / "tree2"
        root2.mkdir()
        build_tree(root2)
        one_shot = one_shot_ms(root)
        budget = args.budget if args.budget > 0 else round(one_shot / args.slice_div / 1000.0, 5)
        out["tree"] = {
            "dirs": DIRS, "files_per_dir": FILES_PER_DIR,
            "reference": ref, "one_shot_ms": round(one_shot, 1),
            "slice_budget_sec": budget, "slice_div": args.slice_div,
        }
        print(f"[树] {DIRS}×{FILES_PER_DIR} 文件，一次性全扫 {one_shot:.1f} ms，"
              f"预算片 = 全扫/{args.slice_div} = {budget} s")
        print(f"[参照] 终值 mb={ref['mb']} files={ref['files']} entries={ref['entries_total']}")

        impls = (
            ("现状", _CountedCurrent),
            ("A·单推进者", _NonBlockingAdvance),
            ("B·共享队列", _WorkSharingAdvance),
        )
        for rnd in range(1, args.rounds + 1):
            round_out: dict = {"round": rnd, "arms": []}
            for label, factory in impls:
                # 同一路径并发：真实负载的形状（/status、/cache-scan、预览都传 _browser_data_dir()）
                same = run_concurrent(factory(), [root], args.k, budget)
                # 两路径并发：D2 原文处方（按路径分锁）真正针对的形状
                split = run_concurrent(factory(), [root, root2], args.k // 2, budget)
                # 波数到 done：改法有没有把「扫完」拖慢（浪费人手）
                waves = run_waves_until_done(factory, root, args.k, budget, 30.0)
                for kind, arm in (("同路径", same), ("两路径", split), ("到done波数", waves)):
                    arm["arm"] = f"{label}·{kind}"
                    round_out["arms"].append(arm)
                    if "waves_to_done" in arm:
                        print(f"[R{rnd} {arm['arm']:<16}] 波数={arm['waves_to_done']:<3} "
                              f"每波avg={arm['wave_avg_ms']:<8} 每波p95={arm['wave_p95_ms']:<8} "
                              f"扫完墙钟={arm['wall_to_done_ms']:<9} "
                              f"干了活的手/调用={arm['hands_used_ratio']}")
                    else:
                        print(f"[R{rnd} {arm['arm']:<16}] calls={arm['calls']:<3} "
                              f"这片={arm['slices_executed']:<3} p50={arm['p50_ms']:<7} "
                              f"p95={arm['p95_ms']:<7} max={arm['max_ms']:<8} "
                              f"总墙钟={arm['total_wall_ms']:<8} "
                              f"done/scanning={arm['done_states']}/{arm['scanning_states']} "
                              f"立刻放回={arm['short_circuited']}")
            out["rounds"].append(round_out)

        # 遍历吞吐：单线程一片能走几个目录 —— 用来看 B 的多线程是否真买到并行
        throughput = {}
        for label, factory in impls:
            svc = factory()
            run_concurrent(svc, [root], 1, budget)
            throughput[label] = {
                "slices": svc.slices,
                "entries_scanned": svc.snapshot(root, advance=False)["scanned_entries"],
            }
        out["throughput_one_call"] = throughput
        print(f"[单到达吞吐] {json.dumps(throughput, ensure_ascii=False)}")

        # —— 正确性 1：推进到 done 的终值必须与一次性参照逐字段相同
        # —— 正确性 2：并发推进同一棵树不得重复计数（共享队列失守则 scanned_entries 超条目上限）
        out["correctness"] = {
            "reference": {k: ref[k] for k in ("mb", "files", "entries_total")},
        }
        for label, factory in impls[1:]:
            solo = final_values(factory(), root)
            race_service = factory()
            run_concurrent(race_service, [root], args.k, budget)
            race = final_values(race_service, root)
            out["correctness"][label] = {
                "solo_final": solo,
                "solo_identical": solo["mb"] == ref["mb"] and solo["files"] == ref["files"],
                "race_final": race,
                "race_over_counted": race["scanned_entries"] > ref["entries_total"],
                "race_identical": race["mb"] == ref["mb"] and race["files"] == ref["files"],
            }
            print(f"[正确性 {label}] 单推进终值={solo} identical="
                  f"{out['correctness'][label]['solo_identical']} | 并发后终值={race} "
                  f"identical={out['correctness'][label]['race_identical']} "
                  f"over_counted={out['correctness'][label]['race_over_counted']}"
                  f"（条目上限 {ref['entries_total']}）")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    dest = REPO / "perf" / "2026-10-31-full-api" / "results" / "d2_lock_probe.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物 → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
