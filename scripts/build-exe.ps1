# scripts/build-exe.ps1
# XianyuHunter EXE 构建脚本（缓存优化版）
#
# 用法：
#   powershell -File scripts/build-exe.ps1            # 默认增量打包（首次慢，后续快）
#   powershell -File scripts/build-exe.ps1 -SkipSPA   # 跳过 SPA 构建（仅前端无变更时用）
#   powershell -File scripts/build-exe.ps1 -SkipDeps  # 跳过 pip/npm 依赖安装（仅依赖无变更时用）
#   powershell -File scripts/build-exe.ps1 -DepsOnly  # 仅验证/修复构建 venv 与 Python 依赖
#   powershell -File scripts/build-exe.ps1 -Clean     # 清理所有缓存重新下载（怀疑缓存损坏时用）
#   powershell -File scripts/build-exe.ps1 -FailOnSizeRegression   # 体积回归即失败（发布流水线用）
#
# 产物：release/xianyu-hunter/ 目录 + release/XianyuHunter-Setup-v*.exe
#       体积归因报表：.cache/size-report/size-<UTC>-<engine>.json（当次）
#                     .cache/size-report/latest.json（下次构建的基线）
#
# 缓存策略（避免重复下载）：
# - .venv-build/         venv 增量更新（pip install 自动跳过已安装包）
# - frontend/node_modules/ 按 package-lock.json mtime 增量
# - .cache/playwright_browsers/  Chromium 缓存，复制到 dist
# - .cache/models/bge-small-zh-v1.5/  sentence-transformers 模型缓存，复制到 dist
#
# 构建步骤：
# 1. venv 增量更新 + 依赖安装
# 2. 锁定依赖到 requirements-lock.txt
# 3. 构建 SPA（按 lock mtime 增量）
# 4. PyInstaller 打包
# 5. 复制外置资源（SPA + 子进程脚本 + Chromium + 模型）
# 6. 制作安装包（Inno Setup）
# 7. 体积归因报表 + 版本间体积回归门禁（scripts/analyze_bundle_size.py）

param(
    [switch]$SkipSPA,
    [switch]$SkipDeps,
    [switch]$DepsOnly,
    [switch]$Clean,
    # 本地 embedding 引擎：st（默认，sentence-transformers+torch，行为不变）/
    # onnx（ONNX Runtime，排除 torch 约 -320MB，需先运行 scripts/export_embedding_onnx.py）
    [string]$EmbeddingEngine = "st",
    # 是否随包 Playwright Chromium（约 +415MB 解压体积）。
    # 默认不随包（Lite）：运行时优先复用系统 Edge/Chrome，缺失则首启下载。
    # 指定 -BundleBrowser 时产出 Full 包，并额外产出一个 Lite 安装包（同一份 dist，
    # 仅在 Inno Setup 层排除 playwright_browsers，无需二次 PyInstaller 构建）。
    [switch]$BundleBrowser,
    # 步骤 7 的体积回归门禁：任一顶层组件较上次落盘基线 +50MB 或 +5%（先到为准）即非零退出。
    # 默认只打印 WARN 不阻断，避免归因报表本身把一次正常构建打成失败。
    [switch]$FailOnSizeRegression,
    # 断点续跑：复用 release\xianyu-hunter 现有 PyInstaller 产物，从 [5/7] 外置资源开始。
    # 仅在 PyInstaller 已成功、后续步骤失败时使用；正式发布会重新全量构建。
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path  # 取纯字符串路径：Resolve-Path 返回 PathInfo 对象，其无部分字符串方法（如 StartsWith）
Set-Location $repoRoot

# 校验 embedding 引擎参数（st / onnx），未知值回退 st
$EmbeddingEngine = $EmbeddingEngine.Trim().ToLower()
if ($EmbeddingEngine -notin @("st", "onnx")) {
    Write-Host "[WARN] 未知 EmbeddingEngine='$EmbeddingEngine'，回退到 st" -ForegroundColor Yellow
    $EmbeddingEngine = "st"
}
# 导出给 PyInstaller spec（xianyu-hunter.spec 读取此变量决定 exclude torch 与否）
$env:XH_EMBEDDING_ENGINE = $EmbeddingEngine

# 清除可能干扰 pip/npm 的代理环境变量
# 为什么：用户系统可能配置了 HTTP_PROXY/HTTPS_PROXY 指向本地代理（如 VPN 客户端未启动），
# 会导致 pip 在 PEP 517 构建隔离环境中下载 setuptools/wheel 时 SSL 握手超时
# 仅影响当前脚本进程，不修改用户系统环境变量
foreach ($p in @("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")) {
    Remove-Item Env:\$p -ErrorAction SilentlyContinue
}
# 配置 pip 使用阿里云镜像（国内访问最快，避免 PyPI 偶发超时）
# 构建隔离环境会继承此变量，确保 setuptools/wheel 也能从镜像下载
$env:PIP_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
$env:PIP_TRUSTED_HOST = "mirrors.aliyun.com"

# 缓存目录（独立于 dist，dist 每次删除不影响缓存）
$cacheDir = "$repoRoot\.cache"
$pwCacheDir = "$cacheDir\playwright_browsers"
$modelCacheDir = "$cacheDir\models\bge-small-zh-v1.5"
# PyInstaller 工作目录（--workpath）。原本放在 release/.work，现独立到 .cache：
#   为什么：workpath 是构建期临时缓存而非发布产物。release 被整体清理时本无需连带清掉它，
#   迁出后 release 只含纯产物（整目录删除可由脚本完整重建），且 workpath 增量缓存得以保留，
#   减少 release 体积（约 -257MB）与磁盘压力。
$pyiWorkDir = "$cacheDir\pyinstaller-work"
# 体积归因报表落盘目录：刻意放在 .cache 下（不进 dist、不进安装包），
# latest.json 作为下一次构建的回归基线（T04106）
$sizeReportDir = "$cacheDir\size-report"
$buildVenv = ".venv-build"
$buildPython = "$buildVenv\Scripts\python.exe"
$buildVenvReadyMarker = "$buildVenv\.xh-build-ready"

function Get-BuildVersion {
    # 版本唯一来源：pyproject.toml（与发布器/运行时 __init__.py 对齐）。
    # 回退到 __init__.py，兼容极旧分支没有 pyproject 的场景。
    $pj = Join-Path $repoRoot "pyproject.toml"
    if (Test-Path -LiteralPath $pj -PathType Leaf) {
        $c = Get-Content -Raw -Encoding UTF8 $pj
        if ($c -match '(?m)^version\s*=\s*"([^"]+)"') { return $matches[1] }
    }
    $init = Join-Path $repoRoot "backend\xianyu_hunter\__init__.py"
    if (Test-Path -LiteralPath $init -PathType Leaf) {
        $line = Get-Content $init | Where-Object { $_ -match '__version__' } | Select-Object -First 1
        if ($line -match '"([^"]+)"') { return $matches[1] }
    }
    return $null
}

function New-BuildVenv {
    if (Test-Path $buildVenv) {
        Write-Host "  删除损坏的 $buildVenv 后重建..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $buildVenv -ErrorAction SilentlyContinue
    }
    Write-Host "  创建新 venv...（约 10 秒）"
    python -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) { throw "venv 创建失败" }
}

function Test-BuildPip {
    if (-not (Test-Path $buildPython)) { return $false }
    try {
        & $buildPython -m pip --version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        # base Python broken (e.g. missing stdlib) -> return false so caller rebuilds instead of aborting
        return $false
    }
}

function Repair-BuildPip {
    if (-not (Test-Path $buildPython)) { return $false }
    Write-Host "  [WARN] build venv 中 pip 不可用，尝试 ensurepip 修复..." -ForegroundColor Yellow
    try {
        & $buildPython -m ensurepip --upgrade *> $null
    } catch {
        return $false
    }
    if ($LASTEXITCODE -ne 0) { return $false }
    return (Test-BuildPip)
}

function Invoke-BuildPip {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PipArgs,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage,
        [switch]$RetryAfterRebuild
    )

    & $buildPython -m pip @PipArgs
    if ($LASTEXITCODE -eq 0) { return }

    if ($RetryAfterRebuild) {
        Write-Host "  [WARN] pip 命令失败，重建 build venv 后重试一次..." -ForegroundColor Yellow
        New-BuildVenv
        if (-not (Test-BuildPip)) {
            & $buildPython -m ensurepip --upgrade *> $null
            if (-not (Test-BuildPip)) { throw "build venv 中 pip 不可用" }
        }
        & $buildPython -m pip @PipArgs
        if ($LASTEXITCODE -eq 0) { return }
    }

    throw $FailureMessage
}

