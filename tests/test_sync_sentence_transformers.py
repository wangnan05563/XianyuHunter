"""scripts/sync-sentence-transformers.py 的分支判据单测（T04184）。

这一步的价值只在「把预期空转和真实缺口分开」：
- 当前 spec 把 sentence_transformers 收进 PYZ，`_internal/` 下没有松散目录，
  旧实现为此打印 `[WARN] ... dirs not found, skip`，每次构建都像 RAG 出了缺陷；
- 反过来，真正的缺口（PYZ 没收录）也必须非 0 退出，不能一并归进「跳过」。
所以按分支测 main()，不起 PyInstaller；再加两条源码形态守卫，钉住
「路径必须显式传入」和「调用方真的传了」。
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_PS1 = REPO_ROOT / "scripts" / "build-exe.ps1"
_SYNC_PY = REPO_ROOT / "scripts" / "sync-sentence-transformers.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_st_under_test", _SYNC_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _make_venv(tmp_path: Path) -> Path:
    """最小构建 venv：包体（含非 .py 资源）+ dist-info 壳"""
    venv = tmp_path / "site-packages"
    pkg = venv / "sentence_transformers" / "base"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg.parent / "__init__.py").write_text("PKG_INIT\n", encoding="utf-8")
    (pkg / "model.py").write_text("MODEL\n", encoding="utf-8")
    (pkg / "resources.json").write_text('{"ok": 1}', encoding="utf-8")
    info = venv / "sentence_transformers-9.9.9.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\n", encoding="utf-8")
    return venv


def _toc(tmp_path: Path, names: list[str]) -> Path:
    """按 PYZ-00.toc 的 repr 形态造 toc；顺带塞一条含包名的数据文件行当诱饵"""
    body = "".join(f"  ('{n}', 'x.py', 'PYMODULE'),\n" for n in names)
    path = tmp_path / "PYZ-00.toc"
    path.write_text(
        f"[\n{body}  ('some/path/sentence_transformers.txt', 'y', 'DATA'),\n]\n",
        encoding="utf-8",
    )
    return path


def _main(tmp_path: Path, venv: Path, dist: Path, toc: Path) -> int:
    return mod.main([
        "--venv-site-packages", str(venv),
        "--dist-internal", str(dist),
        "--pyz-toc", str(toc),
    ])


def test_pyz_modules_only_counts_quoted_module_literals(tmp_path: Path) -> None:
    """toc 里的数据文件路径不能算成模块：否则缺口会被误判成「已收录」"""
    toc = _toc(tmp_path, ["sentence_transformers", "sentence_transformers.base.model"])
    assert mod.pyz_modules(toc, "sentence_transformers") == {
        "sentence_transformers",
        "sentence_transformers.base.model",
    }


def test_pyz_only_shape_is_expected_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """本次真实构建形态：无松散目录 + PYZ 完整覆盖期望集 → 退出码 0，且不再打 WARN"""
    monkeypatch.setattr(mod, "expected_modules", lambda pkg: {"sentence_transformers.base.model"})
    venv = _make_venv(tmp_path)
    out = tmp_path / "_internal"
    out.mkdir()
    rc = _main(tmp_path, venv, out, _toc(tmp_path, ["sentence_transformers.base.model"]))
    printed = capsys.readouterr().out
    assert rc == 0
    assert "[SKIP]" in printed and "1/1 个子模块" in printed
    assert "[WARN]" not in printed
    assert not (out / "sentence_transformers").exists()  # 跳过不等于凭空造目录
    assert not any(d.name.startswith("sentence_transformers-") for d in out.iterdir())


def test_pyz_coverage_gap_is_fatal(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """spec 请求的模块没全进 PYZ：这是「只收到半个包」的本体，必须非 0 并点名缺谁"""
    monkeypatch.setattr(
        mod, "expected_modules",
        lambda pkg: {"sentence_transformers.base.model", "sentence_transformers.sentence_transformer.model"},
    )
    venv = _make_venv(tmp_path)
    out = tmp_path / "_internal"
    out.mkdir()
    rc = _main(tmp_path, venv, out, _toc(tmp_path, ["sentence_transformers.base.model"]))
    printed = capsys.readouterr().out
    assert rc == 1
    assert "[ERROR]" in printed and "sentence_transformers.sentence_transformer.model" in printed


def test_expected_modules_degrades_when_pyinstaller_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyInstaller 不可用（脚本被单独跑）：返回 None，不能因为拿不到期望集就判失败"""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "PyInstaller", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", types.SimpleNamespace())
    assert mod.expected_modules("sentence_transformers") is None


