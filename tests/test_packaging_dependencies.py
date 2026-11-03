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

