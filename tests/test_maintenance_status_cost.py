"""A4：`GET /api/maintenance/status` 的降本判据（TTL / 预热 / 日志统计 / 构建 venv 排除）

压测基线：单发 **1.6 s**，v1 负载轮里 P95 = 60 033 ms（客户端 60 s 超时判 000，超时率 37.5%）。
`maintenance_cost_probe.py` 在本机把成本拆开（真实库 `mode=ro`、真实目录只读遍历）：

| 段 | 实测 | 结论 |
|---|---|---|
| 6 张表 `SELECT COUNT(*)`（18 386 行 events 等） | 每张 ~0.1 ms | **不是**成本项；A4 原文的「近似行数 / 计数器表」没必要，精确计数保留 |
| 日志目录 glob + 每文件 stat | 5.7 ms（每文件 3 次系统调用） | 合并成 1 次 → 1.6 ms |
| 增量扫描 `snapshot(budget=0.5)` | 冷 19 ms / 热 0 ms | 预算只是上限，不是常态成本 |
| `__pycache__` 全树遍历 | **1 860 ms**（`os.walk` 3 442 ms 一次实测） | 其中 1 578 个目录来自**未排除的 `.venv-build/`**；排除后 18 个目录 / ~400 ms |
| 完整函数（bypass_cache） | 冷 2 235 ms / 热 5 205 ms | 与上面相加吻合 |

因此本文件钉四件事：TTL 拉到 300 的前提（失效点已存在）、启动预热且不占事件循环、
日志统计每个文件只 stat 一次、构建 venv 不再进 `__pycache__` 统计。

安全边界：日志目录/扫描根全部指向 tmp；`/status` 的数据库依赖用「connect 即抛」的假
engine 短路（不碰真实库）；不启动浏览器/调度器。
"""
from __future__ import annotations

import ast
import asyncio
import collections
import datetime
import pathlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from xianyu_hunter.web import cache as web_cache
from xianyu_hunter.web import startup
from xianyu_hunter.web.routes import api_maintenance as am


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    """本文件会调用被装饰的同步函数（bypass_cache 会写回缓存），测试间别串味"""
    web_cache.invalidate_stats_cache()
    yield
    web_cache.invalidate_stats_cache()


class _BrokenEngine:
    """connect 即抛：让 /status 的数据库段走 except 分支，避免碰真实库"""

    def connect(self):
        raise RuntimeError("测试内不连数据库")


def _call_status(monkeypatch, log_dir: Path):
    monkeypatch.setattr(am, "_log_dir", lambda: log_dir)
    # 扫描与 pycache 两段都是全树遍历，本文件只关心日志段，其余短路
    monkeypatch.setattr(am, "_browser_data_dir", lambda: None)
    monkeypatch.setattr(am, "is_frozen", lambda: True)
    container = SimpleNamespace(repo=SimpleNamespace(engine=_BrokenEngine()))
    return am.maintenance_status(container=container, bypass_cache=True)


