# Xianyu Hunter — 修复后 SonarQube 扫描报告

**项目 Key**：`xianyu_hunter`
**扫描时间**：2026-11-14 20:05
**扫描方式**：降级 sonar-scanner CLI 8.0.1.6346（MCP 工具不可用）
**服务端**：SonarQube Community Build 26.1.0.118079（`http://localhost:9000`，状态 UP）
**本报告对应 CE 任务**：`c5991c45-8d56-4727-a718-ef5b72e98776` → SUCCESS（executionTimeMs 73178，warningCount 1，仅 1 个文件缺 SCM blame）
**扫描耗时**：9 分 47 秒（缓存已热）

---

## 一、质量门禁状态

**整体状态**：✅ 通过（`status=OK`，`caycStatus=compliant`）

| 指标 | 当前值 | 阈值 | 状态 |
|------|--------|------|------|
| 新代码覆盖率（new_coverage） | **84.7%** | ≥80% | ✅ |
| 新代码重复率（new_duplicated_lines_density） | **0.0%** | ≤3% | ✅ |
| 新增问题数（new_violations） | **0** | 0 | ✅ |

> 对比周期：`PREVIOUS_VERSION`（基线 2026-11-14T17:13:22+08:00），即与修复前那次扫描对比。

---

## 二、整体指标：修复前 vs 修复后

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| OPEN 问题总数 | 19 | **0** | **-19** |
| Bugs | 1 | **0** | -1 |
| Vulnerabilities | 0 | 0 | 0 |
| Code Smells | 18 | **0** | -18 |
| 技术债（sqale_index） | 149 | **0** | -149 |
| 可靠性评级 | C（3.0） | **A（1.0）** | ↑ |
| 安全评级 | A（1.0） | A（1.0） | — |
| 可维护性评级 | A（1.0） | A（1.0） | — |
| 认知复杂度总量 | 13,977 | 13,935 | -42 |
| 重复率 | 0.8% | 0.8% | — |
| 覆盖率（Sonar 口径） | 16.9% | **52.9%** | +36.0pp |
| 代码行数（ncloc） | 99,243 | 99,327 | +84（拆分引入的函数签名/文档） |

> 覆盖率从 16.9% 升到 52.9% 不是本次补了测试，而是基线扫描时使用的 `coverage.xml` 是旧的局部报告；本次重扫前跑了全量测试（pytest 自身统计 73%，Sonar 按可映射源文件折算 52.9%）。

---

## 三、扫描轮次与收敛过程

| 轮次 | 目的 | 结果 | 耗时 |
|------|------|------|------|
| 第 1 轮（基线） | 摸清问题面 | 19 OPEN（10 CRITICAL / 3 MAJOR / 6 MINOR） | 53:42 |
| 第 2 轮（首验） | 验证 19 条修复 | **2 个新问题**（S1192 browser_kernel.py、S125 evaluations_list.py），门禁 new_violations=2 → ERROR | 26:43 |
| 第 3 轮（终验） | 修掉自身引入的 2 条 + 刷新覆盖率 | **0 OPEN**，门禁 OK（new_coverage 84.7%） | 9:47 |

收敛判据：`target_open_count=0` 达成，未触发 `max_scan_iterations` 上限。

---

## 四、修复明细

### 4.1 基线 19 条（全部代码修复，无 NOSONAR 抑制）