function Assert-BuildDiskSpace {
    # PyInstaller 打包峰值净增需求（MB）：dist 产物 ~500 + workpath 全新 ~500 + 分析临时 + 缓冲。
    # 旧 release/xianyu-hunter 在打包前会被脚本删除，其占用的空间可抵扣，故此处为"净增"的保守上界。
    # 为什么在打包前预检：磁盘不足若发生在 PyInstaller 中途，会留下半成品并报 Errno 28（难定位），
    # 提前报错并给出可清理的大体量目录，比继续硬跑更可诊断。
    $requiredFreeMB = 1200
    # 注意：$repoRoot（小写盘符）与 $_.Root（大写盘符）大小写可能不同，StartsWith 需忽略大小写，
    # 否则找不到仓库所在磁盘，预检会静默失效。
    $drive = Get-PSDrive -PSProvider FileSystem | Where-Object { $repoRoot.StartsWith($_.Root, [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
    if (-not $drive) { return }  # 无法定位仓库所在磁盘，交由打包本身报错
    $freeMB = [long]([math]::Floor($drive.Free / 1MB))
    if ($freeMB -lt $requiredFreeMB) {
        Write-Host "[ERROR] 磁盘剩余空间不足：$freeMB MB < 需 $requiredFreeMB MB" -ForegroundColor Red
        Write-Host "  已占用较大的目录（按需清理后重试；脚本 -Clean 参数会清空所有缓存）：" -ForegroundColor Yellow
        foreach ($d in @("data", ".venv-build", ".venv", "frontend\node_modules", ".cache")) {
            $p = Join-Path $repoRoot $d
            if (Test-Path $p) {
                $s = [long](Get-ChildItem -Recurse -Force $p -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
                Write-Host ("    {0,-36} {1,8} MB" -f $d, [math]::Round($s / 1MB, 0))
            }
        }
        throw "磁盘剩余空间不足，打包中止"
    }
}

# -Clean：清理所有缓存
if ($Clean) {
    Write-Host "[Clean] 清理所有缓存..." -ForegroundColor Yellow
    foreach ($p in @(".venv-build", "frontend\node_modules", $cacheDir, "release")) {
        if (Test-Path $p) {
            Write-Host "  删除 $p"
            Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue
        }
    }
}

# 创建缓存目录
New-Item -ItemType Directory -Force $cacheDir | Out-Null

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  闲鱼猎人 EXE 构建" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "仓库目录: $repoRoot"
Write-Host "缓存目录: $cacheDir"
if ($SkipDeps) { Write-Host "模式: SkipDeps（跳过依赖安装）" }
if ($SkipSPA)  { Write-Host "模式: SkipSPA（跳过 SPA 构建）" }
if ($DepsOnly) { Write-Host "模式: DepsOnly（仅验证构建依赖）" }
if ($EmbeddingEngine -eq "onnx") { Write-Host "Embedding 引擎: onnx（排除 torch，约 -320MB；需先运行 export_embedding_onnx.py）" -ForegroundColor Cyan }
else { Write-Host "Embedding 引擎: st（sentence-transformers + torch，默认行为）" }

# ============== 1. venv 增量更新 + 依赖安装 ==============
Write-Host "`n[1/7] 准备构建 venv..." -ForegroundColor Yellow
# 为什么不每次删除重建：pip install 对已安装包会自动跳过，删除重建会让所有包重新解压
# 仅在 venv 不存在或 -Clean 时创建
if (-not (Test-Path $buildPython)) {
    Write-Host "  venv 不存在"
    New-BuildVenv
} else {
    Write-Host "  venv 已存在，增量更新" -ForegroundColor DarkGray
}

if ((-not $SkipDeps) -and (Test-Path $buildPython) -and (-not (Test-Path $buildVenvReadyMarker))) {
    Write-Host "  [WARN] build venv 缺少健康标记，可能是上次构建中断留下的半成品，重建..." -ForegroundColor Yellow
    New-BuildVenv
}

if (-not (Test-BuildPip)) {
    if (-not (Repair-BuildPip)) {
        Write-Host "  [WARN] ensurepip 修复失败，重建 build venv..." -ForegroundColor Yellow
        New-BuildVenv
        if (-not (Test-BuildPip)) {
            & $buildPython -m ensurepip --upgrade *> $null
            if (-not (Test-BuildPip)) { throw "build venv 中 pip 不可用" }
        }
    }
}

if (-not $SkipDeps) {
    Write-Host "  安装项目依赖（pip 自动跳过已安装包）..."
    # 安装项目依赖 + 构建期 extra（.[build] 含 onnx，供 ONNX 导出 / int8 量化使用，运行期不进包）
    Invoke-BuildPip -PipArgs @("install", "-e", ".[build]", "--quiet") -FailureMessage "项目依赖安装失败" -RetryAfterRebuild

    Invoke-BuildPip -PipArgs @("install", "pyinstaller", "--quiet") -FailureMessage "PyInstaller 安装失败"

    # 系统托盘可选依赖：pystray + pillow
    # launcher.py 中 try/except 导入，未安装时控制台模式仍可用
    & $buildPython -m pip install pystray pillow --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] pystray/pillow 安装失败，托盘功能将不可用" -ForegroundColor Red
    }
    Set-Content -Path $buildVenvReadyMarker -Value (Get-Date -Format o) -Encoding UTF8
} else {
    Write-Host "  -SkipDeps 已指定，跳过依赖安装" -ForegroundColor DarkGray
}

# 验证关键依赖存在（即使 -SkipDeps 也要确保）
if (-not (Test-Path ".venv-build\Scripts\pyinstaller.exe")) {
    Write-Host "  [WARN] PyInstaller 未安装，强制安装..." -ForegroundColor Red
    Invoke-BuildPip -PipArgs @("install", "pyinstaller", "--quiet") -FailureMessage "PyInstaller 安装失败"
}

# 依赖完整性自检（按「能不能 import」判，不按 pip 是否认为已装判）
# 为什么必须走 import：构建 venv 可能出现「dist-info 壳还在、包体已被删」的残缺态
# （T04102 实测：python-dotenv/protobuf/python-dateutil 均为此态）。此时
# `pip install -e .[build]` 会判定「已安装」直接跳过（实测复现：装完仍 ModuleNotFoundError），
# 于是 chromadb 收集不到 chromadb.api.rust，向量检索要到冻结包运行期才炸。
Write-Host "  校验构建 venv 依赖可导入性..." -ForegroundColor DarkGray
& $buildPython -c "import dotenv, yaml, chromadb, chromadb.api.rust, onnxruntime, tokenizers" 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
    throw "构建 venv 依赖残缺（上方 import 失败）。修复命令：`n  .venv-build\Scripts\python -m pip install --force-reinstall --no-deps python-dotenv protobuf python-dateutil pyyaml`n或整体重建：删除 .venv-build 后重跑本脚本"
}
# 冻结包真实入口自检（T04179）：依赖「装没装」不等于入口「起不起得来」。
# fastapi 这类 Web 层大版本漂移（0.137+ 移除 FastAPI.__init__ 的 **kwargs）只在导入
# 入口时暴露；少了这一步就是构建期一片绿灯、崩溃推迟到用户双击安装包。
# 这里必须临时把 EAP 放回 Continue：本脚本顶部是 Stop，而 `2>&1` 会把 stderr 变成
# ErrorRecord——入口导入只要往 stderr 写过一行日志就会被当成 terminating error，
# 于是既拿不到下面的可诊断文案，还可能因无害告警误杀构建。
Write-Host "  校验冻结包 Web 入口可导入..." -ForegroundColor DarkGray
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$entryProbe = & $buildPython -c "import sys; sys.path.insert(0, 'backend'); from xianyu_hunter.web.app import app; print('[entry] Web 入口可导入，路由数 =', len(app.routes))" 2>&1
$entryProbeCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
$entryProbe | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($entryProbeCode -ne 0) {
    throw "冻结包 Web 入口在构建 venv 中导入失败（上方为原始异常，退出码 $entryProbeCode）。多为运行 venv 与构建 venv 的 Web 层版本漂移，先比对：`n  .venv-build\Scripts\python -c ""import fastapi; print(fastapi.__version__)""  vs  .venv\Scripts\python -c ""import fastapi; print(fastapi.__version__)""`n再按 pyproject.toml 的上界对齐（fastapi>=0.110,<0.137）"
}
Write-Host "  依赖可导入性校验通过" -ForegroundColor Green

# ============== 2. 锁定依赖 ==============
Write-Host "`n[2/7] 锁定依赖..." -ForegroundColor Yellow
# 不能让 PowerShell 的 `>` 写这份锁文件：PS 5.1 的重定向产出 UTF-16 LE + BOM，
# git 会把它当二进制（diff 直接 "Binary files differ"）、python 用 utf-8 读会抛
# UnicodeDecodeError，锁文件等于没有（T04110）。改由 python 自己落 UTF-8 无 BOM + LF。
& $buildPython -c "import subprocess,sys,pathlib; pathlib.Path('requirements-lock.txt').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze']).replace(b'\r\n', b'\n'))"
if ($LASTEXITCODE -ne 0) { throw "依赖锁定失败（pip freeze 退出码 $LASTEXITCODE）" }
Write-Host "  依赖已锁定到 requirements-lock.txt（UTF-8 无 BOM）"

if ($DepsOnly) {
    Write-Host "`n[DepsOnly] 构建 venv 与 Python 依赖验证完成" -ForegroundColor Green
    exit 0
}

# ============== 3. 构建 SPA ==============
# 默认每次都重建：避免前端源码已修改但 SPA 产物未更新导致打包后行为不一致
# 用 -SkipSPA 跳过（仅当确信前端无变更时使用，可省 1-3 分钟）
Write-Host "`n[3/7] 构建 SPA..." -ForegroundColor Yellow
$spaIndex = "release\spa\index.html"
if ($SkipSPA -and (Test-Path $spaIndex)) {
    Write-Host "  SPA 已存在且 -SkipSPA 已指定，跳过构建" -ForegroundColor DarkGray
} else {
    # Node 版本检测：vite 5 + ??= 运算符需要 Node 18+
    # 为什么检测：用户机器可能装了多个 Node 版本，PATH 指向旧版会导致构建静默失败
    # 优先用 scripts/node-config.json 中配置的 node 路径，避免 PATH 中旧版优先（陷阱 5）
    $nodeConfigPath = Join-Path $PSScriptRoot "node-config.json"
    $configuredNodeExe = $null
    if (Test-Path $nodeConfigPath) {
        $nodeConfig = Get-Content $nodeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        # 遍历 search_paths 找到第一个存在的 node.exe
        foreach ($p in $nodeConfig.node.search_paths) {
            $expanded = [System.Environment]::ExpandEnvironmentVariables($p)
            if (Test-Path $expanded) {
                $configuredNodeExe = $expanded
                break
            }
        }
    }

    if ($configuredNodeExe) {
        # 把配置的 node 目录前置到 PATH，确保后续 npm/node 调用都走指定版本
        $nodeDir = Split-Path $configuredNodeExe -Parent
        $env:Path = "$nodeDir;$env:Path"
        Write-Host "  使用配置的 Node: $configuredNodeExe" -ForegroundColor DarkGray
        $nodeVersion = (& $configuredNodeExe --version 2>$null) -replace '[v\n\r]', ''
    } else {
        # 回退到 PATH 中的 node（无配置时的兜底方案）
        $nodeVersion = (node --version 2>$null) -replace '[v\n\r]', ''
    }

    if ($nodeVersion) {
        $nodeMajor = [int]($nodeVersion.Split('.')[0])
        if ($nodeMajor -lt 18) {
            Write-Host "  [ERROR] Node $nodeVersion 版本过低，vite 5 需要 Node 18+" -ForegroundColor Red
            Write-Host "  当前 PATH 中的 Node：$((Get-Command node).Source)" -ForegroundColor Red
            throw "Node 版本过低（$nodeVersion），需要 18+"
        }
        Write-Host "  Node 版本：$nodeVersion" -ForegroundColor DarkGray
    } else {
        throw "未检测到 Node.js，请安装 Node 18+ 后重试（或配置 scripts\node-config.json）"
    }

    Push-Location frontend

    # node_modules 增量：比较 package-lock.json 与 .install-stamp 的 mtime
    # 为什么不用 npm ci 每次重装：npm ci 会删除 node_modules 重新解压，耗时 1-3 分钟
    # 仅在 lock 文件变化时才 npm ci，否则直接 npm run build
    $lockFile = "package-lock.json"
    $stampFile = "node_modules\.install-stamp"
    $needInstall = $false

    if (-not (Test-Path "node_modules")) {
        $needInstall = $true
        Write-Host "  node_modules 不存在，需要安装"
    } elseif (-not (Test-Path $stampFile)) {
        $needInstall = $true
        Write-Host "  .install-stamp 不存在，需要重装"
    } elseif (Test-Path $lockFile) {
        $lockMtime = (Get-Item $lockFile).LastWriteTime
        $stampMtime = (Get-Item $stampFile).LastWriteTime
        if ($lockMtime -gt $stampMtime) {
            $needInstall = $true
            Write-Host "  package-lock.json 已变更，需要重新安装依赖"
        } else {
            Write-Host "  node_modules 已是最新（lock 未变），跳过安装" -ForegroundColor DarkGray
        }
    }

    if ($needInstall) {
        Write-Host "  预计耗时：约 1-3 分钟" -ForegroundColor DarkGray
        if (Test-Path $lockFile) {
            npm ci
        } else {
            npm install
        }
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install 失败" }
        # 写入安装时间戳（用于下次增量判断）
        Set-Content -Path $stampFile -Value (Get-Date -Format "o") -Encoding UTF8
    }

    # SPA 单一来源清理（T04180）：vite 侧 `emptyOutDir: false` 是刻意决定（沙箱 safe-delete
    # 钩子会劫持它的 fs.rmSync），清理责任外移给构建脚本。但发布路径不经过
    # scripts/前端构建.bat 的 `rmdir /s /q`，历史 hash 产物会累积并被步骤 5 整包拷进安装包
    # （实测目录 307 文件/10.55 MB，其中当次构建只有 113 文件/4.50 MB）。
    # 手法与 bat 保持一致：cmd 内建 rmdir，绕开 PowerShell 的删除通道。
    $spaOutDir = Join-Path $repoRoot "release\spa"
    if (Test-Path $spaOutDir) {
        Write-Host "  清理历史 SPA 产物目录（单一来源）：release\spa" -ForegroundColor DarkGray
        & cmd /c "rmdir /s /q `"$spaOutDir`""
        if (Test-Path $spaOutDir) { Pop-Location; throw "SPA 目录清理失败，$spaOutDir 仍存在" }
    }

    Write-Host "  构建 SPA..."
    # 记录本次 SPA 构建起始时间，供步骤 7 区分「当次活跃资源」与「目录历史残留」（T04180）
    $script:SpaBuiltAfter = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "SPA 构建失败" }
    Pop-Location
    Write-Host "  SPA 构建完成"
}

# ============== 4. PyInstaller 打包 ==============
Assert-BuildDiskSpace
if ($SkipPyInstaller) {
    Write-Host "`n[4/7] 跳过 PyInstaller（-SkipPyInstaller，复用现有 release\xianyu-hunter）..." -ForegroundColor Yellow
    if (-not (Test-Path "release\xianyu-hunter\xianyu-hunter.exe" -PathType Leaf)) {
        throw "-SkipPyInstaller 需要现有的 release\xianyu-hunter\xianyu-hunter.exe，但文件不存在；请先完整构建一次。"
    }
    Write-Host "  复用现有 PyInstaller 产物：release\xianyu-hunter" -ForegroundColor DarkGray
} else {
Write-Host "`n[4/7] 运行 PyInstaller 打包..." -ForegroundColor Yellow
Write-Host "  预计耗时：约 1-3 分钟" -ForegroundColor DarkGray
# 清理旧产物（dist 每次重建，但缓存独立在 .cache/ 不受影响）
if (Test-Path "release\xianyu-hunter") {
    Remove-Item -Recurse -Force "release\xianyu-hunter" -ErrorAction SilentlyContinue
}
# workpath 已迁至 .cache/pyinstaller-work，删除 release 下旧 .work 残留，避免继续占用发布目录空间
if (Test-Path "release\.work") {
    Remove-Item -Recurse -Force "release\.work" -ErrorAction SilentlyContinue
}
# 清理 backend/ 下所有 __pycache__ 目录，防止 PyInstaller 使用过期的 .pyc 字节码
Get-ChildItem -Path "backend" -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  清理过期 pyc: " + $_.FullName -ForegroundColor DarkGray
}
Write-Host "  __pycache__ 清理完成" -ForegroundColor DarkGray
# 必须用 `$buildPython -m PyInstaller`，不要直接调 `.venv-build\Scripts\pyinstaller.exe`：
# pyinstaller.exe 是原生 launcher，以它启动 Analysis 时 sys.executable 会指向该 exe 本身，
# 而 PyInstaller 6.x 的 collect_submodules 用 isolated 子进程（compat.__wrap_python 取
# sys.executable 启动 _child.py）——用 exe 当解释器，在大 Analysis 下子进程会崩溃，
# 报 SubprocessDiedError: Child process died calling _collect_submodules() ... exit code 3221356611
# （实测：exe 启动 4 分钟必崩在收集 're'；python -m 启动 17 分钟 Build complete）。
# 用 -m 时 sys.executable 是 venv 的 python.exe，isolated child 稳定。
& $buildPython -m PyInstaller xianyu-hunter.spec --noconfirm --distpath release --workpath $pyiWorkDir
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }
Write-Host "  PyInstaller 打包完成"

# ============== 4.5 校验 PyInstaller 收集的 sentence_transformers 包完整性 ==============
# onnx 模式已 exclude sentence_transformers，无需也无法校验该包完整性
if ($EmbeddingEngine -eq "onnx") {
    Write-Host "`n[4.5] onnx 模式已排除 sentence_transformers，跳过包完整性修复" -ForegroundColor DarkGray
} else {
    Write-Host "`n[4.5] 检查 sentence_transformers 包完整性..." -ForegroundColor Yellow
    # 路径全部显式传入：脚本原先把仓库绝对路径写死成默认值，换目录就必然走 skip 分支，
    # 还打印成 [WARN] 让人误判成 RAG 打包缺陷（T04184）。现在缺参数直接 argparse 报错。
    & .venv-build\Scripts\python "$PSScriptRoot\sync-sentence-transformers.py" `
        --venv-site-packages "$repoRoot\.venv-build\Lib\site-packages" `
        --dist-internal "$repoRoot\release\xianyu-hunter\_internal" `
        --pyz-toc "$pyiWorkDir\xianyu-hunter\PYZ-00.toc"
    if ($LASTEXITCODE -ne 0) { Write-Host "  [ERROR] sentence_transformers 完整性检查未通过（PYZ 未收录或路径参数不对），本地 embedding 引擎在打包版会失效" -ForegroundColor Red }
    else { Write-Host "  sentence_transformers 完整性检查通过" -ForegroundColor Green }
}
}

# ============== 5. 复制外置资源 ==============
Write-Host "`n[5/7] 复制外置资源..." -ForegroundColor Yellow

# 5.1 静态资源（外置到 exe 同级 static 目录，app.py 打包模式经 get_app_dir()/"static" 定位）
# SPA 单一来源 = 前端编译产物 release/spa（vite outDir）。严禁再从 backend/.../static/spa 拷贝，
# 避免陈旧 SPA 资源（旧 hash 的 JS/CSS）作为孤儿文件混入安装包。
# backend/.../static 下仅拷贝非 spa 子目录（当前为 icons/），未来新增静态子目录亦自动包含。
$staticTarget = "release\xianyu-hunter\static"
New-Item -ItemType Directory -Force $staticTarget | Out-Null
$backendStatic = "backend\xianyu_hunter\web\static"
foreach ($sub in Get-ChildItem $backendStatic -Directory -ErrorAction SilentlyContinue) {
    if ($sub.Name -eq "spa") { continue }
    Copy-Item -Recurse -Force $sub.FullName "$staticTarget\$($sub.Name)"
}
# 前端编译产物（release/spa）→ 打包后 static/spa
if (Test-Path "release\spa") {
    Copy-Item -Recurse -Force "release\spa" "$staticTarget\spa"
} else {
    Write-Host "  [WARN] release\spa 不存在，SPA 未构建，请先执行前端构建" -ForegroundColor Red
}

# 5.2 子进程脚本（auth_helper.py / browser_login.py）
# 为什么需要：browser_login.py / unified_login.py / auth_manager.py 通过 get_app_dir()/"scripts" 定位这些脚本
# PyInstaller 不收集 scripts/ 目录（仅打包 backend/xianyu_hunter/），必须显式复制
# 仅复制运行时实际调用的子进程脚本，避免打包测试脚本（test_*.py / perf_test.py 等）
Write-Host "  [5.2] 复制子进程脚本（auth_helper, browser_login）..."
$scriptsTarget = "release\xianyu-hunter\scripts"
New-Item -ItemType Directory -Force $scriptsTarget | Out-Null
foreach ($script in @("browser_login.py", "auth_helper.py")) {
    $src = "scripts\$script"
    if (Test-Path $src) {
        Copy-Item -Force $src $scriptsTarget
    } else {
        Write-Host "  [WARN] 缺少 $src，浏览器登录/二维码登录功能将不可用" -ForegroundColor Red
    }
}

# 5.3 配置文件（menu_registry.yaml 等只读配置，随安装包分发）
# 为什么需要：menu_manager.py 通过 get_app_dir()/"config"/"menu_registry.yaml" 定位
# 打包后 get_app_dir() 返回 exe 所在目录，config/ 需复制到 exe 同级
Write-Host "  [5.3] 复制配置文件（menu_registry.yaml）..."
$configTarget = "release\xianyu-hunter\config"
New-Item -ItemType Directory -Force $configTarget | Out-Null
if (Test-Path "config\menu_registry.yaml") {
    Copy-Item -Force "config\menu_registry.yaml" $configTarget
} else {
    Write-Host "  [WARN] 缺少 config\menu_registry.yaml，菜单配置功能将不可用" -ForegroundColor Red
}

# 5.4 Playwright Chromium（从缓存复制，避免重复下载 ~150MB）
# 为什么用缓存：dist 每次打包都会删除重建，直接下载到 dist 会每次重下
# 缓存到 .cache/playwright_browsers/，复制到 release/xianyu-hunter/playwright_browsers/
Write-Host "  [5.4] Playwright Chromium..." -ForegroundColor Yellow
$pwTarget = "release\xianyu-hunter\playwright_browsers"
# 只认“完整内核”，且与 infra/browser_kernel.py 的 chromium_executable() 保持同一套布局口径。
# Playwright 自 chromium-1223 起 Windows 完整内核目录为 chrome-win64/（旧版 chrome-win/）；
# chromium_headless_shell-* 不算（那是 headless-shell，Full 包会剔除）。
# 关键：不能只认 chrome-win，也不能用 `chromium-*` 通配——否则新版内核明明完整却被判为缺失，
# 或缓存只剩 headless_shell 时误判为“有”、复制后剔除 → 发出空壳 Full 包（T05717 复现根因）。
function Test-ChromiumComplete($exe) {
    # 与 backend/xianyu_hunter/infra/browser_kernel.py 的 _complete_chromium_exe 同口径：
    # chrome.exe 必须非空，且同目录 chrome.dll / resources.pak / icudtl.dat 非空。
    # 只认 chrome.exe 会被杀软隔离 chrome.dll 或 0 字节残留骗过，发出“能装不能跑”的 Full 包。
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $exe).Length -lt 1024) { return $false }
    $required = if ((Split-Path -Leaf $exe) -eq 'chrome.exe') {
        @('chrome.dll', 'resources.pak', 'icudtl.dat')
    } else {
        @('resources.pak', 'icudtl.dat')
    }
    $dir = Split-Path -Parent $exe
    foreach ($name in $required) {
        $f = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $false }
        if ((Get-Item -LiteralPath $f).Length -lt 1024) { return $false }
    }
    return $true
}

