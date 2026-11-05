# 优化建议清单（基于 2026-10-31 全量 API 压测数据）

> 每条给出：**具体优化内容** → **证据**（实测数字 + 源码位置） → **量化预期收益** → **优先级** → **验证方式**。
> 收益口径说明：下文所有「目标值」以本次实测的**同类廉价接口**为地板参照 —— `GET /api/anticrawl/fingerprint` 9.0 ms、`GET /api/notifier/quiet-hours` 8.4 ms、`GET /api/anticrawl/freq/delay` 7.4 ms（单用户基线，纯配置/内存读取）。凡「缓存命中」类预测均按该地板 + 少量序列化开销（10~20 ms）估算，未采用无参照的外推。
> 优先级 = 影响面（该接口在真实控制台中的调用频率 × 劣化幅度）÷ 实现成本。

## 0. 汇总表（按优先级）

| 编号 | 优化项 | 优先级 | 关键指标预期变化 | 成本 |
|---|---|---|---|---|
| B1 | 缓存单飞（single-flight） | **高** | 长跑混合场景 avg 200.5 → ≤80 ms；长跑整体 P95 544.5 → ≤250 ms | 低（`web/cache.py` 约 20 行）✅ **已落地** |
| B5 | SWR「后台刷新」实为同步 + 异步分支静默失效 | **高** ✅ **已落地** | `maintenance/status` 过期后首次调用 ≈1.6 s → ≈0；消除 SWR 冻结隐患 | 低 |
| C2 | `/api/auth/me` 去重复文件解析 | **高** ✅ **已落地（①⑤′；②③④判定不落；根因归属被实测推翻）** | ~~avg 152.6 → ≤50 ms~~ **作废**：处理器本身 ~4~7 ms（进程内）/ p50 ~10 ms（安静服务端）。实际落地效果 = **每请求 SQL 2.0 → 1.0 条**，省掉的是 0.5~9 ms 的 StaticPool 单连接占用（随订单规模）；HTTP avg 在本机噪声下**方向不可判定**，并发墙归 D1 | 低 |
| C4 | 反爬策略探测降本 + 缓存 | **高** | avg 1 094.7 → ≤30 ms；96 并发 2 511.8 → ≤100 ms | 低 ✅ **已落地（①③；②不落地，且「≤30 ms」经实测作废 → 约 190~250 ms）** |
| D1 | 慢同步接口独立线程池 | **高** ✅ **已落地（2026-11-04；原文建议的 `anyio.to_thread.RunVars` 装机 anyio 4.13 根本不存在 → 改为 `create_app()` 里按名单集中改写；`logs/search` 因压测样本全是 422、无实测占用而判定不入道）** | 沙箱实测（生产同形，两轮同向）：廉价接口 p50 **3 509.6 / 1 153.9 → 34.3 / 59.8 ms**，60 s 完成样本 **16 / 34 → 504 / 334**，`queue_p95` **974.4 → 6.7 ms** 而 `work_p50` 恒 ~4 ms。~~48/96 并发轻组 P95 ≤1 000 ms、缺陷率 <0.5%~~ **本轮不发布**，须同一 JMX 复跑 | 中 |
| A2 | `eval.*` 事件查询下推 | **高** ✅ **已落地（2026-11-04；方向与原文相反：前缀→等值；「缓存命中态」与「≤60 ms」被实测推翻）** | ~~`/api/evaluations` avg 195.9 → ≤60 ms~~ **作废**：压测身份（`user_id="default"`）读到 0 行，195.9 ms 里不含这笔成本（归 D1）。真实收益在**数据主人身份**：端点中位 **574.7 / 586.5 → 238.1 / 228.6 ms**，取数段 **218.7 / 256.2 → 35.9 / 65.3 ms**，每请求 SQL 6 → 5 条；5× 副本下取数段再省 ≈4.8×（抗增长） | 中 → 实际低（未做 SQL 分页/聚合下推） |
| C6 | PyYAML 走纯 Python 后端，libyaml 已在而未用 | **高**（新发现，非压测得出）✅ **已落地（读侧）** | 同一份 config.yaml：解析 **47.7 → 6.5 ms（7.4×，实测落地值）**；写侧不换（C dumper 把 emoji 写成 `\U0001F4F7` 转义，产物不再逐字节相同），序列化那 21~27 ms 留在原地 | 极低（新增 `infra/yaml_io.safe_load` + 9 个读盘点） |
| B2 | 慢接口纳入 TTL 缓存（raw/share/snapshots/preview） | ~~高~~ → **撤回 4/5 条** | 轻组基线 avg 50.9 → ≤25 ms 的预期**不成立**（见 §B2 反证）；只做安全残留 = config memo | 低 |
| A4 | `maintenance/status` 计数降本 | **高** ✅ **已落地（2026-11-03）** | 完整重算 **2 235 / 5 205 ms → 188 / 170 ms**（「≤200 ms」在重算路径成立），消除 60 s 超时（曾致 P95 60 033 ms / 37.5%）。**归因修正**：6 张表 `COUNT(*)` 每张仅 ~0.1 ms，真正成本是漏排除的 `.venv-build/` pycache（1 578 个目录 / 131.7 MB）；原文 ①②（近似行数/后台计数）因此不成立 | 低 |
| A1 | 补齐 5 个过滤列索引 | 中 | `prices/category-comparison` 长跑 avg 580.2 → ≤200 ms | 低 |
| C3 | `_load_yaml` 走单例缓存 | 中 ✅ **部分落地（config memo）** | `config/raw` 132.4 → ≤15 ms 的预期**过高**：实测解析 52 ms、脱敏+序列化另占 ~33 ms，memo 只砍前者（→ 约 80~90 ms）。写侧 `preview/save` 各去掉一次全量读 | 低 |
| C1 | `_dir_size` 双调用合并 | 中 | ✅ 已落地（2026-11-03）：实测降幅 **43%**（不是 50%，一遍里还有 26% 固定开销），另把 `/status` 的同步走盘移出事件循环；`vector-admin/snapshots` 233.8 → 待复跑（≤120 ms 未证实） | 极低 |
| D2 | 打破 `cache_scan` 进程级 RLock 串行 | 中 ✅ **已落地（2026-11-05，但落地的不是「按路径分锁」**：三个调用点传的是同一个目录，串行的不是路径而是预算片；改为「时间片不持锁 + 同键单推进者」） | 合成树实测：同路径 8 个并发到达 p95 **42.4 / 36.7 → 9.9 / 2.8 ms**，扫完墙钟不变（省掉的是排队不是吞吐）；96 并发 avg 1 822.8 → ≤400 ms **仍待 JMX 复跑** | 低 |
| D3 | 写侧批量 UPSERT + 池/超时调优 | 中 | 16 并发写 TPS 51.9 → ≥66（回到峰值档）；P95 635.8 → ≤400 ms | 中 |
| D4 | 大结果集流式导出 | 中 | `db-admin/tables/items/export` P99 1 827.7 → ≤600 ms；私有内存峰值 617.7 MB 下降 | 中 |
| E1 | 多 worker 部署（纯 Web） | 中 | 吞吐 80 → ≥150 rps；96 并发 P99 4 296.9 → ≤1 500 ms | 中（需配合 B1/B3） |
| A5 | 日志/错误检索改前缀匹配或 FTS5 | 中 | `logs/search` 132.9 → ≤40 ms | 中 |
| B3 | 启动预热扩展 | 中 | 冷启动首请求 1.1 s → ≤100 ms | 低 |
| E3 | 依赖不可用的 503 语义改造 | 中 ✅ **已落地（2026-11-03）** | 30 min 内 795 次 503 → 0（改为 200 + 单条 SSE error 事件，保留 status/code/detail），长跑缺陷率 0.74% → 0 | 低 |
| C5 | 评估列表 N+1 批量化 | 中 | 评估列表 P99 858 → ≤300 ms | 中 |
| B4 | 缓存容量上限/LRU（原「键粒度 + 容量上限」） | 中↑（原低）✅ **已落地（2026-11-05；RSS 归因被实测推翻 —— 全量 GET 扫一遍只留 7 条 / 0.02 MB，增长维度是键基数不是值体积）** | ~~RSS 峰值 414.5 → ≤350 MB；长跑斜率 +75.9 MB/h → ≈0~~ **作废**（缓存量级差 3~4 个数量级）。改为：把「未约束查询参数撑开键空间」这条路封死（`cache_max_entries`，默认 2000 ≈ 最坏 8.8 MB） | 中 |
| E2 | SSE 与 API 线程池隔离 | 低 | 长连接不再挤占同池（见「本次自伤实验」证据） | 中 |

---

## A. 数据库查询优化

### A1. 补齐 5 个高频过滤列索引 — 优先级 **中**
**具体优化内容**：在 `backend/xianyu_hunter/infra/db_models.py` 的 `__table_args__` 中补建索引，并按现有迁移通道 `init_db._migrate_create_index` 落库：
- `items.price`（区间过滤）
- `evaluations.risk_level`（`SUM(CASE …)` 分组统计）
- `price_history.user_id`（多用户隔离过滤；该表当前只有 `ix_price_history_item_captured(item_id, captured_at)`，见 `db_models.py:177`）
- `orders.type`
- `task_links.updated_at`

**证据**：已核对 `db_models.py:117-119/194-195/214-215/323-328` 的现有索引清单，上述列**均无索引**。使用点：`web/routes/stats_price_trend.py:49-51`、`web/routes/stats_overview.py:134-141`、`infra/repo_price_history.py:126,137`、`infra/repo_orders.py:90`、`web/routes/price_dashboard.py:421`。实测体现为 `GET /api/prices/category-comparison` 在 1 并发时 29.9 ms、12 并发长跑 580.2 ms（P95 1 329.9 / P99 1 831.4）—— 该接口在 `task_id=None` 时无 WHERE 条件全表扫 `items`（`price_dashboard.py:95-98`）。
**量化预期收益**：本副本 items 仅 774 行时全表扫已是百毫秒级；补索引后区间过滤类接口 P95 预计由 1 330 → **≤500 ms**（12 并发），且随数据量增长由线性恶化转为对数级。**当前数据量下延迟收益有限，属「防恶化」型投资**，故列为中优先级而非高。
**验证方式**：`EXPLAIN QUERY PLAN` 前后对比 + 同一 JMX（`reads_light`）复跑，看 `category-comparison` 与 `stats/price-trend` 的 P95。

**A1 落地状态（2026-11-06；原文 5 条里只落 3 条，1 条改复合形态、2 条判定不落）**：用 `a1_index_probe.py` 在合成库（每表 2 万行，约为真实 items 774 / task_links 2 050 的 10~25×）上逐条试索引，判据是「`EXPLAIN QUERY PLAN` 是否真的改用它」+ 15 次中位耗时 + 写侧 3 000 行插入代价：
- ✅ `items.price`：`stats_price_trend` 的 `price between ? and ? order by publish_time desc limit 2000` **15.09 → 7.17 ms**（计划从「publish_time 索引全扫」改为「price 索引区间 + 临时 B-TREE 排序」）；写代价 3 000 行 **12.5 → 16.6 ms（+33%）**。
- ✅ `evaluations.risk_level`：`stats_overview` 的无 WHERE `SUM(CASE risk_level='low')` 变成**覆盖索引扫描**（`SCAN evaluations USING COVERING INDEX ix_evaluations_risk_level`），**3.45 → 2.17 ms**；写代价 3 000 行 **7.9 → 14.1 ms（+78%，折合每行 +2 µs）**。
- ✅ `orders.type` **落地形态改为 `(type, created_at)` 复合**：原文建议的单列实测 **4.85 → 5.49 ms（更慢，仍要临时排序）**；复合形态 **4.08 → 0.50 ms（−88%）且计划里没有 `TEMP B-TREE`**；写代价 3 000 行 16.6 → 17.3 ms（+4%）。
- ❌ `price_history.user_id` **判定不落**：两个读点（`repo_price_history.list_recent` / `get_min_price`）都以 `item_id` 等值前缀走既有 `(item_id, captured_at)`，加索引前后计划与耗时都不变（0.31 → 0.33 ms），只剩写代价。
- ❌ `task_links.updated_at` **判定不落**：单列不改变计划（仍走 `ix_task_links_type_key`；49.91 → 40.60 ms 是缓存预热，不可归因）；复合 `(link_type, updated_at)` 虽被选用，但只 **32.74 → 31.43 ms（−4%）** —— 成本在逐行 `json_extract`，而写代价 +46%（14.4 → 21.0 ms/3 000 行）。

落地：ORM `__table_args__` 三处 `Index(...)`（新建库路径）+ `init_db` 三条 `_migrate_create_index`（旧库路径）。回归 `tests/test_perf_a1_indexes.py` 8 例（三个索引存在、三条查询计划断言、两条「判定不落」的反向门禁、ORM 声明与迁移一致、`init_db` 幂等、旧库 drop 后迁移回填）；`a1_mutation_run.py` 4 红（删 items/orders 迁移行、ORM 改错列、把 `price_history.user_id` 加回来）+ 2 绿（重复 seller 索引 / 只改 docstring），`db_models.py` 按 sha256 逐字节还原。**接口级数字仍待同一 JMX 复跑**：`prices/category-comparison` 的 580.2 ms 长跑 avg 与 `stats/price-trend` 的 P95 是下一个复跑核对项。

### A2. `eval.*` 事件全量重载下推到 SQL — 优先级 **高** ✅ **已于 2026-11-04 落地（取数口径收窄；原文建议的 `LIKE 'eval.%'` 恰是实测更慢的形态，「缓存命中态」与「≤60 ms」两处被推翻）**
**原建议（② 的 SQL 形态与「缓存命中态」两处被实测修正，见下方落地状态）**：`GET /api/evaluations` 目前经 `web/routes/evaluations_list.py:732` 调 `container.repo.list_events_by_type_prefix(...)`，其默认 `limit=50000`（`infra/repo_events.py:337`，注释自陈「评估事件量级的上限」），把事件整段读进 Python 再过滤 / 分页 / 求中位数；另在同函数额外发一次 COUNT（`repo_events.py:333-365`）。改为：SQL 侧 `WHERE type LIKE 'eval.%' AND …` + `LIMIT/OFFSET`（或 keyset 分页）+ `COUNT(*) OVER ()` 单趟取总数，聚合（中位数/分位）改为 SQL 侧或物化到快照表。
**证据**：基线 1 并发 19.8 ms（缓存命中态）→ 12 并发长跑 **195.9 ms / P95 456.9 / P99 858.0**；同族 `evaluations/distribution` 190.4、`evaluations/threshold-suggestion` 180.2、`evaluations/brands` 173.1 —— 说明整族都被同一重载支配。
**量化预期收益**：长跑该族 avg **195.9 → ≤60 ms**（-70%），P99 **858 → ≤250 ms**；由于评估列表是控制台首页的核心调用，混合场景整体 avg 200.5 可下移约 5~8%。
**验证方式**：同一 JMX 复跑 + `sqlite3 .stats` 或 `EXPLAIN QUERY PLAN`；比对事件表行数翻倍后的 P95（抗增长才是真收益）。

**落地状态（2026-11-04）**：✅ 已落地，但落地的杠杆与原文 ① 相反 —— **不是「改成 `LIKE 'eval.%'`」，而是「从前缀改成等值」**；且**压测里那 195.9 ms 根本不包含本条描述的成本**。四份判据脚本都在**生产库的一致性快照副本**上跑（sqlite3 `backup` API 拷到 `D:/tmp/xh-a2-probe/`、`chdir` 过去，配置四份一并复制；生产文件从不被打开，5× 那轮往副本里插数据）：`a2_evals_probe.py`（逐阶段计时 + `EXPLAIN QUERY PLAN`）、`a2_enrich_probe.py`（两种身份 + cProfile）、`a2_ab.py`（前后各两轮、每轮 7 次取中位）、`a2_growth.py`（5× 合成副本、前后腿交替）。

