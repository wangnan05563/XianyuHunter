#Requires -Version 5.1
<#
.SYNOPSIS
    XianyuHunter 一键构建 + 打包 + 发布到 GitHub Releases（配置驱动，无硬编码）
.DESCRIPTION
    读取 config.publish.json：先执行构建流水线（更新版本号 -> PyInstaller 打包 -> Inno Setup 安装包），
    再定位产物（优先 release\XianyuHunter-Setup-v<VER>.exe，缺失则把 release\xianyu-hunter\ 压缩为 zip），
    推导版本号、生成/复用发布说明，最后调用通用发布器 publish-release.py 上传。
    所有项目名 / 路径 / 版本 / token 均来自配置或环境变量，脚本本身零硬编码。
    用法：
      scripts\一键发布.bat                      （构建并真实发布）
      scripts\一键发布.bat -SkipBuild          （跳过构建，直接发布已有产物）
      set GH_DRY_RUN=1 && scripts\一键发布.bat （只打印将要执行的动作，不真实上传）
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [switch]$SkipBuild,
    [switch]$DryRun
)

# 支持通过环境变量控制（参考 18_comparePakage 一键发布.bat 的用法）
if ($env:GH_DRY_RUN -eq '1') { $DryRun = $true }
if ($env:XH_SKIP_BUILD -eq '1') { $SkipBuild = $true }

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
# 流水线启动时间：用于判定「release 目录里的安装包到底是不是本次构建产出的」。
# 不对 release 目录里的旧产物做任何假设，宁可报错让人看清，也不把旧包当新包发出去。
$script:PublishStartedAt = Get-Date

# ---- 与 .bat chcp 65001 匹配（UTF-8 输出） ----
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---- 工具函数 ----
function Resolve-Python {
    $cands = if ($cfg.runtime -and $cfg.runtime.python_candidates) { $cfg.runtime.python_candidates } else { @() }
    foreach ($c in $cands) {
        $p = [System.Environment]::ExpandEnvironmentVariables($c)
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-AssetVersion($assetPath) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($assetPath)
    if ($name -match '^XianyuHunter-Setup-v(.+)$') { return $matches[1] }
    if ($name -match '^XianyuHunter-(.+)$') { return $matches[1] }
    return $null
}

function Get-PyprojectVersion($root) {
    $pj = Join-Path $root "pyproject.toml"
    if (-not (Test-Path $pj -PathType Leaf)) { return $null }
    $c = Get-Content -Raw -Encoding UTF8 $pj
    if ($c -match '(?m)^version\s*=\s*"([^"]+)"') { return $matches[1] }
    return $null
}

function Get-Sha256($path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-AssetInfo($path) {
    # Windows PowerShell 5.1 对符号链接 FileInfo.Length 返回 0；体积/新鲜度/哈希
    # 必须读真实目标，否则合法资产会被误判为 0 字节。
    $info = Get-Item -LiteralPath $path
    if ($info.LinkType -eq 'SymbolicLink' -and $info.Target) {
        $target = @($info.Target)[0]
        if (-not [System.IO.Path]::IsPathRooted($target)) {
            $target = Join-Path $info.DirectoryName $target
        }
        Write-Host "[build-publish] 资产为符号链接，读取目标: $target" -ForegroundColor Yellow
        return Get-Item -LiteralPath $target
    }
    return $info
}

function Get-CompleteChromiumExe($base) {
    # 发布门禁与 build-exe.ps1 Test-ChromiumComplete / 运行时 browser_kernel
    # 使用同一判据：exe + chrome.dll/resources.pak/icudtl.dat 均非空。
    if (-not (Test-Path -LiteralPath $base -PathType Container)) { return $null }
    $fullDirs = Get-ChildItem -LiteralPath $base -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike 'chromium_headless_shell-*' }
    foreach ($d in $fullDirs) {
        foreach ($rel in @('chrome-win64\chrome.exe', 'chrome-win\chrome.exe',
                           'chrome-linux64\chrome', 'chrome-linux\chrome')) {
            $exe = Join-Path $d.FullName $rel
            if (Test-CompleteChromiumExe $exe) { return $exe }
        }
        $fallback = Get-ChildItem -LiteralPath $d.FullName -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-CompleteChromiumExe (Join-Path $_.FullName 'chrome.exe') -or
                           Test-CompleteChromiumExe (Join-Path $_.FullName 'chrome') } |
            Select-Object -First 1
        if ($fallback) {
            $candidate = Join-Path $fallback.FullName 'chrome.exe'
            if (Test-CompleteChromiumExe $candidate) { return $candidate }
            return (Join-Path $fallback.FullName 'chrome')
        }
    }
    return $null
}

