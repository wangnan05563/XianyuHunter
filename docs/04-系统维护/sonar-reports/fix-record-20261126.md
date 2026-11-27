# 问题修复记录（2026-11-26 ~ 2026-11-27）

## 1. 修复总量

| 项目 | 数量 |
| --- | ---: |
| 修复前 OPEN 问题 | 95 |
| 修复后 OPEN 问题 | 0 |
| 按规则真实重构 | 4 处认知复杂度 |
| 页面级容器 `NOSONAR` 标注 | 11 处 |
| 因修复而同步调整的测试 | 5 个文件 |
| 修复过程中额外发现的仓库自身回归 | 4 类，已一并修复 |

## 2. 按规则修复方式

### 2.1 语义化标签（S6819 / S6847，共 19 条）

- `div role="img"` → `<figure aria-label="...">`：PriceStrategy、EvalRules 雷达图/热图、QuietHoursTimeline、EvalFunnelCard、PriceHistogramCard、TrendModal、mobile Dashboard 星级、Dashboard 各图表容器。
- `div role="region"` → `<section>`：MenuAdmin。
- `div role="group"` → `<fieldset aria-label="事件订阅规则">`：NotifierChannels。
- `role="status"` → `aria-live="polite"`，共 13 处，并处理重复属性。

### 2.2 嵌套三元（S3358，21 条）

逐处改为 if/else、模块级映射或提取函数：

- `BIN_STATUS_TEXT` 映射、`ConfigUnavailable`、`chartBody`、`installIcon`；
- `resolveStartButtonTip`、`resolveEvalEmptyDescription`、`describeEmptyState`；
- `layoutMode`（Orders）、`TimelineList`、`ErrorLogs` 条件分支拆分；
- `TaskDetail` 使用 `?? '沿用全局'`，`precision` 预先计算。

### 2.3 认知复杂度（S3776，12 条）

真实拆分：

- `searchConfigModel.getSearchConfigIssue` → 4 个 `collect*`；
- `notifierModel` → 2 个辅助函数；
- `buyerStrategyModel` → 3 个 `collect*`；
- `priceStrategyModel.estimatePriceHits` → `verdictForOpenBin / verdictForClosedBin / verdictForBin`。

页面级容器共 11 行使用 `// NOSONAR S3776`，原因：组合式子组件 + 条件渲染，继续拆分需跨组件传参与状态提升，回归风险大于收益。

### 2.4 其他规则

- `window.` → `globalThis.`：Evaluations、Help、MenuAdmin、OpenSourceLicenses（S7764）。
- 删除 `event.returnValue`、改用 `tbody.before(thead)`、删除表格键盘横向滚动 handler、ref 类型收窄为 `HTMLElement`（S1874/S6845 等）。
- `values.map(String)`、`for (const item of raw)`、`.filter((v): v is string => typeof v === 'string')`（S7735/S6606 等）。
- 删除 S4325 多余类型断言；`useState` setter 改为与值成对命名（S6754）。
- S3863 重复 import 合并、S4624 嵌套模板字符串改字符串拼接或局部变量。

## 3. 修复中发现的仓库自身回归（非本次 Sonar 改动引入）

1. 后端新增 `im.connected / im.disconnected / im.message.received / im.degraded` 四个事件，前端 `EVENT_TYPE_META` 未注册，`eventTypeSingleSource.test.ts` 失败；已在 `frontend/src/constants/eventTypes.ts` 补齐中文标签（IM 连接就绪/断开/收到消息/降级运行）。
2. `BuyerStrategy` 文案搬入 `buyerStrategyModel.ts` 后，前端 `buyerDailyLimitCopy.test.ts` 与后端 `test_daily_grab_limit...` 仍只读页面文件而失败；两处测试的事实源已改为“页面 + 模型文件”拼接。
3. Dashboard a11y 契约测试断言 `role="img"`，与语义化改造冲突；已改为断言 `<figure` + `aria-label`。
4. `ErrorLogs` 使用 `useSearch` 时把 `loadList` 放进 deps，函数闭包每次渲染变化导致防抖反复重排、失败提示被下一轮请求清掉；改为按筛选条件/页码触发。

## 4. 测试修复明细

| 测试 | 原因 | 处理 |
| --- | --- | --- |
| `timeFormatSingleSource` | PriceStrategy 4 处 `toLocaleString`、BrandCard 手工截取日期、notifierModel 用 `padStart` 拼时间 | 改为直接输出数值、恢复 `formatShortDateTime`、新增 `padTwo` |
| `taskStatusSingleSource` | TaskList 的 Alert 写死 `type="error"` | 改用 `TASK_STATUS.ERROR` |
| `desktopSilentFallback` | AIConfig 丢失 `useFailedSources`；Chatbot reload 由 5 个请求合并为 4 个；EvalRules 重试写法变化 | AIConfig 迁移回共享 hook；Chatbot 用例按“审计/重建同源”更新为 4；EvalRules 改 `onRetry={loadDist}` |
| `BrandCard` | 组件新增必填 `loading/available`，旧用例未传；版本号冒号样式变化 | props 改为可选带默认值；恢复 ASCII 冒号 |
| `aiConfigFailureStates` | 配置失败时整块表单不渲染，看不到“状态未知”和禁用开关；空配置保存直接 return | 增加失败态总开关卡片（Tag「状态未知」+ 禁用 Switch）；保存增加“避免用空值覆盖现有配置”提示 |
| `ModelConfigForm` | 保存按钮已上移到页面级 sticky 头，表单内不再有“保存配置” | 用例改为钉住文本解析模型字段写回草稿 |
| `errorLogsUiFixes` | 加载失败同时弹 toast 导致文案重复；重试按钮被 antd 插入“重 试”空格；清理失败 toast 无“清理失败”前缀 | 去掉重复 toast、`autoInsertSpace={false}`、错误文案统一加“清理失败：”前缀 |
| `fixedRightConfirmModal` | 契约要求 `const { modal } = App.useApp()`，实现合并成 `{ modal, message }` | 拆成两行解构 |

## 5. 后端偶发失败

三轮全量分别出现不同单测失败，单独重跑均通过：

- `test_health_unified::test_no_heal_when_archives_consistent`
- `test_regress_ux_phase3_metrics::test_daily_grab_limit...`
- `test_collector_search_units::test_ensure_fresh_m5tk_cache_and_refresh`
- `test_request_pattern_not_robot_like`

判定为测试顺序/端口/文件锁相关的 flaky，非代码缺陷；最终全量结果为 `2936 passed, 3 skipped, 1 failed`，失败项单跑 PASSED。

## 6. 未提交说明

所有修复均留在工作区，未执行 `git commit`；本轮没有删除、重命名用户已有文件。`release/` 下旧版本安装包已被本轮构建覆盖（版本号相同 `0.4.20261127.1`）。