| # | 规则 | 文件:行 | 级别 | 修复手法 |
|---|------|---------|------|----------|
| 1 | `python:S1192` | `infra/browser_kernel.py:39` | CRITICAL | 提取 `_EDGE_EXE_NAME` / `_CHROME_EXE_NAME` 常量，`_EDGE_PATHS`/`_CHROME_PATHS`/`_APP_PATH_EXE` 改为 f-string 引用 |
| 2 | `python:S3776` | `infra/browser_kernel.py:420` | CRITICAL | `describe_kernel` 拆出 `_available_status` / `_kernel_status`，消除 7 分支嵌套三元 |
| 3 | `python:S3776` | `infra/browser_kernel.py:269` | CRITICAL | `resolve_kernel` 只留入参归一化 + 分派，拆出 `_resolve_explicit_policy` / `_resolve_auto` |
| 4 | `python:S3776` | `modules/xianyu_im/client.py:108` | CRITICAL | `run` 拆出 `_receive_loop(transport)`，返回 `(动作, 原因)` 二元组，主函数只做分发 |
| 5 | `python:S3776` | `web/routes/api_ai.py:1518` | CRITICAL | `test_embedding_connection` 拆出 `_resolve_embedding_base_url` / `_resolve_embedding_model` / `_resolve_embedding_dimensions` |
| 6 | `python:S3776` | `web/routes/evaluations_list.py:46` | CRITICAL | `_compute_market_stats_for_tasks` 拆出 `_fill_batch_stats` / `_fill_one_task_stats` / `_fill_global_stats` |
| 7 | `python:S3776` | `config.py:215` | CRITICAL | `_persist_to_env` 拆出 `_read_env_lines` / `_is_env_assignment` / `_env_key_of` / `_upsert_env_lines` |
| 8 | `python:S3776` | `web/routes/api_maintenance.py:246` | CRITICAL | `maintenance_status` 拆出 `_db_stats` / `_scan_log_files` / `_log_stats` / `_fill_browser_data_stats` / `_fill_pycache_stats` / `_cache_stats` |
| 9 | `python:S3776` | `web/cache.py:182` | CRITICAL | `_single_flight` 拆出 `_claim_flight` / `_wait_flight` / `_lead_flight`，角色判定与 flight 登记保持同一锁临界区 |
| 10 | `python:S3776` | `web/cache.py:225` | CRITICAL | `_single_flight_async` 拆出 `_claim_async_flight` / `_wait_async_flight` / `_lead_async_flight` |
| 11 | `python:S7497` | `modules/xianyu_im/client.py:151` | MAJOR（BUG） | 清理后 `raise` 重抛 `CancelledError`，并在 docstring 说明"吞掉取消会让 `task.cancel()` 表现为正常返回" |
| 12 | `python:S1871` | `web/services/cache_scan.py:101` | MAJOR | 合并为 `if scan is None or self._is_expired(scan)`，新增 `_is_expired` 辅助方法 |
| 13 | `python:S3358` | `web/routes/api_vector_admin.py:237` | MAJOR | 新增模块级 `_probe_outcome(hits, raw)`，用 if 链返回 `OK` / `BELOW_THRESHOLD` / `NO_MATCH` |
| 14 | `python:S7503` | `modules/xianyu_im/service.py:72` | MINOR | `async def start` 改同步 `def start`（函数体无挂起点），同步改 4 处调用点：`web/startup.py:321`、`tests/test_xianyu_im_service.py` L205/L213/L223 |
| 15 | `typescript:S7764` | `components/TidalForagers/index.tsx:18` | MINOR | `window.matchMedia` → `globalThis.matchMedia`，与同行的 `globalThis.window` 判空一致 |
| 16 | `typescript:S1874` | `pages/Chatbot/components/markdownHighlight.ts:29` | MINOR | 废弃 `Content` → `RootContent`，import 改 `Element, ElementContent, Root, RootContent` |
| 17 | `typescript:S1874` | `pages/Chatbot/components/markdownHighlight.ts:47` | MINOR | `collectText` 形参类型改 `RootContent` |
| 18 | `typescript:S1128` | `components/DiffPreviewModal.tsx:5` | MINOR | 删除未使用的 `import type { AppConfig }` |
| 19 | `typescript:S1128` | `pages/Chatbot/Config.tsx:18` | MINOR | type import 中移除 `ChatbotConfig`，保留 `KBStatus`/`KBVersion`/`FAQ`/`AuditLog` |

### 4.2 首验引入、终验前已修掉的 2 条

| # | 规则 | 文件:行 | 级别 | 来源 | 修复 |
|---|------|---------|------|------|------|
| 20 | `python:S1192` | `infra/browser_kernel.py:32` | CRITICAL | 拆分常量时，`_CHROMIUM_EXE_SUBDIRS` 内仍有两处裸字面量 `"chrome.exe"` | 改为引用 `_CHROME_EXE_NAME` |
| 21 | `python:S125` | `web/routes/evaluations_list.py:67` | MAJOR | 辅助函数 docstring 写成了 `（task_id=None）`，被判定为"注释掉的代码" | 改写为「（未指定任务）」 |

### 4.3 修复过程中修正的自引入缺陷（非规则问题）

`api_maintenance.py` 首次重构时，`@router.get("/status")` 装饰器与 TTL 注释块留在原位，而 6 个辅助函数被插到装饰器之后，导致路由错误挂到 `_db_stats(container: Container)` 上，FastAPI 在导入期抛 `Invalid args for response field ... Container`，8 个测试文件 collection error。已把「段落头 + 路由装饰器 + TTL 注释」整体移回 `@cached_ttl` 之前，装饰器顺序恢复为 `router.get` 在上、`cached_ttl` 在下。

---

## 五、验证结果

### 5.1 重扫