- **「缓存命中态」不成立**：`list_evaluations` / `list_evaluation_brands` / `evaluations_distribution` / `feedback_stats` 四个只读端点**都没有 `@cached_ttl`** —— 没有缓存可命中，基线里的 19.8 ms 只是单发耗时。
- **压测身份一行评估数据都没读**：压测走 WEB_TOKEN 直通，中间件把 `request.state.user_id` 置为 `"default"`（`web/middleware/auth.py:123`），而真实库里 1 867 条 `eval.*` 事件全部归属同一个数据主人账号（`events.user_id`，形如手机号，明文不落本文档）。同一条请求换身份：沙箱态 median **4.8 ms / total=0**，主人态 median **306.1 ms / total=377**。于是 12 并发长跑的 **195.9 ms 里不含「多读 5× 行」**，那段是并发排队（归 **D1**）；本条的收益只落在**账号里有评估数据的真实用户**路径上，压测口径测不到。这是「同一份压测数据、不同身份」这类误判的又一例（同 C2）。
- **等值才走索引**：`type LIKE 'eval.%' ORDER BY created_at DESC LIMIT n` 的 `EXPLAIN QUERY PLAN` 是 `SCAN events USING COVERING INDEX ix_events_created_type`（按 created_at 从新到旧扫全表、逐行判 LIKE），等值 `type = 'eval.scored'` 是 `SEARCH events USING INDEX ix_events_type_created (type=?)`。真实库 18 388 条 events 里 `eval.%` 命中 1 867 条，其中 **1 490 条是 `eval.passed`**（NotifierHub 推钉钉时写入，`payload=None`、`stage=notify`），四个只读端点紧接着就在 Python 端按 `type != "eval.scored"` 丢弃 —— 白读 5.0× 行，且每行都要在 `_row_to_dict` 里做一次 `json.loads`；另附发的 `count(*)` 在这 4 个调用点全部以 `rows, _ =` 丢弃。
- **落地效果（主人态、进程内直调，两轮同向）**：端点中位 **574.7 / 586.5 → 238.1 / 228.6 ms（-59% / -61%）**；取数段 **218.7 / 256.2 ms @1 867 行 → 35.9 / 65.3 ms @377 行**；每请求 SQL **6 → 5 条**；冷启动 740.4 / 449.7 → 311.2 / 311.8 ms。
- **「avg → ≤60 ms」这条目标不成立**：取数收窄后，cProfile 显示剩余大头在 enrich 而不是 SQL —— 3 次主人态调用共 2.356 s 里 `_enrich_eval_record` 累计 **1.267 s（54%）**、原 `list_events_by_type_prefix` 只占 0.521 s（22%），热点是 `normalize_display_brand → _compact_text`（**73 251 次 `re.sub` / 累计 0.704 s**，外加 `re._compile` 73 746 次的缓存查找）。要下探 60 ms 得动品牌归一化（预编译 pattern + 按 title 结果 memo），那是新的一条（记为 **A2′**，见下方残余），不在 A2 范围。
- **抗增长才是本条真正买到的东西**（5× 合成副本：events 25 856 / `eval.%` 9 335 / `eval.scored` 1 885，前后腿交替）：取数段 **699.8 / 820.9 → 143.0 / 175.6 ms（≈4.8×↓）**，端点中位 **1 208.9 / 1 705.4 → 609.1 / 1 027.2 ms**，每请求 SQL **8 → 5 条**。原文「当前数据量下不致命、随采集累积线性恶化」的判断在增长口径下成立，且斜率差由索引形态决定，不是行数比例。

落地的三项：① `infra/repo_events.list_events_by_type()` —— 等值过滤、`user_id is not None` 才加隔离条件（与 `list_events` 的「`None` = 不过滤」口径一致）、**返回 `list` 而不是 `(rows, total)`**、不附发 COUNT；② `evaluations_common.load_eval_scored_events()` 作为评估族只读 GET 的统一取数入口（把「为什么等值」写在一处）；③ 4 个只读端点接线：`list_evaluations`、`list_evaluation_brands`、`evaluations_distribution`、`feedback_stats`（后者原先就把 `_EVAL_SCORED_TYPE` 当**前缀**传进 `list_events_by_type_prefix`，即 `LIKE 'eval.scored%'`，顺带也修掉「`eval.scored_extra` 会被读进来」这个潜在口径漏洞）。

**刻意不落**：① 原文的 SQL 侧 `LIMIT/OFFSET` 分页与 `COUNT(*) OVER ()` —— 列表的过滤条件依赖 join items 之后的价格/标题字段，`total` 也是 enrich + 过滤之后才成立的，把分页下推会改变语义；② 中位数/分位下推 SQL 或物化快照 —— 收窄后取数只剩 377 行的读，聚合是否仍然贵需要复测才定，不为未测的成本引入 SQL 侧口径漂移；③ 两个 POST 路径（`evaluations_batch._collect_evaluated_ids`、`evaluations_recompute`）**保留 `eval.` 宽口径** —— 它们问的是「这个 item 是否被评估流程触碰过」，改成等值会让 `eval.passed` 覆盖的条目从「已评估」范围里消失，属行为变更而非降本；`list_events_by_type_prefix` 的形状（元组 + COUNT）因此一字未动。

回归：`tests/test_eval_events_query_pushdown.py`（14 例）—— 仓储层锁「等值不顺带命中 `eval.scored_extra`（并把前缀语义留作对照组）」「恰 1 条 SQL 且不含 `count(`/前缀方法仍是 2 条」「`user_id` 隔离与 `None=不过滤`」「共享入口返回 list 且入参必须是精确类型」；端点层锁「跨账号：别人的 `eval.scored` 混不进当前账号的列表」「混入 `eval.passed` 时列表结果不变」「把 `list_events_by_type_prefix` 打成炸弹后 `/api/evaluations` 仍 200（丢弃式解包改错了不会报错，只有炸弹能发现）」「brands/distribution/feedback 三端点各取数一次、类型都是 `eval.scored`、user 都是 `default`，且通知事件不被误算成 `insufficient_count`」（这条是历史缺陷回归）；AST 门禁锁「4 个只读函数必须用共享入口、不得出现前缀取数」与「2 个 POST 函数必须继续用前缀」（后者防的是将来「顺手统一」）。判据集 = 这 14 例 + 评估族 5 个既有文件（`test_evaluations_sold_filter` / `_brand_facets` / `_price_filter` / `test_enrich_eval_overwrite` / `test_auto_collect_stats_endpoint`）共 **66 例，基线全绿**；全量后端 **2 781 例通过、0 失败**（16 min 03 s）。

**语义等价性另有一条数据侧判据**（生产库 `mode=ro` 复核）：`eval.passed` 覆盖的 item_id 里，**没有任何一个** item 缺 `eval.scored` 行（`NOT EXISTS` 计数 = 0），且 1 867 条 `eval.*` 全部归属那一个数据主人账号、`user_id='default'` 为 0 条 —— 所以把范围从 `eval.` 收到 `eval.scored` 不会让品牌候选集或列表少任何一个条目，改动是纯降本而不是语义收缩。

**变异验证**（`a2_mutation_run.py`，9 腿串行 + 负对照，基线 66 例全绿前置）：等值退回前缀 `LIKE` → 1 例红；把 COUNT 加回来并改回元组返回 → 33 例红；等值查询丢掉 `user_id` 隔离 → 2 例红（仓储层 + 端点层各一条）；共享入口把精确类型换成前缀字面量 → 30 例红；`/api/evaluations` 回退前缀 → 2 例红；`/evaluations/brands` 回退前缀 → 2 例红；distribution 回退前缀 → 2 例红；feedback 回退前缀 → 2 例红；batch 的宽口径被「顺手统一」成等值 → 1 例红（AST 门禁）。负对照（只把等值方法的 `limit` 默认值 50 000 → 500 000）66 例全绿。6 个源文件按 sha256 逐字节还原。
**这一轮纪律上的收获**（首轮作废重跑的原因）：L5b 的锚点少写一行缩进，拼出孤立的 `)`，pytest 以 `exit=2`（收集期 SyntaxError）「翻红」—— 脚本原先把 `exit != 0` 一律当成「这条腿有判据」，于是假绿了 9/9。现补两道闸：注入前 `ast.parse` 变异文本、判定只认 `exit == 1`（跑到用例且有失败），2/3/4 一律标为无效并打印原始输出尾部。

**A2′ 落地（2026-11-06；原「残余」线索，其中「请求内 memo」被实测推翻）**：热点确认在 `collector_utils.normalize_display_brand` —— 每条 display 都把 title / candidate / **整张别名表（~70 组、200 个别名）** 重跑一遍 `re.sub`，377 条记录的单次请求累计 **73 251 次 / 401.1 ms**，占 `_enrich_eval_record` 的 57%。落地的两处：① `_compact_text` 改为模块级预编译的 `_COMPACT_TEXT_RE`；② `_BRAND_ALIASES` 的压缩结果在导入期预计算成 `_COMPACT_BRAND_ALIASES`（别名表是常量，压缩结果只依赖常量内容）。品牌比对同时改为「按 canonical 组求交集」—— 原实现是逐组判断 `candidate 命中该组某别名 and title 命中该组某别名`，若直接合并成「任意别名 + 任意别名」，「联想 + 尼康 Z9」会被误判为匹配（重构中途真实踩到，已钉成用例）。

**「按 (candidate, title) 做请求内 memo」判定不落**：真实库 377 条 `eval.scored` 只有 **363 个唯一 title**（重复 14 次 / 3.7%），2 000 条 `task_links.display` 里重复更低；同进程 A/B 里 memo 版比无 memo 版慢 **15%**（键构造与查表开销 > 命中收益）。与 B4 的教训一致：不要把「可能的重复」当成本收益前提，先数一遍。

**判据（同一份 `a2_enrich_probe.py`，落地前后各一轮；原探针缺 `DATA_UID` 赋值导致主人态永远读 0 行，已就地补上）**：3 次主人态调用下 `_enrich_eval_record` 累计 **706.9 → 256.3 ms**（占 `list_evaluations` **60.8% → 39.5%**）；`_compact_text` **73 251 次 / 401.1 ms → 2 367 次 / 11.7 ms**（调用 −97%、时间 −97%）；`_brand_candidate_matches_title` **360.2 → 110.3 ms**；端点中位 **148.2 → 121.5 ms（−18%）**、冷启动 **200.1 → 151.6 ms（−24%）**。**A2 原文的「推向 60 ms」仍不成立**：enrich 仍是列表里最大的单项，剩余成本转到 `_row_to_dict` / `json.loads` / 成色标签扫描。

**同进程 A/B（消除机器外部负载对前后两轮探针的干扰）**：2 000 条真实 `task_links.display`、9 轮交错，品牌归一化段旧实现复刻 **423.7 → 175.0 ms（0.41×，−59%）**。逐轮比值中位约 1.96×，单轮异常（1.09×）出现在 new 侧同被外部负载打断的那一轮。

**语义等价**：22 427 组（2 427 组真实数据 + 20 000 组 fuzz）逐条对比旧实现复刻，`_brand_candidate_matches_title` / `infer_brand_from_title` / `normalize_display_brand` 差异全 0。

**回归与变异**：`tests/test_brand_display_normalize.py` 新增 4 例（`"om-d"` 必须靠压缩后才命中无连字符标题、跨组别名不得匹配、两处 `_compact_text` 调用次数上界）；`a2_prime_mutation_run.py` 2 红（别名表退回未压缩 / 组交集退化成 `or`）+ 2 绿（接错对象 / 只改 docstring），`collector_utils.py` 按 sha256 逐字节还原。

### A4. `maintenance/status` 计数降本 — 优先级 **高**
**原建议（①② 后被实测修正，见下方落地状态）**：`web/routes/api_maintenance.py:236-250` 对 `tasks/items/task_links/events/orders/notifications` 逐张 `SELECT COUNT(*)`（`:245`），再 glob 日志目录 + `_cache_scan.snapshot()` 全树遍历。改为：① 用 `PRAGMA`/`sqlite_stat1` 近似行数或维护计数器表；② 全量精确计数移到后台调度任务，接口只读快照；③ 现有 `@cached_ttl(60, …)`（`:235`）把 TTL 提到 300 s 并启用 `stale_while_revalidate` 命中优先。
**证据**：单发探针 1.6 s；**v1 负载轮中该接口 P95 = 60 033 ms（= 客户端 60 s 超时判 000，超时率 37.5%）**，直接把 `baseline_light` 整轮数据打废（该轮已作废重跑）。为控制影响面，最终分层把它判为 `probe_single`（见 `tier-rules.json` 修正规则「单请求耗时 >60 s … 留在负载轮会把整轮 TPS 稀释成假瓶颈」）。
**量化预期收益**：接口自身 avg **1.6 s → ≤200 ms**；更重要的是**消除 60 s 级超时长尾**，使任何并发档位不再出现整轮污染。
**验证方式**：把它重新放回 `reads_light` 负载轮复跑，确认 000 计数为 0。

**落地状态（2026-11-03）**：✅ 已落地，但**原文的成本归因被实测推翻**，落地的杠杆也与 ①②③ 不同。`maintenance_cost_probe.py` 在真实库（`mode=ro`）上逐段计时：

- 6 张表 `SELECT COUNT(*)`（events 18 386 行、items 最大）**每张 ~0.1 ms** —— ①「`sqlite_stat1` 近似行数」与 ②「计数移到后台计数器表」**都不成立**：精确计数本来就不要钱，换成近似只是白丢正确性。
- 增量扫描 `_cache_scan.snapshot(budget=0.5)` 冷 **19~32 ms**、热 **0 ms** —— 预算是上限不是成本。
- 日志目录：每文件 3 次系统调用（`is_file()` + 两次 `stat()`），8 个文件 **5.7 ms → 1 次 stat 后 1.6 ms**（本机单次 `stat` ≈1.6 ms）。
- **真正的 1.6 s+ 是 `__pycache__` 全树遍历**：`_EXCLUDE_SCAN_DIRS` 漏了构建虚拟环境 `.venv-build/`，把第二套 site-packages 的 1 578 个缓存目录算进「应用编译缓存」（1 596 → 排除后 18 个目录、131.7 → 9.4 MB、1 860 → 167 ms）。这同时是个**正确性 bug**：界面上的「Python 编译缓存」原本几乎全是打包 venv 的数字，清理也够不着它。
- 完整函数（`bypass_cache=True`，含冷扫描）**2 235 / 5 205 ms → 188 / 170 ms**；缓存命中 ≈0。`≤200 ms` 在「重算」路径达成，命中路径远低于它。

落地的三项：① `_EXCLUDE_SCAN_DIRS` 补 `.venv-build`；② 日志统计合并为每文件 1 次 `stat`（同时不再因单个文件读不到而整段失败）；③ `@cached_ttl` **60 → 300 + SWR** —— 前提是失效点本来就在：三个清理入口（cache/database/logs）写盘后各调一次 `invalidate_stats_cache()`，增量扫描 `/cache-scan` 到 `done` 时也失效一次；另在 `_warm_stats_cache` 里加第 5 个预热 key，且**走 `asyncio.to_thread`**（它是同步 `def`，直接 await 会在启动期卡住事件循环）。

回归：`tests/test_maintenance_status_cost.py`（TTL/SWR AST 门禁、三入口失效门禁、每文件 1 次 stat、空/缺目录、预热落在线程池且失败隔离）+ `tests/test_maintenance_cleanup.py` 新增 `.venv-build` 不统计/不清理断言。变异验证 5 腿各自翻红（去掉 `.venv-build` 排除 → 4 例；日志退回 3 次 stat → 1 例；TTL 写回 60 → 1 例；去掉预热 → 2 例；预热不走 `to_thread` → 1 例），负对照（改同一集合里另一条排除项 `dist`）保持全绿，文件按 sha256 还原。**残余边界**：`_iter_pycache_dirs` 仍是全树 `os.walk`，在「缓存过期 + 恰好有并发」时每个 TTL 窗口仍会付 ~170 ms；若后续再把其它构建产物目录引入仓库，需要同步补进 `_EXCLUDE_SCAN_DIRS`，否则成本会重演。

