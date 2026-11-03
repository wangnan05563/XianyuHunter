# -*- mode: python ; coding: utf-8 -*-
#
# XianyuHunter PyInstaller spec（onedir 模式）
#
# 入口：scripts/launcher.py（启动 uvicorn + 自动开浏览器 + 单实例锁的打包入口）
# pathex：scripts（入口与子进程脚本所在目录）+ backend（xianyu_hunter 包根）
#
# 关键 hiddenimports：
#   (a) Repository 动态 Mixin（repository_base.py 用 importlib.import_module 按字符串加载
#       10 个 repo_* 模块）。这些是字符串导入，PyInstaller 静态分析无法发现，必须显式声明，
#       否则运行到 Repository 组合时会 ModuleNotFoundError。
#   (b) uvicorn.* 的 loops/protocols/lifespan 子模块（uvicorn 运行时按配置动态导入，
#       PyInstaller 不一定能静态捕获）。
#   (c) chromadb / onnxruntime / sentence_transformers —— 含大量 C 扩展与动态子模块，
#       标准做法是用 collect_submodules 全量收集（见 docs/archive/评估项目打包EXE安装包可行性.md）。
#
# 关于 duckdb：chromadb 1.x 已不再将 duckdb 作为依赖引入（旧版 design doc 中的
#   collect_submodules('duckdb') 在当前依赖树下会因 ModuleNotFoundError 直接让 spec 崩溃），
#   因此本 spec 仅在 duckdb 实际可导入时才收集，缺失则安全跳过。
#
# 防御性收集：所有 collect_submodules 都包了 try/except，任一可选 C 扩展包缺失都不会阻断构建。
#
# 其余依赖（fastapi / torch / playwright / loguru 等）均为静态 import，由 PyInstaller
#   模块发现 + 各包 hook 自动收集（含约 5600 个包内 DATA 文件），无需在此显式列出。
#
# 运行时只读资源（static / scripts / models / resources / playwright_browsers）由 build-exe.ps1
#   的"复制外置资源"步骤放入 release/xianyu-hunter/，本 spec 不再重复打包，避免路径错乱。
#
# 配置 config/*.yaml 在冻结模式从 %APPDATA%/XianyuHunter/config 读取（运行时目录，
#   非打包内），本 spec 不打包配置。
#
# ===== 本地 embedding 引擎切换（安装包瘦身 P0）=====
# 由环境变量 XH_EMBEDDING_ENGINE 控制（build-exe.ps1 在 PyInstaller 前设置）：
#   - "st"（默认）：sentence-transformers(torch) 后端，行为不变（约 +320MB）
#   - "onnx"：ONNX Runtime 后端，exclude torch / sentence_transformers / transformers，
#     运行时只靠 onnxruntime + tokenizers(Rust) + numpy，安装包可减 ~320MB。
# 注：tokenizers 在两种模式下都需要（ONNX 后端运行期也要分词），故始终收集，不 exclude。

import os

from PyInstaller.utils.hooks import collect_submodules

# 读取构建期 embedding 引擎选择；缺省 "st"（与历史构建一致）
_EMBEDDING_ENGINE = os.environ.get("XH_EMBEDDING_ENGINE", "st").strip().lower()
if _EMBEDDING_ENGINE not in ("st", "onnx"):
    # 防御：未知值回退到 st，避免构建出无法加载 embedding 的包
    _EMBEDDING_ENGINE = "st"

repo_root = os.path.dirname(os.path.abspath(SPEC))  # spec 所在目录 = 仓库根


def safe_collect_submodules(pkg_name):
    """收集子模块；若顶层包不可导入（缺失/可选依赖）则安全返回空列表，不阻断构建。"""
    try:
        return collect_submodules(pkg_name)
    except Exception:
        return []


# (c) 含 C 扩展的依赖全量子模块收集（缺失则跳过）
hiddenimports = []
_chroma_mods = safe_collect_submodules('chromadb')
# chromadb 1.5.9 的默认后端是 chromadb.api.rust.RustBindingsAPI（chromadb/__init__.py 实测）。
# 该子树一旦没进包，冻结包的向量检索/RAG 会在运行期才炸，而构建期一片绿灯——
# T04093 取证到的正是这种形态（构建 venv 里 python-dotenv 只剩 dist-info 壳，
# collect_submodules 抛 ModuleNotFoundError 被 safe_collect_submodules 吞成空列表）。
# 断言按「收集结果」而非「是否抛异常」：collect_submodules 对可选子树
# （chromadb.server.fastapi 缺 opentelemetry）只告警不抛，全量 raise 会误伤正常构建。
if "chromadb.api.rust" not in _chroma_mods:
    raise RuntimeError(
        "[spec] chromadb 子模块收集缺少 chromadb.api.rust（仅收到 %d 个），"
        "打包版向量检索会断链，构建中止。根因通常是构建 venv 依赖残缺"
        "（dist-info 壳在、包体已被删；pip 会判为已安装而跳过修复）。修复命令：\n"
        "  .venv-build\\Scripts\\python -m pip install --force-reinstall --no-deps "
        "python-dotenv protobuf python-dateutil\n"
        "自检命令：\n"
        "  .venv-build\\Scripts\\python -c \"import chromadb.api.rust; print('ok')\""
        % len(_chroma_mods)
    )