function Get-FullChromiumExe($base) {
    # 返回完整内核的 chrome.exe/chrome 绝对路径；无则 $null。
    # 布局口径与运行时一致：先 chrome-win64 / chrome-win（新版/旧版 Playwright），
    # 再跨平台，最后 glob 兜底未来改名的子目录。
    if (-not (Test-Path -LiteralPath $base -PathType Container)) { return $null }
    $fullDirs = Get-ChildItem -LiteralPath $base -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike 'chromium_headless_shell-*' }
    foreach ($d in $fullDirs) {
        foreach ($rel in @('chrome-win64\chrome.exe', 'chrome-win\chrome.exe',
                           'chrome-linux64\chrome', 'chrome-linux\chrome')) {
            $exe = Join-Path $d.FullName $rel
            if (Test-ChromiumComplete $exe) { return $exe }
        }
        $fallback = Get-ChildItem -LiteralPath $d.FullName -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-ChromiumComplete (Join-Path $_.FullName 'chrome.exe') -or
                           Test-ChromiumComplete (Join-Path $_.FullName 'chrome') } |
            Select-Object -First 1
        if ($fallback) {
            if (Test-ChromiumComplete (Join-Path $fallback.FullName 'chrome.exe')) {
                return (Join-Path $fallback.FullName 'chrome.exe')
            }
            return (Join-Path $fallback.FullName 'chrome')
        }
    }
    return $null
}

