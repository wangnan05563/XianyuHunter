from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "xianyu-hunter.spec"


def test_playwright_dependency_is_pinned_to_verified_minor() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]

    playwright_specs = [
        dep for dep in dependencies if dep.lower().startswith("playwright")
    ]

    assert playwright_specs == ["playwright>=1.60,<1.61"]


# ---------------------------------------------------------------------------
# T04104：spec 卫生排除 + UPX 明确决策
# ---------------------------------------------------------------------------

def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_compiles() -> None:
    # .spec 是 Python 源码；Analysis 依赖构建机环境，这里只做语法校验
    compile(_spec_text(), "xianyu-hunter.spec", "exec")


def test_spec_excludes_build_only_dependencies() -> None:
    """构建期/测试期依赖必须留在包外。

    onnx 仅 scripts/export_embedding_onnx.py 使用；pytest 家族在 backend/ 与 launcher
    里 0 处 import；Pillow 的 AVIF 插件无使用方（托盘图标是 Image.new 现画）。
    实测这些模块仍会被 PyInstaller 经间接链收进 PYZ，所以必须显式 exclude。
    """
    text = _spec_text()
    for mod in ("onnx", "_pytest", "pytest", "pluggy", "iniconfig", "PIL.AvifImagePlugin"):
        assert f"'{mod}'" in text, f"spec 缺少排除项：{mod}"


def test_spec_does_not_claim_upx_while_disabled() -> None:
    """UPX 决策：显式关闭（构建机无 upx，upx=True 只是空转）。"""
    text = _spec_text()
    assert not re.search(r"^\s*upx\s*=\s*True\b", text, re.M)
    assert len(re.findall(r"^\s*upx\s*=\s*False\b", text, re.M)) == 2  # EXE 与 COLLECT 两处


def test_runtime_entrypoints_do_not_import_build_only_dependencies() -> None:
    """排除项的前提：运行期入口确实不 import 它们（否则构建会把功能删掉）。"""
    pattern = re.compile(r"^\s*(?:from|import)\s+(?:pytest|_pytest|pluggy|iniconfig|onnx)\b", re.M)
    sources = list((ROOT / "backend" / "xianyu_hunter").rglob("*.py"))
    sources.append(ROOT / "scripts" / "launcher.py")
    offenders = []
    for path in sources:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"运行期入口出现构建期依赖 import：{offenders}"


# 与 spec 的 _build_only_excludes 保持一致；扩列时两处同改
_EXCLUDED_AT_RUNTIME = ("onnx", "pytest", "_pytest", "pluggy", "iniconfig", "PIL.AvifImagePlugin")

_PROBE_SRC = """
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BLOCKED = {block_names!r}


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError("[probe] excluded from bundle: %s" % name)
        return None


sys.meta_path.insert(0, Blocker())
for _m in list(sys.modules):
    if _m.split(".")[0] in BLOCKED:
        del sys.modules[_m]

import chromadb
import onnxruntime
from xianyu_hunter.web.app import app

assert not [m for m in sys.modules if m.split(".")[0] in BLOCKED]
print("PROBE_OK", chromadb.__version__, onnxruntime.__version__, len(app.routes))
"""


def test_excluded_modules_are_not_needed_at_runtime() -> None:
    """冻结语义探针：把排除项设为不可导入，后端主链路仍必须起得来。

    静态 grep 只能证明「我们的代码不 import 它们」，证明不了第三方包的可选链
    （numpy.testing / Pillow 插件注册表）在运行期不会回头找它们——那需要真机重建。
    这里用 sys.meta_path 拦截模拟「包里根本没有这些模块」，把风险在 CI 阶段就挡掉。
    """
    import subprocess

    probe = _PROBE_SRC.replace(
        "{block_names!r}", repr(list(_EXCLUDED_AT_RUNTIME))
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, f"探针失败：\n{result.stdout}\n{result.stderr}"
    assert "PROBE_OK" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# T04110：requirements-lock.txt 必须是可读文本（PowerShell 的 `>` 会写成 UTF-16 LE）
# ---------------------------------------------------------------------------

_LOCK = ROOT / "requirements-lock.txt"
_BUILD_PS1 = ROOT / "scripts" / "build-exe.ps1"


def test_requirements_lock_is_utf8_text() -> None:
    raw = _LOCK.read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff")), "锁文件是 UTF-16，git 会当二进制、diff/grep 全废"
    assert not raw.startswith(b"\xef\xbb\xbf"), "锁文件带 UTF-8 BOM，pip -r 解析易出错"
    text = raw.decode("utf-8")  # 非 UTF-8 时这里就抛
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) > 50, f"锁文件行数异常：{len(lines)}"
    assert "\r" not in text, "锁文件应统一 LF，避免与 git 的 text 归一化打架"


def test_build_script_does_not_redirect_pip_freeze_with_shell() -> None:
    """守门：`pip freeze > file` 这一行就是缺陷本体，别让它回来。

    Windows PowerShell 的重定向落 UTF-16 LE + BOM，与 python 侧写出的字节完全不同；
    只有跑完整构建才看得见，所以按源码形态判。
    """
    text = _BUILD_PS1.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"pip\s+freeze\s*>", line)
    ]
    assert not offenders, f"build-exe.ps1 用 shell 重定向写锁文件：{offenders}"


