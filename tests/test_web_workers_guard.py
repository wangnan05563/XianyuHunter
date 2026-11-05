"""E1 护栏：`--workers > 1` 与 `--with-scheduler` 必须互斥

`__main__.web` 的注释一直写着「多 worker + 调度器会让每个 worker 各起一套浏览器/
调度器」，但代码不拦：4 个 worker = 4 个 Chromium + 4 份轮询，既抢浏览器锁也重复
采集。本文件锁三件事：
1. 默认（带调度器）+ workers>1 → 退出码 2，且不启动 uvicorn；
2. `--no-with-scheduler` + workers>1 → 放行，workers 原样传给 uvicorn；
3. `--reload` + workers>1 → 仍强制降为 1（既有行为不被新护栏打乱）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from typer.testing import CliRunner

from xianyu_hunter.__main__ import app as cli

runner = CliRunner()


class _UvicornSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, *args, **kwargs) -> None:
        self.calls.append(kwargs)


def _patch_uvicorn(monkeypatch) -> _UvicornSpy:
    spy = _UvicornSpy()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=spy.run))
    return spy


def _all_output(result) -> str:
    return (result.output or "") + (getattr(result, "stderr", "") or "")


def test_workers_with_scheduler_is_rejected(monkeypatch) -> None:
    spy = _patch_uvicorn(monkeypatch)
    result = runner.invoke(cli, ["web", "--workers", "4"])

    assert result.exit_code == 2, _all_output(result)
    assert "互斥" in _all_output(result)
    assert spy.calls == [], "护栏应在 uvicorn.run 之前生效"


def test_workers_without_scheduler_passes_through(monkeypatch) -> None:
    spy = _patch_uvicorn(monkeypatch)
    result = runner.invoke(cli, ["web", "--workers", "4", "--no-with-scheduler"])

    assert result.exit_code == 0, _all_output(result)
    assert len(spy.calls) == 1
    assert spy.calls[0]["workers"] == 4


def test_reload_still_forces_single_worker(monkeypatch) -> None:
    spy = _patch_uvicorn(monkeypatch)
    result = runner.invoke(cli, ["web", "--workers", "4", "--reload", "--no-with-scheduler"])

    assert result.exit_code == 0, _all_output(result)
    assert spy.calls[0]["workers"] == 1