# ============== TTL：拉长前要先确认失效点都在 ==============
def _status_decorator() -> ast.Call:
    tree = ast.parse(Path(am.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "maintenance_status":
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "cached_ttl":
                    return dec
    raise AssertionError("没找到 maintenance_status 上的 cached_ttl 装饰器")


def test_status_ttl_is_300_with_swr():
    """TTL=300 是 A4 的主杠杆（0.4~2 s 的全树遍历从 60 次/时降到 12 次/时）

    代价是「清理后仍看到旧数字」的窗口变长，所以它必须与失效点同时成立 ——
    失效点由 `test_maintenance_cache_scan.py::test_cache_scan_endpoint_advances_and_invalidates_on_done`
    与 `test_maintenance_cleanup.py::test_real_cleanup_invalidates_status_cache` 分别钉住。
    """
    dec = _status_decorator()
    assert dec.args and isinstance(dec.args[0], ast.Constant)
    assert dec.args[0].value == 300, f"TTL 实际是 {dec.args[0].value}"
    kwargs = {kw.arg: kw.value for kw in dec.keywords}
    assert isinstance(kwargs.get("stale_while_revalidate"), ast.Constant)
    assert kwargs["stale_while_revalidate"].value is True, "过期不阻塞（SWR）不能丢"


def test_cleanup_entry_points_invalidate_status_cache():
    """三个清理入口（cache/database/logs）都必须在写盘后失效概览缓存

    这是 TTL 300 的另一个前提：拉长窗口的前提是「用户自己的动作会踢掉旧值」。
    """
    tree = ast.parse(Path(am.__file__).read_text(encoding="utf-8"))
    posts = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
        and getattr(dec.func, "attr", "") == "post"
        and dec.args
        and isinstance(dec.args[0], ast.Constant)
    }
    assert set(posts) == {"cleanup_cache", "cleanup_database", "cleanup_logs"}, sorted(posts)
    for name, node in posts.items():
        calls = {
            getattr(c.func, "id", getattr(c.func, "attr", ""))
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
        }
        assert "invalidate_stats_cache" in calls, f"{name} 写盘后没有失效概览缓存"


# ============== 日志统计：每个文件只 stat 一次 ==============
def test_log_stats_one_stat_per_file(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    expected = {}
    for i, name in enumerate(("app.log", "app.log.1", "app.log.2", "app.log.3")):
        p = log_dir / name
        p.write_bytes(b"x" * (100_000 * (i + 1)))
        mtime = 1_700_000_000 + i * 86_400
        import os

        os.utime(p, (mtime, mtime))
        expected[name] = p.stat().st_size

    seen: collections.Counter[str] = collections.Counter()
    real_stat = pathlib.Path.stat

    def counting_stat(self, *args, **kwargs):
        if self.parent == log_dir:
            seen[self.name] += 1
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", counting_stat)
    logs = _call_status(monkeypatch, log_dir)["logs"]

    assert logs["file_count"] == 4
    assert logs["total_size_mb"] == round(sum(expected.values()) / 1024 / 1024, 2)
    assert logs["oldest"] == datetime.datetime.fromtimestamp(1_700_000_000).strftime("%Y-%m-%d")
    # 原实现 is_file()+stat()+stat() = 每文件 3 次；本条是「只 stat 一次」的直接判据
    assert dict(seen) == {name: 1 for name in expected}, seen


def test_log_stats_empty_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    logs = _call_status(monkeypatch, log_dir)["logs"]
    assert logs == {"file_count": 0, "total_size_mb": 0, "oldest": None}


def test_log_stats_missing_dir(tmp_path, monkeypatch):
    logs = _call_status(monkeypatch, tmp_path / "nope")["logs"]
    assert logs == {"file_count": 0, "total_size_mb": 0, "oldest": None}


# ============== 启动预热：加上 maintenance_status，且不占事件循环 ==============
async def test_warm_stats_cache_warms_maintenance_status_off_loop(monkeypatch):
    """它的全树遍历必须在工作线程里做：同步 def 预热若直接 await，会卡住启动期的事件循环"""
    from xianyu_hunter.web.routes import business_kpi, stats_overview, stats_today, trend

    loop_thread = threading.get_ident()
    records: dict[str, int] = {}
    seen_kwargs: dict = {}

    def make_async(name: str):
        async def _fake(*args, **kwargs):
            records[name] = threading.get_ident()

        return _fake

    def fake_status(*args, **kwargs):
        records["maintenance_status"] = threading.get_ident()
        seen_kwargs.update(kwargs)

    monkeypatch.setattr(business_kpi, "_compute_business_kpi", make_async("business_kpi"))
    monkeypatch.setattr(stats_today, "_compute_stats_today", make_async("stats_today"))
    monkeypatch.setattr(stats_overview, "_overview", make_async("stats_overview"))
    monkeypatch.setattr(trend, "_compute_stats_trend", make_async("stats_trend"))
    monkeypatch.setattr(am, "maintenance_status", fake_status)

    container = object()
    await startup._warm_stats_cache(container)

    assert set(records) == {"business_kpi", "stats_today", "stats_overview", "stats_trend", "maintenance_status"}
    assert records["business_kpi"] == loop_thread, "协程预热本来就该在事件循环里"
    assert records["maintenance_status"] != loop_thread, "同步全树遍历没有走 to_thread，会卡住启动"
    assert seen_kwargs.get("container") is container


async def test_warm_stats_cache_tolerates_failures(monkeypatch):
    """一个预热失败不能影响其余（原有语义，别在加第 5 个时弄丢）"""
    from xianyu_hunter.web.routes import business_kpi, stats_overview, stats_today, trend

    async def _boom_async(*args, **kwargs):
        raise RuntimeError("预热失败")

    calls: list[str] = []

    async def ok_async(*args, **kwargs):
        calls.append("ok")

    def ok_status(*args, **kwargs):
        calls.append("status")

    monkeypatch.setattr(business_kpi, "_compute_business_kpi", _boom_async)
    monkeypatch.setattr(stats_today, "_compute_stats_today", ok_async)
    monkeypatch.setattr(stats_overview, "_overview", _boom_async)
    monkeypatch.setattr(trend, "_compute_stats_trend", ok_async)
    monkeypatch.setattr(am, "maintenance_status", ok_status)

    await startup._warm_stats_cache(object())
    assert calls == ["ok", "ok", "status"]
