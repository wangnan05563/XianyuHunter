"""浏览器内核解析策略测试（infra/browser_kernel.py）。

为什么单独测试：Worker、扫码登录、二维码抓取三个入口共用这一份解析结果。
顺序错一步（例如 Lite 包先去找随包 Chromium）或显式策略静默降级，
表现都不是崩溃而是「登录态写进去读不出来」——必须用单测钉住顺序与报错。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xianyu_hunter.infra import browser_kernel as bk


def _fake_chromium(root: Path, rev: int = 1223) -> Path:
    d = root / f"chromium-{rev}"
    (d / "chrome-win").mkdir(parents=True, exist_ok=True)
    (d / "chrome-win" / "chrome.exe").write_bytes(b"")
    return d


@pytest.fixture
def system_browsers(monkeypatch, tmp_path):
    """可控的系统浏览器发现结果。"""
    edge = tmp_path / "msedge.exe"
    chrome = tmp_path / "chrome.exe"
    edge.write_bytes(b"")
    chrome.write_bytes(b"")
    state = {"msedge": str(edge), "chrome": str(chrome)}
    monkeypatch.setattr(bk, "find_system_browser", lambda kind: state.get(kind))
    return state


@pytest.fixture(autouse=True)
def _patch_real_bundled(monkeypatch):
    # 保存真实实现，供需要真实目录扫描的用例复用（避免递归 patch）
    monkeypatch.setattr(bk, "_real_bundled_dir", bk.bundled_chromium_dir, raising=False)


# ---------------------------------------------------------------------------
# 顺序：auto = Edge → Chrome → 随包 → 下载
# ---------------------------------------------------------------------------

def test_auto_prefers_edge(system_browsers, monkeypatch, tmp_path):
    _fake_chromium(tmp_path)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: tmp_path / "chromium-1223")
    k = bk.resolve_kernel("auto", allow_download=False)
    assert k.kind == "msedge"
    assert k.source == "system-edge"
    assert k.launch_kwargs()["channel"] == "msedge"


def test_auto_falls_back_to_chrome(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = None
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: tmp_path / "chromium-1223")
    k = bk.resolve_kernel("auto", allow_download=False)
    assert k.kind == "chrome"
    assert k.source == "system-chrome"


def test_auto_falls_back_to_bundled(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = system_browsers["chrome"] = None
    d = _fake_chromium(tmp_path)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: d)
    k = bk.resolve_kernel("auto", allow_download=False)
    assert k.kind == "bundled"
    # 显式 executable_path：下载兜底目录不一定在 Playwright 默认搜索路径里
    assert k.launch_kwargs() == {"executable_path": str(d / "chrome-win" / "chrome.exe")}


def test_auto_downloads_when_nothing_available(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = system_browsers["chrome"] = None
    root = tmp_path / "pw"
    root.mkdir()
    monkeypatch.setattr(bk, "browsers_dir", lambda: root)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: bk._real_bundled_dir(root))

    calls: list[Path] = []

    def fake_install(dest: Path) -> None:
        calls.append(dest)
        _fake_chromium(dest)

    monkeypatch.setattr(bk, "_install_chromium", fake_install)
    k = bk.resolve_kernel("auto", allow_download=True)
    assert calls == [root]
    assert k.kind == "bundled"
    assert k.source == "downloaded"


def test_auto_without_download_raises_actionable_error(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = system_browsers["chrome"] = None
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path)
    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk.resolve_kernel("auto", allow_download=False)
    msg = str(ei.value)
    assert "allow_browser_download=false" in msg
    assert "Edge" in msg and "Chrome" in msg
    assert str(tmp_path) in msg


# ---------------------------------------------------------------------------
# 显式策略：缺失必须报错，不得静默降级
# ---------------------------------------------------------------------------

def test_explicit_msedge_missing_raises(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = None
    # 即使有 Chrome / 随包 Chromium 也不能顶替
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: tmp_path)
    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk.resolve_kernel("msedge", allow_download=False)
    assert "msedge" in str(ei.value)


def test_explicit_chrome_missing_raises(system_browsers):
    system_browsers["chrome"] = None
    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk.resolve_kernel("chrome", allow_download=False)
    assert "chrome" in str(ei.value)


def test_explicit_bundled_ignores_system_browser(system_browsers, monkeypatch, tmp_path):
    d = _fake_chromium(tmp_path)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: d)
    k = bk.resolve_kernel("bundled", allow_download=False)
    assert k.kind == "bundled"
    assert "channel" not in k.launch_kwargs()


def test_explicit_bundled_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path)
    with pytest.raises(bk.BrowserKernelUnavailable):
        bk.resolve_kernel("bundled", allow_download=False)


# ---------------------------------------------------------------------------
# 配置 / 诊断
# ---------------------------------------------------------------------------

def test_normalize_policy_unknown_falls_back_to_auto():
    assert bk.normalize_policy("EDGE") == "auto"
    assert bk.normalize_policy("chrome") == "chrome"
    assert bk.normalize_policy(None) == "auto"


def test_describe_kernel_reports_active_kernel(system_browsers, monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path)
    info = bk.describe_kernel("auto")
    assert info["policy"] == "auto"
    assert info["active"] == "msedge"
    assert info["system_chrome"] == system_browsers["chrome"]


def test_describe_kernel_unavailable_when_download_disabled(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = system_browsers["chrome"] = None
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path)
    monkeypatch.setattr(bk, "allow_download_from_config", lambda: False)
    assert bk.describe_kernel("auto")["active"] == "unavailable"


def test_describe_kernel_download_on_first_use(system_browsers, monkeypatch, tmp_path):
    system_browsers["msedge"] = system_browsers["chrome"] = None
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path)
    monkeypatch.setattr(bk, "allow_download_from_config", lambda: True)
    assert bk.describe_kernel("auto")["active"] == "download-on-first-use"


# ---------------------------------------------------------------------------
# 随包目录扫描
# ---------------------------------------------------------------------------

def test_bundled_dir_picks_highest_revision(tmp_path):
    _fake_chromium(tmp_path, rev=999)
    _fake_chromium(tmp_path, rev=1223)
    assert bk.bundled_chromium_dir(tmp_path).name == "chromium-1223"


def test_bundled_dir_skips_incomplete_install(tmp_path):
    d = tmp_path / "chromium-1223"
    (d / "chrome-win").mkdir(parents=True)  # 没有 chrome.exe
    assert bk.bundled_chromium_dir(tmp_path) is None


def test_frozen_driver_missing_raises_instead_of_recursing(monkeypatch, tmp_path):
    """冻结包缺 Playwright 驱动时绝不能退回 sys.executable（会再启动一次主程序）。"""
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: tmp_path)
    monkeypatch.setattr(bk, "browsers_dir", lambda: tmp_path / "pw")
    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk._driver_cli()
    assert "Playwright 驱动" in str(ei.value)


# ---------------------------------------------------------------------------
# T05717：Full 构建随包 Chromium 缺失必须报“安装包不完整”，而非退化为联网下载
# ---------------------------------------------------------------------------

def test_is_bundled_build(monkeypatch, tmp_path):
    # 非 frozen：永远 False（dev 模式走普通解析链）
    monkeypatch.setattr(bk.sys, "frozen", False, raising=False)
    assert bk.is_bundled_build() is False

    app_dir = tmp_path / "app"
    (app_dir / "playwright_browsers").mkdir(parents=True)
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir)
    # Full 构建：安装目录存在 playwright_browsers 目录 → 视为随包
    assert bk.is_bundled_build() is True

    # Lite 构建：安装目录无 playwright_browsers → 视为未随包
    app_dir2 = tmp_path / "app2"
    app_dir2.mkdir()
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir2)
    assert bk.is_bundled_build() is False

    # 显式 root 可测，不依赖全局状态
    assert bk.is_bundled_build(tmp_path / "x") is False
    assert bk.is_bundled_build(app_dir) is True


def test_full_build_missing_chromium_reports_integrity_not_download(system_browsers, monkeypatch, tmp_path):
    # Full 构建：安装目录 playwright_browsers 存在（随包标记），但内核缺失
    system_browsers["msedge"] = system_browsers["chrome"] = None
    app_dir = tmp_path / "app"
    (app_dir / "playwright_browsers").mkdir(parents=True)
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: app_dir / "playwright_browsers")

    calls = []
    monkeypatch.setattr(bk, "_install_chromium", lambda dest: calls.append(dest))

    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk.resolve_kernel("auto", allow_download=True)
    # 关键：Full 构建缺失 chromium 绝不应触发联网下载
    assert not calls, "Full 构建缺失 chromium 不应触发下载"
    msg = str(ei.value)
    assert "安装包" in msg and ("完整性" in msg or "不完整" in msg)
    assert str(app_dir / "playwright_browsers") in msg


def test_full_build_missing_bundled_policy_reports_integrity(system_browsers, monkeypatch, tmp_path):
    # 显式 browser.system_browser=bundled 的 Full 构建缺失时同样走完整性错误
    system_browsers["msedge"] = system_browsers["chrome"] = None
    app_dir = tmp_path / "app"
    (app_dir / "playwright_browsers").mkdir(parents=True)
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir)
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: None)
    monkeypatch.setattr(bk, "browsers_dir", lambda: app_dir / "playwright_browsers")
    monkeypatch.setattr(bk, "_install_chromium", lambda dest: None)  # 若被调用会静默无操作

    with pytest.raises(bk.BrowserKernelUnavailable) as ei:
        bk.resolve_kernel("bundled", allow_download=True)
    assert "安装包" in str(ei.value)


def test_lite_build_missing_chromium_downloads(system_browsers, monkeypatch, tmp_path):
    # Lite 构建：安装目录无 playwright_browsers → 视为 Lite，缺失时走下载兜底
    system_browsers["msedge"] = system_browsers["chrome"] = None
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir)
    # 下载后用真实扫描逻辑确认浏览器落地（而非永远返回 None，否则会误报“下载后找不到”）
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: bk._real_bundled_dir(root))

    dl_root = tmp_path / "appdata" / "playwright_browsers"
    dl_root.mkdir(parents=True)
    monkeypatch.setattr(bk, "browsers_dir", lambda: dl_root)

    calls = []
    def fake_install(dest: Path) -> None:
        calls.append(dest)
        _fake_chromium(dest)

    monkeypatch.setattr(bk, "_install_chromium", fake_install)
    k = bk.resolve_kernel("auto", allow_download=True)
    # 下载兜底仍然生效（Lite 包本就不随 Chromium）
    assert calls == [dl_root]
    assert k.kind == "bundled"
    assert k.source == "downloaded"


def test_full_build_present_chromium_returns_bundled(system_browsers, monkeypatch, tmp_path):
    # Full 构建随包内核完整 → 直接返回 bundled，不触发下载
    system_browsers["msedge"] = system_browsers["chrome"] = None
    app_dir = tmp_path / "app"
    (app_dir / "playwright_browsers").mkdir(parents=True)
    monkeypatch.setattr(bk.sys, "frozen", True, raising=False)
    monkeypatch.setattr("xianyu_hunter.paths.get_app_dir", lambda: app_dir)
    d = _fake_chromium(app_dir / "playwright_browsers")
    monkeypatch.setattr(bk, "bundled_chromium_dir", lambda root=None: d)
    monkeypatch.setattr(bk, "_install_chromium", lambda dest: None)

    k = bk.resolve_kernel("auto", allow_download=True)
    assert k.kind == "bundled"
    assert k.launch_kwargs()["executable_path"] == str(d / "chrome-win" / "chrome.exe")