### A5. 日志/错误检索避免前后通配 — 优先级 **中**
**具体优化内容**：`infra/repo_error_logs.py:63-65` 对 `error_type` / `request_path` 使用 `%keyword%` 通配（索引不可用）；`web/routes/api_logs.py:127-162` 的 `logs/search` 先取 1 000 条事件再在 Python 侧做子串过滤。改为：前缀匹配 `keyword%` + 索引，或引入 SQLite FTS5 虚拟表；过滤条件下推到 SQL。
**证据**：`logs/search` 基线 12.4 ms → 长跑 132.9 ms（P95 308.4）；其 4xx 夹具率虽高但不影响延迟口径。
**量化预期收益**：长跑 avg **132.9 → ≤40 ms**，且检索结果不再受「1 000 条截断」影响正确性。
**验证方式**：构造 10 万级 events 副本，对比 P95 是否随数据量线性增长。

**A5 判定不落（2026-11-06，`a5_like_probe.py` 实测；原文两半都无落点）**：原文把两个不同接口混在一条里，实测后各自都不成立：

1. **`repo_error_logs` 的 `%LIKE%`**（`/api/error-logs` 列表）：真实库 `error_logs` 只有 **2 112 行**，`error_type LIKE '%kw%'` 直接 SQL **7.78 ms**、`request_path LIKE '%api%'` **4.50 ms**；走真实仓储 `list_error_logs` 分别 **1.8 ms / 10.0 ms**。前缀匹配的对照**没有更快**（`error_type LIKE 'network%'` 7.05 ms；`request_path LIKE 'api%'` **29.74 ms**，因为 `ix_error_logs_request_path` 只能做前缀过滤，`ORDER BY timestamp` 还要回表排序）。而且前缀匹配会**改语义** —— 前端搜索框的提示就是「按状态与错误类型搜索」（`ErrorLogs.tsx:423`），用户输入的是部分类型名/路径片段。FTS5 要建虚表 + 触发器同步，换来 <10 ms 的收益，不划算。
2. **`logs/search` 的「先取 1 000 条再 Python 过滤」**：这个接口的 `q` 是 **Python 端子串匹配**（`api_logs.py` 的 `_filter_log_candidates`），SQL 侧本来就没有 `%LIKE%`，所以「前缀匹配 / FTS5」对它无落点。进程内实测：取 1 000 行候选 **31.9 ms**（含每行 `json.loads`）+ Python 过滤 **6.2 ms** ≈ 38 ms；把 `q` 下推到 SQL（`message LIKE ? OR payload LIKE ?`）要 **21.8 ms** 再 + 准确 `COUNT(*)` **14.0 ms** = **37.7 ms** —— **延迟没降**，换来的只是 `total_estimate` 口径从「最近 1 000 条里的命中」变成「全部历史命中」（同一关键词 14 → 52 条）。这是**产品口径改进**，不是性能收益；前端确实把 `total_estimate` 当命中总数展示（`Logs.tsx:53` 的 T02678 注释），要不要改语义需要产品确认，不在本性能条目内落。
3. **本条「132.9 → ≤40 ms」不成立**：`logs/search` 的接口自身只有 ~38 ms，HTTP 长跑 132.9 ms 里其余近 100 ms 是中间件/线程池排队（D1 的账），改 LIKE 碰不到它。

---

## B. 缓存策略调整

### B1. 给进程内 TTL 缓存加单飞（single-flight） — 优先级 **高**（本条为全场最大杠杆）
**具体优化内容**：测时版本的 `backend/xianyu_hunter/web/cache.py` 中 `_make_sync_wrapper` 在冷/过期 miss 时直接 `result = func(...)`，**没有 per-key 锁与双重检查**；只有 `stale_while_revalidate` 分支有后台刷新标志。补：per-key 在途表 + 取锁后二次查缓存，未抢到 lead 的请求等待而非重算；并为 `invalidate_stats_cache()` 增加按维度（user/task）失效而非整表清空。

**证据**：① `reads_heavy` 基线 avg **22.7 ms**（单用户，缓存基本命中）→ 同一 135 接口混合场景在 12 并发长跑 avg **200.5 ms**，放大 8.8×；② 同族评估/统计接口长跑均值集中在 165~200 ms 一个窄带（`evaluations/*` 167~196、`notifications*` 170~218、`export*` 134~288），**这是「大家都在等同一份重算」的特征，而不是各自计算不同东西**；③ `startup.py:799-828` 只预热 4 个 key，300 s TTL 到期后首波并发必然击穿。
**量化预期收益**：混合场景 avg **200.5 → ≤80 ms**（-60%），P95 **544.5 → ≤250 ms**，P99 **1 164.5 → ≤450 ms**；`reads_light` 96 并发 P95 3 893.2 预计降至 **≤1 200 ms**（与 D1 叠加后更低）。
**验证方式**：长跑复跑 + 在服务端计数 `func` 实际执行次数（单飞生效应看到重算次数 ≈ 接口数 × 时长/TTL，而非 ≈ 请求数）。

**落地状态（2026-10-31）**：✅ 单飞部分已实现 —— `web/cache.py` 新增 `_single_flight` / `_single_flight_async`（per-key 在途表 + 锁内双重检查；同步用 `threading.Event`、异步用同循环内的 `Future`，跨事件循环不合并），同步/异步两个包装器的 miss 路径都改为经单飞，`bypass_cache=True` 保持「不读缓存、不并入他人在途重算」的原语义。回归测试 `tests/test_cache_single_flight.py`（9 例）锁定「同 key 并发只重算一次」「不同 key 不互相合并」「异常不入缓存且传给跟随者」「TTL 到期后重算」「bypass 仍强制刷新」；变异验证（把两个入口退化成各自重算）后其中 2 例如期失败。**未落**：`invalidate_stats_cache()` 的按维度失效（仍整表清空）留待与 A2/B4 一并处理。

### B2. 把「零并发就慢」的接口纳入 TTL 缓存 — ~~优先级 高~~ → **本条 5 项里 4 项撤回，只做安全残留** ⚠️ **已复核（2026-10-31）**
**原建议**：对 `GET /api/anticrawl/strategy`、`GET /api/vector-admin/snapshots`、`GET /api/config/raw`、`GET /api/config/share`、`GET /api/maintenance/cache-scan` 加 `@cached_ttl`（TTL 30~120 s）。
**复核结论（逐条反证，全部来自现读源码，不是推测）**：
1. **`maintenance/cache-scan` —— 缓存会直接做坏功能。** 它不是「查询」，而是**推进状态机**：`api_maintenance.cache_scan_progress` 每次请求扫预算片并把进度写回进程内快照，`state` 从 `scanning` 走到 `done`；函数 docstring 自己就写着「与 /status 不同，这里不带响应缓存：每次请求都真实推进一小片」。缓存后进度永远停在第一个快照，前端 `Cleanup.tsx` 的 1 200 ms 轮询等不到 `done`，而它又按「统计中禁止清理」禁用按钮 —— 结果是**清理功能永久不可用**。原建议把「这个接口慢」误读成「这个接口可缓存」。
2. **`vector-admin/snapshots` —— 缓存会在用户自己的操作后给出旧列表。** 该页 `VectorAdminPanel.loadAll()` 在创建/删除/恢复之后立刻重拉列表（快照面板的 `items` 直接来自这里，`_list_snapshots` 里每个目录还各调两次 `_dir_size`，见 C1）。TTL 30~120 s 意味着「我删了快照，列表里还在」「刚恢复完，体积还是恢复前的」——这是用户可感知的错数据，不是可接受的近似。降本应走 C1（合并重复遍历），而不是缓存。
3. **`anticrawl/strategy` —— 结果的定义就是「当前环境」。** `login_strategy.LoginStrategySelector.evaluate` 每跳都要现场做 CDP 端口 socket 探测 + 读 Chromium `Network/Cookies` 判断 Cookie 是否仍有效；`api_anticrawl.get_strategy` 把它当「推荐登录策略」直接展示，而 `AntiCrawl/index.tsx` 是 `runInitialize()` → `fetchAll()` 的顺序 —— 用户刚点完「初始化」（或刚把带 CDP 的浏览器打开），页面却仍在按 30~120 s 前的探测结果推荐策略。降本的正确位置是 C4（探测 timeout 与索引），C4 里「② 探测结果整体短 TTL 缓存 10~30 s」同样要加「`/initialize` 之后强制失效」这条护栏才成立。
4. **`config/raw` + `config/share` —— ① 前端根本不消费；② 缓存等于把明文凭据放进进程内缓存。** 全仓 `frontend/**`（ts/tsx/js/vue/html）grep `config/raw|config/share` 零命中，它们之所以有数字，是因为本次目标要求「覆盖所有端点」而进了压测集，不是真实流量。更要紧的是这两个接口返回**整份配置的明文 YAML**（`/raw` 只做了末 4 位脱敏的 `raw` 字段、`/share` 是分享用的渲染文本），把它们放进 `_cache_store` 就是在进程里驻留一份凭据 30~120 s —— 与 B4「大 payload / 导出类显式不进缓存」的方向正好相反。
5. **量化预期「轻组 avg 50.9 → ≤25 ms」的口径本身也错了。** 专项探针 `config_memo_probe.py` 拆出成本：一次 `config.yaml`（7.2 KB）读+解析 **52.5 ms**、`_dump_yaml` 序列化 **32.9 ms**、`_sanitize_dict` 0.1 ms。`/raw` 基线 132.4 ms ≈ 这三段（86 ms）+ 框架与 JSON 序列化余量。TTL 缓存能全砍，memo 只砍解析那段，C6 砍的也是解析那段（换 C loader，约 90%）—— 无论哪种都不到「≤25 ms」；**而 dump 那 33 ms 连 C6 也砍不了**（C dumper 的 emoji 写法不一致，见 §C6），所以 `/raw` 的稳态就在 80~90 ms 附近。
**顺带把 B5 那条建议也收口**：B5 落地后 `stale_while_revalidate` 确实可用了，但**这四条换成 SWR 同样不成立** —— SWR 只是把重算挪出请求路径，旧值照样会被用户看见；`/cache-scan` 更连「同一份结果」都不存在（每次请求的结果就是进度本身）。SWR 的适用面仍然是「重算很贵、旧值可接受一小段时间、且没有紧随其后的用户动作」，本次只有 `maintenance/status` 符合。

**实际落地的残留（只做不会引入失败的那一条）**：给 `api_config._load_yaml()` 加**按文件指纹 memo**（键 = 路径 + `st_mtime_ns` + `st_size`，命中返回 `copy.deepcopy`），并在 6 个 `CONFIG_FILE.write_text` 站点后配对 `_invalidate_yaml_memo()`。
- 它不改语义：**经本模块写盘的路径**上，`_load_yaml` 的返回与磁盘内容严格一致（判据见 `tests/test_config_yaml_memo.py`：外部编辑立刻跟上、同尺寸+同一 mtime tick 的写盘靠显式作废兜住、切换文件不串味、返回值不被调用方写脏）；跨模块写盘的边界另见下面「残余边界」，不要把它读成「全面无陈旧」。
- 它惠及的接口：`/preview`、`/save`、`/import`、`/export`、`/raw`、`/share` 全部走 `_load_yaml`（每次请求各省一次 52 ms 解析）；`GET /api/config` 本身走 `get_config()` 单例，与此无关。
- 为什么「同尺寸写盘」必须显式作废：Windows 文件系统时间粒度约 15.6 ms，「POST /save 后前端立刻 GET」在本地回环上可能落在同一个 tick；`_save` 类改动还常常同尺寸（改一个等长字符串/数字）。这条闭环是产品主循环，不能靠指纹碰运气。
- 残余边界（已钉成用例，防止后来人误判）：**绕过 `api_config` 的写盘**（`api_tunnel`、`api_notifier`、`api_batch_refresh`、`infra/ai_usage` 各自写 `config/config.yaml`）只由指纹兜住；若某次跨模块写盘恰好同尺寸且同一 tick，memo 会给出一个 tick 的旧值，下一次读即自愈。`test_out_of_band_same_fingerprint_write_is_the_residual_gap` 断言的就是这个已知缺口，别把它读成「已全面防护」。
- 并发读写的代次护栏：请求可能在「`stat` → 解析」中间被另一个请求的写盘插队，那份解析结果就是写盘前的旧值，而同尺寸 + 同一 tick 时指纹抓不到它 —— 所以只有 `_yaml_epoch` 在读取期间没动过，才允许把结果存回 memo。**这条是被变异验证逼出来的**：初版我同时写了「存前比对」和「读时比对」两道，去掉任一道用例都照常绿（读侧比对挡不住已经进 memo 的坏值），删成单道之后才有判据。
- 不做的事：不引入 TTL、不进 `_cache_store`（明文凭据不进响应缓存，见第 4 条）、不改 `GET /api/config` 的脱敏路径。
**验证方式**：`tests/test_config_yaml_memo.py`（13 例）+ `config_memo_probe.py` 作为成本判据；变异验证五条退化各自翻红（冷/命中两条 return 都去掉深拷贝→1 例；指纹不再参与判定→2 例；去掉 6 处作废→3 例含源码门禁；指纹去掉路径→1 例；去掉代次护栏→1 例）。真实数字待同一 JMX 复跑替换，本文所有基线仍是修复前口径。

### B3. 预热清单扩展 — 优先级 **中**
**具体优化内容**：`web/startup.py:799-828` 的 `_warm_stats_cache` 目前只暖 `business_kpi:30 / stats_today / stats_overview / stats_trend:events:24` 四个 key。把首页聚合（评估分布、通知未读数）纳入预热，并在每次调度周期后重暖。**原建议里「把 B2 的接口纳入预热」随 B2 一并撤回** —— 预热只是把同样的旧值提前算好，`cache-scan`/`snapshots`/`strategy` 的陈旧问题一条都不因预热而消失；`config/*` 那族现在由 B2 残留的指纹 memo 覆盖（冷的那一次仍要付解析，但只有第一次）。
**证据**：基线首样本级长尾（`reads_light` P99 1 065.4 ms 而 P50 仅 11.1 ms）—— 99% 分位完全由冷算/击穿贡献。
**量化预期收益**：冷启动/首个请求 **1.1 s → ≤100 ms**；P99 与 P50 的比值从 96× 降至 **≤10×**。
**验证方式**：重启实例后立刻打 `reads_light` 基线，比较 P99。

**B3 判定不落（2026-11-06；原文的「首页聚合纳入预热」无落点，量化预期已被 C4 部分兑现）**：
1. **现有预热本身是有效的**：静态核对了 5 个 key 与请求侧 `key_fn` 一一对应 —— `kpi:30`（`business_kpi.py:224` 的 `f"kpi:{range_days}"`）、`stats_overview`、`stats_today`、`trend:events:24:all`（`trend.py:229` 的 `f"trend:{metric}:{range_hours}:{task_id or 'all'}"`）、`maintenance_status`。启动日志里的「business_kpi:30」只是显示名，不是缓存键；`stats_trend:events:24` 同理。
2. **「把首页聚合（评估分布、通知未读数）纳入预热」无落点**：`/api/evaluations/distribution`（`evaluations_distribution.py:468`）与 `/api/notifications/unread_count`（`api_notifications.py:83`）**都没有 `@cached_ttl`**，而预热只能「提前调用被缓存装饰的函数」。要给它们加缓存则是新条目且带真实风险 —— 评估分布随每次新评估变化、未读数是用户点一下就必须变。
3. **「1.1 s → ≤100 ms」已被 C4 部分兑现，但不能 ≤100 ms**：`reads_light` 首样本 1 017.5 ms 的接口是 `GET /api/anticrawl/strategy`，它本身 P50 就有 1 072.8 ms（P99/P50 = 1.1×，整组聚合的「96×」不是它自己的比值）。C4 把 CDP 探测 timeout 1.0 → 0.1 s 后实测约 190~250 ms；残余的 ~109 ms **就是 timeout 预算本身**（本机对未监听端口静默丢包），再往下压只能牺牲慢启动余量，短 TTL 也已判定不落。
4. **「每次调度周期后重暖」同样不落**：TTL 300 s 会自然过期重算，按调度周期强行重暖等于让聚合缓存永不过期，把陈旧风险放大；且调度器与 Web 侧生命周期不同（`--no-with-scheduler` 部署下没有「调度周期」）。
5. **「P99/P50 比值 96× → ≤10×」待同一 JMX 复跑**：比值下降来自 C4 而不是本条；本机复跑前不能宣布。

