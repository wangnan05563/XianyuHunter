# sync-sentence-transformers.py
# 补齐 PyInstaller 输出的 sentence_transformers 松散目录（若该目录存在）
"""
为什么需要这一步：某些 PyInstaller 收集形态会把 sentence_transformers 落到
`_internal/sentence_transformers/` 松散目录但漏掉 `__init__.py` 等非 .py 资源，
需要按构建 venv 的包体补齐。

判据为什么改成「PYZ 收录结果」（T04184 取证）：
- 原脚本把 `venv_st`/`dist_st` 两条路径硬编码成 `D:\\code\\otherProjects\\17_xianyu\\...`，
  而 `build-exe.ps1` 调它时不传参 —— 换个仓库位置就必然走「目录不存在」分支。
- 实测当前 spec（ST 5.7.0）把 185 个 ST 子模块收进 PYZ，构建产物 `_internal/` 下
  根本没有 `sentence_transformers` 目录（`COLLECT-00.toc` 命中 0 条）。也就是说
  「无文件可补」是正常形态，原脚本却为此打印 `[WARN] ... dirs not found, skip`，
  看起来像 RAG 打包缺陷，实际只是步骤空转。
- 现在把「预期跳过」和「真实缺口」分开：PYZ 完整覆盖 `collect_submodules` 实算出的期望集
  → `[SKIP]` 且退出码 0；PYZ 漏收、或既没收录也没有松散目录 → `[ERROR]` 且退出码非 0，
  那才是冻结包缺 embedding 后端。

退出码：0=已补齐或属预期跳过；1=真实缺口（缺包体 / PYZ 未收录 / 路径参数不对）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

PKG = "sentence_transformers"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # 不保留硬编码默认值：路径由 build-exe.ps1 显式传入，缺参数直接报错，
    # 避免在别的机器/目录上静默指向不存在的路径（那正是旧版空转的根因）。
    parser = argparse.ArgumentParser(
        description="补齐 PyInstaller 的 sentence_transformers 松散目录；PYZ-only 形态下属预期跳过"
    )
    parser.add_argument("--venv-site-packages", required=True,
                        help="构建 venv 的 site-packages 目录")
    parser.add_argument("--dist-internal", required=True,
                        help="PyInstaller 产物 _internal 目录")
    parser.add_argument("--pyz-toc", required=True,
                        help="PyInstaller workpath 下的 PYZ-00.toc，用于判定 ST 是否收进 PYZ")
    return parser.parse_args(argv)


def pyz_modules(toc_path: Path, pkg: str) -> set[str]:
    """从 PYZ-00.toc 取属于 pkg 的模块名集合。

    toc 是 Python repr 出来的源码样文本，模块名以 `'a.b.c'` 字面量出现。只匹配引号内
    的字面量，不把磁盘路径、数据文件名误算成模块。
    """
    text = toc_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"'(" + re.escape(pkg) + r"(?:\.[A-Za-z0-9_]+)*)'")
    return {m.group(1) for m in pattern.finditer(text)}


def sync_missing(venv_dir: Path, dist_dir: Path) -> int:
    """把 venv 包体里 dist 缺失的文件补进去（不覆盖已有文件），返回补齐数量"""
    missing = 0
    for root, _dirs, files in os.walk(venv_dir):
        rel = os.path.relpath(root, venv_dir)
        target = dist_dir if rel == "." else dist_dir / rel
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            if "__pycache__" in f:
                continue
            src = Path(root) / f
            dst = target / f
            if not dst.exists():
                shutil.copy2(src, dst)
                missing += 1
    return missing


def copy_dist_info(venv_site: Path, dist_internal: Path, pkg: str) -> list[str]:
    """补齐 `<pkg>-<ver>.dist-info`（松散目录形态下 importlib.metadata 才查得到版本）"""
    copied = []
    for d in sorted(venv_site.iterdir()):
        if not (d.name.startswith(pkg + "-") and d.name.endswith(".dist-info")):
            continue
        dst = dist_internal / d.name
        if not dst.exists():
            shutil.copytree(d, dst)
            copied.append(d.name)
    return copied


def expected_modules(pkg: str) -> set[str] | None:
    """从当前依赖树实算「应收录」的模块集；实算不出就返回 None，降级为只核对 toc。

    本步跑在 `.venv-build` 的解释器里，`collect_submodules` 与 spec 看到的是同一套依赖树，
    所以「期望集 − PYZ 集 = 空」就是完整覆盖，不需要拍一个数字当下限。
    代价：会 import 一次 torch/ST（实测约 1 分钟），相对完整构建 ~28 分钟可接受。
    """
    try:
        from PyInstaller.utils.hooks import collect_submodules
    except Exception:
        return None
    try:
        return set(collect_submodules(pkg))
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    venv_site = Path(args.venv_site_packages)
    dist_internal = Path(args.dist_internal)
    pyz_toc = Path(args.pyz_toc)
    venv_pkg = venv_site / PKG
    dist_pkg = dist_internal / PKG

    if not pyz_toc.exists():
        print(f"[ERROR] PYZ toc 不存在: {pyz_toc}（--pyz-toc 要指向 --workpath 下的 PYZ-00.toc）")
        return 1
    collected = pyz_modules(pyz_toc, PKG)

    if dist_pkg.exists():
        # 松散目录形态：原语义仍然有效，按 venv 包体补齐缺文件
        if not venv_pkg.exists():
            print(f"[ERROR] 产物有松散目录但构建 venv 缺包体: {venv_pkg}"
                  "（dist-info 壳在、包体被删时 pip 不会自动修复）")
            return 1
        n = sync_missing(venv_pkg, dist_pkg)
        copied = copy_dist_info(venv_site, dist_internal, PKG)
        print(f"[SYNC] 松散目录补齐 {n} 个文件"
              + (f"；dist-info 复制 {', '.join(copied)}" if copied else "；dist-info 已存在"))
        return 0

    if collected:
        # 松散目录不在，但 ST 以字节码进了 PYZ —— 覆盖比对才是这一步真正的自检
        expected = expected_modules(PKG)
        if expected is None:
            print(f"[SKIP] 属预期：{PKG} 的 {len(collected)} 个子模块已收进 PYZ；"
                  f"依赖树不可实算（PyInstaller 不可用），未做覆盖比对，{dist_pkg} 不存在无需修复")
            return 0
        gap = sorted(expected - collected)
        if gap:
            print(f"[ERROR] PYZ 漏收 {len(gap)}/{len(expected)} 个 {PKG} 子模块："
                  f"{', '.join(gap[:10])} —— spec 请求的集合没全进包，运行期会 import 失败")
            return 1
        print(f"[SKIP] 属预期：{PKG} 已完整收进 PYZ（{len(collected)}/{len(expected)} 个子模块），"
              f"{dist_pkg} 不存在，无松散目录需修复")
        return 0

    print(f"[ERROR] PYZ 未收录 {PKG}，产物里也没有松散目录 {dist_pkg} "
          "—— 冻结包缺本地 embedding 后端。"
          "自检：.venv-build\\Scripts\\python -c \"from sentence_transformers.sentence_transformer.model import SentenceTransformer\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
