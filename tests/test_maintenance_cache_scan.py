"""缓存统计的增量扫描（T02738 / PRD 5.1 派生单）

背景：`GET /api/maintenance/status` 与缓存预览原来都在请求线程里全量遍历浏览器
profile（实测 2073 文件 / 426MB，磁盘争用下单次 449.5s）。本文件锁住四件事：

1. 分批可续跑：预算耗尽时在**目录之间**停住，下次从中断处继续且不重复计数；
2. 秒级回包：/status 与预览在预算很小也立即返回 state=scanning，不是空白等待；
3. 终值一致：扫完后 MB/文件数与一次性遍历完全相同；删除后进度作废重扫；
4. 一片不挡别人（性能线 §D2）：时间片在锁外走，迟到的调用回当前进度而不是排队，
   作废/清理不被在飞的那一片卡住，同一路径同时只有一个推进者。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from xianyu_hunter.web.routes import api_maintenance
from xianyu_hunter.web.routes.api_maintenance import CleanupRequest
from xianyu_hunter.web.services import cache_scan as cache_scan_module
from xianyu_hunter.web.services.cache_scan import CacheScanService


class _RecordingRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def save_event(self, payload):  # 审计写入只用于断言 pending 不落库
        self.events.append(payload)


class _StubContainer:
    def __init__(self) -> None:
        self.repo = _RecordingRepo()


def _request(user_id: str = "default"):
    return SimpleNamespace(state=SimpleNamespace(user_id=user_id))


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    profile = tmp_path / "browser-data"
    (profile / "Default" / "Network").mkdir(parents=True)
    (profile / "Default" / "Network" / "Cookies").write_bytes(b"x" * 1_572_864)
    (profile / "Local State").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(api_maintenance, "_browser_data_dir", lambda: profile)
    # 每个测试一份独立进度，避免模块级单例把状态带到别的用例
    monkeypatch.setattr(api_maintenance, "_cache_scan", CacheScanService())
    return profile


# ============== 服务层：分批推进与终值 ==============
def test_small_tree_finishes_in_one_snapshot(tmp_path):
    root = tmp_path / "tree"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f1").write_bytes(b"x" * 1_048_576)
    (root / "f2").write_bytes(b"y" * 2_097_152)

    snap = CacheScanService().snapshot(root, budget=5.0)
    assert snap["state"] == "done"
    assert snap["files"] == 2
    assert snap["mb"] == 3.0


def test_budget_cut_between_directories_resumes_without_double_counting(tmp_path, monkeypatch):
    """预算在目录之间切断：下次继续，累计条目数不能大于真实条目数"""
    root = tmp_path / "tree"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / f"{name}.bin").write_bytes(b"x" * 1024)

    service = CacheScanService()
    clock = iter([0.0, 0.0, 100.0])

    class _Clock:
        def monotonic(self):
            return next(clock, 101.0)

    monkeypatch.setattr(
        cache_scan_module, "time",
        SimpleNamespace(monotonic=_Clock().monotonic, time=time.time),
    )

    first = service.snapshot(root, budget=10.0)
    assert first["state"] == "scanning"
    # 根目录已完整走完（2 个子目录记入条目），子目录尚未进入，文件数仍为 0
    assert first["scanned_entries"] == 2
    assert first["files"] == 0

    second = service.snapshot(root, budget=10.0)
    assert second["state"] == "done"
    assert second["files"] == 2
    # 根 2 个目录项 + 两个子目录各 1 个文件 = 4；根目录没有被重复扫描
    assert second["scanned_entries"] == 4


def test_budget_zero_returns_immediately_as_scanning(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f").write_bytes(b"x")

    snap = CacheScanService().snapshot(root, budget=0.0)
    assert snap["state"] == "scanning"
    assert snap["scanned_entries"] == 0


def test_done_result_is_reused_until_ttl_then_rescanned(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f1").write_bytes(b"x" * 1024)

    # 负 TTL 让「已完成即过期」成立，避免 0 秒 TTL 受同一次时钟滴答影响而偶发不重扫
    service = CacheScanService(ttl_sec=-1.0)
    assert service.snapshot(root, budget=5.0)["files"] == 1

    (root / "f2").write_bytes(b"x" * 1024)
    assert service.snapshot(root, budget=5.0)["files"] == 2

    service_long_ttl = CacheScanService(ttl_sec=300.0)
    assert service_long_ttl.snapshot(root, budget=5.0)["files"] == 2
    (root / "f3").write_bytes(b"x" * 1024)
    assert service_long_ttl.snapshot(root, budget=5.0)["files"] == 2


def test_invalidate_forces_rescan(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f1").write_bytes(b"x" * 1024)

    service = CacheScanService()
    assert service.snapshot(root, budget=5.0)["files"] == 1
    (root / "f2").write_bytes(b"x" * 1024)
    service.invalidate(root)
    assert service.snapshot(root, budget=5.0)["files"] == 2


def test_advance_false_does_not_consume_budget(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f").write_bytes(b"x")

    service = CacheScanService()
    assert service.snapshot(root, budget=5.0, advance=False)["state"] == "scanning"
    assert service.snapshot(root, budget=5.0)["state"] == "done"


# ============== 路由层：/status、预览、/cache-scan ==============
def test_status_returns_scanning_snapshot_without_blocking(profile_dir, monkeypatch):
    monkeypatch.setattr(api_maintenance, "_iter_pycache_dirs", lambda *a, **k: [])
    monkeypatch.setattr(api_maintenance, "STATUS_SCAN_BUDGET_SEC", 0.0)

    status = api_maintenance.maintenance_status.__wrapped__(container=_StubContainer())
    cache = status["cache"]
    assert cache["browser_data_scan"]["state"] == "scanning"
    assert cache["browser_data_files"] == 0
    assert cache["browser_data_path"] == str(profile_dir)


def test_status_reports_scan_result_once_done(profile_dir, monkeypatch):
    monkeypatch.setattr(api_maintenance, "_iter_pycache_dirs", lambda *a, **k: [])

    status = api_maintenance.maintenance_status.__wrapped__(container=_StubContainer())
    cache = status["cache"]
    assert cache["browser_data_scan"]["state"] == "done"
    assert cache["browser_data_files"] == 2
    assert cache["browser_data_mb"] > 0


def test_preview_returns_pending_then_full_preview(profile_dir, monkeypatch):
    monkeypatch.setattr(api_maintenance, "PREVIEW_SCAN_BUDGET_SEC", 0.0)
    container = _StubContainer()

    pending = api_maintenance.cleanup_cache(
        CleanupRequest(target="browser_data", dry_run=True),
        request=_request(), container=container,
    )
    assert pending["pending"] is True
    assert pending["cleaned"] == []
    assert pending["count"] == 0
    assert pending["scan"]["state"] == "scanning"
    # 未扫完＝没有真正评估出条目，不应写一条「0 项完成」的审计
    assert container.repo.events == []

    monkeypatch.setattr(api_maintenance, "PREVIEW_SCAN_BUDGET_SEC", 5.0)
    done = api_maintenance.cleanup_cache(
        CleanupRequest(target="browser_data", dry_run=True),
        request=_request(), container=container,
    )
    assert done["pending"] is False
    assert done["count"] == 1
    assert done["cleaned"][0].startswith("[预览]")
    assert "2 个文件" in done["cleaned"][0]
    assert len(container.repo.events) == 1


def test_cache_scan_endpoint_advances_and_invalidates_on_done(profile_dir, monkeypatch):
    invalidated: list[bool] = []
    monkeypatch.setattr(api_maintenance, "invalidate_stats_cache", lambda: invalidated.append(True))

    assert api_maintenance.cache_scan_progress(budget=0.0)["state"] == "scanning"
    assert invalidated == []

    done = api_maintenance.cache_scan_progress(budget=5.0)
    assert done["state"] == "done"
    assert done["files"] == 2
    # 扫完要踢掉 /status 里可能缓存着的「统计中」快照
    assert invalidated == [True]


def test_cache_scan_endpoint_handles_missing_dir(monkeypatch):
    monkeypatch.setattr(api_maintenance, "_browser_data_dir", lambda: None)
    unavailable = api_maintenance.cache_scan_progress(budget=10_000.0)
    assert unavailable["state"] == "unavailable"
    assert unavailable["path"] is None


def test_cache_scan_endpoint_clamps_client_budget(profile_dir, monkeypatch):
    seen: list[float] = []

    class _Spy:
        def snapshot(self, _path, *, budget=None, advance=True):
            seen.append(budget)
            return {"path": str(profile_dir), "state": "done", "mb": 0, "files": 0,
                    "scanned_entries": 0, "elapsed_sec": 0}

    monkeypatch.setattr(api_maintenance, "_cache_scan", _Spy())
    monkeypatch.setattr(api_maintenance, "invalidate_stats_cache", lambda: None)

    api_maintenance.cache_scan_progress(budget=10_000.0)
    assert seen == [api_maintenance.MAX_POLL_SCAN_BUDGET_SEC]


def test_real_cleanup_drops_cached_scan(profile_dir, monkeypatch):
    monkeypatch.setattr(api_maintenance, "_browser_profile_busy", lambda _p: None)
    service = api_maintenance._cache_scan
    assert service.snapshot(profile_dir, budget=5.0)["files"] == 2

    result = api_maintenance.cleanup_cache(
        CleanupRequest(target="browser_data", dry_run=False),
        request=_request(), container=_StubContainer(),
    )
    assert result["errors"] == []
    assert str(profile_dir) not in service._scans


# ============== 并发：时间片在锁外走（性能线 §D2） ==============
class _BlockingAdvance(CacheScanService):
    """把 `block_key` 那一路的时间片放大成「等信号」，用来把两种等待分开测：等锁 vs 等一片。

    生产里一片是 0.5~2.0 s 的真实遍历，用例不能靠睡等来判断「有没有排队」。
    """

    def __init__(self, *, block_key: str, **kw) -> None:
        super().__init__(**kw)
        self._block_key = block_key
        self.entered = threading.Event()
        self.release = threading.Event()
        self.slice_keys: list[str] = []

    def _advance(self, scan, budget):
        key = scan.stack[0] if scan.stack else ""
        self.slice_keys.append(key)
        if key == self._block_key:
            self.entered.set()
            self.release.wait(5.0)
        super()._advance(scan, budget)


def _start_blocked_slice(service: _BlockingAdvance, root, sink: list | None = None):
    box = sink if sink is not None else []
    worker = threading.Thread(
        target=lambda: box.append(service.snapshot(root, budget=5.0)),
        daemon=True,
    )
    worker.start()
    assert service.entered.wait(5.0), "推进线程没有进入时间片"
    return worker


def test_late_caller_gets_progress_instead_of_queueing_behind_a_slice(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f1").write_bytes(b"x" * 1024)
    service = _BlockingAdvance(block_key=str(root))
    worker = _start_blocked_slice(service, root)
    try:
        t0 = time.perf_counter()
        snap = service.snapshot(root, budget=5.0)
        waited = time.perf_counter() - t0
        assert snap["state"] == "scanning"
        assert snap["files"] == 0
        # 迟到的调用只等锁，不再等别人那一片的墙钟（0.5~2.0 s）
        assert waited < 0.5
        # 同一路径同时只有一个推进者：否则半个目录的条目会被重复统计
        assert service.slice_keys == [str(root)]
    finally:
        service.release.set()
        worker.join(5.0)
    assert service.snapshot(root, budget=5.0)["files"] == 1


def test_invalidate_is_not_blocked_by_an_inflight_slice(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f1").write_bytes(b"x" * 1024)
    service = _BlockingAdvance(block_key=str(root))
    worker = _start_blocked_slice(service, root)
    try:
        t0 = time.perf_counter()
        service.invalidate(root)
        waited = time.perf_counter() - t0
        # 清理动作不能被一个正在跑的统计片卡住
        assert waited < 0.5
        assert str(root) not in service._scans
    finally:
        service.release.set()
        worker.join(5.0)


def test_other_path_is_not_blocked_by_one_paths_slice(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "fa").write_bytes(b"x" * 1024)
    (b / "fb").write_bytes(b"y" * 1024)
    service = _BlockingAdvance(block_key=str(a))
    worker = _start_blocked_slice(service, a)
    try:
        snap_b = service.snapshot(b, budget=5.0)
        assert snap_b["state"] == "done"
        assert snap_b["files"] == 1
        assert service.slice_keys == [str(a), str(b)]
    finally:
        service.release.set()
        worker.join(5.0)


def test_inflight_slice_does_not_resurrect_invalidated_progress(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "f1").write_bytes(b"x" * 1024)
    service = _BlockingAdvance(block_key=str(root))
    result: list[dict] = []
    worker = _start_blocked_slice(service, root, result)
    # 删除发生在这一片的中途：作废之后，在飞的那次回包不能继续报删除前的数字
    service.invalidate(root)
    service.release.set()
    worker.join(5.0)
    assert result[0]["state"] == "scanning"
    assert result[0]["files"] == 0
    assert str(root) not in service._scans
