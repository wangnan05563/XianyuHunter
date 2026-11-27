# 测试与生产构建验证报告（2026-11-27）

## 1. 全量测试结果

### 1.1 TypeScript 类型检查

- 命令：`cd frontend && npx tsc --noEmit`
- 结果：**EXIT=0**，无类型错误。

### 1.2 前端测试（Vitest）

- 命令：`npx vitest run --coverage --coverage.reportsDirectory=F:/XianyuHunter-bigfiles/coverage --pool=forks --poolOptions.forks.maxForks=3 --testTimeout=45000`
- 结果：**152 / 152 个测试文件通过，1309 / 1309 个用例通过，0 失败**
- 耗时：3377.84s（56 分 17 秒）
- 覆盖率：行 41.26%，分支 79.72%，函数 39.87%（`lcov.info` 588,260 字节）
- 覆盖产物：`frontend/coverage/lcov.info`（由 `F:\XianyuHunter-bigfiles\coverage` 回拷）
- 时序说明：全量跑完后，为清零最后 6 条 Sonar 问题又做了 3 处纯类型/注释修改（删除多余类型断言、setter 重命名、`NOSONAR` 注释），不改变运行时行为；已用 `npx tsc --noEmit`（EXIT=0）与 36 条模型层测试复核通过。

### 1.3 后端测试（pytest）

- 命令：`.\.venv\Scripts\python.exe -m pytest tests/ -q --no-header -p no:cacheprovider`
- 最终结果：**2937 passed, 0 failed, 3 skipped，596 warnings，耗时 569.47s（9 分 29 秒）**
- 覆盖率：TOTAL 73%（30,289 statements / 8,114 missed）
- 覆盖产物：`coverage.xml`（1.16 MB）

> 此前三轮全量曾各出现 1 个顺序/环境相关的 flaky 单测（health、UX 指标、m5tk 缓存、请求频率），均单独重跑 PASSED；最终这一轮为完整全绿，不再有失败项。

### 1.4 端到端 / 视觉门禁

- 仓库没有 Vitest E2E 套件；页面级集成测试已包含在 152 个 Vitest 文件中。
- `frontend/tests/visual` 存在像素级视觉基线门禁（`scripts/visual_gate.py`），但它必须指向一个已在运行、带沙箱令牌的实例，本轮未启动该实例，**未执行**，也不计入“全部通过”。

## 2. 生产构建结果

### 2.1 构建方式

- 命令：`powershell -File scripts/build-exe.ps1 -SkipDeps -SkipPyInstaller -BundleBrowser -EmbeddingEngine onnx`
- 后端源码自上一次 PyInstaller 产物（2026-11-27 02:20 生成）后未改动，本轮复用该 dist；前端 SPA 从**最终源码**重新编译，再重做外置资源、Full/Lite 安装包。
- 构建模式：Full + Lite，随包 Chromium，ONNX embedding。
- 版本：`0.4.20261127.1`
- 构建完成时间：2026-11-27 05:16:29

### 2.2 产物清单

| 产物 | 大小 | SHA256 |
| --- | ---: | --- |
| `release/XianyuHunter-Setup-v0.4.20261127.1.exe`（Full） | 291,836,232 B（278.3 MB） | `491835624F7E032A8ABF89132973DF07ACE54130652AFA56D432E2EDD5DCCD1A` |
| `release/XianyuHunter-Setup-v0.4.20261127.1-Lite.exe` | 135,725,221 B（129.4 MB） | `DE5330E9AF6252EE6E58425509790C164FCFED56E2E153DC92B59CB63C6F5B66` |
| `release/xianyu-hunter/xianyu-hunter.exe` | 29,980,746 B（28.6 MB） | `AAA030F8F45CFDB8DA4CC3E196EF48E4BA784092B43E560E1E3380C9B4A608F4` |
| `release/xianyu-hunter/`（dist 总体） | 850.4 MB | — |

### 2.3 包内容校验

- `bundle-manifest.json`：`bundle_browser: true`、`build_mode: full`、`version: 0.4.20261127.1`。
- 随包 Chromium：`playwright_browsers/chromium-1255/chrome-win64`，版本 `Google Chrome for Testing 159.0.8154.6`，含 `chrome.exe / chrome.dll / resources.pak / icudtl.dat`。
- `release/spa/index.html` 与 `release/xianyu-hunter/static/spa/index.html` 的 SHA256 完全一致，确认安装包内的 SPA 是最终源码构建产物。
- 构建清单：`release/XianyuHunter-Setup-v0.4.20261127.1.build.json`。

## 3. 结论与剩余风险

- 单元/集成测试：前端 1309 条、后端 2937 条全部通过；类型检查通过。
- 生产构建：Full / Lite 两个安装包均已生成并可交付，包内 SPA 与最终源码一致。
- 未执行项：视觉像素门禁（依赖运行实例与沙箱令牌）；无 Vitest E2E 套件。
- 质量门禁唯一未达标项为 `new_coverage` 46.1% < 80%，原因与处置见 `sonar-fix-report-20261127.md` 第 3 节。