### B4. 缓存容量上限与淘汰 — 优先级 **中**（原列「低」，因资源口径修正后证据变强而上调，见 REPORT §6.8）
**具体优化内容**：`web/cache.py` 的 `_cache_store` 无容量上限/淘汰策略；加 `max_entries` + LRU，并对大 payload（导出类、`snapshots`、`logs/search` 响应）显式不进缓存；同时给缓存加「条目数 + 字节数」可观测指标。
**证据**：只统计 SUT 存活样本后，长跑 30 min 窗口内 RSS 六段为 `346.8 → 354.7 → 368.5 → 374.5 → 373.4 → 378.4 MB`（拟合 **+75.9 MB/h**），全程 8 段 `203 → 217 → 311 → 322 → 332 → 357 → 372 → 377 MB`，峰值 **414.5 MB**、私有内存峰值 **617.7 MB**，且**负载停止后不回落**（终止时仍 375.6 MB）。同期句柄恒定（`627~631`），因此不像连接/FD 泄漏；最可解释的来源就是这份无上限的进程内缓存（叠加 SQLite 页缓存与分配器 arena）。**在 30 min 尺度上仍无法与慢泄漏区分** —— 本条既是优化，也是将问题变为可判定的手段。
**量化预期收益**：RSS 峰值 **414.5 → ≤350 MB**，长跑窗口斜率 **+75.9 MB/h → ≈0**（有上限后应收敛为平台）；把「内存增长是缓存还是泄漏」从不可归因变成可判定。
**验证方式**：4 h 长跑（见 §E4 与 CONCLUSION 的未达成项）分段对比 RSS 斜率 —— 若加限制后斜率归零即确认原因为缓存，若仍爬升则为泄漏，需转 `tracemalloc` 定位。

**落地记录（2026-11-05，`web/cache.py` + `config.py` + `web/app.py`）**

- **先证前提，两条探针**（`b4_cache_census.py` / `b4_key_ceiling_probe.py`，产品在 `results/b4_cache_census.json`、`results/b4_key_ceiling.json`）：
  - 形状清点：全仓只有 **8** 个 `@cached_ttl` 站点，其中 4 个的 key 直接拼查询参数（`kpi:%s`、`hist:%s:%s`、`eval_funnel:%s`、`trend:%s:%s:%s`），而路由声明不约束这些参数（`metric: str = "events"`、`range_hours: int = 24`、`bins: int = 0`、`task_id: str | None`）。
  - 值体积：用 ASGI transport 把 `results/per_endpoint.json` 里 **106** 个 GET label 全扫一遍（87×200 / 11×404 / 7×422 / 1×400），缓存里只落下 **7 条 / 23 620 B（0.02 MB）**，单条最深 **8 139 B**（`trend:events:24:all`）。**原文「无上限缓存 ⇒ RSS 爬升」在值体积维度不成立** —— 差 3~4 个数量级。
  - 键基数：60 个真实 task 各打一次 `trend` + `histogram` → **+120 条 / 539 865 B**（每 task 2.0 条、8 997.75 B，1.4 s）；再伪 200 个前端从不发送的 `metric` → **+200 条 / 128 090 B**（单条 640.45 B，1.2 s）。终点 327 条、深量 **0.66 MB**，而同场 RSS 从 144.2 → 170.6 MB（**Δ 26.4 MB ≈ 深量的 40 倍**）—— 分配器/页缓存/别处的对象，不是这份缓存。
  - 过期不回收（代码层已读出的结论用探针再钉一次）：把一条缓存的时间戳拨到 TTL 之外，读侧 `hit_after_expiry=False`，但 `entry_still_present=True`（保活 44 474 B）——过期条目只在被同 key 覆盖时才消失。
  - 外推：沙箱库 248 个 task 全被打过一遍也才 **2.1 MB**。所以「容量上限」不是省内存的手段，**它兜的是键空间**：`metric`/`range_hours`/`task_id` 不受约束，任何调用方（脚本、旧前端、恶意请求）都能线性加条目且永不自清。
- **落地形态（最小改动）**：`_cache_store` 由普通 dict 换成 `infra/lru.py::LRUDict`（该原语本就是为「长跑进程内存无限增长」造的，dict 子类、`.get()` 与 `__setitem__` 都维护 LRU 顺序，调用方零改动）；新增 `Settings.cache_max_entries: int = 2000`；`web/cache.py` 暴露 `configure_cache_max_entries()`，由 `create_app()` 在装配阶段（紧随 `apply_slow_lanes`）套用一次。
  - 为什么不在导入期或写入路径读配置：`get_settings()` 会读 `.env`、必要时取 keyring 并落 token；模块级调用等于给 `import xianyu_hunter.web.cache` 加磁盘与凭据副作用（单测直接 import 本模块）。写入路径只在首次调用才解析，代价虽小，但同样把「缓存能不能写」与配置读绑在一起，不划算。
  - 2000 的取法：正常流量只留个位数条目，上限永不触发；按每 task 2.0 条 / 8 997.75 B 外推，打满 2 000 条约 **8.8 MB**。配 <=0 或读配置失败时保持代码内兜底值（同 2000），不会把上限变成 0 而把缓存整体关掉。
  - **`stale_while_revalidate` 的旧值不会被正常流量挤掉**：`_try_get_cache` / `_try_get_stale` 都走 `LRUDict.get()`，命中即续命，所以 `maintenance_status` 这种每 300 s 必被读一次的 key 恒在队尾；否则 A4 的 188/170 ms 重算会重新落回到过期请求头上。这一点由回归用例单独守住，而不是靠「上限很大碰不到」。
- **明确不做的两件**：① **字节计量**——实测驻留 0.02 MB，为它加一次深遍历（还得分摊 `id()` 去重）成本大于收益，条目数已足够定位键空间问题；② **「大 payload 显式不进缓存」**——复核后 8 个站点里没有导出/快照/日志检索这类大响应，`config/raw` / `config/share` 本来就没挂缓存（§C3 已判定永远不挂），所以没有可加的名单，凭空加一条黑名单只是把未来的误用变成猜测。
- **回归与变异**：新增 `tests/test_cache_capacity.py`（8 例：LRU 驱逐、装饰器下条目数恒定、被淘汰后干净重算、stale 值被读续命、全清/前缀失效不变、非正上限被拒、默认值一致、`create_app` 真的接到配置上）；缓存族 5 个文件 **55 例全绿**；后端全量 **2 818 passed / 0 failed**（17:28）。
  - 变异验证（`b4_mutation_run.py`，7 腿串行、判定只认 `exit == 1`）**5 红 2 绿且全部符合预期**：写入绕过 LRU（只写不驱逐）→ 14 例红；上限套用变空操作 → 5 例红；读命中不续命（LRU 退化为 FIFO）→ 1 例红；非正上限放行 → 1 例红；装配期漏接 `create_app` → 1 例红；「接错对象」把前缀匹配改成等价 `any()` → 28 例绿；只改 docstring 措辞 → 28 例绿。`web/cache.py` + `web/app.py` + `infra/lru.py` 按 sha256 逐字节还原。
  - **首轮 M01 的教训**：最初把「退回普通 dict」作为变异，结果 pytest `exit=2`（4 个用例文件在收集期就撞 `'dict' has no attribute 'maxsize'`）—— 按本条自己的纪律，`exit != 1` 不算红，判 INVALID 并重做；改成「保留 LRUDict 类型、只绕过 `__setitem__`」后才得到有效红腿。**判据要能失败，也要失败得「合法」**。
  - 环境项：全量前一轮（同一套代码）出现 7 条红 —— `test_health_unified.py` 5 条、`test_anticrawl_integration.py` 的噪声比例用例（100 次 p=0.3 落在 19/100）、`test_cache_swr_refresh.py` 的计时用例（0.408 s > 0.083 s 门限），整轮墙钟 **46 min**；随后一轮 **2 818 passed / 0 failed / 17 min 28 s**。三组失败单独复跑全部转绿，与 D2 记录的外部负载抖动一致，不作为本条回归判据。
- **仍未定论**：RSS 斜率（`sut_resource.csv` 1.18 h 内 222 → 374 MB）**不能归给这份缓存**，本轮只把它从嫌疑名单里划掉；定论仍需 §E4 的 ≥4 h 长跑 + `tracemalloc`，不由本条宣布「已解决」。

### B5. `stale_while_revalidate` 的「后台刷新」其实是同步的，异步分支还会静默失效 — 优先级 **高**（B1 落地后新暴露，非负载测量得出）✅ **已于 2026-10-31 落地**
**具体优化内容**：`web/cache.py` 的 `_maybe_refresh_stale` docstring 写「启动守护线程异步刷新」，函数体却直接在请求线程里 `func(*args, **kwargs)` —— 快路径因此变成「先跑完整重算，再把**旧值**返回给这个请求」。`_maybe_refresh_stale_async` 更糟：它在已运行的事件循环里调 `asyncio.run(...)`，必然抛 `RuntimeError` 并被 `except Exception: pass` 吞掉（还留下一条 `coroutine was never awaited` 警告），于是走 SWR 的协程接口**过期后再也不会被刷新**。修法：同步分支改成真正起守护线程（或复用 B1 的单飞 + 一个「仅一个线程去重算」的分支），异步分支改成 `loop.create_task(...)` 而非 `asyncio.run`。
**证据**（专项探针 `swr_probe.py`，非负载观测；被测函数人为设为重算耗时 0.6 s、TTL 1 s）：
- 同步 + SWR：冷路径 `0.600 s` → 过期后首次调用 `0.602 s` 且返回**旧值 1**（承诺的 ≈0 未兑现）；再调用一次才 `0.000 s / 2`。即 `/api/maintenance/status` 每 60 s 窗口后的那一个请求付全款、还给客户端过期数据。
- 异步 + SWR：过期后连续 6 次调用全部返回 `1`，底层重算次数恒为 `1` —— 值被冻结。
- 现网影响面：全仓只有 `api_maintenance.py` 的 `maintenance_status`（同步 `def`；A4 后 TTL 300 s）在用 SWR，所以修复前**只表现为「过期请求被阻塞」，冻结那条尚未被触发**；但它是给下一个吃 `stale_while_revalidate` 的 `async def` 端点挖好的坑，也正是 B2 原建议里要鼓励的用法。
**量化预期收益**：`/api/maintenance/status` 过期后首次调用 **1 600 ms（表 A 实测冷路径）→ ≈0**（返回旧值、刷新在后台），并把「SWR 用一次就静默失效」这一类潜在事故清零。
**落地状态**：已按上述修法改动 `web/cache.py`（同步分支起 `cache-swr-refresh` 守护线程、异步分支 `create_task` 并把任务强引用挂在 `_bg_tasks` 上，asyncio 对任务只持弱引用）。两条分支的重算都改走 B1 的 `_single_flight` / `_single_flight_async`，因此后台刷新与「同时到达的冷请求」互并而非各算一次；刷新失败只记 WARNING、保留旧值并释放名额（下次 TTL 窗口可重试）。回归为 `tests/test_cache_swr_refresh.py`（6 例：过期调用不被阻塞、后台写回、同 key 只刷一次、失败保留旧值且可重试、SWR 下 `bypass_cache` 仍强制重算、异步不再冻结）；变异验证把两条分支退回修复前实现后，6 例中 4 例失败（阻塞判据、惊群判据、异步冻结、异步失败重试），恢复后 49 例缓存用例全绿。`swr_probe.py` 保留为可读判据，其打印已变为「过期请求被重算阻塞 = False / 值被冻结 = False」；表 A/A4 的 1.6 s 数字仍是修复前基线，待复跑。
**落地时确立的两条约束**：① 后台刷新线程可能在响应早已返回之后才跑完，因此被 `cached_ttl` 装饰的函数只能依赖进程级资源（`/api/maintenance/status` 用的是 `container.repo.engine.connect()`，连接池线程安全，合规），不能依赖随请求结束而关闭的会话；② 现有端点级测试（`test_maintenance_cleanup.py`）一律走 `maintenance_status.__wrapped__(...)` 绕开装饰器，所以 SWR 快路径从来只由装饰器级用例覆盖——B5 的两条判据补在 `tests/test_cache_swr_refresh.py`，不在路由测试里。

---

## C. 代码逻辑精简

### C2. `/api/auth/me` 去除重复文件解析与多余 SQL — 优先级 **高** ✅ **已于 2026-11-03 落地（只落 ① 与 ⑤ 的节流形态；②③④ 判定不落；本条的根因归属与「≤50 ms」目标都被实测推翻）**
**具体优化内容**：`web/routes/auth_query.py` 中：① `_resolve_current_user_id(request)` 在一次请求里被调用两次（`:336` 与 `:406`），合并为一次并透传；② `_check_cookies`（`:341` → 其内部 `:83`）每次都 `invalidate_cache` 后重读 cookies JSON 与 `last_login_cookies.json`，改为带 TTL 的进程内 memo；③ `get_userinfo()`（`:344`）再次读文件，复用 ② 的结果；④ `_sync_nick_to_users_table` 的写库（`:369`）仅在昵称变化时才执行（先比对）；⑤ `scan_and_notify`（`:403`）移出 `/me` 请求路径（改调度触发）。
**证据**：基线 avg **152.6 ms**（P95 394.7 / P99 492.7），长跑 **414.3 ms**（P95 1 049.5），96 并发 2 365.2 ms。同时须澄清：**鉴权本身不是瓶颈** —— `web/middleware/auth.py` 的 `verify_session` 走 sha256 + `_verify_cache`（TTL 300 s），非 bcrypt；同族 `POST /api/auth/me/refresh` 仅 17.9 ms，可作「去掉文件重读后的落点」参照。
**量化预期收益**：avg **152.6 → ≤50 ms**（对标 `me/refresh` 17.9 ms + 用户信息查询）；长跑 **414.3 → ≤120 ms**。该接口是控制台每个页面加载的必经调用，收益覆盖全站请求，故列为高优先级。 **← 根因归属与这条目标数字已由实测作废：`/me` 处理器本身只有 ~4~7 ms（进程内）/ ~10 ms p50（安静服务端），152.6 ms 不是「重复文件解析 + 多余 SQL」；见下方落地记录。**
**验证方式**：`/me` 单接口阶梯（1/16/48 并发）复跑；统计每请求的文件 open 次数。

**落地记录（2026-11-03，`web/routes/auth_query.py` + `web/services/notification_engine.py`）**

