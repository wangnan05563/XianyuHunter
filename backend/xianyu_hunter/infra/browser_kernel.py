"""浏览器内核解析：系统 Edge → 系统 Chrome → 随包 Chromium → 首启下载。

为什么必须集中一处：Worker（infra/browser.py）、扫码登录（scripts/browser_login.py）、
二维码/昵称抓取（scripts/auth_helper.py）都会启动浏览器，而 Chromium 的 Cookie
是 DPAPI 加密且 profile 布局随内核不同。三个入口若各挑各的内核，就会出现
「A 内核写的登录态，B 内核读不出来」——所以解析策略必须是全局的、写进配置的。

解析顺序（auto）：msedge → chrome → 随包 chromium → 下载兜底。
Lite 安装包不随包 Chromium，前两级命中系统浏览器；都不命中且允许下载时，
把 Chromium 装到可写的用户目录（PLAYWRIGHT_BROWSERS_PATH）。
"""
from __future__ import annotations

import os
import shutil
import subprocess as sp
import sys
from dataclasses import dataclass
from pathlib import Path

from xianyu_hunter.infra.logger import get_logger

logger = get_logger()

POLICIES = ("auto", "msedge", "chrome", "bundled")

_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_APP_PATH_EXE = {"msedge": "msedge.exe", "chrome": "chrome.exe"}


class BrowserKernelUnavailable(RuntimeError):
    """系统浏览器与随包 Chromium 都不可用，且不允许/无法下载。

    消息必须是可诊断的：用户按提示能自查（装了哪个浏览器、网络是否可用、
    手动安装命令是什么），而不是只看到一句 ImportError。
    """


@dataclass(frozen=True)
class BrowserKernel:
    kind: str                     # msedge | chrome | bundled
    source: str                   # system-edge | system-chrome | bundled | downloaded
    channel: str | None = None
    executable_path: str | None = None

    def launch_kwargs(self) -> dict:
        """给 launch_persistent_context / launch 用的内核选择参数。"""
        kwargs: dict = {}
        if self.channel:
            kwargs["channel"] = self.channel
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        return kwargs

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "source": self.source,
            "channel": self.channel,
            "executable_path": self.executable_path or "",
        }


