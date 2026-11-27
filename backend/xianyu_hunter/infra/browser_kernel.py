"""浏览器内核解析：系统 Edge → 系统 Chrome → 随包 Chromium → 首启下载。

为什么必须集中一处：Worker（infra/browser.py）、扫码登录（scripts/browser_login.py）、
二维码/昵称抓取（scripts/auth_helper.py）都会启动浏览器，而 Chromium 的 Cookie
是 DPAPI 加密且 profile 布局随内核不同。三个入口若各挑各的内核，就会出现
「A 内核写的登录态，B 内核读不出来」——所以解析策略必须是全局的、写进配置的。

解析顺序（auto）：msedge → chrome → 随包 chromium → 下载兜底。
Lite 安装包不随包 Chromium，前两级命中系统浏览器；都不命中且允许下载时，
把 Chromium 装到可写的用户目录（PLAYWRIGHT_BROWSERS_PATH）。

内核布局兼容（T05717）：Playwright 自 chromium-1223 起 Windows 完整内核目录为
chrome-win64/（旧版为 chrome-win/）。所有「判定存在 / 生成启动路径」统一经由
chromium_executable()，绝不硬编码单一目录名，否则随包内核明明完整却会被判为缺失。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess as sp
import sys
from dataclasses import dataclass
from pathlib import Path

from xianyu_hunter.infra.logger import get_logger

logger = get_logger()

POLICIES = ("auto", "msedge", "chrome", "bundled")

_EDGE_EXE_NAME = "msedge.exe"
_CHROME_EXE_NAME = "chrome.exe"
_EDGE_PATHS = [
    rf"C:\Program Files (x86)\Microsoft\Edge\Application\{_EDGE_EXE_NAME}",
    rf"C:\Program Files\Microsoft\Edge\Application\{_EDGE_EXE_NAME}",
]
_CHROME_PATHS = [
    rf"C:\Program Files\Google\Chrome\Application\{_CHROME_EXE_NAME}",
    rf"C:\Program Files (x86)\Google\Chrome\Application\{_CHROME_EXE_NAME}",
]
_APP_PATH_EXE = {"msedge": _EDGE_EXE_NAME, "chrome": _CHROME_EXE_NAME}


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


# Full/Lite 身份的显式清单（build-exe.ps1 5.4 节）。放在应用根目录而不是
# playwright_browsers 内部：整个内核目录被杀软隔离/清理时，清单仍在，运行时
# 才能报“安装包不完整”，而不是误判成 Lite 转去联网下载。
_BUNDLE_MANIFEST = "bundle-manifest.json"

# 兼容旧 Full 包：旧版把标记写在 playwright_browsers 内部。
_LEGACY_BUNDLE_MARKER = ".xh-full-bundle"

# 完整 Chromium 可执行文件在 chromium-<rev>/ 下的候选布局。
# Playwright 自 chromium-1223 起在 Windows 上把目录从 chrome-win 改为 chrome-win64
# （实测 .cache/playwright_browsers/chromium-1223/chrome-win64/chrome.exe）。
# 只认 chrome-win 会让「内核明明完整随包」被判为缺失——T05717 复现的真正根因。
# 列表覆盖新旧 Windows 布局与常见跨平台布局，末尾另有 glob 兜底未来再次改名。
_CHROMIUM_EXE_SUBDIRS = (
    ("chrome-win64", _CHROME_EXE_NAME),
    ("chrome-win", _CHROME_EXE_NAME),
    ("chrome-linux64", "chrome"),
    ("chrome-linux", "chrome"),
)

# 只有 chrome.exe 存在不代表内核可用：杀软隔离 chrome.dll / 磁盘写满留下
# 0 字节文件时，Playwright 仍会“找到”内核，但一启动就崩。这里用关键文件
# 非空作为最小完整性判据（构建期还会额外跑 chrome --version）。
_REQUIRED_CHROMIUM_FILES = {
    _CHROME_EXE_NAME: ("chrome.dll", "resources.pak", "icudtl.dat"),
    "chrome": ("resources.pak", "icudtl.dat"),
}
_MIN_CHROMIUM_FILE_BYTES = 1024


def _non_empty_file(path: Path, min_bytes: int = _MIN_CHROMIUM_FILE_BYTES) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _complete_chromium_exe(exe: Path) -> bool:
    """判断一个候选 chrome.exe 是否满足最小可启动完整性。"""
    if not _non_empty_file(exe):
        return False
    required = _REQUIRED_CHROMIUM_FILES.get(exe.name)
    if required is None:
        return False
    base = exe.parent
    return all(_non_empty_file(base / name) for name in required)


def chromium_executable(chromium_dir: Path) -> Path | None:
    """返回 chromium-<rev> 目录下完整内核的可执行文件路径；找不到返回 None。

    单一真相源：bundled_chromium_dir（判定内核是否存在）与 _bundled_kernel
    （给出 executable_path）都必须经此解析，杜绝「判定有、启动路径却算错」。
    headless-shell 的可执行文件名是 chrome-headless-shell.exe，不匹配 chrome.exe，
    因此天然不会被误认作完整内核。
    """
    for subdir, exe_name in _CHROMIUM_EXE_SUBDIRS:
        exe = chromium_dir / subdir / exe_name
        if _complete_chromium_exe(exe):
            return exe
    # 兜底：未来 Playwright 再改目录名时，扫描一层子目录内的 chrome.exe / chrome
    for pattern in ("*/chrome.exe", "*/chrome"):
        for exe in sorted(chromium_dir.glob(pattern)):
            if _complete_chromium_exe(exe):
                return exe
    return None


def read_bundle_manifest(root: Path | None = None) -> dict | None:
    """读取 Full/Lite 身份清单；不存在或损坏时返回 None（兼容旧包）。"""
    if root is None:
        from xianyu_hunter.paths import get_app_dir

        root = get_app_dir()
    path = root / _BUNDLE_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def is_bundled_build(root: Path | None = None) -> bool:
    """判断当前运行的是否为随包 Chromium 的 Full 构建（T05717）。

    判据优先级：
    ① 应用根目录 bundle-manifest.json 的 bundle_browser（Full/Lite 安装包各自携带，
       即使 playwright_browsers 整个目录被删也能识别 Full）；
    ② 兼容旧包：playwright_browsers/.xh-full-bundle 标记；
    ③ 兼容更旧包：目录内存在可用的完整 chromium-* 内核。
    纯空目录/空 chromium-* 子目录不算 Full。
    dev 模式（非 frozen）永远返回 False，走普通解析链（含下载兜底）。
    """
    if not getattr(sys, "frozen", False):
        return False
    if root is None:
        from xianyu_hunter.paths import get_app_dir

        root = get_app_dir()
    manifest = read_bundle_manifest(root)
    if manifest is not None:
        return bool(manifest.get("bundle_browser"))
    pw = root / "playwright_browsers"
    if not pw.is_dir():
        return False
    if (pw / _LEGACY_BUNDLE_MARKER).is_file():
        return True
    try:
        return any(
            d.is_dir()
            and d.name.startswith("chromium-")
            and not d.name.startswith("chromium_headless_shell-")
            and chromium_executable(d) is not None
            for d in pw.iterdir()
        )
    except OSError:
        return False


def bundled_chromium_dir(root: Path | None = None) -> Path | None:
    """返回可用的 chromium-<rev> 目录（含完整内核）；不存在返回 None。"""
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
        # chromium_headless_shell-<rev> 同样匹配 glob，显式排除
        if not d.is_dir() or d.name.startswith("chromium_headless_shell-"):
            continue
        if chromium_executable(d) is not None:
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
    # 与 bundled_chromium_dir 共用 chromium_executable()：新版 chrome-win64 /
    # 旧版 chrome-win 都能拿到正确的启动路径，不再硬编码单一布局。
    exe = chromium_executable(d)
    if not exe:
        return None
    # 显式给 executable_path：下载兜底装到的目录未必等于 Playwright 默认搜索路径，
    # 只靠 PLAYWRIGHT_BROWSERS_PATH 环境变量在进程间传递容易漏（脚本/子进程）。
    return BrowserKernel(
        kind="bundled",
        source=source,
        executable_path=str(exe),
    )


def resolve_kernel(policy: str | None = None, allow_download: bool | None = None) -> BrowserKernel:
    """按策略解析内核。解析不出且允许下载时自动安装 Chromium。"""
    policy = normalize_policy(policy if policy is not None else policy_from_config())
    if allow_download is None:
        allow_download = allow_download_from_config()

    if policy == "auto":
        return _resolve_auto(allow_download)
    return _resolve_explicit_policy(policy, allow_download)


def _resolve_explicit_policy(policy: str, allow_download: bool) -> BrowserKernel:
    """按显式策略（msedge / chrome / bundled）解析；解析不出时抛出可诊断错误。

    独立成函数是为了让每条策略分支的复杂度各自计算（S3776）：resolve_kernel
    只负责归一化入参并分派，分支细节留在这里。
    """
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
    k = _bundled_kernel()
    if k:
        return k
    # Full 构建随包 Chromium 缺失 = 安装包损坏 / 误装 -Lite，离线无法自修，
    # 不应再尝试联网下载（T05717：移除“缺失即下载”的强依赖，改为提示检查安装包完整性）。
    if is_bundled_build():
        raise _bundled_integrity_error()
    if allow_download:
        return _download_and_resolve()
    raise _bundled_missing_error()


def _resolve_auto(allow_download: bool) -> BrowserKernel:
    """auto：Edge → Chrome → 随包 → 下载；全都不命中时抛出可诊断错误。"""
    for kind in ("msedge", "chrome"):
        k = _resolve_system(kind)
        if k:
            return k
    k = _bundled_kernel()
    if k:
        return k
    # 同上：Full 构建随包 Chromium 缺失必须明确报“安装包不完整”，而非静默转下载
    if is_bundled_build():
        raise _bundled_integrity_error()
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


def _describe_browsers_tree(root: Path) -> str:
    """把浏览器目录的实际内容压成一行诊断文本，便于用户/支持直接贴出来定位。"""
    if not root.is_dir():
        return "（目录不存在）"
    try:
        names = sorted(p.name for p in root.iterdir())
    except OSError as e:
        return f"（目录不可读：{e}）"
    if not names:
        return "（目录为空）"
    return ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")


def _bundle_integrity_dir() -> Path:
    """完整性错误/诊断应指向应用自己的随包目录，而不是外部 env 覆盖的缓存。"""
    manifest = read_bundle_manifest()
    if manifest is not None and manifest.get("bundle_browser"):
        from xianyu_hunter.paths import get_app_dir

        return get_app_dir() / "playwright_browsers"
    return browsers_dir()


def _bundled_integrity_error() -> BrowserKernelUnavailable:
    """Full 构建随包 Chromium 缺失（T05717）。

    这是安装包损坏、误装 -Lite 版本或内核布局不匹配的表现，离线环境无法自动修复，
    必须提示用户检查安装包完整性，而不是退化为“联网下载”（下载在离线/
    无权限的 Program Files 目录下本就会失败，只会甩出更难懂的报错）。
    """
    dest = _bundle_integrity_dir()
    return BrowserKernelUnavailable(
        "安装包内的 Chromium 不完整或缺失：在 "
        f"{dest} 未找到可用的完整内核（期望 chromium-*/chrome-win64/chrome.exe "
        "或 chromium-*/chrome-win/chrome.exe，且 chrome.dll 等关键文件非空）。"
        f"\n实际目录内容：{_describe_browsers_tree(dest)}"
        "\n这通常是安装包损坏或误装了 -Lite 版本，离线环境无法自动修复。请："
        "① 重新运行官方完整版安装程序（非 -Lite）；"
        f"② 或手动把完整 Chromium 放到 {dest}；"
        "③ 若本机有网络，可执行 `playwright install chromium` 拉取后重启程序。"
    )


def _download_and_resolve() -> BrowserKernel:
    dest = browsers_dir()
    _install_chromium(dest)
    k = _bundled_kernel(source="downloaded")
    if not k:
        raise BrowserKernelUnavailable(
            "已执行 Playwright Chromium 安装，但未在 "
            f"{dest} 找到可用的完整内核。实际目录内容：{_describe_browsers_tree(dest)}"
            "\n请检查网络后重试，或手动执行：playwright install chromium"
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


def _available_status(kind: str, present: object, source: str) -> tuple[str, str]:
    """内核存在时回带 (kind, source)，不存在统一报 unavailable（不编造来源）。"""
    if present:
        return kind, source
    return "unavailable", ""


def _kernel_status(
    policy: str, edge: str | None, chrome: str | None, bundled: Path | None,
) -> tuple[str, str]:
    """把「策略 + 各级内核探测结果」收敛成 (active, source)。

    显式策略只看自己那一级；auto 依次回落到 Edge → Chrome → 随包 → 首启下载。
    独立成函数是让 describe_kernel 保持只读诊断的单一职责（S3776）。
    """
    if policy == "msedge":
        return _available_status("msedge", edge, "system-edge")
    if policy == "chrome":
        return _available_status("chrome", chrome, "system-chrome")
    if policy == "bundled":
        return _available_status("bundled", bundled, "bundled")
    if edge:
        return "msedge", "system-edge"
    if chrome:
        return "chrome", "system-chrome"
    if bundled:
        return "bundled", "bundled"
    if allow_download_from_config():
        return "download-on-first-use", ""
    return "unavailable", ""


def describe_kernel(policy: str | None = None) -> dict:
    """给 /api/about 用的只读诊断信息（不触发下载、不改状态）。"""
    policy = normalize_policy(policy if policy is not None else policy_from_config())
    edge = find_system_browser("msedge")
    chrome = find_system_browser("chrome")
    bundled = bundled_chromium_dir()
    active, source = _kernel_status(policy, edge, chrome, bundled)
    exe = chromium_executable(bundled) if bundled else None
    return {
        "policy": policy,
        "active": active,
        "source": source,
        "system_edge": edge or "",
        "system_chrome": chrome or "",
        "bundled_chromium": str(bundled) if bundled else "",
        "bundled_executable": str(exe) if exe else "",
        "browsers_dir": str(browsers_dir()),
        # T05717：把实际目录内容带出来，离线登录失败时无需重装即可自查
        "browsers_dir_contents": _describe_browsers_tree(browsers_dir()),
    }