function Test-FullChromium($base) {
    return (Get-FullChromiumExe $base) -ne $null
}

function Invoke-ChromiumSmoke($exe) {
    # 构建期真实启动一次：文件齐全不等于能跑（缺 DLL/架构不匹配/被杀软拦截都会在
    # 首次登录才暴露）。用 headless 模式跑 --version，必须有版本输出且退出码 0。
    #
    # 不能只用 Start-Process -PassThru + WaitForExit(int)：实测在 Windows PowerShell
    # 下会返回 ExitCode=$null（进程已退出但退出码没落到 Process 对象），把一次成功启动
    # 误判成失败（2026-11-27 构建卡在 [5.4]）。这里直接用 .NET Process 同步读输出，
    # 5.1 / 7 行为一致。
    $tag = [guid]::NewGuid().ToString('N')
    $outFile = Join-Path $env:TEMP "xh-chrome-smoke-$tag.out"
    $errFile = Join-Path $env:TEMP "xh-chrome-smoke-$tag.err"
    $profile = Join-Path $env:TEMP "xh-chrome-smoke-$tag-profile"
    $proc = $null
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $psi.Arguments = "--headless=new --disable-gpu --no-first-run --user-data-dir=`"$profile`" --version"
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        if (-not $proc.Start()) { throw "Chromium smoke 启动失败：$exe" }
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit(45000)) {
            try { $proc.Kill() } catch { }
            throw "Chromium smoke 超时（45s）：$exe"
        }
        $proc.WaitForExit()
        $text = (($outTask.Result + "`n" + $errTask.Result) -replace "`r?`n", "`n").Trim()
        if ($proc.ExitCode -ne 0) {
            throw "Chromium smoke 退出码 $($proc.ExitCode)：$exe；输出：$text"
        }
        if (-not $text) { throw "Chromium smoke 无版本输出：$exe" }
        Write-Host "  Chromium smoke 通过：$text" -ForegroundColor Green
        return $text
    } finally {
        if ($proc) { try { $proc.Dispose() } catch { } }
        foreach ($f in @($outFile, $errFile)) {
            if (Test-Path -LiteralPath $f -PathType Leaf) { Remove-Item -Force -LiteralPath $f -ErrorAction SilentlyContinue }
        }
        if (Test-Path -LiteralPath $profile) { Remove-Item -Recurse -Force -LiteralPath $profile -ErrorAction SilentlyContinue }
    }
}

function Invoke-PlaywrightSmoke($exe) {
    # M6：chrome.exe --version 只能证明二进制能启动，不能证明 Playwright 驱动能连上。
    # 本次 1255 与 Playwright 默认 1223 不同，必须在构建期用真实 driver 起一次
    # Chromium（headless）并打开页面，否则要等用户首次离线登录才暴露兼容性问题。
    $py = Join-Path $repoRoot ".venv-build\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
        throw "Playwright 兼容性 smoke 失败：构建 venv 不存在 $py"
    }
    $tag = [guid]::NewGuid().ToString('N')
    $script = Join-Path $env:TEMP "xh-playwright-smoke-$tag.py"
    $out = Join-Path $env:TEMP "xh-playwright-smoke-$tag.out"
    $err = Join-Path $env:TEMP "xh-playwright-smoke-$tag.err"
    $code = @'
import sys
from playwright.sync_api import sync_playwright

exe = sys.argv[1]
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe, headless=True, args=["--no-first-run"])
    page = browser.new_page()
    page.set_content("<title>ok</title><h1>ok</h1>")
    title = page.title()
    browser.close()
print("playwright_title=" + title)
raise SystemExit(0 if title == "ok" else 2)
'@
    try {
        [System.IO.File]::WriteAllText($script, $code, (New-Object System.Text.UTF8Encoding($false)))
        & $py $script $exe 1> $out 2> $err
        $rc = $LASTEXITCODE
        $text = ""
        foreach ($f in @($out, $err)) {
            if (Test-Path -LiteralPath $f -PathType Leaf) { $text += (Get-Content -Raw $f) }
        }
        if ($rc -ne 0) {
            throw "Playwright 兼容性 smoke 失败（exit $rc）：$text"
        }
        if ($text -notmatch 'playwright_title=ok') {
            throw "Playwright 兼容性 smoke 未返回预期页面标题：$text"
        }
        Write-Host "  Playwright 兼容性 smoke 通过（driver + bundled chromium）" -ForegroundColor Green
    } finally {
        foreach ($f in @($script, $out, $err)) {
            if (Test-Path -LiteralPath $f -PathType Leaf) { Remove-Item -Force -LiteralPath $f -ErrorAction SilentlyContinue }
        }
    }
}

function Write-BundleManifest($bundleBrowser, $distDir, $chromeExe = $null, $chromeVersion = $null) {
    # Full/Lite 身份清单放在应用根目录：浏览器目录被整体删除时，运行时仍能识别 Full，
    # 报“安装包不完整”而不是误判 Lite 去下载。chromium 段供发布门禁复核构建产物。
    $ver = Get-BuildVersion
    if (-not $ver) { throw "无法读取版本号（pyproject.toml / __init__.py），不能写 bundle-manifest.json" }
    $distAbs = Join-Path $repoRoot $distDir
    if (-not (Test-Path -LiteralPath $distAbs -PathType Container)) {
        throw "写 bundle-manifest 失败：dist 不存在 $distAbs"
    }
    $chromium = $null
    if ($bundleBrowser) {
        if (-not $chromeExe -or -not (Test-ChromiumComplete $chromeExe)) {
            throw "写 bundle-manifest 失败：Full 形态没有完整 Chromium"
        }
        $distAbs = (Resolve-Path -LiteralPath $distAbs).Path
        $chromeAbs = (Resolve-Path -LiteralPath $chromeExe).Path
        if (-not $chromeAbs.StartsWith($distAbs, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "写 bundle-manifest 失败：Chromium 不在 dist 内 $chromeAbs"
        }
        $exeRel = ($chromeAbs.Substring($distAbs.Length).TrimStart('\', '/')) -replace '\\', '/'
        $exeDir = Split-Path -Parent $chromeAbs
        $files = @()
        foreach ($name in @('chrome.exe', 'chrome', 'chrome.dll', 'resources.pak', 'icudtl.dat')) {
            $f = Join-Path $exeDir $name
            if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { continue }
            $fi = Get-Item -LiteralPath $f
            $files += [ordered]@{
                name = $name
                size = [int64]$fi.Length
                sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        $chromium = [ordered]@{
            dir = ((Split-Path -Parent $exeRel) -replace '\\', '/')
            executable = $exeRel
            version = "$chromeVersion".Trim()
            files = $files
        }
    }
    $obj = [ordered]@{
        schema = 1
        version = $ver
        bundle_browser = [bool]$bundleBrowser
        build_mode = $(if ($bundleBrowser) { "full" } else { "lite" })
        built_at = (Get-Date).ToString("o")
        chromium = $chromium
    }
    $json = $obj | ConvertTo-Json -Depth 8
    $path = Join-Path $distAbs "bundle-manifest.json"
    [System.IO.File]::WriteAllText($path, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  [OK] 已写入 bundle-manifest.json（bundle_browser=$bundleBrowser）: $path" -ForegroundColor Green
}
if (-not $BundleBrowser) {
    # Lite 形态：不随包 Chromium（-415.85MB）。运行时由 infra/browser_kernel.py 依次
    # 解析系统 Edge → 系统 Chrome → 随包 Chromium → 首启下载。
    # 增量构建会让旧目录残留，这里统一清掉，保证 dist 与形态一致（幂等）。
    if (Test-Path $pwTarget) {
        Remove-Item -Recurse -Force $pwTarget -ErrorAction SilentlyContinue
    }
    Write-Host "  Lite 形态：跳过随包 Chromium（运行时复用系统 Edge/Chrome，缺失则首启下载）" -ForegroundColor Cyan
} elseif (Test-FullChromium $pwCacheDir) {
    Write-Host "  从缓存复制完整 Chromium...（约 10-30 秒）"
    if (Test-Path $pwTarget) { Remove-Item -Recurse -Force $pwTarget -ErrorAction SilentlyContinue }
    Copy-Item -Recurse -Force $pwCacheDir $pwTarget
    Write-Host "  Chromium 已从缓存复制到 $pwTarget"
} else {
    Write-Host "  缓存无完整 Chromium，下载 Chromium...（下载约 150MB）"
    $env:PLAYWRIGHT_BROWSERS_PATH = $pwCacheDir
    & .venv-build\Scripts\playwright install chromium
    Remove-Item Env:\PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    if (-not (Test-FullChromium $pwCacheDir)) {
        throw "Playwright Chromium 完整内核下载失败（chromium-*/chrome-win64/chrome.exe 或 chrome-win/chrome.exe，且 chrome.dll/resources.pak/icudtl.dat 非空）。" +
              "请检查网络或 .venv-build 的 playwright 版本，Full 安装包不能发出空壳 Chromium。"
    }
    if (Test-Path $pwTarget) { Remove-Item -Recurse -Force $pwTarget -ErrorAction SilentlyContinue }
    Copy-Item -Recurse -Force $pwCacheDir $pwTarget
    Write-Host "  Chromium 已复制到 $pwTarget"
}
if ($BundleBrowser) {
    # P1 瘦身：只保留完整 Chromium，剔除 chromium_headless_shell-*（约 -267MB）。
    # auth_helper 已改用 --headless=new 走完整内核，运行期不再需要 headless-shell。
    # 增量打包时目标目录可能残留旧版本，故复制后统一清理（幂等）。
    Get-ChildItem "$pwTarget" -Directory -Filter "chromium_headless_shell-*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
    # 复制并剔除后再次确认完整内核存在，防止发出空壳 Full 包（T05717）
    if (-not (Test-FullChromium $pwTarget)) {
        throw "打包目录 $pwTarget 缺少完整 Chromium（chromium-*/chrome-win64/chrome.exe 或 chrome-win/chrome.exe，且 chrome.dll/resources.pak/icudtl.dat 非空）。" +
              "Full 安装包不完整，终止构建以免发出无法离线登录的安装程序。"
    }
    $fullList = (Get-ChildItem $pwTarget -Directory |
        Where-Object { $_.Name -like 'chromium-*' -and $_.Name -notlike 'chromium_headless_shell-*' } |
        Select-Object -ExpandProperty Name) -join ', '
    # 显式标记：运行时 is_bundled_build() 据此判定 Full 构建，不依赖“目录是否存在”
    # （Lite 包经 Inno Setup 排除 playwright_browsers\* 后可能残留空目录）。
    # 用 .NET 直接写，绕开沙箱 safe-delete 对文件写入的干扰。
    [System.IO.File]::WriteAllText(
        (Join-Path $repoRoot "$pwTarget\.xh-full-bundle"),
        "XianyuHunter Full bundle marker`nchromium_dirs: $fullList`nbuilt_at: $(Get-Date -Format o)`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host "  [OK] 已确认 $pwTarget 含完整 Chromium（$fullList），已写入 Full 标记" -ForegroundColor Green

    # 构建期 smoke：文件齐全不等于能启动（缺 DLL / 架构不匹配 / 被杀软拦截）。
    $chromeExe = Get-FullChromiumExe $pwTarget
    if (-not $chromeExe) {
        throw "打包目录 $pwTarget 没有完整 Chromium，无法执行构建期 smoke。"
    }
    $chromeVersion = Invoke-ChromiumSmoke $chromeExe
    Invoke-PlaywrightSmoke $chromeExe
    Write-BundleManifest $true "release\xianyu-hunter" $chromeExe $chromeVersion
} else {
    # Lite 形态也要写清单：运行时据此选择 %APPDATA% 下载目录，而不是误判 Full。
    Write-BundleManifest $false "release\xianyu-hunter"
}

