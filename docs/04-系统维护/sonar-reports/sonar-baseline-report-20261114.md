# Xianyu Hunter — 修复前 SonarQube 扫描报告

**项目 Key**：`xianyu_hunter`
**项目名**：Xianyu Hunter
**扫描时间**：2026-11-14
**扫描方式**：降级 sonar-scanner CLI 8.0.1.6346（MCP 工具不可用）
**扫描器**：`D:\code\sonar\sonar-scanner-8.0.1.6346-windows-x64`
**服务端**：SonarQube Community Build 26.1.0.118079（`http://localhost:9000`，状态 UP）
**CE 任务**：`9a8b6bd7-bcf3-404e-bc3e-14766e46787f` → SUCCESS（executionTimeMs 103930）
**分析 ID**：`436058cc-133f-4231-92e0-7cee8f675ac8`
**扫描耗时**：53 分 42 秒（本机并行跑 Jenkins / Gradle / 另一项目 sonar 扫描，CPU 竞争明显）

---

## 一、质量门禁状态

**整体状态**：✅ 通过（`status=OK`，`caycStatus=compliant`）

Community Build 默认质量门不含自定义条件，`conditions` 为空数组，因此没有 new_coverage / duplicated_lines 之类的阈值判定项。以下为项目整体指标实测值，供人工判断：

| 指标 | 当前值 | 参考阈值 | 判定 |
|------|--------|----------|------|
| 代码行数（ncloc） | 99,243 | — | — |
| Bugs | 1 | 0 | ❌ |
| Vulnerabilities | 0 | 0 | ✅ |
| Code Smells | 18 | 0 | ❌ |
| 覆盖率 | 16.9% | ≥80% | ❌（历史基线，非本次新增） |
| 重复率 | 0.8% | ≤3% | ✅ |
| 认知复杂度总量 | 13,977 | — | — |
| 技术债（sqale_index） | 149（约 6.2 小时） | — | — |
| 可靠性评级 | C（3.0） | A | ❌ |
| 安全评级 | A（1.0） | A | ✅ |
| 可维护性评级 | A（1.0） | A | ✅ |

> 可靠性评级为 C 完全由 1 个 BUG（`python:S7497`）拉低，修复后应回到 A。

---

## 二、扫描范围

| 项 | 数值 |
|----|------|
| 后端源码 | `backend/xianyu_hunter`，Sonar 实际分析 427 个 Python 文件 |
| 前端源码 | `frontend/src`，401 个 ts/tsx 文件 |
| 测试代码 | `tests`（`sonar.tests`，不参与问题统计） |
| SCM blame 覆盖 | 683 个文件（1 个文件缺 blame，`warn_only`，不阻塞） |
| 扫描语言 | Python 3.10+ / TypeScript（`sonar.typescript.tsconfigPath=frontend/tsconfig.json`） |
| 排除项 | `node_modules` / `dist` / `.venv` / `.venv-build` / `**/*.html` / `**/*.css` 等 |

**配置来源**：项目自有的 `sonar-project.properties`（保留未覆盖，扫描时仅通过环境变量补充 `SONAR_HOST_URL` / `SONAR_TOKEN`）。

---

## 三、问题统计

### 按严重级别

| 级别 | 数量 | 占比 |
|------|------|------|
| BLOCKER | 0 | 0% |
| CRITICAL | 10 | 52.6% |
| MAJOR | 3 | 15.8% |
| MINOR | 6 | 31.6% |
| INFO | 0 | 0% |
| **合计** | **19** | **100%** |

### 按类型

| 类型 | 数量 | 占比 |
|------|------|------|
| CODE_SMELL | 18 | 94.7% |
| BUG | 1 | 5.3% |
| VULNERABILITY | 0 | 0% |

### 按语言

| 语言 | 数量 | 占比 |
|------|------|------|
| Python | 14 | 73.7% |
| TypeScript | 5 | 26.3% |

---

## 四、五维度覆盖说明

| 维度 | 覆盖方式 | 结果 |
|------|----------|------|
| 代码规范 | S1192 / S1871 / S1874 / S1128 / S7764 / S3358 | 7 条 |
| 潜在 Bug | BUG 类型规则（S7497）+ 异步语义规则（S7503） | 2 条 |
| 安全漏洞 | VULNERABILITY 类型 + 安全规则集（`security_rating=1.0`） | 0 条 |
| 性能 | 规则集无直接命中；S3776 认知复杂度为可维护性指标，不等价于性能缺陷 | 0 条 |
| 代码复杂度 | S3776（认知复杂度 16~26，阈值 15） | 9 条 |

> 安全热点（hotspots）维度未能覆盖：当前 token 无 `Security Hotspot` 权限，`/api/hotspots/search` 返回 403，热点数按 0 记录。安全维度以 issue 类型（VULNERABILITY + 安全规则）间接覆盖。

---

## 五、问题明细（共 19 条）

### 🔴 CRITICAL（10）