def test_expected_modules_degrades_when_collection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """collect_submodules 自己抛错（依赖树残缺）：同样返回 None，交给 toc 判据兜底"""
    import sys
    import types

    def boom(pkg, *a, **kw):
        raise ModuleNotFoundError("No module named 'sentence_transformers'")

    hooks = types.SimpleNamespace(collect_submodules=boom)
    monkeypatch.setitem(sys.modules, "PyInstaller", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    assert mod.expected_modules("sentence_transformers") is None


def test_loose_dir_gets_missing_files_without_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """松散目录形态仍按 venv 补齐，并带上 dist-info；已有文件不能被覆盖"""
    venv = _make_venv(tmp_path)
    out = tmp_path / "_internal"
    pkg = out / "sentence_transformers" / "base"
    pkg.mkdir(parents=True)
    existing = pkg.parent / "__init__.py"
    existing.write_text("KEEP_ME\n", encoding="utf-8")
    rc = _main(tmp_path, venv, out, _toc(tmp_path, []))
    printed = capsys.readouterr().out
    assert rc == 0
    assert "[SYNC]" in printed
    assert existing.read_text(encoding="utf-8") == "KEEP_ME\n"
    assert (pkg / "model.py").read_text(encoding="utf-8") == "MODEL\n"
    assert (pkg / "resources.json").exists()  # 非 .py 资源同样是补齐对象
    assert (out / "sentence_transformers-9.9.9.dist-info" / "METADATA").exists()


def test_missing_loose_dir_body_is_fatal(tmp_path: Path) -> None:
    """产物有松散目录但构建 venv 没包体：无源可补，必须非 0"""
    _make_venv(tmp_path)  # 造一个正常 venv，但下面指向不存在的 site-packages
    out = tmp_path / "_internal" / "sentence_transformers"
    out.mkdir(parents=True)
    rc = _main(
        tmp_path,
        tmp_path / "site-packages-absent",
        out.parent,
        _toc(tmp_path, ["sentence_transformers"]),
    )
    assert rc == 1


def test_gap_when_pyz_has_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PYZ 未收录且无松散目录：这是真实缺口，不能当跳过"""
    venv = _make_venv(tmp_path)
    out = tmp_path / "_internal"
    out.mkdir()
    rc = _main(tmp_path, venv, out, _toc(tmp_path, ["other_pkg"]))
    printed = capsys.readouterr().out
    assert rc == 1
    assert "[ERROR]" in printed


def test_missing_toc_file_is_fatal(tmp_path: Path) -> None:
    """--pyz-toc 指错（workpath 迁移/改名）不能被读成「属预期跳过」"""
    venv = _make_venv(tmp_path)
    out = tmp_path / "_internal"
    out.mkdir()
    rc = _main(tmp_path, venv, out, tmp_path / "nope.toc")
    assert rc == 1


def test_paths_are_required_args() -> None:
    """少传参数必须直接失败：旧版靠硬编码默认值静默空转，正是 T04184 本体"""
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["--dist-internal", "x"])
    assert excinfo.value.code != 0


def test_no_hardcoded_repo_path_in_script_code() -> None:
    """去掉模块 docstring（里面是取证叙述，允许提到旧路径）后，代码里不该有仓库绝对路径"""
    text = _SYNC_PY.read_text(encoding="utf-8")
    doc_node = ast.parse(text).body[0]
    lines = text.splitlines(keepends=True)
    # 按源码行区间剔除 docstring：ast.get_docstring 返回的是反转义后的文本，
    # 与源里写着的 `D:\\code\\...` 不逐字相同，直接 str.replace 会剔不掉。
    code_only = "".join(lines[: doc_node.lineno - 1] + lines[doc_node.end_lineno :])
    offenders = [line.strip() for line in code_only.splitlines()
                 if re.search(r"[A-Za-z]:[\\/]{1,2}(?:code|Users)[\\/]", line, re.I)]
    assert not offenders, f"脚本残留硬编码路径：{offenders}"


def test_build_script_passes_all_three_paths() -> None:
    """调用方必须真的传参：required 只在传参到位时才有意义"""
    text = _BUILD_PS1.read_text(encoding="utf-8")
    for flag in ("--venv-site-packages", "--dist-internal", "--pyz-toc"):
        assert flag in text, f"build-exe.ps1 未传 {flag}"
    assert "PYZ-00.toc" in text, "未把 PYZ toc 传进来，脚本无从区分预期跳过与真实缺口"