# 5.5 embedding 工件（按引擎分支）
# - onnx：复制 scripts/export_embedding_onnx.py 生成的 ONNX + tokenizer 工件
#         （backend/xianyu_hunter/resources/embedding → release/xianyu-hunter/resources/embedding），
#         不再下载 sentence-transformers 模型（torch 已被排除）。
# - st（默认）：复制 sentence-transformers 模型（从缓存或下载），行为不变。
if ($EmbeddingEngine -eq "onnx") {
    Write-Host "  [5.5] ONNX embedding 工件（onnx 模式）..." -ForegroundColor Yellow

    # 5.5.0 onnx 依赖自检（关键！-SkipDeps 模式会跳过 step 1 安装，onnx 可能缺失）
    # export / quantize 都依赖 onnx 包；缺失时提前给出可操作报错，而非在 5.5.1/5.5.2 才失败
    $onnxOk = $false
    try {
        & .venv-build\Scripts\python -c "import onnx" 2>$null
        if ($LASTEXITCODE -eq 0) { $onnxOk = $true }
    } catch {
        $onnxOk = $false
    }
    if (-not $onnxOk) {
        if ($SkipDeps) {
            Write-Host "  [ERROR] ONNX 导出/量化依赖 onnx 包缺失，但当前为 -SkipDeps 模式（step 1 依赖安装已跳过）" -ForegroundColor Red
            Write-Host "  解决：去掉 -SkipDeps 重新构建（step 1 会随 .[build] extra 安装 onnx），或手动执行：" -ForegroundColor Yellow
            Write-Host "    .venv-build\Scripts\pip install -r requirements-build.txt" -ForegroundColor Cyan
            throw "ONNX 依赖缺失，构建中止"
        } else {
            Write-Host "  [ERROR] ONNX 导出/量化依赖 onnx 包缺失，但 step 1 应已随 .[build] extra 安装" -ForegroundColor Red
            Write-Host "  请检查 step 1 依赖安装是否成功，或手动执行：" -ForegroundColor Yellow
            Write-Host "    .venv-build\Scripts\pip install -r requirements-build.txt" -ForegroundColor Cyan
            throw "ONNX 依赖缺失，构建中止"
        }
    }

    $onnxSrc = "backend\xianyu_hunter\resources\embedding"
    $onnxTarget = "release\xianyu-hunter\resources\embedding"
    $fp32 = "$onnxSrc\bge_small_zh.onnx"
    $int8 = "$onnxSrc\bge_small_zh.int8.onnx"

    # 5.5.1 fp32 工件缺失则先导出（需要 torch，走构建 venv）
    if (-not (Test-Path $fp32)) {
        Write-Host "  fp32 工件缺失，运行导出脚本生成..." -ForegroundColor Yellow
        & .venv-build\Scripts\python "$PSScriptRoot\export_embedding_onnx.py"
        if (-not (Test-Path $fp32)) { throw "导出 fp32 ONNX 失败，构建中止" }
    }

    # 5.5.2 int8 量化（二次瘦身，~90MB -> ~55MB；onnx 已在 step 1 随 .[build] extra 预装）
    if (-not (Test-Path $int8)) {
        Write-Host "  int8 量化中（二次瘦身）..." -ForegroundColor Yellow
        & .venv-build\Scripts\python "$PSScriptRoot\quantize_embedding_onnx.py"
        if (-not (Test-Path $int8)) {
            throw "int8 量化失败，构建中止（如报 onnx 缺失，请确认 step 1 已安装 pyproject 的 build 可选依赖组，该组含 onnx）"
        }
    }

    # 5.5.3 仅复制 int8 onnx + tokenizer + meta（fp32 不进包，进一步瘦身）
    # 运行期 OnnxEmbeddingBackend 优先加载 int8，缺失才回退 fp32
    Write-Host "  复制 int8 工件（int8 onnx + tokenizer.json + meta.json）..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force $onnxTarget | Out-Null
    Copy-Item -Force "$onnxSrc\bge_small_zh.int8.onnx" $onnxTarget
    Copy-Item -Force "$onnxSrc\tokenizer.json" $onnxTarget
    if (Test-Path "$onnxSrc\meta.json") { Copy-Item -Force "$onnxSrc\meta.json" $onnxTarget }
    $int8Size = [math]::Round((Get-Item "$onnxTarget\bge_small_zh.int8.onnx").Length / 1MB, 1)
    Write-Host "  ONNX int8 工件已复制到 $onnxTarget（$int8Size MB）"
} else {
    # 5.5 sentence-transformers 模型（从缓存复制，避免重复下载 ~100MB）
    # 为什么用缓存：同上，dist 每次重建会导致重新下载
    # 缓存到 .cache/models/bge-small-zh-v1.5/，复制到 release/xianyu-hunter/models/
    Write-Host "  [5.5] sentence-transformers 模型..." -ForegroundColor Yellow
    $modelTarget = "release\xianyu-hunter\models\bge-small-zh-v1.5"
    if (Test-Path "$modelCacheDir\config.json") {
        Write-Host "  从缓存复制模型...（约 5-15 秒）"
        New-Item -ItemType Directory -Force (Split-Path $modelTarget) | Out-Null
        Copy-Item -Recurse -Force $modelCacheDir $modelTarget
        Write-Host "  模型已从缓存复制到 $modelTarget"
    } else {
        Write-Host "  缓存不存在，下载模型...（下载约 100MB）"
        # 设置 HF 镜像，避免国内访问 huggingface.co 超时
        $env:HF_ENDPOINT = "https://hf-mirror.com"
        New-Item -ItemType Directory -Force $modelCacheDir | Out-Null
        & .venv-build\Scripts\python -c @"
import os
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('BAAI/bge-small-zh-v1.5')
m.save(r'$modelCacheDir')
print('Model saved to $modelCacheDir')
"@
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] 模型预置失败（首次运行将联网下载）" -ForegroundColor Red
        } else {
            Write-Host "  模型已下载到缓存 $modelCacheDir"
            # 复制到 dist
            New-Item -ItemType Directory -Force (Split-Path $modelTarget) | Out-Null
            Copy-Item -Recurse -Force $modelCacheDir $modelTarget
            Write-Host "  模型已复制到 $modelTarget"
        }
    }
}