- **先证前提，四条探针**：`c2_me_probe.py`（进程内直调 + 文件 open / SQL 计数）→ `/me` 稳态 **3.6~7.2 ms**，其中 `_check_cookies` 1.7 ms、`scan_and_notify` 0.5 ms、cookie 读+解析 0.7 ms；`c2_server_probe.py`（真 uvicorn、独立沙箱、合成登录态）→ 安静服务端 **p50 ≈ 10 ms**，同场 `/healthz` 21.7 ms、`/cookie/health` 6.6 ms；`c2_ladder.py`（阶梯 + 两条对照）→ 稳态每请求 **2.0 条 SQL**：`SELECT * FROM users WHERE user_id=?`（取展示名）+ `SELECT orders.… LIMIT 200`（订单超时扫描）。
- **回头看原始样本：152.6 ms 根本不是「一次调用的成本」**：`results/baseline_light.jsonl.gz` 里 `/me` 的 56 个样本实测分布是 **p50 85.6 / p90 370.3 / p95 490.1 / max 548.6 ms** —— 平均值是被长尾抬上去的；`soak_mixed.jsonl.gz` 的 794 个样本是 p50 316.2 / p95 1 058.7 / max 2 620.1。同一份 `setup_sandbox.py` 核对过：它**不生成任何 cookie / userinfo / xh_token 工件**，所以那批样本走的是未登录分支、且没有会话身份 —— 但我们的隔离沙箱测的是**更重的已登录分支**（多一次 users 查询 + 昵称同步 + 会话解析），c=1 仍然只有 8~10 ms。**所以「分支不同」解释不了 8.5× 的差距**，剩下的只能是环境项：同场景里排在 `/me` 前面的慢接口（`anticrawl/strategy` ~1 s、`maintenance/status` ~1.6 s）占着 anyio 40 线程与 StaticPool 单连接，`/me` 在它们后面排队 —— 同一阶梯里「处理器近乎为空」的 `POST /me/refresh` 地板就实测 **57.9~178.4 ms**（c=48），「无处理器的 401」才 ~30 ms。**结论：本条把 avg 归给「路由体重复文件解析」是错的**；可归给路由体自身的边际成本在 c=48 只有 ~55 ms，且其中一半是那 2 条 SQL 抢单连接（`infra/db_models.py:872`）。**未定论的部分**：85.6 ms 的中位数在我们环境复现不出来，定论要等同一 JMX 复跑 + 关掉后台线程的对照组，本轮不下「已解释」的结论。
- **① 已落**：`/me` 里 `_resolve_current_user_id` 从两次并成一次（`auth_query.py:339` 解析，`:414` 复用 `current_uid`）。理由不依赖 `/me` 快不快，而依赖 `UserManager.verify_session` 的锁形状：**整个方法在进程级 `self._lock` 下执行，锁范围盖住查库**，同请求两次 = 两次全局串行化点，纯白付。
- **⑤ 的原方案不可行，改成按用户节流**：原文要「移出 `/me` 请求路径、改调度触发」——但 `H-05` 注释里给出的替代触发点 `/api/notifications/scan` **在代码里不存在**（全量路由核对），而本模块的设计原则是「零侵入：不引入新 cron / 定时器」。移出等于砍掉订单超时提醒的唯一生产者。落地形态改为 `/me` 显式带窗口：`ORDER_SCAN_MIN_INTERVAL_SEC = 30.0` → `scan_and_notify(min_scan_interval=…)` → `scan_order_timeouts(min_interval=…)`，**同一用户在 30 s 窗口内只扫一次**，窗口内成本 **0.002 ms**（闸门判定）。取 30 s 的判据：最松一档阈值是 5 min，30 s 最多让一条提醒晚 30 s 出现（阈值跨度的 1%），却把「每请求一条 `list_orders(limit=200)`」压成「每用户每 30 s 一条」。默认值仍为 `0.0` = 不节流，显式调用方（测试、按需扫描）语义不变；分桶键 `user_id or "*"` 保住 T02618 的「范围 = 归属」口径，A 的流量不吃掉 B 的提醒。
- **节流省掉的到底是什么（确定量）**：`c2_scan_cost.py` 在临时库按行数阶梯测（只测扫描侧，通知写入走替身仓储）：

  | 该用户订单行数 | `list_orders(200)` | 放行的一轮扫描 | 窗口内的 `/me` |
  |---|---|---|---|
  | 0 | 0.52 ms | 0.48 ms | 0.002 ms |
  | 50 | 2.08 ms | 2.77 ms | 0.002 ms |
  | 200 | 7.96 ms | 8.85 ms | 0.002 ms |
  | 2 000 | 8.94 ms | 10.34 ms | 0.002 ms |

  每请求少 1 条 SQL 往返 = **少占 0.5~9 ms 的全进程那一条连接**（随订单规模增长； shipped 库里该用户几乎无订单，故今天取低端）。
- **阶梯为什么不能作为「降到 ≤50 ms」的证据**：同一份未节流代码在 c=48 三轮实测 **256.2 / 117.7 / 233.5 ms**，空处理器地板自己在同一轮次间摆 **57.9~178.4 ms**——摆幅大于本改动的期望收益。节流后两轮 c=48 为 **158.8 / 219.6 ms**，与基线区间重叠，**方向不可判定**。所以本条**不发布 avg 数字**，只发布上面那张确定量表 + 每请求 SQL 2.0 → 1.0（`c2_ladder.py` 的 `[A]` 段逐条列出）。**c=1 也测不出差异**（8.0/9.0 → 9.3/10.6 ms），因为空表扫描本来就只值 0.5 ms。并发墙归 **D1**（线程池隔离），不归本条。
- **②不落**（与 C4 ② 同源判断）：`_check_cookies` 每请求 `invalidate_cache` 不是疏漏，是**跨进程新鲜度契约**——浏览器登录子进程是独立 Python 进程，写完 `cookies_{uid}.json` 只更新它自己的缓存；主进程若吃 30 s TTL，最长 30 s 认不出新登录的 user_id。该行为被 `tests/test_cookie_layer_sync_fix.py` 显式钉住（≥6 例）。改成 mtime+size 指纹能保住语义，但那要求把钉住的行为改口径，为 ~1~3 ms 的收益不值。**收益实测**：探针里 1.7 ms，占比极小。
- **③④其实早已成立**：③ `AuthManager.get_userinfo()` 自带 300 s 内存缓存（`USERINFO_TTL`），并非每请求读文件；④ `_sync_nick_to_users_table` 先比对 `valid_nick != current_nick` 才 UPDATE，昵称未变时不写库。原条目把这两处当成本条的成本，是当时未读实现的误判。
- **另一条 memo 也判定不落**：稳态剩下的那条 `SELECT * FROM users` 可以做进程内 memo，但 `users.nickname/custom_alias` 有多个写入方（前端改别名、`merge_duplicate_accounts` 合并、`identify_or_create` 建号），memo 必须配齐失效点才成立——与 §B2/C4 对「秒级稳定默认缓存候选」的口径一致。收益是 1 条往返（0.3~0.5 ms 级），代价是把「改了别名界面不刷新」这类真实故障引进控制台必经接口。
- **验证**：`tests/test_auth_me_scan_throttle.py`（12 例）+ `tests/test_regress_notification_user_scope.py`（T02618 口径，含改写的聚合入口透传例）。节流侧钉「窗口内不查库 / 默认不节流 / 窗口到期恢复 / 按用户分桶 / `None` 独占一桶 / 16 线程同时到达只放行一次」；功能侧钉「首次调用仍产出 5min 通知、dedup_key 逐字节不变、窗口内第二个请求不重复写、掉线告警不被连带节流」；接线侧钉两条静态守卫（`/me` 只解析一次身份、必须把常量窗口透传下去）与两条行为守卫（同一个 uid 既用于读 cookie 也用于通知归属；无会话身份时仍回退 `owner_user_id`）。**顺带补的一处判据**：`scan_and_notify` 对每条规则包了 `except Exception → 记 WARNING、该档计 0`，替身签名漂移会被**静默吞掉**（本条就是这么暴露的：`stats` 照常，只有 `seen` 少一项），因此聚合入口用例把入参一并写进断言。变异验证见 `perf/2026-10-31-full-api/c2_mutation_run.py`（6 腿 + 1 负对照）。
- **一处过程失误与更正**：第一轮变异跑（后台）与我做 A/B 阶梯的源码文件互换**并发执行**，负对照腿被踩到（exit=1 且汇总行没打出来），该轮判定作废；重跑给 runner 加了两道闸：起跑前逐字节校验两份文件等于「已落地」版 sha256（不符直接中止），负对照非 0 时摊开原始输出尾部以便区分「用例不认这条改动」和「腿被打断」。重跑判据：**6/6 腿翻红 + 负对照 33 例全绿 + 两份文件按 sha256 逐字节还原**（L1 闸门登记挪到扫描之后 → 2 例红（窗口内照样查库 + 16 线程齐发只该放行一次）、L2 默认窗口写成 30 s → 1 例红（显式调用方被静默节流）、L3 分桶塌成一个 → 2 例红（按用户 / `None` 独占桶）、L4 聚合入口不透传窗口 → 2 例红（含 T02618 那条，正是「签名漂移被 `except Exception` 静默吞掉」的形态）、L5a 路由不传窗口 → 2 例红、L5b 路由第二次解析身份 → 2 例红）；负对照改的是**同文件相邻**的掉线告警 dedup_key，用例必须认不出它 —— 全绿。两条 A/B 阶梯轮次（`--rebuild` 保证同一初始沙箱：上一版复用沙箱会被启动期的 `token_renewer`/`cookie_rotator` 把 session 层标失效，改的是 `/me` 走的分支，不是同一件事）也顺带说明了**为什么本条不能拿 HTTP avg 当判据**：未节流基线自己 c=48 三轮 256.2 / 117.7 / 233.5 ms。

### C4. 反爬策略接口降本（socket 探测 + Chromium Cookie 全表扫） — 优先级 **高** ✅ **已于 2026-11-03 落地（只落 ①③，②刻意不落；「≤30 ms」经实测作废）**
**具体优化内容**：`backend/xianyu_hunter/modules/login_strategy.py:144` 用 `socket.create_connection(("127.0.0.1", cdp_target_port), timeout=1.0)` 判断 CDP 可用性 —— 端口未开时**每次请求都吃满 1 s**；`:150-184` 再打开 Chromium `Network/Cookies` SQLite 做全表 `LIKE '%goofish%'`。改为：① 探测 timeout 降到 0.1 s 并 fail-fast；② 探测结果整体短 TTL 缓存（10~30 s）；③ Cookie 计数改用 `WHERE host_key = ?` 精确匹配（该表有 `idx_user_cookies_user_host` 类比索引可用，见 `db_models.py:776`）—— **这一句在落地时被推翻**（那个索引是我们自己的表；等价匹配会漏掉子域形态），实际做的是关键名下推 + `LIMIT 1`，见下方落地记录。
**证据**：`GET /api/anticrawl/strategy` 基线 avg **1 094.7 ms**（≈ 1 s 探测 + 扫描），P99 1 216.8 ms；长跑 1 300.7 ms；96 并发 2 511.8 ms。同族 `anticrawl/fingerprint` 仅 9.0 ms，证明「不算这一跳」的成本地板。
**量化预期收益**：avg **1 094.7 → ≤30 ms**（-97%）。这同时是 D1 的关键前提——它曾是最占用 anyio 线程的接口之一。 **← 「≤30 ms」已由实测作废（①③ 只能到约 190~250 ms），见下方落地记录。**
**复核注（2026-10-31，与 §B2 第 3 条同源）**：① 探测 timeout 与 ③ 精确匹配是纯降本，随时可做；但 **②「探测结果整体短 TTL 缓存」不能裸上** —— `evaluate()` 的语义是「当前环境」，`AntiCrawl/index.tsx` 又是 `runInitialize()` → `fetchAll()`，缓存必须配对失效（至少 `/initialize`、登录态变更、会话失效三处），否则页面会在用户刚开 CDP/刚登录后继续推荐旧策略。TTL 也应按 B2 的口径压到 10 s 以内并显式失效，而不是当作「秒级稳定」的默认缓存候选。
**验证方式**：CDP 关闭态下压该接口，确认不再出现 ~1 s 常数项。

**落地记录（2026-11-03，`modules/login_strategy.py`）**

- **先证前提，再动手**：本条把 ~1 s 归给「端口未开时 `create_connection(timeout=1.0)` 吃满超时」，而这台机器上回环连**没监听的**端口到底是立刻 RST 还是静默丢包，决定这条成不成立。`loopback_connect_control.py` 四个对照（bind+listen 不 accept / +accept / 真实 HTTP 往返 / 空闲端口分别 1.0 s 与 0.1 s）实测：**没监听的端口收不到 RST，连接吃满 timeout**（1.0 s → ~1 010 ms，0.1 s → ~109 ms），真正在 accept 的监听端口 **0.76 ms** 完成。前提成立。（中途一版探针把「自建的监听端口」误标成 timeout，一度指向「前提不成立」——是探针的标签错了，不是现象错了。）
- **① 已落**：探测超时提到类常量 `LoginStrategySelector.CDP_PROBE_TIMEOUT = 0.1`，调用处用它。`c4_after_probe.py` 实测（9 次采样取中位）：CDP 关闭态这一跳 **1 014.2 → 109.1 ms（省 905.1 ms，9.3×）**；同一轮里真在 accept 的端口 **0.60 ms** 仍判可用（0.1 s 预算对它留了 ~160× 余量）；完整 `evaluate()` 地板 **~1 s → 109.5 ms**。
- **② 不落**（与 §B2 第 3 条同源）：`evaluate()` 的语义就是「当前环境」，TTL 缓存必须配对失效才成立 —— 至少 `/initialize`、登录态变更、会话失效、以及「用户刚把带 CDP 的浏览器打开」四类边界。本轮不引入「界面在用户当场改完环境后继续推荐旧策略」这种真实失败，所以只做了不改语义的两项。
- **③ 的落地形态与本条原文不同，且原文的索引依据是错的**：`db_models.py:776` 的 `idx_user_cookies_user_host` 属于**我们自己的** `user_cookies` 表，Chromium 那个 `Network/Cookies` 文件里没有它；而 `host_key = ?` 精确匹配会**改判定语义** —— 同一站点在 Chromium 里同时存在 `.goofish.com` / `www.goofish.com` / `s.taobao.com` 几种形态，`LIKE '%goofish%'` 正是在覆盖它们。改成**把关键名下推给 SQL 并命中一条即停**：`SELECT 1 FROM cookies WHERE (host_key LIKE ? OR host_key LIKE ?) AND name IN (...) LIMIT 1`，谓词集合不变。实测 `anticrawl_probe_cost.py`：5 000 行 **8.24 → 3.44 ms**（布尔逐形状相同）；走真实代码路径复核：命中 **0.87 ms**、最坏情况（全表无关键名）仍要扫完 **2.47 ms**。这一项本来就只占毫秒级，**不是那一秒的来源** —— 收益小、语义零风险，所以照做，但别指望它降本。
- **本条「avg → ≤30 ms」作废**：残余的 109 ms **就是预算本身**（本机静默丢包 ⇒ 这一跳的耗时恰好等于 timeout），①③ 相加只能到 **约 190~250 ms**（= 基线 1 094.7 扣掉省下的 895 ms）。再往下只有两条路：把常量继续压小（0.05 s → 约 54 ms，代价是给慢启动的 CDP 更少余量），或落 ② 的短 TTL + 显式失效。**接口级与并发级数字（含 96 并发 2 511.8 ms）仍待同一 JMX 复跑。**
- **验证**：`tests/test_anticrawl_strategy_cost.py`（19 例）。探测侧钉「调用处取的是类常量」「预算 ≤0.2 s」「关闭端口整次探测 <0.5 s」「真在 accept 的端口仍判可用」「探测失败不往上抛、`evaluate()` 回落到扫码」；Cookie 侧钉**与改写前等价**（8 个形状同时断言新实现与内联的参照实现 `legacy_check` 一致，防止「用例跟着新语义一起改错」）、host 与 name 两个过滤都还活着、`name IN` + `LIMIT 1` 确实进了 SQL 文本、缺库 / 无 `cookies` 表 / 非 SQLite 文件三种坏形态照旧 False。安全边界：全部用例 `chdir` 到 tmp 并打桩 `get_config` / `get_browser_data_dir`，不读真实 `data/browser-data` 与真实 Cookie 库。
- **变异验证四腿 + 负对照**（串行）：timeout 写回 `1.0` → 2 例红；SQL 分支整体退回原句 → 1 例红；去掉 `name IN` 下推 → 5 例红；第二个 host 图案接错（`%taobao%` 写成 `%goofish%`）→ 1 例红；**负对照**改 `infra/browser.py` 里另一处 CDP 超时 → 全绿（证明用例认的是本模块这一跳，而不是任意一次 socket 调用）。还原后 sha256 与基线逐字节相同。判据脚本 `loopback_connect_control.py` / `anticrawl_probe_cost.py` / `c4_after_probe.py` / `c4_mutation_run.py` 均只读、只在临时目录造数据。

