"""打包产物体积归因：读取 PyInstaller 的 COLLECT/PYZ 清单 + 外置资源目录，
输出分组件体积表与瘦身杠杆测算。数据口径见 docs/requirements/打包体积优化评估报告-20261102.md。

用法（构建之后运行，workpath 默认 .cache/pyinstaller-work）：
    python scripts/analyze_bundle_size.py [--workpath .cache/pyinstaller-work/xianyu-hunter]
"""
from __future__ import annotations

import argparse
import ast
import collections
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MB = 1048576.0

# Windows 控制台默认 GBK 代码页，中文表头会变乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_toc(path: Path):
    top = ast.literal_eval(path.read_text(encoding="utf-8"))
    # 两个清单都是 (条目列表, 归档路径) 形态；PYZ 反过来是 (归档路径, 条目列表)
    for candidate in top if isinstance(top, tuple) else (top,):
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], (list, tuple)):
            return candidate
    raise ValueError(f"无法解析清单结构: {path}")


def _dir_size(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workpath", default=".cache/pyinstaller-work/xianyu-hunter")
    args = parser.parse_args()

    work = REPO_ROOT / args.workpath
    collect_toc = work / "COLLECT-00.toc"
    pyz_toc = work / "PYZ-00.toc"
    if not collect_toc.is_file():
        print(f"[ERROR] 未找到 {collect_toc}，请先执行 scripts/build-exe.ps1")
        return 1

    exe = 0
    internal: dict[str, int] = collections.Counter()
    files: dict[str, int] = collections.Counter()
    total = 0
    for dest, source, typ in _load_toc(collect_toc):
        try:
            size = os.path.getsize(source)
        except OSError:
            continue
        total += size
        if typ == "EXECUTABLE":
            exe += size
            continue
        key = dest.replace("\\", "/").split("/")[0]
        internal[key] += size
        files[key] += 1

    print("== PyInstaller 清单 ==")
    print(f"EXE                  : {exe / MB:9.2f} MB")
    print(f"_internal (bin/data) : {(total - exe) / MB:9.2f} MB")
    print(f"\n{'组件':<28}{'体积':>12}{'文件数':>9}{'占 _internal':>14}")
    internal_total = total - exe
    for key, size in internal.most_common(15):
        print(f"{key:<28}{size / MB:10.2f} MB{files[key]:9d}{100 * size / internal_total:13.1f}%")

    if pyz_toc.is_file():
        src: dict[str, int] = collections.Counter()
        mods: dict[str, int] = collections.Counter()
        for name, path, _typ in _load_toc(pyz_toc):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            top = name.split(".")[0]
            src[top] += size
            mods[top] += 1
        pyz_total = sum(src.values())
        ratio = exe / pyz_total if pyz_total else 0.0
        print(f"\n== PYZ 纯 Python（源码 {pyz_total / MB:.2f} MB → EXE {exe / MB:.2f} MB，压缩比 {ratio:.3f}）==")
        for key, size in src.most_common(12):
            print(f"{key:<28}{size / MB:10.2f} MB{mods[key]:9d} 模块")

    print("\n== 外置资源 ==")
    pw = REPO_ROOT / ".cache/playwright_browsers"
    for child in sorted(pw.iterdir()) if pw.is_dir() else []:
        size = _dir_size(child)
        if child.is_dir() and child.name.startswith("chromium_headless_shell"):
            marker = "（构建时剔除，不进包）"
        else:
            marker = ""
        print(f"{child.name:<30}{size / MB:10.2f} MB {marker}")
    for label, path in (
        ("models/bge-small-zh-v1.5", REPO_ROOT / ".cache/models/bge-small-zh-v1.5"),
        ("release/spa", REPO_ROOT / "release/spa"),
    ):
        print(f"{label:<30}{_dir_size(path) / MB:10.2f} MB")
    # 「实际进包」必须以 dist 为准：Lite 包不拷 playwright_browsers，
    # 缓存里即便有 Chromium 也不代表安装包带了它（T04103）
    pw_dist = REPO_ROOT / "release/xianyu-hunter/playwright_browsers"
    shipped = _dir_size(pw_dist)
    if not (REPO_ROOT / "release/xianyu-hunter").is_dir():
        suffix = "（dist 不存在，尚未构建）"
    elif not shipped:
        suffix = "（Lite 形态：不随包）"
    else:
        suffix = ""
    print(f"{'chromium 实际进包':<27}{shipped / MB:10.2f} MB{suffix}")

    print("\n== 瘦身杠杆（engine=onnx 时从 _internal 消失的组件）==")
    for key in ("torch", "transformers", "scipy", "scipy.libs", "sklearn"):
        print(f"  -{key:<24}{internal.get(key, 0) / MB:9.2f} MB")
    onnx_leak = internal.get("onnx", 0) + sum(v for k, v in internal.items() if k.startswith("onnx-"))
    print(f"  -{'onnx（构建期泄漏）':<22}{onnx_leak / MB:9.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