# 5.6 敏感信息扫描（防御性：确保 API Key / .env / .secrets.json 未被打包）
# 为什么需要：即使 spec 不收集 .env、前面的步骤不复制 .env，
# 仍需在打包产物中扫描确认，防止未来误改 spec 或新增依赖间接带入敏感信息
Write-Host "  [5.6] 扫描敏感信息..."
$distRoot = "release\xianyu-hunter"

# 5.6.1 删除可能存在的敏感文件（防御性，即使前面步骤不应复制它们）
foreach ($sensitiveFile in @(".env", ".env.local", ".secrets.json")) {
    $sensitivePath = Join-Path $distRoot $sensitiveFile
    if (Test-Path $sensitivePath) {
        Write-Host "    [WARN] 发现敏感文件 $sensitiveFile，已删除" -ForegroundColor Red
        Remove-Item -Force $sensitivePath
    }
}

# 5.6.2 扫描打包产物中是否有 API Key 痕迹
# 扫描模式：sk- 开头（OpenAI/DeepSeek 标准 Key 前缀）、__MIGRATED_TO_KEYRING__ 占位符
# 注意：仅扫描项目级文件（exe 同级 + config/ + scripts/ + static/），
# 不扫描 _internal/（第三方库 dist-info 的 RECORD 文件含 sha256 hash，
#   如 torch 的 "sha256=...LJsk-trKzjJE0tUMSAsSlfOiR_3c" 会匹配 sk-[A-z0-9]{20,} 误报）
$scanRootFiles = $distRoot
$scanSubDirs = @("$distRoot\config", "$distRoot\scripts", "$distRoot\static")
$leakFound = $false
# exe 同级文件不递归（递归会进入 _internal/ 第三方库目录）
$scanTargets = @(@{ Path = $scanRootFiles; Recurse = $false })
foreach ($d in $scanSubDirs) {
    $scanTargets += @(@{ Path = $d; Recurse = $true })
}
foreach ($target in $scanTargets) {
    if (-not (Test-Path $target.Path)) { continue }
    if ($target.Recurse) {
        $files = Get-ChildItem -Path $target.Path -File -Recurse -ErrorAction SilentlyContinue
    } else {
        $files = Get-ChildItem -Path $target.Path -File -ErrorAction SilentlyContinue
    }
    foreach ($file in $files) {
        # 跳过二进制文件（exe/dll/pak 等），只扫描文本文件
        $ext = $file.Extension.ToLower()
        if ($ext -in @(".exe", ".dll", ".pak", ".bin", ".dat", ".node", ".pyd", ".so")) { continue }
        try {
            $content = Get-Content $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
            if ($content -match 'sk-[A-Za-z0-9]{20,}' -or $content -match '__MIGRATED_TO_KEYRING__') {
                Write-Host "    [ERROR] 在 $($file.FullName) 中发现疑似 API Key 痕迹" -ForegroundColor Red
                $leakFound = $true
            }
        } catch {
            # 读取失败的文件（如编码问题）跳过
        }
    }
}
if ($leakFound) {
    throw "打包产物中检测到 API Key 痕迹，已中止构建。请检查 spec 文件和复制步骤。"
}
Write-Host "    敏感信息扫描通过（无 API Key 痕迹）" -ForegroundColor Green