### C3. `_load_yaml()` 走单例缓存 — 优先级 **中**
**具体优化内容**：`web/routes/api_config.py:45-48` 每次调用都 `CONFIG_FILE.read_text()` + `yaml.safe_load()` 整份配置，绕过了 `infra/yaml_config.py:784-789` `get_config()` 已有的单例缓存。改为复用单例 + mtime 失效。模板接口同一形态：`web/routes/api_templates.py:65-66 list_templates` 每次调 `_load_presets`(`:39` 读 YAML) + `_load_user`(`:47` 读 JSON)，`POST`(`:76-77 save_template`) 还要再读后整表写回 —— 应按 mtime memo 化。
**证据**：`config/raw` 132.4 ms、`config/share` 104.7 ms、`GET /api/templates` 115.8 ms、`POST /api/config/preview` 151.0 ms、`POST /api/templates` 200.9 ms（均 1 并发基线）；长跑分别 334.4 / 292.7 / 296.4 / 419.0 / 377.3 ms。
**量化预期收益**：命中后 **≤15 ms**（-88%）；写侧 `preview/save` 去掉一次全量读后 avg **151 → ≤80 ms**。
**验证方式**：同一 JMX 复跑 + 配置文件 mtime 变更后确认仍能读到新值（正确性回归）。
**落地状态（2026-10-31）**：✅ config 侧已按 mtime 指纹 memo 落地（详见 §B2「实际落地的残留」：`api_config._load_yaml` + 6 处写盘配对作废 + 存储侧代次护栏 + `tests/test_config_yaml_memo.py` 13 例）。
**本条「≤15 ms」需要下调**：探针把成本拆开后，`/raw` 的 132.4 ms 里解析 52.5 ms、`_dump_yaml` 序列化 32.9 ms —— memo 只砍前者，命中后仍有 33 ms 的 dump 加框架余量，落在 **80~90 ms** 而不是 15 ms。解析那段已由 **C6**（读侧换 libyaml C loader）在冷路径上砍掉约 41 ms；**dump 那 27~33 ms 治不了** —— C dumper 对 emoji 不遵守 `allow_unicode`，写侧不换（见 §C6），所以 `/raw` 命中后的 80~90 ms 是稳态而非过渡值。这仍然比给这两个前端并不消费的接口加 TTL 缓存（见 B2 第 4 条反证）对症。
**C3 残余（api_templates）落地状态（2026-11-06）**：✅ 读侧指纹 memo 已落 —— `_load_cached()` 按 `(mtime_ns, size, 读取代次)` 命中，`_load_presets` / `_load_user` 都走它；`_save_user` 写盘后 `_bump_epoch` + `_MEMO.pop` 配对作废（同尺寸 + 同一 mtime tick 的改写，单靠文件指纹识别不到），并用读取代次防止「stat 与 parse 之间被写盘插队」的在飞请求把旧值存回 memo。模板文件本身很小，收益与 config 侧（52.5 ms 解析）不是一个量级：`c3_templates_probe.py` 实测进程内 `GET /api/templates` 处理器 **2.23 → 0.53 / 0.58 ms（−76%）**（presets YAML 1.32 → 0.64~0.91 ms、user JSON 1.15 → 0.34~0.65 ms），命中路径的残余就是两次 `os.stat`（本机中位 0.28 ms/次）。**deepcopy 方案被实测否掉**：112 条用户模板 deepcopy 1.14 ms ≈ 重新 `json.load` 1.15 ms，会把收益吃光，最终返回浅拷贝列表（现有调用方只做拼接/过滤/append 新元素）。**本条「≤15 ms」与模板侧无关**：基线 HTTP `GET /api/templates` 115.8 ms 里能归到模板解析的只有 ~2 ms，其余是中间件/线程池排队（D1 的账）；接口级数字仍待同一 JMX 复跑。回归 `tests/test_templates_memo.py` 9 例（解析一次/外部编辑跟上/同尺寸同 tick 写盘后仍读到新值/返回副本/缺文件与坏 JSON 恢复/写盘配对作废 AST 门禁）；`c3_mutation_run.py` 3 红（指纹不参与命中 / 写盘不作废 / 直接返回缓存对象）+ 2 绿（异常元组顺序 / 只改 docstring），`api_templates.py` 按 sha256 逐字节还原。


### C6. PyYAML 走的是纯 Python 后端，libyaml 已在依赖里却没被用 — 优先级 **高**（新发现，非压测得出；`config_memo_probe.py` 暴露）✅ **已于 2026-11-01 落地（只落读侧）**
**具体优化内容**：`yaml.safe_load` 绑定的是 PyYAML 的**纯 Python** `SafeLoader`；本机 `yaml.__with_libyaml__ is True`，说明 `CSafeLoader` 可用。**注意 `CSafeLoader` 就是 `SafeLoader` 的 C 实现，不是 `yaml.load` 的裸 `Loader`/`FullLoader`**：同样不构造任意 Python 对象，不放宽反序列化面。受益面不止 `api_config`：`infra/yaml_config.load_yaml`（启动与每次 reload 都要读 config.yaml + eval.yaml + 子配置）、`api_templates`、`api_tunnel`/`api_notifier`/`api_batch_refresh`/`infra/ai_usage`/`menu_manager`/`repo_param_rules` 的读盘同样在用纯 Python 后端。
**证据**（同一份 7.2 KB / 35 顶层键的 `config/config.yaml`，各 20 次采样取中位）：
- 解析：`safe_load` 中位 **52.5 ms**（最快 40.4）→ `CSafeLoader` 中位 **4.7 ms**（最快 3.9），**11.3×**；两份结果 `== ` 相等。
- 序列化：`_dump_yaml` 中位 **32.9 ms** → `CSafeDumper` 中位 **6.3 ms**；在 `config.yaml` 这一份上输出逐字节相同（中文不转义、`sort_keys=False` 的插入顺序、`{}` / `''` / `null` 的写法）。
- 读文件本身不是成本：`read_text` 中位 0.36 ms。

**⚠️ 「逐字节相同」当时被我过度泛化了 —— 所以只有读侧能落地**：把上面那条断言从「一份文件」升级成「仓库里 7 份真实 YAML 逐份比对」（`config_memo_probe.py` 已改成逐份打印）之后，**6/7 相同、`preset_templates.yaml` 不同**。原因是 `CSafeDumper` 对**超出 BMP 的字符**（emoji，模板图标 `📷`（码位 U+1F4F7））不遵守 `allow_unicode=True`，写成 `"\U0001F4F7"` 转义，而纯 Python dumper 写原字符。两种写法都能被任一侧 loader 读回同一个值，但**磁盘产物会变**：写盘、备份、`_diff` 都建立在「同一个 dict 两次 dump 结果一致」之上，而模板 `icon` 是用户可见、可手改的字段。因此本轮只切读侧，**dump 继续走 `yaml.safe_dump`**（那 21~27 ms 不拿）。这条分歧钉在 `tests/test_yaml_io_equivalence.py::test_c_dumper_would_mangle_astral_chars_so_dump_stays_python` —— 它断言的是「两边确实不一致」，将来 libyaml 修了这个行为时用例会红并提示重新评估写侧。
**落地状态（2026-11-01）**：✅ 新增 `backend/xianyu_hunter/infra/yaml_io.py`（只导出 `safe_load`；`_LOADER = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader`，libyaml 缺失时退回纯 Python —— 精简镜像里这是真实形态，不是想象的场景），9 个读盘点改为经它：`api_config._load_yaml`、`infra/yaml_config.load_yaml`、`infra/repo_param_rules`、`infra/ai_usage`、`web/services/menu_manager`、`web/routes/api_templates`、`api_tunnel`、`api_notifier`、`api_batch_refresh`。写侧 7 处 dump 一字未动。
- **实测落点**（同机、只读真实 `config.yaml`，15 次采样取中位）：`yaml.safe_load` 47.7 ms → `yaml_io.safe_load` **6.5 ms（7.4×）**；`yaml_config.load_yaml`（含开文件）12.0 ms。启动与每次 `reload_config()` 都要走的这条路径同享。
- **前置等价性用例先行**（`tests/test_yaml_io_equivalence.py`，56 例）：结构相等（10 种真实形状 × 仓库在用的 3 组 dump 参数 × 交叉读写）、空文档返回 `None`、异常**类**与纯 Python 一致（`menu_manager` 的 `except yaml.YAMLError` 覆盖面不能缩；实测两侧同为 `ScannerError` 子类，只有消息文本不同，而仓库里没有代码断言消息文本）、`!!python/object` 照旧被拒、文件对象入参仍支持（`load_yaml` / `_load_presets` 传的是句柄）、仓库 7 份真实 YAML 逐份读出结构相等、后端选择跟 `hasattr(yaml, "CSafeLoader")` 走（防止悄悄退回纯 Python 而用例全绿）。
- **订正原「为什么没有直接落地」里的两处错误**（本轮逐个调用点复核）：① 原写「`import_config` 靠 `YAMLError` 转 400 的语义要重测」站不住 —— `/import` 收的是 **JSON body**（`body.get("config")` 必须是 dict），不做 YAML 文本解析，全仓 `YAMLError` 只有 `menu_manager.py:123` 一处捕获，已进等价性用例；② 原写「`dump` 参数在 C 后端的兼容面要逐个调用点确认」—— 这条不仅成立而且比预想严重（就是上面的 astral 字符分歧），因此写侧整段撤回。
**量化预期收益**：`config/raw` 132.4 → **约 90 ms**（只省解析 41~48 ms；dump 那 27 ms 留在原地，与 C3 的 80~90 ms 口径一致）；`config/share`、`config/export` 同量级；`config/preview`、`config/save` 各省一次解析（叠在 B2 的 memo 之上时读侧收益只在冷的那一次 —— **但启动/reload 路径的 `yaml_config.load_yaml` 每次都吃这条收益**）。**注意**：这是「砍掉常量项」，不改变并发下的排队，因此不能替代 D1/B1。
**验证方式**：`tests/test_yaml_io_equivalence.py`（56 例）+ `tests/test_config_yaml_memo.py`（13 例，计数 shim 的 patch 目标随之内移到 `api_config.yaml_io`）+ 受影响面（config/tunnel/notifier/menu/templates/param_rules/ai_usage/yaml_config）全绿 + 全量回归。`config_memo_probe.py` 保留为成本判据，现已含逐份 dump 比对。变异验证两腿各自翻红（把 `_LOADER` 硬编码成纯 Python `SafeLoader` → 1 例；换成 `FullLoader` → 3 例）—— **第二腿最初只翻 1 例**，暴露出「拒绝 python 标签」那条对 FullLoader 是无效的（`!!python/object/apply` 连 FullLoader 都拒），于是补了 `test_rejection_is_not_vacuous` 正面断言「FullLoader 接受 `!!python/name`、我们拒」，判据本身才成立。真实数字待同一 JMX 复跑替换。

### C1. 快照目录体积重复遍历合并 — 优先级 **中** ✅ **已于 2026-11-03 落地（并多做了一件原文归到「证据」里的事）**
**具体优化内容**：`web/routes/api_vector_admin.py:103-104` 对同一目录连续调用 `_dir_size(d)` 两次（分别填 `size_bytes` 与 `size_display`）。改为一次求值后复用。
**证据**：`GET /api/vector-admin/snapshots` 基线 avg **233.8 ms**（P95 630.2），长跑 450.7 ms；其成本主要是磁盘遍历（`:64-72` 的 `rglob` 还跑在 async handler 内，会阻塞事件循环）。
**量化预期收益**：**直接减半** → avg ≤120 ms。（原写「叠加 B2 缓存后 ≤20 ms」已作废：B2 对本接口撤回，理由是删/建/恢复快照后列表必须立刻反映磁盘现状，见 §B2 第 2 条。）
**验证方式**：单接口阶梯复跑，比较 `size_bytes` 一致性。

**落地记录（2026-11-03）**
- **已做**：`_list_snapshots` 内先 `size_bytes = _dir_size(d)`，`size_display` 由同一个值格式化（`api_vector_admin.py:101-107`）。主目录体积抽成 `_main_data_size(persist_path)`，`/status` 的「主目录 + 全部快照」两次遍历合并成**一次 `asyncio.to_thread` 跳转**。
- **两处必须订正**：① 原文「`rglob` 跑在 async handler 内，会阻塞事件循环」对 **`/snapshots` 不成立** —— 它是同步 `def` handler，FastAPI 把它投进 anyio 线程池，代价是占一个线程令牌（D1 的账），不是占事件循环；真正占事件循环的是 `async def get_status`（它 `await vector_store.count()` 之后直接在循环里走全量 `rglob`）。② 探针实测降幅是 **43%**，不是干净的 50%：一遍遍历里除了 `stat` 还有 `iterdir`/`st_mtime`/排序/建字典的固定开销，实测 3 200 次 stat 的单遍 5.03 ms 折算 + 固定 1.75 ms（占比约 26%）。因此 **≤120 ms 这个目标本轮不能宣布达成** —— 真实快照目录只有几百个文件时遍历在 233.8 ms 里占多少并未测过，必须用同一 JMX 复跑替换。
- **判据**：`perf/2026-10-31-full-api/dir_size_probe.py`（合成 8 目录 × 400 文件；两遍 11 811 ms → 一遍 6 777 ms，降到 57%，两侧结果逐字段相同）。[B] 段直接量到「遍历期间事件循环的心跳推进次数」：修前中位 **1 次**、修后 **376 次** —— 这就是 B1 单飞救不了的那一段：lead 在循环里同步走盘时，同一 key 的跟随者连「等结果」都做不到，整个循环都停着。
- **回归**：`tests/test_vector_admin_snapshot_size.py` 19 例（每个目录只遍历一次 ×2 条路径、display 与 bytes 同源、两个快照不串体积、嵌套目录计入而目录本身不计、缺根目录返回空、`_main_data_size` 排除 `snapshots`、`/status` 遍历不在工作线程之外、`/status` 全字段与空快照两种终态）。三条变异各自翻红：改回双调用 → 2 例（只统计遍历次数，不校验数值，所以「同样的值走两遍」这种退化只有计数能抓）；去掉 `to_thread` → 1 例；`_main_data_size` 不再排除 snapshots → 2 例。
- **顺带记录的环境事实**：这台机器上单次 `Path.stat()` 约 **1.6 ms**（3 200 次 ≈ 5 s），比正常 NVMe 高一个数量级，几乎肯定是安全软件逐文件扫描。**凡是「glob 目录 + 逐个 stat」的接口都要按这个量级估成本**（A4 的日志统计、`_cache_scan` 的用户数据目录扫描都在此列）。


### C5. 评估列表市场统计去 N+1 — 优先级 **中**
**具体优化内容**：`web/routes/evaluations_list.py:69-80` 的 `compute_market_stats_batch` 在缓存 miss 时**按 task_id 逐个**调用 `_compute_sold_range`。改为单条 `IN (...)` / 分组聚合一次取回。
**证据**：评估族在长跑中齐处 167~196 ms 窄带（A2/B1 与本条互相放大），`evaluations/1000915580797/seller-trend` 171.6 ms。
**量化预期收益**：多任务用户的评估列表 P99 **858 → ≤300 ms**；调用 task 数越多收益越大（当前是线性放大）。
**验证方式**：种子注入 20 个 task 后对比 P95 斜率。

**C5 落地状态（2026-11-06）**：✅ 批量化落地 —— `compute_market_stats_batch` 的 cache miss 集合改走 `_compute_market_stats_for_tasks()` → `price_dashboard._compute_sold_range_batch()`：任务价格区间 1 条 SQL、已售样本 1 条（`task_id IN (...)`，`LIMIT 5000 × 任务数`，与原单任务 `LIMIT 5000` 的「任意截断」语义对齐）、回退样本 0~1 条（只对已售样本不足的任务查）。统计口径没有复制实现：`_compute_sold_range` 与批量版共用同一段 `_summarize_price_stats()`（从原函数里抽出来的 mixed / all_fallback / 分位数逻辑），行情页/直方图/趋势页仍走单任务版，`task_id=None`（全任务模式）也保持单条语义（它要按每个任务自己的区间过滤后合并）。