def normalize_policy(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in POLICIES:
        return v
    if v:
        logger.warning("未知的 browser.system_browser='{}'，按 auto 处理", value)
    return "auto"


def policy_from_config() -> str:
    """读取配置里的解析策略；环境变量 XH_BROWSER_KERNEL 优先（运维/测试用）。"""
    env = os.environ.get("XH_BROWSER_KERNEL", "").strip()
    if env:
        return normalize_policy(env)
    try:
        from xianyu_hunter.infra.yaml_config import get_config

        return normalize_policy(get_config().browser.system_browser)
    except Exception as e:  # noqa: BLE001 - 配置不可用不能拖垮浏览器启动
        logger.debug("读取 browser.system_browser 失败，按 auto 处理: {}", e)
        return "auto"


def allow_download_from_config() -> bool:
    try:
        from xianyu_hunter.infra.yaml_config import get_config

        return bool(get_config().browser.allow_browser_download)
    except Exception:  # noqa: BLE001
        return True


def _registry_app_path(exe_name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        ) as key:
            path, _ = winreg.QueryValueEx(key, "")
            if path and Path(path).exists():
                return str(path)
    except OSError:
        pass
    return None


def find_system_browser(kind: str) -> str | None:
    """查找系统 Edge / Chrome 可执行文件。kind ∈ {msedge, chrome}。"""
    exe = _APP_PATH_EXE.get(kind)
    if not exe:
        return None
    hit = _registry_app_path(exe)
    if hit:
        return hit
    for p in _EDGE_PATHS if kind == "msedge" else _CHROME_PATHS:
        if Path(p).exists():
            return p
    return shutil.which(exe)


def browsers_dir() -> Path:
    """随包/已下载 Chromium 的根目录（Playwright 的 PLAYWRIGHT_BROWSERS_PATH）。"""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        from xianyu_hunter.paths import get_app_dir

        return get_app_dir() / "playwright_browsers"
    return Path.home() / "AppData" / "Local" / "ms-playwright"


def bundled_chromium_dir(root: Path | None = None) -> Path | None:
    """返回可用的 chromium-<rev> 目录；不存在返回 None。"""
    root = root or browsers_dir()
    if not root.is_dir():
        return None

    def _rev(name: str) -> int:
        try:
            return int(name.rsplit("-", 1)[-1])
        except ValueError:
            return 0

    # 按 revision 数值倒序（字符串序会把 chromium-999 排到 chromium-1223 前面）
    for d in sorted(root.glob("chromium-*"), key=lambda p: _rev(p.name), reverse=True):
        if d.is_dir() and (d / "chrome-win" / "chrome.exe").is_file():
            return d
    return None


def _resolve_system(kind: str) -> BrowserKernel | None:
    path = find_system_browser(kind)
    if not path:
        return None
    return BrowserKernel(
        kind=kind,
        source="system-edge" if kind == "msedge" else "system-chrome",
        channel=kind,
        executable_path=path,
    )


def _bundled_kernel(source: str = "bundled") -> BrowserKernel | None:
    d = bundled_chromium_dir()
    if not d:
        return None
    # 显式给 executable_path：下载兜底装到的目录未必等于 Playwright 默认搜索路径，
    # 只靠 PLAYWRIGHT_BROWSERS_PATH 环境变量在进程间传递容易漏（脚本/子进程）。
    return BrowserKernel(
        kind="bundled",
        source=source,
        executable_path=str(d / "chrome-win" / "chrome.exe"),
    )


def resolve_kernel(policy: str | None = None, allow_download: bool | None = None) -> BrowserKernel:
    """按策略解析内核。解析不出且允许下载时自动安装 Chromium。"""
    policy = normalize_policy(policy if policy is not None else policy_from_config())
    if allow_download is None:
        allow_download = allow_download_from_config()

    if policy == "msedge":
        k = _resolve_system("msedge")
        if k:
            return k
        raise BrowserKernelUnavailable(
            "配置 browser.system_browser=msedge，但本机未找到 Microsoft Edge。"
            "可安装 Edge 后重试，或把该项改为 auto/chrome；"
            "诊断：检查 C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe 是否存在。"
        )
    if policy == "chrome":
        k = _resolve_system("chrome")
        if k:
            return k
        raise BrowserKernelUnavailable(
            "配置 browser.system_browser=chrome，但本机未找到 Google Chrome。"
            "可安装 Chrome 后重试，或把该项改为 auto/msedge。"
        )
    if policy == "bundled":
        k = _bundled_kernel()
        if k:
            return k
        if allow_download:
            return _download_and_resolve()
        raise _bundled_missing_error()

    # auto：Edge → Chrome → 随包 → 下载
    for kind in ("msedge", "chrome"):
        k = _resolve_system(kind)
        if k:
            return k
    k = _bundled_kernel()
    if k:
        return k
    if allow_download:
        return _download_and_resolve()
    raise _bundled_missing_error()


def _bundled_missing_error() -> BrowserKernelUnavailable:
    return BrowserKernelUnavailable(
        "未找到可用的浏览器内核：本机没有 Microsoft Edge / Google Chrome，"
        "安装包也未随带 Chromium，且 browser.allow_browser_download=false。"
        "解决①安装 Edge 或 Chrome；②开启 browser.allow_browser_download 后重启，"
        "程序会自动下载 Chromium 到 "
        f"{browsers_dir()}。"
    )


def _download_and_resolve() -> BrowserKernel:
    dest = browsers_dir()
    _install_chromium(dest)
    k = _bundled_kernel(source="downloaded")
    if not k:
        raise BrowserKernelUnavailable(
            "已执行 Playwright Chromium 安装，但未在 "
            f"{dest} 找到 chromium-*。请检查网络后重试，或手动执行："
            "playwright install chromium"
        )
    return k


def _driver_cli() -> list[str]:
    """构造 Playwright CLI 调用：冻结包用随包 node.exe，开发环境用 python -m。"""
    if getattr(sys, "frozen", False):
        from xianyu_hunter.paths import get_app_dir

        driver = get_app_dir() / "_internal" / "playwright" / "driver"
        node = driver / "node.exe"
        cli = driver / "package" / "cli.js"
        if node.exists() and cli.exists():
            return [str(node), str(cli)]
        # 冻结包绝不能退回 sys.executable：那会把 xianyu-hunter.exe 当解释器再启动一次
        raise BrowserKernelUnavailable(
            f"安装包内未找到 Playwright 驱动（{driver}），无法自动下载 Chromium。"
            "请安装 Microsoft Edge 或 Google Chrome 后重启；"
            "或手动下载 Chromium 放到 " + str(browsers_dir()) + "。"
        )
    exe = shutil.which("playwright")
    if exe:
        return [exe]
    return [sys.executable, "-m", "playwright"]


def _install_chromium(dest: Path) -> None:
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(dest)
    cmd = [*_driver_cli(), "install", "chromium"]
    logger.info("下载 Playwright Chromium 到 {} ...", dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        proc = sp.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except (OSError, sp.TimeoutExpired) as e:
        raise BrowserKernelUnavailable(
            f"启动 Chromium 下载失败（{e}）。请手动执行：playwright install chromium，"
            f"并把 PLAYWRIGHT_BROWSERS_PATH 指向可写目录（当前 {dest}）。"
        ) from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise BrowserKernelUnavailable(
            "Chromium 下载失败（通常是网络受限）。可手动下载后放到 "
            f"{dest}，或修改 browser.system_browser 使用系统浏览器。"
            f"输出尾部：{' | '.join(tail)}"
        )


def describe_kernel(policy: str | None = None) -> dict:
    """给 /api/about 用的只读诊断信息（不触发下载、不改状态）。"""
    policy = normalize_policy(policy if policy is not None else policy_from_config())
    edge = find_system_browser("msedge")
    chrome = find_system_browser("chrome")
    bundled = bundled_chromium_dir()
    if policy == "msedge":
        active, source = ("msedge" if edge else "unavailable"), ("system-edge" if edge else "")
    elif policy == "chrome":
        active, source = ("chrome" if chrome else "unavailable"), ("system-chrome" if chrome else "")
    elif policy == "bundled":
        active, source = ("bundled" if bundled else "unavailable"), ("bundled" if bundled else "")
    elif edge:
        active, source = "msedge", "system-edge"
    elif chrome:
        active, source = "chrome", "system-chrome"
    elif bundled:
        active, source = "bundled", "bundled"
    elif allow_download_from_config():
        active, source = "download-on-first-use", ""
    else:
        active, source = "unavailable", ""
    return {
        "policy": policy,
        "active": active,
        "source": source,
        "system_edge": edge or "",
        "system_chrome": chrome or "",
        "bundled_chromium": str(bundled) if bundled else "",
        "browsers_dir": str(browsers_dir()),
    }