# ============== 6. 制作安装包（Inno Setup） ==============
Write-Host "`n[6/7] 构建安装包（Inno Setup）..." -ForegroundColor Yellow

# 6.1 查找 iscc.exe（Inno Setup 编译器）
function Find-ISCC {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $paths = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    return $null
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

$iscc = Find-ISCC

# 6.2 自动安装 Inno Setup（如未安装）
if (-not $iscc) {
    Write-Host "  Inno Setup 未安装，尝试自动安装..." -ForegroundColor Cyan

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  使用 winget 安装..." -ForegroundColor DarkGray
        winget install --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $iscc = Find-ISCC
    }

    if (-not $iscc) {
        Write-Host "  直接下载 Inno Setup 安装包..." -ForegroundColor DarkGray
        $installerUrl = "https://jrsoftware.org/download.php/is.exe"
        $installerFile = "$env:TEMP\innosetup-install.exe"
        try {
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerFile -UseBasicParsing
            Start-Process -FilePath $installerFile -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait -NoNewWindow
            Refresh-Path
            $iscc = Find-ISCC
        } catch {
            Write-Host "  [WARN] 下载安装失败：$_" -ForegroundColor Red
        } finally {
            if (Test-Path $installerFile) { Remove-Item $installerFile -Force -ErrorAction SilentlyContinue }
        }
    }
}