判据（`c5_market_stats_probe.py`，合成库 20 个任务 × （60 条已售 link + 60 条 items），7 轮中位）：逐条 **40 条 SQL / 17.1 ms** → 批量 **2 条 SQL / 4.7 ms（0.27×，−73%）**。SQL 条数与任务数解耦，任务越多比值越低。**接口级 P99 858 → ≤300 ms 本轮不发布**：进程内直调只证明「每次冷算的 SQL 与耗时降了 4~7 倍」，HTTP 层还有 D1 的线程池排队，须同一 JMX 复跑。

回归 `tests/test_evaluations_market_stats_batch.py` 7 例（批量结果与逐任务 `_compute_sold_range` 逐字段相同且覆盖 sold/mixed/all_fallback 三种 source、SQL 次数与任务数解耦（批量 ≤3 / 逐条 ≥2N）、`task_id=None` 仍走单条、cache 命中不查库、批量整段失败退回逐任务、单个任务失败只丢它自己、接线门禁「必须调用批量入口」）；`c5_mutation_run.py` 4 红（退回逐 task 循环 / 每 task 套用第一个任务的区间 / 批量回退丢掉 range_days 过滤 / 回退判定反向）+ 2 绿（给区间查询多带一列 status / 只改 docstring），`price_dashboard.py` 与 `evaluations_list.py` 按 sha256 逐字节还原。

---

## D. 连接池配置调优与线程模型

### D1. 慢同步接口与快接口隔离线程池 — 优先级 **高** ✅ **已于 2026-11-04 落地（落地面与原文不同：无 RunVars API；名单按实测成本重划；归因拆成「IO 型 = 令牌饥饿」与「CPU 型 = GIL 并发面」两笔账）**
**具体优化内容**：本项目大量 handler 是同步 `def`，FastAPI 一律丢进 anyio 线程池（默认 **40 token**，代码未覆写）。给已知慢接口（`maintenance/*`、`export*`、`logs/search`、`vector-admin/*`、`anticrawl/strategy`）单独设置 `anyio.to_thread.RunVars`/`CapacityLimiter`（如 4），并把 `vector-admin` 的 `rglob`、`_dir_size` 移出 async handler。
**证据（饥饿的直接形态）**：96 并发时，**本身只要 8.4 ms 的** `GET /api/notifier/quiet-hours` 变成 avg 2 096.8 ms；60 s 窗口内它的完成样本数从基线的 55 掉到 **7**，`vector-admin/snapshots` 只剩 **3**，`maintenance/cache-scan` 剩 **6** —— 完成数骤降 + 廉价接口延迟被抬到 2 s，是「排队等线程」而非「服务算得慢」的教科书特征。轻组整体：16 并发 P95 782.7 → 48 并发 2 064.0 → 96 并发 3 893.2 ms，真实缺陷率 1.81% → 2.03% → **3.17%**。
**量化预期收益**：48/96 并发轻组 P95 **2 064/3 893 → ≤1 000 ms**；缺陷率 **2.03/3.17% → <0.5%**；廉价接口（`quiet-hours`、`fingerprint`）在高并发下回到 ≤100 ms。
**验证方式**：同一 `reads_light` JMX 在 48/96 并发复跑，比较 `notifier/quiet-hours` 的 avg 与完成样本数（样本数恢复即证明隔离生效）。

**落地状态（2026-11-04）**：✅ 已落地（`web/slow_lane.py` + 配置 `slow_lane_tokens=4` + `create_app()` 里 `apply_slow_lanes(app)`），但**落地的不是原文写的那个 API**，且原文两处口径被实测改写。四份判据脚本：`d1_inventory.py`（形状前提）、`d1_lane_probe.py`（隔离能否兑现 + 替代方案对照，产物 `results/d1_lane_arms{,_r2,_c,_c2}.json`）、`d1_hog_nature.py`（慢族的成本性质，`results/d1_hog_nature.json`）、`d1_lane_budget.py`（容量 4 的 Little's law 依据，`results/d1_lane_budget.json`）。

- **原文建议的 `anyio.to_thread.RunVars` 在装机 anyio 4.13.0 里不存在**（`hasattr(anyio.to_thread, "RunVars") == False`），而 `starlette.concurrency.run_in_threadpool` 调 `to_thread.run_sync(func)` 时**不传 limiter**、也不接受 limiter 参数 —— 公开 API 里没有 per-route 落点。落地形态因此是集中式改写：对名单内路由把 `route.dependant.call` 换成「`await anyio.to_thread.run_sync(partial(func, **kw), limiter=CapacityLimiter(cap))`」的协程包装，再清掉 `Dependant` 的 `is_coroutine_callable`/`is_gen_callable`/`is_async_gen_callable`/`cache_key` 这几个 `cached_property`，最后 `route.app = request_response(route.get_route_handler())` 重建 —— 少任何一步都不生效（`get_request_handler` 在构造时就把 `is_coroutine` 烧进闭包）。
- **必须用 `fastapi.routing.request_response`**：FastAPI 那份是 Starlette 的 vendored 副本，额外往 scope 里塞 `fastapi_inner_astack` / `fastapi_function_astack`，而路由处理器里对此有断言。首版误用 `starlette.routing.request_response`，**每个请求**撞 `AssertionError: fastapi_inner_astack not found in request scope`（6 例请求类测试全红才逼出来）。
- **为什么不用装饰器**：`startup.py:835` 与十余处测试把这些处理器**按同步函数直接调用**（`test_db_admin_edit_guard.py:212` 还 `inspect.signature(admin.export_rows)`）。装饰器会把它们变成协程函数 → 连带崩掉。集中改写保住处理器对象、签名与 openapi（实测 562 条路由 / 480 个 openapi path 逐字节不变）。代价是依赖 FastAPI 内部结构，故名单失配直接抛错、并由测试断言「在途数真的被限住」，改版失效时翻红而不是静默退化。
- **形状前提先验过再谈数字**：`d1_inventory.py` 清点 **277 个处理器 / 231 个同步（83.4%）/ 46 个 async**。原文「大量 handler 是同步 def」成立，但「一律丢进线程池」只对那 231 个成立 —— 46 个 `async def` 根本不进池，给它们限流只会白排队（代码里显式跳过）。D1 用来证明饥饿的三条廉价接口（`GET /api/notifier/quiet-hours`、`GET /api/anticrawl/fingerprint`、`GET /api/anticrawl/freq/delay`）实测**都是同步 def**，确实与慢接口抢同一个池，前提成立。
- **机制证据（把「相关」变成「因果」的那一步）**：探针把每次请求拆成 `queue_ms`（等令牌+派发）与 `work_ms`（处理器自己算）。**生产同形**的臂（`sync Depends` + 3 000 行响应体，hog×40 走共享池）：廉价接口 p50 **3 509.6 / 1 153.9 ms**，而 `queue_p95` **974.4 / 978.6 ms**、`work_p50` 恒 **~4 ms**、线程池峰值借用 **40/40**、60 s 完成样本 **1 006 → 16 / 34**。同一 hog 走 `CapacityLimiter(4)` 专用道：p50 **34.3 / 59.8 ms**、`queue_p95` **6.7 / 8.9 ms**、完成数 **504 / 334**、专用道峰值 **4/4**。两轮同向（`results/d1_lane_arms_c2.json`）。
- **两个「更省事」的方案实测更差，判定不落**：① 只把全局令牌抬到 128（A4 臂）廉价接口 p50 **49.1 / 174.0 ms**、完成数 **257 / 62**（空载地板 p50 21.7 / 21.1 ms），hog 越多借用越深（峰值 44 / 34）；② 另开一条**不限容**的 limiter（A3 臂，刻意负对照）p50 **113.5 / 341.6 ms**、完成数 **104 / 24**、专用道借用 34 / 31 —— 等于没隔离，只是换池排队。**起作用的变量是上限，不是「有第二条道」**。
- **CPU 型慢接口的延迟来自 GIL，不是排队**（原文明示不了的这一半）：A5 臂（CPU hog×40 吃满共享池）p50 **6 901 ~ 11 666 ms**，可 `queue_p95` 只有 **69 ~ 289 ms**、`work_p50` 4~5 ms —— 排队根本拆不出这个量级，是解释器锁把事件循环一起冻住（`d1_hog_nature.py` 的 `ticker_ratio`：纯 Python 忙等 **0.492**（<1 即心跳落后）vs `time.sleep` 锚点 **1.105**）。同一条 hog 走专用道（A6/B3 臂）p50 **41.4 ~ 66.9 ms**、`queue_p95` **2 ~ 8 ms**。所以专用道对 CPU 型同样有效，但**归因要分开写**：IO 型是令牌饥饿，CPU 型是 GIL 并发面。
- **名单按实测成本划，不按原文的文件名**：原文点名的 `logs/search` **判定不入道** —— 它在压测里的样本是 `422×286`（`results/endpoint_table.md`），从未真正执行，没有占用数据就不能限流；同理 `logs/export`、`logs/request/{id}`、`error-logs` 族。`db-admin/tables/{table}/rows`（avg 34.2 ms）低于判据也不入道。最终 **16 条**：12 条读接口 + 4 条结构性长任务（`maintenance/{cache,database,logs}`、`vector-admin/snapshots/{id}` DELETE）。判据 = 同步 def 且（(a) 读接口实测 ≥90 ms 或 (b) 全库清理/VACUUM/递归删目录/全量导出级）。写侧接口不入道（瓶颈是 WAL 单写者，归 D3）。
- **容量 4 的依据**（`d1_lane_budget.py`，Little's law）：按 `agg_phases.json` 逐接口吞吐 × 基线单次占用算慢族在途需求，最坏的 `stress_light_t96` 档为 **2.14 个在途**（需求 4.9 rps、平均占用 436.8 ms），最大单项是 `GET /api/anticrawl/strategy`（1.75）；`soak_mixed` 档 1.41。cap=4 的吞吐上限 4 ÷ 0.4368 ≈ **9.16 rps > 4.9 rps** → 够用且不会把慢族自己堵死。⚠ 首版把 `mean_hold` 写成 `total_inflight/total_inflight`（恒报 1 000 ms 且误判「堵道」），已订正为 `total_inflight/offered_rps`。另注：名单里的占用数字仍是 C4/A4 修复前的基线，复跑后若某条掉到 90 ms 以下应移出 —— `tests/test_slow_lane.py::test_slow_routes_are_pinned` 把 16 条成员逐条钉住，改动必须显式过这道。
- **验证**：`tests/test_slow_lane.py` **15 例全绿**（13.47 s）；受影响族回归 **370 passed**（167.5 s）；全量后端 **2 796 passed / 0 failed**（20:22，`EXIT=0`）。变异验证 **12 腿 + 负对照** 各自按预期翻红/全绿，`config.py` 与 `slow_lane.py` 按 sha256 逐字节还原（逐腿现场 `results/d1_mutation.log`；`*.log` 被 `.gitignore` 排除、只留本机，历轮变异跑的日志同样不入库 —— 判据可由 `d1_mutation_run.py` 原样复现）。
- **变异跑出来的两条副产品**（写在这里免得下次重犯）：① 首版 L01 只把构造改成 `CapacityLimiter(inf)`、**保留** `elif limiter.total_tokens != capacity` 那句夹逼 → **全绿**，因为第二次取用时容量被自动夹回来（同目录 `d1_mutation_first_pass_selfheal.log`，同样不入库）。「不限容」这种退化必须连夹逼一起摘才抓得到，反过来也说明现实现对「容量写错」多一道兜底。② 第一版测试用 `sl._normalize()` 找 `/xianyu` 挂载 —— 等于借被测函数自己核对，「不剥前缀」这条变异就跟着一起变绿；已改成显式枚举两份挂载路径，该腿才真的翻红。③「忘传 `limiter=`」的腿最有价值：所有功能测试全对，只有「在途恰为 cap」这条钉得住 —— 隔离类改动最容易的失效形态正是「照跑不误但没收益」。
- **本轮不发布的数字**：轻组 48/96 并发 P95 ≤1 000 ms、缺陷率 <0.5%、廉价接口 ≤100 ms 都要同一份 JMX（`jmeter-config-v2.yml` + `aggregate.py`）复跑才成立。本地沙箱只证明「机制在生产同形结构下兑现了方向」，不等于生产接口级终值。
- **探针事故记录（判据脚本自身的坑）**：`results/d1_lane_arms_c.json` 整轮 `server not ready` —— 两次探针并发共用固定沙箱目录，先收尾的那个 `rmtree` 把还在跑的那个的应用源码与服务端日志一起删了。修法是沙箱按 PID 命名 + 诊断输出（alive/rc/app_src/日志尾部）+ **按 PID 树杀**（venv 的 `python.exe` 是 launcher，派生子解释器，`proc.terminate()` 只杀父进程会留下孤儿 uvicorn 占着端口）。

### D2. 解除缓存扫描的进程级互斥 — 优先级 **中** ✅ **已于 2026-11-05 落地（落地的不是原文的「按路径分锁」——真实负载只有一个路径；改的是「时间片不再持锁 + 同键单推进者」）**
**原建议**：`web/services/cache_scan.py:52` 的 `self._lock = threading.RLock()` 在 `snapshot()`（`:66`）里对**所有路径**互斥，使 `/maintenance/cache-scan` 与 `/maintenance/status`（内部也调 `snapshot`）在并发下完全串行。改为按扫描路径分锁，或改增量/无锁读。
**证据**：`maintenance/cache-scan` 基线 94.1 ms → 长跑 287.7（P99 1 581.4）→ 96 并发 avg 1 822.8 ms 且窗口内仅完成 6 次；`maintenance/status` 的 60 s 超时（A4）与该锁的串行化叠加。
**量化预期收益**：两接口并发下不再互相阻塞，96 并发 avg **1 822.8 → ≤400 ms**。
**验证方式**：并发两个不同路径的 `cache-scan`，确认总耗时 ≈ 单路径耗时而非两倍。

**落地状态（2026-11-05）**：✅ 已落地，但**原文的处方（按扫描路径分锁）在真实负载里买不到东西**，落地的杠杆是「时间片不再持锁」。判据 `d2_lock_probe.py`（产物 `results/d2_lock_probe.json`；合成树跑在按 PID 命名的临时目录，不读真实浏览器目录、不读任何 cookie 文件）：