function Test-CompleteChromiumExe($exe) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $exe).Length -lt 1024) { return $false }
    $required = if ((Split-Path -Leaf $exe) -eq 'chrome.exe') {
        @('chrome.dll', 'resources.pak', 'icudtl.dat')
    } else { @('resources.pak', 'icudtl.dat') }
    $dir = Split-Path -Parent $exe
    foreach ($name in $required) {
        $f = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $false }
        if ((Get-Item -LiteralPath $f).Length -lt 1024) { return $false }
    }
    return $true
}

function Resolve-EmbeddingEngine {
    # 发布包用哪条 embedding 链由 config 决定（build_pipeline.embedding_engine），
    # 命令里只写 __EMBEDDING_ENGINE__ 占位符。历史缺陷（T04093 P1）：步骤命令裸调
    # build-exe.ps1、从不传引擎，于是永远吃脚本默认值 st，每一版都把 torch 全家桶
    # （实测 360.03 MB）带进安装包，拖了多少版没人发现。
    # 该值最终进 Invoke-Expression，所以只接受白名单，不做任何“透传”。
    $e = if ($cfg.build_pipeline -and $cfg.build_pipeline.embedding_engine) {
        "$($cfg.build_pipeline.embedding_engine)".Trim().ToLower()
    } else { "st" }
    if (@("st", "onnx") -notcontains $e) {
        Write-Error "[build-publish] build_pipeline.embedding_engine 只能是 st / onnx，收到：$e"
        exit 1
    }
    return $e
}

function Resolve-BundleBrowser {
    # Full/Lite 双形态由 config 决定（build_pipeline.bundle_browser），命令里只写
    # __BUNDLE_BROWSER__ 占位符。开启后 build-exe.ps1 用同一份 dist 额外产出一个
    # 不含 playwright_browsers 的 -Lite 安装包（不触发第二次 PyInstaller 构建）。
    # 该值最终进 Invoke-Expression，因此只回显开关本身，不透传配置里的原始文本。
    $v = if ($cfg.build_pipeline) {
        $p = $cfg.build_pipeline.PSObject.Properties["bundle_browser"]
        if ($p) { $p.Value } else { $false }
    } else { $false }
    if ($v -isnot [bool]) {
        Write-Error "[build-publish] build_pipeline.bundle_browser 必须是 true / false，收到：$v"
        exit 1
    }
    if ($v) { return "-BundleBrowser" }
    return ""
}

# ---- 配置加载 ----
if (-not (Test-Path $ConfigPath)) { Write-Error "config 未找到: $ConfigPath"; exit 1 }
$cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
$rel = $cfg.github_release
if (-not $rel -or $rel.enabled -ne $true) {
    Write-Host "[build-publish] github_release 未启用（enabled != true），跳过发布" -ForegroundColor Yellow
    exit 0
}

# ---- 解析 python（配置驱动，绝不硬编码运行时路径；陷阱 57） ----
$py = Resolve-Python
if (-not $py) { Write-Error "[build-publish] 未找到 python（请配置 runtime.python_candidates 或安装 python）"; exit 1 }
Write-Host "[build-publish] python: $py" -ForegroundColor Cyan
# 把 python 所在目录前置到 PATH，使构建脚本内的 `python` 命中同一解释器
$pyDir = Split-Path $py
$env:PATH = "$pyDir;$env:PATH"