| # | 规则 | 文件 | 行 | 类型 | 描述 |
|---|------|------|----|------|------|
| 1 | `python:S1192` | `backend/xianyu_hunter/infra/browser_kernel.py` | 39 | CODE_SMELL | 字面量 `"chrome.exe"` 重复 3 次，应提取常量 |
| 2 | `python:S3776` | `backend/xianyu_hunter/infra/browser_kernel.py` | 420 | CODE_SMELL | `describe_kernel` 认知复杂度 26，超限 15 |
| 3 | `python:S3776` | `backend/xianyu_hunter/infra/browser_kernel.py` | 269 | CODE_SMELL | `resolve_kernel` 认知复杂度 21，超限 15 |
| 4 | `python:S3776` | `backend/xianyu_hunter/modules/xianyu_im/client.py` | 108 | CODE_SMELL | `run` 认知复杂度 23，超限 15 |
| 5 | `python:S3776` | `backend/xianyu_hunter/web/routes/api_ai.py` | 1518 | CODE_SMELL | `test_embedding_connection` 认知复杂度 16，超限 15 |
| 6 | `python:S3776` | `backend/xianyu_hunter/web/routes/evaluations_list.py` | 46 | CODE_SMELL | `_compute_market_stats_for_tasks` 认知复杂度 16，超限 15 |
| 7 | `python:S3776` | `backend/xianyu_hunter/config.py` | 215 | CODE_SMELL | `_persist_to_env` 认知复杂度 16，超限 15 |
| 8 | `python:S3776` | `backend/xianyu_hunter/web/routes/api_maintenance.py` | 246 | CODE_SMELL | `maintenance_status` 认知复杂度 23，超限 15 |
| 9 | `python:S3776` | `backend/xianyu_hunter/web/cache.py` | 182 | CODE_SMELL | `_single_flight` 认知复杂度 19，超限 15 |
| 10 | `python:S3776` | `backend/xianyu_hunter/web/cache.py` | 225 | CODE_SMELL | `_single_flight_async` 认知复杂度 19，超限 15 |

### 🟠 MAJOR（3）

| # | 规则 | 文件 | 行 | 类型 | 描述 |
|---|------|------|----|------|------|
| 11 | `python:S7497` | `backend/xianyu_hunter/modules/xianyu_im/client.py` | 151 | BUG | `asyncio.CancelledError` 在清理后未重新抛出 |
| 12 | `python:S1871` | `backend/xianyu_hunter/web/services/cache_scan.py` | 101 | CODE_SMELL | 该分支与第 97 行分支实现完全相同，应合并 |
| 13 | `python:S3358` | `backend/xianyu_hunter/web/routes/api_vector_admin.py` | 237 | CODE_SMELL | 嵌套三元表达式应抽为独立语句 |

### 🟢 MINOR（6）

| # | 规则 | 文件 | 行 | 类型 | 描述 |
|---|------|------|----|------|------|
| 14 | `python:S7503` | `backend/xianyu_hunter/modules/xianyu_im/service.py` | 72 | CODE_SMELL | `async def start` 内无任何 await，应改同步 |
| 15 | `typescript:S7764` | `frontend/src/components/TidalForagers/index.tsx` | 18 | CODE_SMELL | 使用 `window`，应优先 `globalThis` |
| 16 | `typescript:S1874` | `frontend/src/pages/Chatbot/components/markdownHighlight.ts` | 29 | CODE_SMELL | 类型 `Content` 已废弃 |
| 17 | `typescript:S1874` | `frontend/src/pages/Chatbot/components/markdownHighlight.ts` | 47 | CODE_SMELL | 类型 `Content` 已废弃（第二处） |
| 18 | `typescript:S1128` | `frontend/src/components/DiffPreviewModal.tsx` | 5 | CODE_SMELL | 未使用的 import `AppConfig` |
| 19 | `typescript:S1128` | `frontend/src/pages/Chatbot/Config.tsx` | 18 | CODE_SMELL | 未使用的 import `ChatbotConfig` |

---

## 六、修复计划

按 `Blocker > Critical > Major > Minor > Info` 排序，分三批执行，全部采用代码修复，不使用 `NOSONAR` 抑制：

| 批次 | 涉及级别 | 条数 | 处理策略 |
|------|----------|------|----------|
| 第一批 | CRITICAL（10） | 10 | 复杂度拆分：抽子函数 / 抽常量，保持原有异常文案与调用语义 |
| 第二批 | MAJOR（3） | 3 | BUG 修复（重抛 CancelledError）+ 合并相同分支 + 拆嵌套三元 |
| 第三批 | MINOR（6） | 6 | 未用 import 清理 / 废弃类型替换 / `globalThis` 替换 / async 改同步（含 4 处调用点同步修改） |

**回归保护**：`service.start()` 由异步改同步会改变调用契约，须同步修改 `web/startup.py` 与 `tests/test_xianyu_im_service.py` 中的调用点；复杂度拆分不得改变日志文案与异常信息。

---

## 七、结论

- 基线共 **19 个 OPEN 问题**（0 BLOCKER / 10 CRITICAL / 3 MAJOR / 6 MINOR），无安全漏洞。
- 问题高度集中在**认知复杂度**（9/19）与**代码规范**（7/19），属可维护性债务，不影响运行时正确性；唯一功能性缺陷是 `CancelledError` 未重抛（BUG）。
- 仓库整体健康：安全评级 A、可维护性 A、重复率 0.8%、漏洞 0。
- 覆盖率 16.9% 为历史遗留短板，不在本次修复范围（本次仅保证新增/修改路径有测试覆盖）。

**原始数据**：`docs/04-系统维护/sonar-reports/baseline-issues.json`（19 条 issue 完整字段，含 issue key）
**原始日志**：`sonar-baseline-scan.log`
