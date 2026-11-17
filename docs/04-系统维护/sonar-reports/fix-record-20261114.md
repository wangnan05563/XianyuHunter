# SonarQube 问题修复记录摘要

- 项目：`xianyu_hunter`（Xianyu Hunter，FastAPI + React）
- 扫描服务：SonarQube Community Build 26.1.0.118079（`http://localhost:9000`）
- 记录日期：2026-11-14
- 基线报告：`sonar-baseline-report-20261114.md`
- 修复后报告：`sonar-fix-report-20261114-2030.md`

## 1. 总览

| 指标 | 基线（第 1 轮） | 终验（第 3 轮） |
|---|---|---|
| OPEN 问题 | 19 | 0 |
| 严重级别分布 | CRITICAL 10 / MAJOR 3 / MINOR 6 | — |
| 类型分布 | CODE_SMELL 18 / BUG 1 | — |
| 语言分布 | Python 14 / TypeScript 5 | — |
| 质量门禁 | — | OK（new_coverage 84.7%、new_violations 0、new_duplicated_lines_density 0.0%） |
| Bugs / Vulnerabilities / Code Smells | 1 / 0 / 18 | 0 / 0 / 0 |
| Reliability / Security / Maintainability | — | 全部 A（1.0） |

修复原则：**全部通过修改代码消除根因，未使用任何 NOSONAR 抑制注释**。

## 2. 基线 19 条修复明细

### 2.1 CRITICAL（10 条，均为认知复杂度或重复字面量）

| # | 规则 | 文件 | 修复方式 |
|---|---|---|---|
| 1 | `python:S1192` | `backend/xianyu_hunter/infra/browser_kernel.py` | `"chrome.exe"` 重复字面量抽为 `_CHROME_EXE_NAME` / `_EDGE_EXE_NAME` 常量，路径表改用 f-string 组合 |
| 2 | `python:S3776` | `infra/browser_kernel.py` — `resolve_kernel` | 拆为 `_resolve_explicit_policy` + `_resolve_auto` |
| 3 | `python:S3776` | `infra/browser_kernel.py` — `describe_kernel` | 拆为 `_available_status` + `_kernel_status` |
| 4 | `python:S3776` | `modules/xianyu_im/client.py` — `run` | 接收循环抽为 `_receive_loop(transport)`，返回 `("stop"/"degrade"/"reconnect", 原因)` |
| 5 | `python:S3776` | `web/cache.py` — `_single_flight` | 拆为 `_claim_flight` / `_wait_flight` / `_lead_flight`，角色判定与 flight 登记保持同一锁临界区 |
| 6 | `python:S3776` | `web/cache.py` — `_single_flight_async` | 按同样的三件套拆出异步版本 |
| 7 | `python:S3776` | `web/routes/api_ai.py` — `test_embedding_connection` | 拆为 `_resolve_embedding_base_url` / `_resolve_embedding_model` / `_resolve_embedding_dimensions` |
| 8 | `python:S3776` | `web/routes/evaluations_list.py` — `_compute_market_stats_for_tasks` | 拆为 `_fill_batch_stats` / `_fill_one_task_stats` / `_fill_global_stats`（全局分支日志文案原样保留） |
| 9 | `python:S3776` | `web/routes/api_maintenance.py` — `maintenance_status` | 拆为 `_db_stats` / `_scan_log_files` / `_log_stats` / `_fill_browser_data_stats` / `_fill_pycache_stats` / `_cache_stats` |
| 10 | `python:S3776` | `config.py` — `_persist_to_env` | 拆为 `_read_env_lines` / `_is_env_assignment` / `_env_key_of` / `_upsert_env_lines` |

### 2.2 MAJOR（3 条）

| # | 规则 | 文件 | 修复方式 |
|---|---|---|---|
| 11 | `python:S7497`（BUG） | `modules/xianyu_im/client.py:151` | 捕获 `CancelledError` 后清理再 `raise` 重抛，docstring 说明「吞掉取消会让调用方 `task.cancel()` 表现为正常返回」 |
| 12 | `python:S1871` | `web/services/cache_scan.py:101` | 两个相同分支合并为 `if scan is None or self._is_expired(scan)`，新增 `_is_expired` |
| 13 | `python:S7503` | `modules/xianyu_im/service.py:72` | `async def start` 改为同步 `def start(self) -> None`；同步修正 4 处调用点：`web/startup.py:321`（去掉 `create_task`）、`tests/test_xianyu_im_service.py` L205/L213/L223（去掉 `await`） |