Push-Location $RepoRoot
try {
    # ---- 1) 构建流水线 ----
    if (-not $SkipBuild) {
        if ($cfg.build_pipeline -and $cfg.build_pipeline.steps) {
            $i = 0
            foreach ($step in $cfg.build_pipeline.steps) {
                $i++
                $cmd = $step.command
                if (-not $cmd) { continue }
                $cmd = $cmd -replace '__SCRIPTS__', $PSScriptRoot
                $cmd = $cmd -replace '__EMBEDDING_ENGINE__', (Resolve-EmbeddingEngine)
                $cmd = $cmd -replace '__BUNDLE_BROWSER__', (Resolve-BundleBrowser)
                Write-Host "[build-publish] [$i/$($cfg.build_pipeline.steps.Count)] $($step.name)" -ForegroundColor Cyan
                Write-Host "  > $cmd"
                Invoke-Expression $cmd
                if ($LASTEXITCODE -ne 0) {
                    $onFail = if ($step.on_fail) { $step.on_fail } else { "stop" }
                    if ($onFail -eq "skip") { Write-Warning "  step failed, skip"; continue }
                    if ($onFail -eq "warn") { Write-Warning "  step failed, continue"; continue }
                    Write-Error "[build-publish] 构建步骤失败 (exit $LASTEXITCODE): $($step.name)"
                }
            }
        } else {
            Write-Host "[build-publish] 未配置 build_pipeline.steps，跳过构建阶段" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[build-publish] -SkipBuild 已指定，跳过构建阶段" -ForegroundColor Yellow
    }

    # ---- 1.5) 发布前质量门禁（与 -SkipBuild 解耦）----
    # 不能把门禁包在 (-not $SkipBuild) 里：-SkipBuild 恰恰是“复用已有产物”的路径，
    # 最需要校验产物身份/完整性。这里以 build-exe.ps1 收尾写出的 build sidecar 为事实源，
    # 校验版本、Full/Lite 形态、bundle-manifest、Chromium 关键文件与主资产哈希。
    $expectedVer = Get-PyprojectVersion -root $RepoRoot
    if (-not $expectedVer) {
        Write-Error "[build-publish] 无法读取 pyproject.toml 版本号，拒绝发布（不能靠文件名猜版本）"
        exit 1
    }
    $bundleMode = ((Resolve-BundleBrowser) -eq "-BundleBrowser")
    if ($bundleMode) {
        $distDir = Join-Path $RepoRoot "release\xianyu-hunter"
        $pwDist = Join-Path $distDir "playwright_browsers"
        $sidecarPath = Join-Path $RepoRoot "release\XianyuHunter-Setup-v$expectedVer.build.json"
        if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
            Write-Error "[build-publish] 缺少构建侧车 $sidecarPath。请先完整运行 build-exe.ps1（不要用 -SkipBuild 复用旧产物）；-SkipBuild 只能发布带侧车的构建。"
            exit 1
        }
        $sidecar = Get-Content -Raw -Encoding UTF8 $sidecarPath | ConvertFrom-Json
        if ("$($sidecar.version)" -ne $expectedVer) {
            Write-Error "[build-publish] 构建侧车版本 $($sidecar.version) ≠ pyproject $expectedVer，拒绝发布过期产物"
            exit 1
        }
        if ([bool]$sidecar.bundle_browser -ne $true) {
            Write-Error "[build-publish] config 要求 Full 包，但构建侧车 bundle_browser=false。请用 -BundleBrowser 重新构建"
            exit 1
        }
        $manifestPath = Join-Path $distDir "bundle-manifest.json"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            Write-Error "[build-publish] Full dist 缺少 bundle-manifest.json：$manifestPath"
            exit 1
        }
        $manifestHash = Get-Sha256 $manifestPath
        if ("$($sidecar.manifest.sha256)" -ne $manifestHash) {
            Write-Error "[build-publish] bundle-manifest.json 哈希与构建侧车不一致（dist 在构建后被改动？）"
            exit 1
        }
        $manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
        if ([bool]$manifest.bundle_browser -ne $true) {
            Write-Error "[build-publish] bundle-manifest.json 的 bundle_browser 不是 true"
            exit 1
        }
        if ("$($manifest.version)" -ne $expectedVer) {
            Write-Error "[build-publish] bundle-manifest.json 版本 $($manifest.version) ≠ $expectedVer"
            exit 1
        }
        $manifestExeRel = "$($manifest.chromium.executable)" -replace '/', '\'
        if (-not $manifestExeRel) {
            Write-Error "[build-publish] bundle-manifest.json 未记录 chromium.executable"
            exit 1
        }
        $manifestExe = Join-Path $distDir $manifestExeRel
        if (-not (Test-CompleteChromiumExe $manifestExe)) {
            Write-Error "[build-publish] manifest 指向的 Chromium 不完整（exe 或 chrome.dll/resources.pak/icudtl.dat 缺失/为空）：$manifestExe"
            exit 1
        }
        $actualExe = Get-CompleteChromiumExe $pwDist
        if (-not $actualExe) {
            Write-Error "[build-publish] $pwDist 中没有完整 Chromium（chrome.exe + chrome.dll/resources.pak/icudtl.dat 非空）"
            exit 1
        }
        if ([System.IO.Path]::GetFullPath($manifestExe) -ne [System.IO.Path]::GetFullPath($actualExe)) {
            Write-Error "[build-publish] manifest 记录的 Chromium 路径与实际扫描结果不一致：$manifestExe ≠ $actualExe"
            exit 1
        }
        $manifestFiles = @($manifest.chromium.files)
        if ($manifestFiles.Count -eq 0) {
            Write-Error "[build-publish] bundle-manifest.json 未记录 Chromium 关键文件清单"
            exit 1
        }
        $manifestDir = Join-Path $distDir ("$($manifest.chromium.dir)" -replace '/', '\')
        foreach ($mf in $manifestFiles) {
            $fp = Join-Path $manifestDir $mf.name
            if (-not (Test-Path -LiteralPath $fp -PathType Leaf)) {
                Write-Error "[build-publish] manifest 记录的关键文件不存在：$fp"
                exit 1
            }
            $fi = Get-Item -LiteralPath $fp
            if ([int64]$fi.Length -ne [int64]$mf.size) {
                Write-Error "[build-publish] Chromium 关键文件体积变化：$($mf.name) ($($fi.Length) ≠ $($mf.size))"
                exit 1
            }
            if ((Get-Sha256 $fp) -ne "$($mf.sha256)") {
                Write-Error "[build-publish] Chromium 关键文件哈希变化：$($mf.name)"
                exit 1
            }
        }
        # 主资产也必须与侧车一致：修掉“同名文件被替换/旧包残留”仍被发布的路径。
        $mainName = "XianyuHunter-Setup-v$expectedVer.exe"
        $mainPath = Join-Path $RepoRoot $rel.asset.dir
        $mainPath = Join-Path $mainPath $mainName
        if (-not (Test-Path -LiteralPath $mainPath -PathType Leaf)) {
            Write-Error "[build-publish] 缺少本次版本主资产：$mainPath"
            exit 1
        }
        $mainInfo = Resolve-AssetInfo $mainPath
        $sidecarAsset = @($sidecar.assets) | Where-Object { $_.name -eq $mainName } | Select-Object -First 1
        if (-not $sidecarAsset) {
            Write-Error "[build-publish] 构建侧车未记录主资产 $mainName"
            exit 1
        }
        if ([int64]$mainInfo.Length -ne [int64]$sidecarAsset.size) {
            Write-Error "[build-publish] 主资产体积与构建侧车不一致：$($mainInfo.Length) ≠ $($sidecarAsset.size)"
            exit 1
        }
        $mainHash = Get-Sha256 $mainInfo.FullName
        if ($mainHash -ne "$($sidecarAsset.sha256)") {
            Write-Error "[build-publish] 主资产 SHA256 与构建侧车不一致：$mainPath"
            exit 1
        }
        Write-Host "[build-publish] 质量门禁通过：版本 $expectedVer，Full Chromium 完整且与侧车哈希一致" -ForegroundColor Green
    }

    # ---- 2) 定位产物（安装包是唯一主资产；缺失即失败，不再静默降级为 zip） ----
    # 历史缺陷：这里原先「没找到安装包就把 release\xianyu-hunter 压成 zip 发布」，
    # 于是一旦构建阶段没产出安装包（如 Inno Setup 失败只 WARN、PyInstaller 中断），
    # 流水线仍会拿一个 zip 当主资产去发，用户看到的是「发布成功」——实际发出去的根本
    # 不是安装包，欠的那次发布也没补上。现在只接受安装包；确实要发 zip 必须显式在
    # config 里写 asset.allow_zip_fallback=true。
    $adir = $rel.asset.dir
    $glob = $rel.asset.glob
    $excl = if ($rel.asset.exclude_patterns) { $rel.asset.exclude_patterns } else { @() }
    $assetDir = Join-Path $RepoRoot $adir
    if (-not (Test-Path -LiteralPath $assetDir -PathType Container)) {
        Write-Error "[build-publish] 资产目录不存在: $assetDir"; exit 1
    }
    # H3：主资产必须由 pyproject 当前版本精确决定，不能再按 LastWriteTime 取“最新”。
    # 否则上一版遗留的同 glob 安装包（或临时重打的旧包）会被当成本次产物上传。
    $expectedName = $glob -replace '\*', $expectedVer
    $asset = Join-Path $assetDir $expectedName
    $allowZip = $false
    $zf = $rel.asset.PSObject.Properties["allow_zip_fallback"]
    if ($zf) { $allowZip = ($zf.Value -eq $true) }
    if (Test-Path -LiteralPath $asset -PathType Leaf) {
        # exact hit
    } elseif ($allowZip) {
        # 回退：把 dir 模式产物压缩为 zip（陷阱 49：优先 Compress-Archive）
        Write-Host "[build-publish] 未找到安装包，按配置 allow_zip_fallback 压缩为 zip..." -ForegroundColor Yellow
        $zipName = "XianyuHunter-$expectedVer.zip"
        $zipPath = Join-Path $assetDir $zipName
        $srcDir = Join-Path $RepoRoot "release\xianyu-hunter"
        if (-not (Test-Path -LiteralPath $srcDir -PathType Container)) {
            Write-Error "[build-publish] 未找到构建产物: $srcDir"; exit 1
        }
        if (-not (Test-Path -LiteralPath $zipPath -PathType Leaf)) {
            Compress-Archive -Path (Join-Path $srcDir "*") -DestinationPath $zipPath -Force
        }
        $asset = $zipPath
    } else {
        Write-Error (("[build-publish] 未找到当前版本 {0} 的安装包：{1}。" +
                      "不会用同 glob 的旧包顶替。构建阶段没有产出安装包，或产物已被清理；" +
                      "本次不发布任何资产。请查看构建日志中 [6/7] 安装包步骤。") -f $expectedVer, $asset)
        exit 1
    }
    # 产物有效性 + 新鲜度：安装包必须非空，且在本次流水线启动之后生成。
    # 少了这一步，release 目录里上一版遗留的 Setup exe 会被当成本次产物重新上传，
    # 结果就是「发布成功但内容还是旧的」。
    # 符号链接（磁盘紧张时 release 可能指向其他盘）必须读真实目标，PS 5.1 Length=0。
    $assetCheck = Resolve-AssetInfo $asset
    if ($assetCheck.Length -le 0) { Write-Error "[build-publish] 资产为 0 字节: $asset"; exit 1 }
    $minMB = 20
    $mf = $rel.asset.PSObject.Properties["min_size_mb"]
    if ($mf) { $minMB = [double]$mf.Value }
    if ($assetCheck.Length / 1MB -lt $minMB) {
        Write-Error ("[build-publish] 安装包体积异常：{0:N1} MB < 期望下限 {1} MB，疑似残缺产物，已中止发布" -f ($assetCheck.Length / 1MB), $minMB)
        exit 1
    }
    if ((-not $SkipBuild) -and $script:PublishStartedAt -and $assetCheck.LastWriteTime -lt $script:PublishStartedAt) {
        Write-Error ("[build-publish] 安装包不是本次构建产物：{0} 最后写入 {1:yyyy-MM-dd HH:mm:ss}，早于流水线启动时间 {2:yyyy-MM-dd HH:mm:ss}。构建未真正生成安装包，已中止发布。" -f `
                     $assetCheck.Name, $assetCheck.LastWriteTime, $script:PublishStartedAt)
        exit 1
    }
    Write-Host ("[build-publish] 资产: {0}（{1:N1} MB，写入于 {2:yyyy-MM-dd HH:mm:ss}）" -f $asset, ($assetCheck.Length / 1MB), $assetCheck.LastWriteTime) -ForegroundColor Green

    # ---- 3) 版本号（H3：与 pyproject 同源，绝不再从旧资产名推导） ----
    $ver = $expectedVer
    Write-Host "[build-publish] 版本: $ver"

    # ---- 4) 发布说明（<VER> 模板；缺失则 git log 自动生成） ----
    $notes = $null
    if ($rel.notes -and $rel.notes.path_template) {
        $notesPath = Join-Path $RepoRoot ($rel.notes.path_template -replace "<VER>", $ver)
        if (-not (Test-Path $notesPath)) {
            if ($rel.notes.auto_generate -ne $false) {
                $auto = Join-Path $RepoRoot (($rel.notes.path_template -replace "<VER>", $ver) -replace "\.md$", ".auto.md")
                $logArgs = if ($rel.notes.auto_generate_git_log) { $rel.notes.auto_generate_git_log } else { "--no-merges --oneline -20" }
                $log = git -C $RepoRoot log @($logArgs -split " ") 2>$null
                Set-Content -Path $auto -Encoding UTF8 -Value ("# Release $ver`n`n（自动生成的发布说明，来自 git 提交记录；正式发布前请替换为正式说明。）`n`n" + ($log -join "`n"))
                $notesPath = $auto
                Write-Host "[build-publish] 自动生成说明: $notesPath" -ForegroundColor Yellow
            } else {
                Write-Error "[build-publish] 说明文件不存在且未启用 auto_generate: $notesPath"; exit 1
            }
        }
        $notes = $notesPath
    } else {
        Write-Error "[build-publish] 未配置 github_release.notes.path_template"; exit 1
    }
    Write-Host "[build-publish] 说明: $notes"

    # ---- 5) 发布（一次 Release 会话：先草稿上传全部资产，校验后再公开） ----
    # H4：不能先发 Full 再发 Lite。首次发布若 Lite 失败，线上会留下一个“成功但缺附件”的
    # 公开 Release。publish-release.py 现在接收全部资产，在 draft 状态下上传/校验，
    # 最后一次性 PATCH 公开；任何一步失败都不会产生公开半成品。
    $pub = Join-Path $RepoRoot $rel.publish_script
    if (-not (Test-Path -LiteralPath $pub -PathType Leaf)) { Write-Error "[build-publish] 发布脚本不存在: $pub"; exit 1 }

    $isBundle = ((Resolve-BundleBrowser) -eq "-BundleBrowser")
    $liteAsset = $null
    $liteName = $null
    if ($isBundle) {
        # 严格按本次版本号取 Lite：通配 v* 会把上一版遗留的 Lite 包当成这次的资产
        $liteName = "XianyuHunter-Setup-v$ver-Lite.exe"
        $liteAsset = Join-Path $assetDir $liteName
        if (-not (Test-Path -LiteralPath $liteAsset -PathType Leaf)) {
            Write-Error "[build-publish] bundle_browser=true 但未找到本次版本的 Lite 安装包: $liteAsset"
            exit 1
        }
        # 与主资产同口径：符号链接读真实目标（PS 5.1 Length=0），并做新鲜度/体积检查。
        $liteInfo = Resolve-AssetInfo $liteAsset
        if ($liteInfo.Length -le 0) { Write-Error "[build-publish] Lite 安装包为 0 字节: $liteAsset"; exit 1 }
        if ($liteInfo.Length / 1MB -lt $minMB) {
            Write-Error ("[build-publish] Lite 安装包体积异常：{0:N1} MB < 期望下限 {1} MB" -f ($liteInfo.Length / 1MB), $minMB)
            exit 1
        }
        if ((-not $SkipBuild) -and $script:PublishStartedAt -and $liteInfo.LastWriteTime -lt $script:PublishStartedAt) {
            Write-Error ("[build-publish] Lite 安装包不是本次构建产物（写入于 {0:yyyy-MM-dd HH:mm:ss}），已中止发布" -f $liteInfo.LastWriteTime)
            exit 1
        }
        if ($bundleMode) {
            $sidecarLite = @($sidecar.assets) | Where-Object { $_.name -eq $liteName } | Select-Object -First 1
            if (-not $sidecarLite) {
                Write-Error "[build-publish] 构建侧车未记录 Lite 资产 $liteName"
                exit 1
            }
            if ([int64]$liteInfo.Length -ne [int64]$sidecarLite.size) {
                Write-Error "[build-publish] Lite 资产体积与构建侧车不一致：$($liteInfo.Length) ≠ $($sidecarLite.size)"
                exit 1
            }
            if ((Get-Sha256 $liteInfo.FullName) -ne "$($sidecarLite.sha256)") {
                Write-Error "[build-publish] Lite 资产 SHA256 与构建侧车不一致：$liteAsset"
                exit 1
            }
        }
        Write-Host ("[build-publish] Lite 资产: {0}（{1:N1} MB）" -f $liteAsset, ($liteInfo.Length / 1MB)) -ForegroundColor Cyan
    }

    # M5：环境变量残留会覆盖配置文件（GH_DRAFT=1 残留会让本该公开的 release 变草稿，
    # GH_PRERELEASE=1 同理）。发布前显式清空，再按本次配置重新设置。
    foreach ($name in @("GH_DRY_RUN", "GH_DRAFT", "GH_PRERELEASE", "GH_RELEASE_NAME", "GH_REPLACE_ASSET")) {
        Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
    }
    $env:GH_TAG = $ver
    if ($rel.repo) { $env:GH_REPO = $rel.repo }
    $dryRunNow = [bool]($DryRun -or $rel.dry_run)
    $env:GH_DRY_RUN = $(if ($dryRunNow) { "1" } else { "0" })
    $env:GH_DRAFT = $(if ($rel.draft) { "1" } else { "0" })
    $env:GH_PRERELEASE = $(if ($rel.prerelease) { "1" } else { "0" })
    $releaseName = if ($rel.release_name) { "$($rel.release_name)" -replace '<VER>', $ver } else { "XianyuHunter v$ver" }
    $env:GH_RELEASE_NAME = $releaseName
    $env:GH_REPLACE_ASSET = "1"
    Write-Host "[build-publish] 发布中 (dry_run=$dryRunNow, release=$releaseName)..." -ForegroundColor Cyan

    $pubArgs = @($pub, $asset, $notes, "--release-name", $releaseName)
    if ($liteAsset) { $pubArgs += @("--asset", $liteAsset) }
    & $py @pubArgs
    if ($LASTEXITCODE -ne 0) { Write-Error "[build-publish] 发布失败 (exit $LASTEXITCODE)"; exit 1 }
    Write-Host "[build-publish] 发布完成（Full/Lite 已作为同一 Release 会话原子发布）" -ForegroundColor Green
} finally {
    Pop-Location
}
exit 0
