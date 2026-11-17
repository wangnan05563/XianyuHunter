# 生产构建验证记录（2026-11-14）

- 项目：`D:\code\otherProjects\XianyuHunter`（git `main`，HEAD `1e5376ae`）
- 构建入口：`scripts\build-exe.ps1 -SkipDeps`（Lite 形态，不随包 Chromium）
- 原始日志：`build-exe2.log`（本次重建）、`build-exe.log`（前一次因磁盘写满中断的日志）
- 版本号：`0.4.20261114.3`（取自 `backend\xianyu_hunter\__init__.py`）
- 结论：**构建成功，退出码 0**，安装包已生成

## 1. 构建环境

| 项 | 值 |
|---|---|
| Python（构建 venv） | 3.13.14（`.venv-build`） |
| PyInstaller | 6.22.3（contrib hooks 2026.10） |
| Node.js | 24.14.0（`D:\code\nodejs24\node.exe`） |
| Inno Setup | 6（`C:\Users\hspcadmin\AppData\Local\Programs\Inno Setup 6\ISCC.exe`） |
| 构建模式 | `SkipDeps`（跳过依赖安装）+ 默认 Lite（运行时复用系统 Edge/Chrome） |

## 2. 步骤与结果

| 步骤 | 结果 | 关键耗时 |
|---|---|---|
| [1/7] 准备构建 venv | 增量更新，依赖可导入性校验通过 | — |
| [2/7] 锁定依赖 | `requirements-lock.txt`（UTF-8 无 BOM） | — |
| [3/7] 构建 SPA | `tsc -b && vite build` 成功，产物 `release\spa` | **61m45s**（`optimizeDeps.force=true` 全量预构建 + 机器高负载） |
| [4/7] PyInstaller 打包 | 分析 9,769,283 ms、PYZ 12s、PKG 2s、EXE 1s、COLLECT 166s，全部 completed successfully | 总计约 **2h43m** |
| [4.5] sentence_transformers 完整性 | 通过：216/216 子模块已收进 PYZ，无松散目录需修复 | — |
| [5/7] 外置资源 | 子进程脚本（auth_helper、browser_login）、`menu_registry.yaml` 已复制；Lite 跳过 Chromium；`bge-small-zh-v1.5` 模型已下载并复制到 dist | — |
| [5.6] 敏感信息扫描 | 通过（无 API Key 痕迹） | — |
| [6/7] Inno Setup 编译 | 成功，`Successful compile (378.937 sec)` | 6m19s |
| [7/7] 体积归因与门禁 | 首次构建无基线，不做回归判定；基线已写入 `.cache\size-report\latest.json` | — |

PyInstaller 两条 warning 均为可选依赖缺失，不影响本项目运行：`chromadb.server.fastapi` 缺 `opentelemetry.instrumentation`、`onnxruntime.quantization.neural_compressor` 缺 `onnx_ir`。

## 3. 产物清单

| 产物 | 路径 | 大小 |
|---|---|---|
| 安装包 | `release\XianyuHunter-Setup-v0.4.20261114.3.exe` | 294,498,078 B（约 280.9 MB） |
| 免安装目录 | `release\xianyu-hunter\` | 995.1 MB / 6,815 个文件 |
| 主程序 | `release\xianyu-hunter\xianyu-hunter.exe` | 87,002,887 B（约 83.0 MB） |
| 前端 SPA | `release\spa\` | 由 [3/7] 生成，被 PyInstaller 收入 dist |

安装包 SHA256：

```
4C235DA547E596DE0212A9C88ED2B0BB89F897BD9B8017C18841064697AA2397
```

## 4. 安装包配置（installer.iss）

- `OutputDir=release`，`OutputBaseFilename=XianyuHunter-Setup-v{#MyAppVersion}`
- `Compression=lzma2` + `SolidCompression=yes`，`ArchitecturesAllowed=x64`，`PrivilegesRequired=admin`
- `[Files] Source: "release\xianyu-hunter\*"`，`Excludes: "*.log data\* .env .secrets.json .env.local"`
- 快捷方式：开始菜单 + 可选桌面图标

## 5. 体积归因（Top 分组）

| 分组 | 体积 |
|---|---|
| scipy | 51.11 MB |
| transformers | 41.33 MB |
| scipy.libs | 19.32 MB |
| sklearn | 11.79 MB |
| onnx（构建期泄漏） | 0.00 MB |

## 6. 与扫描/测试闭环的对应关系

- 本次构建的源码即第 3 轮 SonarQube 扫描确认 **0 OPEN** 的同一份工作区代码（16 个源码/测试文件改动，未提交）。
- 全量测试：`2924 passed / 3 skipped / 5 failed`（35:33），5 个失败全部为 `tests/test_health_unified.py` 的 token TTL 时序脆弱性，隔离复跑 10 passed。详见 `full-tests.log`（因磁盘危机被截断为结论摘要）。
- 重建完成后重跑全量测试以重新生成 `coverage.xml`，结果与本记录同目录的 `full-tests-2.log`（见第 7 节）。

## 7. 重跑测试（重建 coverage）

- 命令：`.\.venv\Scripts\python.exe -m pytest tests/ -q --no-header -p no:cacheprovider`
- 结果：**2932 passed，0 failed，0 skipped**，耗时 1139.43s（18:59），日志 `full-tests-2.log`
- 覆盖率：`coverage.xml` 已重新生成（31,449 行；TOTAL 30,243 语句 / 8,116 未覆盖 / 73%；XML line-rate 0.7316）
- 说明：首轮全量测试的 5 个 `test_health_unified.py` 失败源于 1200 秒 token TTL 与 35 分钟套件时长冲突；本次套件在 19 分钟内跑完，同一批用例全部通过，进一步印证其为时间脆弱性而非代码缺陷。

## 8. 本次构建过程中的异常与处置

1. **磁盘写满导致首次 Inno 编译中断**：`Error in installer.iss: 磁盘空间不足`，D 盘 0 字节可用；同时 PowerShell/Glob 等工具因无法写入临时脚本而整体不可用。
2. **产物被清理**：外部磁盘清理删除了整个 `release`（含 dist 与旧版安装包）与 `.cache`，因此本次为**从零重建**，而非补编安装包。
3. **构建耗时异常**：机器上同时运行大量 Node 进程，PyInstaller 分析阶段从上次的 18.6 分钟放大到 2 小时 43 分钟；SPA 构建亦从数分钟放大到 61 分钟。构建本身无失败。
4. **coverage.xml 曾被截断为占位文件**：磁盘危机期间为腾空间把 31,449 行的 `coverage.xml` 截成 2 行注释；重建后已重跑全量测试重新生成。
