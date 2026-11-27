# SonarQube 修复前扫描报告（2026-11-26）

## 1. 扫描概要

| 项目 | 内容 |
| --- | --- |
| 项目 | XianyuHunter（projectKey: `xianyu_hunter`） |
| 代码版本 | git `main` / HEAD `7606beb0`，产品版本 `0.4.20261126.2` |
| 扫描时间 | 2026-11-26，总耗时约 27 分 20 秒 |
| SonarQube | `http://localhost:9000`，26.1.0.118079 |
| Scanner | sonar-scanner 8.0.1.6346（Windows x64） |
| CE 任务 | `b45e268d-5505-483a-ab59-e8fabdba6a9a`，SUCCESS |
| 代码规模 | 103,887 ncloc |
| 原始问题数 | **95 条 OPEN** |

## 2. 问题分布

### 按严重级别

| 级别 | 数量 |
| --- | ---: |
| BLOCKER | 0 |
| CRITICAL | 12 |
| MAJOR | 45 |
| MINOR | 38 |
| INFO | 0 |

95 条全部位于前端 TypeScript（`frontend/src`），后端 Python 本轮为 0 条。

### 按规则

| 规则 | 级别 | 数量 | 含义 |
| --- | --- | ---: | --- |
| typescript:S3358 | MAJOR | 21 | 嵌套三元表达式 |
| typescript:S6819 | MAJOR | 18 | 用 ARIA role 模拟语义化标签 |
| typescript:S7735 | MINOR | 13 | 可简化的分支/条件写法 |
| typescript:S3776 | CRITICAL | 12 | 认知复杂度超过 15 |
| typescript:S7764 | MINOR | 10 | 应使用 `globalThis` 而非 `window` |
| typescript:S4325 | MINOR | 4 | 多余的类型断言 |
| typescript:S6606 | MINOR | 3 | 可用逻辑赋值/空值合并简化 |
| typescript:S7770 | MINOR | 3 | 其他代码异味 |
| typescript:S3863 | MINOR | 2 | 重复 import |
| typescript:S4624 | MAJOR | 2 | 嵌套模板字符串 |
| typescript:S6845 | MAJOR | 2 | 非交互元素绑定交互事件 |
| typescript:S1874 | MINOR | 1 | 使用已废弃 API |
| typescript:S4138 | MINOR | 1 | 其他代码异味 |
| typescript:S6754 | MINOR | 1 | useState 未按值/设置器成对解构 |
| typescript:S6847 | MAJOR | 1 | 用 ARIA role 模拟语义化标签 |
| typescript:S7768 | MAJOR | 1 | 其他代码异味 |

### 问题最集中的文件

| 文件 | 问题数 |
| --- | ---: |
| `frontend/src/pages/Config/PriceStrategy.tsx` | 14 |
| `frontend/src/pages/MenuAdmin/index.tsx` | 9 |
| `frontend/src/pages/Help/index.tsx` | 7 |
| `frontend/src/pages/Evaluations/index.tsx` | 7 |
| `frontend/src/pages/Config/EvalRules.tsx` | 6 |
| `frontend/src/pages/About/BrandCard.tsx` | 5 |
| `frontend/src/pages/Config/searchConfigModel.ts` | 5 |
| `frontend/src/pages/Chatbot/Config.tsx` | 4 |
| `frontend/src/pages/Config/buyerStrategyModel.ts` | 4 |
| `frontend/src/pages/Config/AIConfig/index.tsx` | 3 |

## 3. 修复前质量门禁

| 条件 | 阈值 | 实际值 | 状态 |
| --- | ---: | ---: | --- |
| new_coverage | ≥ 80% | 3.0% | **ERROR** |
| new_violations | ≤ 0 | 95 | **ERROR** |
| new_duplicated_lines_density | ≤ 3% | 0.60% | OK |

## 4. 结论

基线为 95 条前端 TypeScript 代码异味，无 Blocker、无后端问题；质量门禁因新增违规 95 条、新增代码覆盖率 3.0% 处于失败状态。明细见同目录 `baseline-issues-20261126.json`。