| 项 | 结果 |
|----|------|
| OPEN 问题 | **0**（BLOCKER/CRITICAL/MAJOR/MINOR/INFO 全为 0） |
| 安全热点 | 0（`/api/hotspots/search` 对该 token 返回 403，无法独立枚举） |
| 质量门禁 | OK（new_coverage 84.7% / new_duplicated 0.0% / new_violations 0） |

### 5.2 定向测试（受影响模块 13 个文件）

`168 passed`，覆盖 browser_kernel / cache 容量·单飞·SWR / config env 持久化 / evaluations 市场统计·价格过滤 / maintenance 状态·清理·缓存扫描 / vector 探针与快照 / xianyu_im service。

### 5.3 全量测试

- 命令：`.venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider`（`addopts` 自带 coverage）
- 结果：**2924 passed, 3 skipped, 5 failed**，耗时 35:33，`coverage.xml` 已生成（pytest 统计 73%）
- 5 个失败全部在 `tests/test_health_unified.py`，断言 `status == "healthy"` 实际得到 `degraded`，`has_valid_m5tk` 为 False：该用例用模块导入时刻的 `_NOW` 构造 `_m_h5_tk`，而 `is_m5tk_expired` 的 TTL 是 **1200 秒**，全量套件跑到该文件时已过去约 35 分钟 → token 被判过期。
- 归因验证：单独执行 `pytest tests/test_health_unified.py` → **10 passed in 19.11s**，与本次修改无关，属测试自身的时间脆弱性（既有问题，非回归）。
- 复跑验证（重建后）：全量套件在 18:59 内跑完 → **2932 passed, 0 failed**，同一批用例全部通过，`coverage.xml` 重新生成（30,243 语句 / 73%），日志 `full-tests-2.log`。

### 5.4 生产构建

- 入口：`scripts/build-exe.ps1 -SkipDeps`（默认 Lite 形态，不随包 Chromium）
- 结果：**成功，退出码 0**
  - 免安装目录 `release\xianyu-hunter\`：995.1 MB / 6,815 个文件，主程序 `xianyu-hunter.exe` 87,002,887 B
  - 安装包 `release\XianyuHunter-Setup-v0.4.20261114.3.exe`：294,498,078 B，SHA256 `4C235DA547E596DE0212A9C88ED2B0BB89F897BD9B8017C18841064697AA2397`
  - 前端 SPA：`release\spa`
- 过程异常：首次 Inno 编译因 D 盘写满中断（`磁盘空间不足`），随后外部清理删除了整个 `release` 与 `.cache`，故本次为**从零重建**，日志见 `build-exe2.log`
- 详见 `docs/04-系统维护/sonar-reports/build-verification-20261114.md`（构建日志摘要与产物校验）

---

## 六、遗留说明

1. **安全热点维度**：token 无 `Security Hotspot` 权限，`/api/hotspots/search` 403。安全结论以 issue 类型（VULNERABILITY=0）+ 安全评级 A 为依据，热点数按 0 记录。
2. **整体覆盖率 52.9%**：仍低于通常 80% 的目标，属仓库历史短板；本次门禁只对「新代码」要求 ≥80%，已达标（84.7%）。
3. **`test_health_unified.py` 时间脆弱**：建议把 `_m5tk_value` 改为在用例执行时取 `time.time()`（或把 TTL 放宽到大于套件时长），否则全量套件必然在慢机上翻红。
4. **构建期 warning**：PyInstaller 收集 `chromadb.server.fastapi` / `onnxruntime.quantization.neural_compressor` 子模块时各有一条 ModuleNotFound（缺 opentelemetry.instrumentation / onnx_ir），不影响本项目使用的路径，属既有告警。

---

## 附录

- SonarQube Dashboard：`http://localhost:9000/dashboard?id=xianyu_hunter`
- 项目问题列表：`http://localhost:9000/project/issues?id=xianyu_hunter`
- 基线明细：`docs/04-系统维护/sonar-reports/baseline-issues.json`
- 终验明细：`docs/04-系统维护/sonar-reports/after-issues.json`（0 条）
- 扫描日志：`sonar-baseline-scan.log` / `sonar-rescan.log`（首验）/ `sonar-rescan2.log`（终验）
- 测试日志：`targeted-tests.log` / `full-tests.log`（磁盘危机期间被截断为结论摘要）/ `full-tests-2.log`（重建后重跑）
- 构建日志：`build-exe.log`（首次，因磁盘写满中断）/ `build-exe2.log`（重建，成功）
- 修复记录摘要：`docs/04-系统维护/sonar-reports/fix-record-20261114.md`