- **调用点清点（静态，先于任何测量）**：`_cache_scan` 全仓只有一个进程内实例（`api_maintenance.py:41`），三个调用点 `:320`（`/status`）、`:356`（`/cache-scan`）、`:385`（清理预览）**传的是同一个 `_browser_data_dir()`**（`:109-127` 由单一配置项 `browser.user_data_dir` 解析）。所以「按路径分锁」动的正是那 0 个真实争用面 —— **串行的不是路径，是预算片**。
- **锁的持有形状才是机制**：`_advance()` 原先在 `with self._lock:` 里走完整片，于是迟到的调用排的不是 CPU 的队，而是前一个预算片的墙钟（生产预算 `/status` 0.5 s、预览 0.5 s、轮询 1.0~2.0 s）。探针里把预算定成「一次性全扫的 1/20」让片真的被走完，同路径 8 个并发到达：**现状 p50 42.2 / 36.5 ms（≈ 一整次全扫的墙钟）、p95 42.4 / 36.7 ms → 改后 p50 0.0 / 0.0 ms、p95 9.9 / 2.8 ms**（两轮同向）。
- **候选 A（推进不持锁 + 同键单推进者）胜出；候选 B（待扫目录当共享队列）带实测否掉**。B 让每个到达者都干一片（「干了活的手/调用」= 1.0，A 只有 0.12），看着更划算，但**预算是墙钟**：GIL 下 8 个线程各拿 2.5 ms 只换来约 1/8 的 CPU，切片是被**切碎**而不是并行 —— 到 done 的波数 **6 / 4 波、墙钟 83.8 / 66.7 ms**，比现状的 **1 波、42.6 / 39.8 ms** 更慢。A 的墙钟 **44.4 / 41.1 ms** 与现状持平而等待全部消失：**省掉的纯粹是排队，不是吞吐**。（这条是先按「轮询到 done」测出 A 比现状慢 2.4× 的假象后重做的：睡等节奏把「到达稀疏」错算成「浪费人手」，改成整波到达才有分辨率。）
- **正确性两条硬判据（两轮同向）**：候选推进到 done 的终值与一次性遍历**逐字段相同**（`mb=17.58 / files=18000 / scanned_entries=18060`，恰等于条目上限 → 无重复计数）；8 个并发到达后再推进到 done，终值仍然相同。遍历算法、预算语义、TTL 语义一字未动。
- **落地面**（`web/services/cache_scan.py`）：`snapshot()` 的锁只护「进度表 + 读数」，`_advance()` 移到锁外；`_Scan` 新增 `advancing` 标记保证同一路径同时只有一个推进者；TTL 过期重建从 `snapshot` 内提到 `_entry()`；片走完后的回读**以表里的当前条目为准** —— 若这一片期间被 `invalidate()`（清理/删除）作废，回包报「重新统计中」而不是删除前的旧数字。跨路径并行是顺带买到的，机制不是「分锁」。
- **前端契约已核**：SPA 本来就按 `state=scanning` 显示「浏览器数据统计中（已遍历 N 项）」并轮询 `/cache-scan`，且在扫完前禁止清理（`frontend/src/pages/Maintenance/Cleanup.tsx:158/192/213/513`）—— 迟到者回当前进度正是这套 UI 的既有形态，不新增语义。
- **量化兑现**：原文目标「96 并发 avg 1 822.8 → ≤400 ms」**本轮不发布**，须同一 JMX 复跑（§F 的 ≥4 h 长跑与 48/96 并发档一起补）。本轮能兑现的证据是合成树上的等待消失（p95 42.4 → 9.9 ms）与终值逐字段一致。原文「验证方式：并发两个不同路径」也照做了，但结论要反过来读：两路径并发的确也不再互相挡（现状 burst 总墙钟 81.6 / 83.6 ms ≈ 两棵树之和，改后 16.4 / 17.1 ms），只是**生产里不存在这个形状**。
- **回归**：`tests/test_maintenance_cache_scan.py` 原 13 例契约全保持，新增 4 例（迟到者不退回排队、`invalidate()` 不被在飞的片挡住、别的路径不被一个名额挡住、在飞的片不复活已作废进度）= 17 例；相邻 maintenance/slow_lane 族 66 例通过；全量后端 **2 810 passed / 0 failed**（18:58）
- **变异验证**：8 腿串行（`d2_mutation_run.py`，判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置、逐腿超时、无条件 `finally` 恢复），6 条应红全按预期翻红且**红的正是那条机制自己的判据**：时间片请回锁内 → 3 例（迟到者 / `invalidate` 被挡 / 在飞片复活已作废进度）、去掉同键名额 → 1 例（`slice_keys` 出现两条同路径片）、名额置了不清 → 3 例（第二片永远进不去，`test_budget_cut…_resumes_without_double_counting` 等卡在 `scanning`）、名额改成全局 → 1 例（别的目录被挡）、去掉 TTL 过期重建 → 1 例（终值停在旧数）、在飞那次不回读进度表 → 1 例（旧数字被回给用户）；「接错对象」腿（把名额守卫加在 `invalidate()` 上，动的是即将被丢弃的对象）与只改 docstring 的负对照均全绿；`cache_scan.py` 按 sha256 逐字节还原（`RESULT: 0 腿不成立 / 还原正常`）。**这一轮还撞出一个环境级事实**：首跑（含 `test_slow_lane.py` 的 4 文件口径）负对照腿翻红，红的是 D1 的 `test_cheap_route_not_blocked_by_slow_lane`，且同一轮**未加任何变异的基线**也红 —— 当时机器被别的会话负载占住，同一组维护侧用例从 22 s 涨到 198~315 s，那条用例的在途峰值取样窗口（`hold_ms` 30/60）与调度延迟同量级就会自抖。探针据此加 `--drop-slow-lane` 口径（D1 的用例不参与本条裁决，维护侧 3 文件 51 例判据一条不少），复跑得到上面的干净结论；现场留 `results/d2_mutation_run.log`（首跑）与 `results/d2_mutation_run2.log`（复跑），`*.log` 按 `.gitignore` 不入库、可由脚本原样复现。**这条同时给 D1 留了一条待办**：`test_cheap_route_not_blocked_by_slow_lane` 在外部负载下不稳，要把 `hold_ms` 抬到调度延迟之上（或改判「峰值 ≥ cap+1」为「峰值 ≥ cap+1 且不断言具体并发数」）才算稳判据 —— 属 D1 的判据加固，不在本条动。


### D3. 写侧：批量 UPSERT + 池/超时组合调优 — 优先级 **中**
**具体优化内容**：写阶梯显示 **8 并发即到顶（66.4 TPS）**，16 并发回落到 51.9（-22%）且 P95 已到基线 ×4.3；根因是 SQLite WAL 单写者 + `db_models.py:901-902` 的 `pool_size=20/max_overflow=10` 与 `busy_timeout=10 000`（`:912-913` `synchronous=NORMAL`、`PRAGMA` 设置）。做法：① 写路径合并为批量 UPSERT（`P0-3` 注释里已有同类先例）；② 把 `busy_timeout` 从 10 s 降到 2 s 并对「等锁」加指标暴露（等待时间比报错更危险，因为它把饱和藏起来）；③ 池大小不必再增大——20 > anyio 40 的一半，扩池只延长队列。
**证据**：§3.3 阶梯；全程 5xx ≈0、未出现 `database is locked`（服务端日志关键字命中 0 次），说明冲突被 `busy_timeout` 全部转成等待。
**量化预期收益**：16 并发写 TPS **51.9 → ≥66**（持平峰值档，不再回落），P95 **635.8 → ≤400 ms**；写侧长尾（`baseline_write` max 4 387 ms）收敛。
**验证方式**：`stress_write_t16` 复跑 + 单独统计写接口 avg，验证「吞吐不再回落」。

### D4. 大结果集流式化 — 优先级 **中**
**具体优化内容**：`web/routes/api_export.py:76-98` 虽返回 `StreamingResponse`，但先把最多 5 万行整份 CSV 拼进 `io.StringIO`（`:82-88`），再以 `iter([buf.getvalue()])`（`:91`）单元素「流式」发出 —— 等价于全内存拼接；`web/routes/api_logs.py:127-128` 先取 `min(limit*10, 1000)` 条事件，再在 Python 侧做子串/标签/stage 过滤（`:133-162`，注释自陈 SQLite 无原生 JSON 查询）。改为生成器逐块产出 + 服务端游标分批，过滤条件下推。
**证据**：`GET /api/db-admin/tables/items/export` 基线 35.0 → 长跑 **558.2 ms（P95 1 278.6 / P99 1 827.7）**；`GET /api/config/export` 107.5 → 288.4 ms；`GET /api/export` 16.2 → 134.4 ms。私有内存峰值 617.7 MB（RSS 414.5 MB）。
**量化预期收益**：导出类 P99 **1 827.7 → ≤600 ms**，长跑内存峰值下降（幅度随导出行数而定），并降低与 B4 的耦合。
**验证方式**：774 行副本 vs 5 万行副本各跑一次，比较 RSS 峰值差。

---

## E. 负载均衡方案改进 / 部署形态

### E1. 纯 Web 多 worker（+ 独立调度进程） — 优先级 **中**
**具体优化内容**：`backend/xianyu_hunter/__main__.py:280-284` 的 `--workers` 默认 **1**；`:309-312` 注释自陈「**已实测单 worker 51 → 多 worker 80 req/s**」「生产建议：纯 Web 多 worker（`--no-with-scheduler`）配合独立调度进程」。落地：Web 侧 `--workers N`（N≈物理核数），采集/浏览器留在单进程调度器。
**证据**：本次重组阶梯 4 并发即封顶（81.2 TPS 后持平），CPU avg 56.1%、峰值 269.1%（多核折算）—— **多核有余量而吞吐不涨，正是 GIL 单进程封顶**；与本次实测的 51→80 量级一致。
**量化预期收益**：读吞吐 **≈80 → ≥150 rps**（4 worker，按历史 1.57× 单/多 worker 增益外推）；96 并发 P99 **4 296.9 → ≤1 500 ms**。
**注意（必须先解决的副作用）**：`web/cache.py` 的缓存是**进程内**的，多 worker 会把命中率除以 worker 数，反而放大 B1 的击穿。因此 **B1/B3 必须先落地**，或与共享缓存（Redis / SQLite 快照表）一并设计。这也是本条列中优先级而非高的原因。

### E2. SSE 与 API 线程/连接资源隔离 — 优先级 **低**
**具体优化内容**：SSE（`web/sse_stream.py`，受 `_MAX_SSE_CONNECTIONS`/`_sse_gate` 门控）与短请求共用同一 worker 与线程池。生产建议：SSE 单独进程/端口，或至少把上限与告警暴露到 `/api/metrics`。
**证据**：本次一次旁路的 45 路 SSE 并发实验（17:28 打开、持续 25 s）落在 `baseline_light` 测量窗口内，是我造成的已知污染（见 REPORT §5 表 C）—— 反过来说明 SSE 与 API 的资源确实互相可见。
**量化预期收益**：长连接波动不再影响短请求 P95；可观测性提升（属稳定性收益，非本次直接量化项）。

### E3. 依赖不可用时不要用 5xx — 优先级 **中**
**具体优化内容**：`GET /api/tasks/{id}/links/live` 在采集器不可用时直接 503。改为 200 + `available: false` + 原因字段，或 501/503 但带明确 `code`，并在控制台侧降级展示。
**证据**：30 分钟长跑中 **795 次 503 全部来自该接口（100% 失败）**，构成本轮 0.74% 缺陷率的**全部**；扣掉它后真实缺陷率为 0.00%。
**量化预期收益**：长跑缺陷率 **0.74% → 0**；更重要的是**监控信噪比**——真实故障不再被固定 503 淹没。
**验证方式**：调度器关闭态跑 `soak_mixed`，缺陷率应为 0。

**落地状态（2026-11-03）**：✅ 已落地。选择的是「200 + 流内 error 事件」而不是「503 + 明确 code」：`live_links` 的两个依赖前置检查（`scheduler_off` / `browser_unavailable`）不再抛 `CodedHTTPException(503)`，改为返回 `StreamingResponse`，先发一条
`{"stage":"error","status":503,"code":"scheduler_off|browser_unavailable","detail":"…"}` 再结束。事件字段与原先 HTTP 错误体逐字段一致，前端 `dispatchSSEEvent` 本来就把它还原成 `makeHttpError(data.status || 502, {detail, code})`，`liveSearchError.ts` 又按 `code` 优先分类 —— 因此 UI 的提示文案、重新登录入口、浏览器不可用提示全部不变，只是 HTTP 层不再记 5xx。**为什么选 200 而不是保 503**：该状态是「部署时没开调度器 / 浏览器没起来」，不是这次请求的错误；长跑与线上监控按 HTTP 状态统计失败率时，一个恒定的 503 会把真实故障淹没（795 次占本轮 5xx 的 100%）。**没改的**：任务不存在（404）、任务无关键词（400）仍是真实 HTTP 错误；`/links/refresh` 的同类前置检查也不动（POST 返回 JSON，且不在本轮 503 来源里）。回归：`tests/test_task_links_units.py::test_live_links_endpoint` 改为断言「200 + `text/event-stream` + 单条 error 事件」，并保留 404/400 两条真实错误断言；变异验证 3 腿各自翻红（退回 503 → 1 例、事件丢 `code` → 1 例、媒体类型不再是 SSE → 1 例），负对照（改相邻 400 的 detail）全绿，文件按 sha256 还原。真实缺陷率 0 仍待同一 JMX 复跑确认（本目录 `results/` 仍是修复前基线）。

### E4. 尚未覆盖、需在下一轮补的方向
- **≥4 h 耐久**：本次实跑 30 min（墙钟 1 801.4 s），未达目标要求。改 `jmeter-config-v2.yml` 的 `soak_mixed.duration_override: 14400` 即可原样复跑；4 h 才能给出 RSS/句柄斜率的可靠判定（现在只能说「30 min 内未见泄漏」）。
- **CPU/磁盘 IO 逐秒曲线、网络带宽**：`monitor_sut.py` sidecar 只采进程级 RSS/私有内存/CPU%/句柄/线程；系统级磁盘与网络需另接 PerfMon，本报告未据此下任何结论。
- **真实负载画像**：本次「读:写 ≈ 9:1」是按控制台行为的近似，若有真实访问日志（nginx / uvicorn access log），应据此重定权重后再定容量红线。
- **外置压测机**：负载生成器与被测服务同机会让 P95/P99 虚高（技能判据）。当前所有尾延迟应读作**上界**；换机复跑后拐点位置应仍可比。

---

## F. 短期落地路线（建议顺序）

| 阶段 | 内容 | 预期累计效果 |
|---|---|---|
| **第 1 周（低风险高收益）** | ~~B1 单飞~~ ✅ + ~~B2 慢接口缓存~~（4/5 撤回，残留 config memo ✅）+ ~~C6 换 libyaml 后端~~ ✅（2026-11-01 落地读侧；写侧因 emoji 分歧撤回）+ ~~C1 双调用合并~~ ✅（2026-11-03 落地，实测 -43%；顺带把 `/status` 的同步走盘挪出事件循环）+ ~~C4 探测降本~~ ✅（2026-11-03 落地 ①③，关闭态每请求省 905 ms；②短 TTL 因需配对失效不落；「≤30 ms」作废 → 约 190~250 ms，待复跑）+ ~~A4 计数降本~~ ✅（2026-11-03 落地：完整重算 2 235/5 205 → 188/170 ms，归因改为 `.venv-build` pycache 漏排除 + 日志 3 次 stat + TTL 60→300；①② 计数近似不成立）+ ~~E3 503 语义~~ ✅（2026-11-03 落地：依赖不可用改 200 + 单条 SSE error 事件，UI 行为不变、HTTP 5xx 归零） | 混合场景 avg 200.5 → **≤90 ms**；消除 60 s 超时与 503 噪声；轻组 96 并发 P95 3 893 → **≤1 500 ms**；配置族 132.4 → **约 90 ms**（只有读侧能换 C 后端，dump 留原地；B2 原写的 ≤25 ms 不成立，见 §B2 第 5 条） |
| **第 2–4 周（结构性）** | C2 `/me` 去重 + ~~A2 事件下推~~（✅ 2026-11-04 已落：前缀→等值 + 去掉被丢弃的 COUNT；SQL 分页/聚合下推判定不落 —— 详见 OPTIMIZATIONS §A2）+ ~~D1 线程池隔离~~（✅ 2026-11-04 已落：16 条慢接口走 `CapacityLimiter(4)` 专用道，`slow_lane_tokens` 可关 —— 详见 OPTIMIZATIONS §D1 落地记录）+ ~~D2 解锁~~（✅ 2026-11-05 已落：时间片不再持锁 + 同键单推进者；「按路径分锁」判定不落 —— 三个调用点同一个目录 —— 详见 OPTIMIZATIONS §D2 落地记录） + ~~C3/YAML 单例~~（config 侧已随 B2 残留落地，剩 `api_templates`）+ ~~B4 缓存容量上限~~（✅ 2026-11-05 已落：复用 `LRUDict` + `cache_max_entries`，装配期套用；「RSS 爬升来自缓存」被实测推翻 —— 全量 GET 扫一遍只留 7 条 / 0.02 MB —— 详见 OPTIMIZATIONS §B4 落地记录）+ **≥4 h 长跑复测** | 48/96 并发缺陷率 2.03/3.17% → **<0.5%**；`/me` 全站收益；廉价接口高并发不再被拖到 2 s；RSS 斜率 +75.9 MB/h 得到定论（缓存 or 泄漏） |
| **季度内（容量形态）** | D3 写批量 + D4 流式 + A1 索引 + A5 FTS + E1 多 worker（须先完成 B1/B3） | 读吞吐 80 → **≥150 rps**；写侧 16 并发不再回落；内存峰值收敛；随数据增长的劣化斜率变缓 |

> 每条落地后请用同一份 JMX 复跑对比（`jmeter-config-v2.yml` + `aggregate.py`，其阶段时长已改为取引擎日志实测墙钟），并保留 jtl/jsonl 作为前后证据。