print(f"[spec] chromadb 收集 {len(_chroma_mods)} 个子模块（含 chromadb.api.rust）")
hiddenimports += _chroma_mods
hiddenimports += safe_collect_submodules('onnxruntime')
# tokenizers：ONNX 后端运行期需要分词，两种引擎模式都保留（不 exclude）
hiddenimports += safe_collect_submodules('tokenizers')
if _EMBEDDING_ENGINE == "onnx":
    # onnx 模式：不收集 sentence_transformers（已被 exclude），省去 torch 子模块收集
    logger_extra = f"[spec] embedding_engine=onnx：跳过 sentence_transformers 收集，准备 exclude torch"
    print(logger_extra)
else:
    # st 模式（默认）：收集 sentence-transformers 全部子模块（含其 tokenizers 依赖）
    hiddenimports += safe_collect_submodules('sentence_transformers')
hiddenimports += safe_collect_submodules('duckdb')  # chromadb 1.x 通常不再需要，缺失则跳过

# (b) uvicorn 动态子模块
hiddenimports += [
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
]

# (a) Repository 动态 Mixin（importlib.import_module 字符串加载，必须显式声明）
hiddenimports += [
    'xianyu_hunter.infra.repo_tasks',
    'xianyu_hunter.infra.repo_items',
    'xianyu_hunter.infra.repo_events',
    'xianyu_hunter.infra.repo_error_logs',
    'xianyu_hunter.infra.repo_deps',
    'xianyu_hunter.infra.repo_links',
    'xianyu_hunter.infra.repo_evaluations',
    'xianyu_hunter.infra.repo_orders',
    'xianyu_hunter.infra.repo_notifications',
    'xianyu_hunter.infra.repo_sellers',
]

# 构建期/测试期专用依赖（T04104 卫生项）：运行期 0 处 import，但 PyInstaller 会经
# 间接 import 链把它们收进 PYZ（实测 onnx 266 个模块 + _pytest 78 + pluggy 8 + iniconfig 3）。
# - onnx：仅 scripts/export_embedding_onnx.py 在构建期使用；onnxruntime 运行期不依赖它
# - pytest 家族：backend/ 与 scripts/launcher.py 均无 import，纯属 numpy.testing 等可选链拉入
# - PIL.AvifImagePlugin：托盘图标是 Image.new 现画的 RGBA，从不 open 外部图片；
#   且 Pillow 的 Image.init() 对单个插件 ImportError 是宽容的（只 debug 日志）
_build_only_excludes = [
    'onnx',
    '_pytest', 'pytest', 'pluggy', 'iniconfig',
    'PIL.AvifImagePlugin',
]

# onnx 模式：排除 torch / sentence_transformers / transformers（约 -320MB）
# 注意保留 tokenizers（ONNX 后端运行期仍需分词，由上面的 hiddenimports 收集）
# 追加 scipy / sklearn：二者仅被 sentence-transformers 链路需要（pip show Required-by 实测），
# 但 PyInstaller 会经间接 import 链（非 ST 路径的潜在 import）继续收集（实测 -320MB 后仍占 67MB），故显式排除。
if _EMBEDDING_ENGINE == "onnx":
    _excludes = _build_only_excludes + [
        'torch', 'sentence_transformers', 'transformers', 'scipy', 'sklearn',
    ]
    print("[spec] embedding_engine=onnx：exclude torch/sentence_transformers/transformers/scipy/sklearn")
else:
    _excludes = list(_build_only_excludes)
print(f"[spec] 卫生排除：{', '.join(_build_only_excludes)}")

a = Analysis(
    [os.path.join(repo_root, 'scripts', 'launcher.py')],
    pathex=[os.path.join(repo_root, 'scripts'), os.path.join(repo_root, 'backend')],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ===== UPX 决策（T04104）：显式关闭 =====
# 原配置 upx=True，但构建机 PATH 无 upx 且 spec 未配 upx_dir，实际收益恒为 0（空转）；
# 若真启用，DLL 经 UPX 压缩有杀软误报前科（旧评估文档 P1 第 5 条），而 PyInstaller 的
# PYZ 已用 lzma 压缩，UPX 对 DLL 的边际收益有限。结论：撤掉空转开关，不做「看起来开了
# 其实没开」的配置。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='xianyu-hunter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(repo_root, 'assets', 'xianyu-hunter.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='xianyu-hunter',
)