### 2.3 MINOR（6 条）

| # | 规则 | 文件 | 修复方式 |
|---|---|---|---|
| 14 | `python:S3358` | `web/routes/api_vector_admin.py:237` | 嵌套三元/条件表达式抽为模块级 `_probe_outcome(hits, raw)`，返回 `"OK"/"BELOW_THRESHOLD"/"NO_MATCH"` |
| 15 | `typescript:S1128` | `frontend/src/components/DiffPreviewModal.tsx:5` | 删除未使用的 `import type { AppConfig }` |
| 16 | `typescript:S1128` | `frontend/src/pages/Chatbot/Config.tsx:18` | 从 type import 中移除 `ChatbotConfig`（保留 `KBStatus`/`KBVersion`/`FAQ`/`AuditLog`） |
| 17 | `typescript:S1874` | `frontend/src/pages/Chatbot/components/markdownHighlight.ts` | import 改为 `Element, ElementContent, Root, RootContent`，`collectText(node: RootContent)` |
| 18 | `typescript:S1874` | 同上 | 同步替换已废弃类型引用 |
| 19 | `typescript:S7764` | `frontend/src/components/TidalForagers/index.tsx:18` | `window.matchMedia` → `globalThis.matchMedia` |

## 3. 二次修正（重构中自引入，第 2 轮扫描发现）

| # | 规则 | 文件 | 问题与修正 |
|---|---|---|---|
| A | `python:S1192` | `infra/browser_kernel.py:32` | `_CHROMIUM_EXE_SUBDIRS` 中残留两处裸字面量 `"chrome.exe"` → 改用 `_CHROME_EXE_NAME` |
| B | `python:S125` | `web/routes/evaluations_list.py:67` | 辅助函数 docstring 写成「（task_id=None）」被判为注释掉的代码 → 改写为「（未指定任务）」 |

另有一次结构性返工：`api_maintenance.py` 首次拆分时 `@router.get("/status")` 装饰器与 5 行 TTL 注释留在原位、辅助函数被插入装饰器之后，导致路由挂到 `_db_stats(container: Container)`，FastAPI 导入期抛 `Invalid args for response field`，8 个测试文件 collection error。已把「段落头 + 装饰器 + TTL 注释」整体移回 `@cached_ttl(300, ...)` 之前（当前 L359 路由装饰器、L366 `def maintenance_status`，辅助函数在 L238 起）。

## 4. 验证结果

| 验证项 | 结果 |
|---|---|
| 第 1 轮扫描（基线） | 19 OPEN |
| 第 2 轮扫描（CE `034a7688-9651-4553-9e17-497a7212629c`） | 19 → 2（均为自引入），门禁 ERROR（new_violations=2、new_coverage 10.5%） |
| 第 3 轮扫描（CE `c5991c45-8d56-4727-a718-ef5b72e98776`） | **0 OPEN**，门禁 OK；warn=1 仅为 1 个文件缺 SCM blame |
| 定向测试（13 个受影响文件） | 168 passed（162.01s） |
| 全量测试 | 2924 passed / 3 skipped / 5 failed（35:33） |
| 5 个失败归因 | 全在 `tests/test_health_unified.py`：用例用导入时刻的 `_NOW` 构造 1200 秒 TTL 的 `_m_h5_tk`，套件跑到该文件已过约 35 分钟 → token 判过期。隔离复跑该文件 10 passed（19.11s），属既有时间脆弱性，非本次改动引入 |
| 全量测试（复跑，重建后） | **2932 passed / 0 failed**（18:59），`coverage.xml` 重新生成（30,243 语句 / 73%），日志 `full-tests-2.log` |
| 业务规则门禁 | `verify_business_rule_coverage.ps1`：price_filter 4/4 entry_point PASS，exit=0 |
| 前端类型检查 | `npx tsc --noEmit` exit 0 |

## 5. 遗留说明

1. 整体覆盖率 52.9%（历史短板），但新增代码覆盖率 84.7%，门禁达标。
2. `tests/test_health_unified.py` 的 5 例存在「模块导入时刻 + 1200 秒 TTL」的时序脆弱性，套件整体运行超过 20 分钟后必现；建议后续改为按调用时刻构造 token。
3. hotspots 未能枚举（该 token 对 `/api/hotspots/search` 返回 403），报告中 hotspots 记为 0 是基于扫描器侧结果。
4. 无任何 NOSONAR 抑制，未修改 SonarQube 侧规则或问题状态。