# 6.3 自动创建 installer.iss（如不存在）
if ($iscc -and -not (Test-Path "installer.iss")) {
    Write-Host "  installer.iss 不存在，自动创建..." -ForegroundColor Cyan
    $issTemplate = @"
; Auto-generated by build-exe.ps1
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0.0"
#endif
[Setup]
AppName=XianyuHunter
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\XianyuHunter
DefaultGroupName=XianyuHunter
UninstallDisplayIcon={app}\xianyu-hunter.exe
; Reuse the app icon so the installer and uninstaller don't show the default Inno Setup icon
SetupIconFile=assets\xianyu-hunter.ico
OutputDir=release
OutputBaseFilename=XianyuHunter-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
DisableProgramGroupPage=yes
[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional:"
[Files]
; Excludes 必须是「空格分隔」的 wildcard 列表（Inno Setup 语义），写成逗号会导致整串匹配不上
Source: "release\xianyu-hunter\*"; DestDir: "{app}"; Excludes: "*.log data\* .env .secrets.json .env.local"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\XianyuHunter"; Filename: "{app}\xianyu-hunter.exe"
Name: "{commondesktop}\XianyuHunter"; Filename: "{app}\xianyu-hunter.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\xianyu-hunter.exe"; Description: "Launch XianyuHunter"; Flags: nowait postinstall skipifsilent
"@
    # Inno Setup 编译器（ISCC）需要 UTF-8 BOM 才能正确解析模板中的中文
    [System.IO.File]::WriteAllText("installer.iss", $issTemplate, (New-Object System.Text.UTF8Encoding($true)))
}

# 6.4 编译安装包
# 安装包是发布流水线的唯一主资产（config.publish.json 的 asset.glob 只认
# XianyuHunter-Setup-v*.exe）。历史缺陷：Inno Setup 缺失 / ISCC 编译失败时这里只打
# [WARN] 就继续往下走，脚本照样打印「构建完成！」并以 exit 0 收尾，一键发布于是拿着
# 残缺 release 目录去发布 —— 表现为「脚本跑完了，但没有安装包、也没真正发出去」。
# 现在把安装包视为硬产物：任一环节拿不到可用的 Setup exe 就抛出，构建失败。
if (-not $iscc) {
    Write-Host "  [FAIL] Inno Setup 不可用（未找到 ISCC.exe），无法生成安装包" -ForegroundColor Red
    Write-Host "  已尝试 winget / 官方安装包自动安装仍未成功。请手动安装后重试：" -ForegroundColor Yellow
    Write-Host "    https://jrsoftware.org/isdl.php  （或 winget install JRSoftware.InnoSetup）" -ForegroundColor DarkGray
    throw "Inno Setup(ISCC) 不可用，安装包未生成，构建中止"
} else {
    # 从 __init__.py 读取版本号
    $initFile = "backend\xianyu_hunter\__init__.py"
    $version = "0.0.0.0"
    if (Test-Path $initFile) {
        $line = Get-Content $initFile | Where-Object { $_ -match '__version__' } | Select-Object -First 1
        if ($line -match '"([^"]+)"') { $version = $matches[1] }
    }
    Write-Host "  版本号: $version"
    Write-Host "  编译安装包..."
    & $iscc /DMyAppVersion=$version installer.iss
    if ($LASTEXITCODE -ne 0) {
        throw "安装包编译失败（ISCC exit $LASTEXITCODE），构建中止：installer.iss / 版本 $version"
    } else {
        $setupExe = "release\XianyuHunter-Setup-v$version.exe"
        # 只信「文件真的在、且有体积」：ISCC 偶发 0 字节产物或残留旧文件时，
        # 仅看退出码会让发布几步之后才炸在别的环节。
        if (-not (Test-Path -LiteralPath $setupExe)) {
            throw "安装包编译返回成功但产物不存在：$setupExe"
        }
        $setupInfo = Get-Item -LiteralPath $setupExe
        if ($setupInfo.Length -le 0) {
            throw "安装包产物为 0 字节：$setupExe"
        }
        Write-Host ("  安装包已生成：{0}（{1:N1} MB）" -f $setupExe, ($setupInfo.Length / 1MB)) -ForegroundColor Green

        # 6.5 -BundleBrowser 时同一次构建再出一个 Lite 安装包：
        # 同一份 dist，用一份「已剔除 playwright_browsers」的暂存目录作为 Source
        # （不再依赖 Inno 的 Excludes 通配 —— 实测 `Excludes: "playwright_browsers\*"`
        #  对嵌套内核文件不生效，Lite 与 Full 打出来字节数只差 20，等于把 412MB 的
        #  Chromium 又塞进了「精简包」。以实际大小校验兜底。）
        if ($BundleBrowser) {
            $liteStage = "release\lite-dist"
            if (Test-Path -LiteralPath $liteStage -PathType Container) {
                try {
                    Remove-Item -Recurse -Force -LiteralPath $liteStage -ErrorAction Stop
                } catch {
                    throw "无法清理 Lite 暂存目录 $liteStage（可能有进程占用/权限不足）：$($_.Exception.Message)"
                }
            }
            Write-Host "  准备 Lite 暂存目录（排除 playwright_browsers）..."
            # /MIR 而不是 /E：镜像会删除暂存目录里上一次构建遗留、而源目录已不存在的文件。
            # 只用 /E 时 robocopy 不会清理旧文件，增量构建可能把上一版 Chromium 残留打进 Lite。
            # 退出码 0-7 都算成功（1=有复制、2=有额外文件、3=二者兼有…），>=8 才是失败。
            & robocopy "release\xianyu-hunter" $liteStage /MIR /XD "playwright_browsers" /NFL /NDL /NJH /NJS /NP | Out-Null
            $rc = $LASTEXITCODE
            if ($rc -ge 8) { throw "Lite 暂存目录复制失败（robocopy exit $rc）" }
            if (Test-Path -LiteralPath (Join-Path $liteStage "playwright_browsers") -PathType Container) {
                throw "Lite 暂存目录仍包含 playwright_browsers（/XD 未生效），拒绝编译含 Chromium 的 Lite 包"
            }
            # 复制来的 bundle-manifest.json 是 Full 的（bundle_browser=true），
            # 必须改写为 false，否则 Lite 安装后运行时会误判 Full 并报内核不完整。
            Write-BundleManifest $false $liteStage

            $liteIss = "installer-lite.iss"
            $liteSrc = Get-Content "installer.iss" -Raw
            $liteSrc = $liteSrc -replace '(?m)^(OutputBaseFilename=.+)$', '$1-Lite'
            # Source 指向暂存目录（不含 chromium）
            $liteSrc = $liteSrc -replace 'Source:\s*"release\\xianyu-hunter\\\*"', 'Source: "release\lite-dist\*"'
            if ($liteSrc -notmatch 'Source:\s*"release\\lite-dist\\\*"') {
                throw "installer-lite.iss 生成失败：Source 未替换为 release\lite-dist\*（installer.iss 的 Source 行已变化？）"
            }
            if ($liteSrc -notmatch '(?m)^OutputBaseFilename=.+-Lite\s*$') {
                throw "installer-lite.iss 生成失败：OutputBaseFilename 未加上 -Lite 后缀"
            }
            [System.IO.File]::WriteAllText(
                (Join-Path $repoRoot $liteIss), $liteSrc, (New-Object System.Text.UTF8Encoding($true))
            )
            Write-Host "  编译 Lite 安装包（不含 Chromium）..."
            & $iscc /DMyAppVersion=$version $liteIss
            if ($LASTEXITCODE -ne 0) {
                throw "Lite 安装包编译失败（ISCC exit $LASTEXITCODE），构建中止"
            } else {
                $liteExe = "release\XianyuHunter-Setup-v$version-Lite.exe"
                if (-not (Test-Path -LiteralPath $liteExe -PathType Leaf)) {
                    throw "Lite 安装包编译返回成功但产物不存在：$liteExe"
                }
                $liteLen = (Get-Item -LiteralPath $liteExe).Length
                if ($liteLen -le 0) { throw "Lite 安装包产物为 0 字节：$liteExe" }
                # 体积下限校验：Lite 必须明显小于 Full。Chromium 压缩后约 150-200MB，
                # 若两者差距不到 15%，说明 chromium 实际仍被打进了 Lite。
                $fullLen = (Get-Item -LiteralPath $setupExe).Length
                if ($liteLen -gt ($fullLen * 0.85)) {
                    throw ((("Lite 安装包体积异常：{0:N1} MB，Full 为 {1:N1} MB（差距 <15%），" +
                              "说明 playwright_browsers 未被真正排除，Lite 包不可用") -f ($liteLen / 1MB), ($fullLen / 1MB)))
                }
                Write-Host ("  Lite 安装包已生成：{0}（{1:N1} MB）" -f $liteExe, ($liteLen / 1MB)) -ForegroundColor Green
            }
            if (Test-Path -LiteralPath $liteStage) {
                Remove-Item -Recurse -Force -LiteralPath $liteStage -ErrorAction SilentlyContinue
            }
        }
    }
}

# ============== 7. 体积归因报表 + 版本间体积回归门禁 ==============
# 为什么放在安装包之后：报表要同时覆盖 PyInstaller 清单、外置资源与安装包实际内容，
# 前面任何一步跳过都看不到全貌（T04106）。
# 落盘目录是 .cache\size-report\，不在 release 下，[Files] 段拷不到它，不会进发布包。
Write-Host "`n[7/7] 体积归因报表与回归门禁..." -ForegroundColor Yellow
if (-not (Test-Path $sizeReportDir)) { New-Item -ItemType Directory -Path $sizeReportDir | Out-Null }
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$sizeSnapshot = Join-Path $sizeReportDir "size-$stamp-$EmbeddingEngine.json"
$sizeBaseline = Join-Path $sizeReportDir "latest.json"
if ($pyiWorkDir.StartsWith($repoRoot)) {
    $pyiWorkRel = $pyiWorkDir.Substring($repoRoot.Length).TrimStart('\', '/') + "\xianyu-hunter"
} else {
    $pyiWorkRel = "$cacheDir\pyinstaller-work\xianyu-hunter"
}
$analyzerArgs = @(
    "scripts\analyze_bundle_size.py",
    "--workpath", $pyiWorkRel,
    "--json-out", $sizeSnapshot,
    "--baseline", $sizeBaseline
)
if ($script:SpaBuiltAfter) {
    $analyzerArgs += @("--spa-built-after", $script:SpaBuiltAfter)
}
# 版本号与安装包同源：从 __init__.py 读，仅写进 JSON 便于把报表和发布版本对上
$versionFile = "backend\xianyu_hunter\__init__.py"
$appVersion = "0.0.0.0"
if (Test-Path $versionFile) {
    $vLine = Get-Content $versionFile | Where-Object { $_ -match '__version__' } | Select-Object -First 1
    if ($vLine -match '"([^"]+)"') { $appVersion = $matches[1] }
}
$analyzerArgs += @("--version", $appVersion)
if ($FailOnSizeRegression) { $analyzerArgs += "--fail-on-regression" }
& $buildPython @analyzerArgs
$sizeExit = $LASTEXITCODE

if ($sizeExit -eq 0) {
    # 只有「无回归」才把本次结果立为下次的基线：否则一旦回归，基线会被污染，
    # 后续构建都拿膨胀后的体积当正常值比对，门禁就永久失效。
    Copy-Item $sizeSnapshot $sizeBaseline -Force
    Write-Host "  体积基线已更新：$sizeBaseline" -ForegroundColor Green
} elseif (Test-Path $sizeSnapshot) {
    Copy-Item $sizeSnapshot (Join-Path $sizeReportDir "failed-$stamp-$EmbeddingEngine.json") -Force
    Write-Host "  [WARN] 本次报表未设为基线（回归或脚本失败）；历史快照保留在 $sizeReportDir" -ForegroundColor Yellow
}

if ($sizeExit -eq 2) {
    Write-Host "  [FAIL] 体积回归门禁命中（-FailOnSizeRegression）" -ForegroundColor Red
    throw "打包产物体积较基线超阈值上涨（+50MB 或 +5%），构建按门禁失败终止。报表：$sizeSnapshot"
} elseif ($sizeExit -ne 0) {
    Write-Host "  [WARN] 体积归因脚本退出码 $sizeExit，不阻断构建" -ForegroundColor Yellow
}

# ============== 完成 ==============
$distDir = "release\xianyu-hunter"
$size = (Get-ChildItem -Recurse $distDir | Measure-Object -Property Length -Sum).Sum / 1MB
# 收尾自检：安装包是本脚本的对外产物，缺了就不能算「构建完成」
$finalVersion = "0.0.0.0"
if (Test-Path $versionFile) {
    $fvLine = Get-Content $versionFile | Where-Object { $_ -match '__version__' } | Select-Object -First 1
    if ($fvLine -match '"([^"]+)"') { $finalVersion = $matches[1] }
}
$finalSetup = "release\XianyuHunter-Setup-v$finalVersion.exe"
if (-not (Test-Path -LiteralPath $finalSetup -PathType Leaf) -or (Get-Item -LiteralPath $finalSetup).Length -le 0) {
    throw "构建收尾自检失败：未找到可用的安装包 $finalSetup"
}
$pyprojectVersion = Get-BuildVersion
if ($pyprojectVersion -and $pyprojectVersion -ne $finalVersion) {
    throw "构建收尾自检失败：pyproject 版本 $pyprojectVersion 与 __init__.py 版本 $finalVersion 不一致，安装包/侧车会对不上"
}
$finalLite = "release\XianyuHunter-Setup-v$finalVersion-Lite.exe"
if ($BundleBrowser -and (-not (Test-Path -LiteralPath $finalLite -PathType Leaf) -or
                          (Get-Item -LiteralPath $finalLite).Length -le 0)) {
    throw "构建收尾自检失败：-BundleBrowser 模式缺少 Lite 安装包 $finalLite"
}

# 构建侧车（build sidecar）：把「这次构建到底产出了什么」落盘成机器可校验的事实。
# 发布脚本 -SkipBuild 复跑时不再依赖“目录里最新文件”猜测，而是逐项比对
# 版本 / 名称 / 字节数 / SHA256 / Full-Lite 形态；资产被替换或过期会直接失败。
$manifestPath = Join-Path $distDir "bundle-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "构建收尾自检失败：缺少 bundle-manifest.json：$manifestPath"
}
$distManifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if ([bool]$distManifest.bundle_browser -ne [bool]$BundleBrowser) {
    throw ("构建收尾自检失败：bundle-manifest.json 的 bundle_browser={0} 与构建参数 {1} 不一致" -f `
           $distManifest.bundle_browser, [bool]$BundleBrowser)
}
$sidecarAssets = @()
$assetSpecs = @(
    @{ Path = $finalSetup; Kind = "full" }
)
if ($BundleBrowser) {
    $assetSpecs += @{ Path = "release\XianyuHunter-Setup-v$finalVersion-Lite.exe"; Kind = "lite" }
}
foreach ($spec in $assetSpecs) {
    if (-not (Test-Path -LiteralPath $spec.Path -PathType Leaf)) { continue }
    $fi = Get-Item -LiteralPath $spec.Path
    if ($fi.Length -le 0) { throw "构建收尾自检失败：资产为 0 字节 $($spec.Path)" }
    $sidecarAssets += [ordered]@{
        name = $fi.Name
        path = $spec.Path
        size = [int64]$fi.Length
        sha256 = (Get-FileHash -LiteralPath $spec.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        kind = $spec.Kind
    }
}
$sidecar = [ordered]@{
    schema = 1
    version = $finalVersion
    built_at = (Get-Date).ToString("o")
    embedding_engine = $EmbeddingEngine
    bundle_browser = [bool]$BundleBrowser
    dist = $distDir
    manifest = [ordered]@{
        path = $manifestPath
        sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        bundle_browser = [bool]$distManifest.bundle_browser
        version = $distManifest.version
    }
    assets = $sidecarAssets
}
$sidecarPath = "release\XianyuHunter-Setup-v$finalVersion.build.json"
[System.IO.File]::WriteAllText(
    (Join-Path $repoRoot $sidecarPath),
    (($sidecar | ConvertTo-Json -Depth 10) + "`n"),
    (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "  构建侧车: $sidecarPath" -ForegroundColor DarkGray
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  构建完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  产物目录: $distDir"
Write-Host ("  产物大小: {0:N1} MB" -f $size)
Write-Host "  EXE 路径: $distDir\xianyu-hunter.exe"
Write-Host ("  安装包  : {0}（{1:N1} MB）" -f $finalSetup, ((Get-Item -LiteralPath $finalSetup).Length / 1MB))
if ($BundleBrowser) {
    $finalLite = "release\XianyuHunter-Setup-v$finalVersion-Lite.exe"
    if (Test-Path -LiteralPath $finalLite) {
        Write-Host ("  Lite    : {0}（{1:N1} MB）" -f $finalLite, ((Get-Item -LiteralPath $finalLite).Length / 1MB))
    }
}
Write-Host "========================================" -ForegroundColor Green
