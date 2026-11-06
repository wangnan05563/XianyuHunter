# 交接文档 · 全量 API 性能优化线（截至 2026-11-05）

> 用途：把「已完成 / 待办 / 纪律 / 环境 / 坑」一次性交给下一个会话，避免重复取证与重复踩坑。
> 本文只做索引与交接，**不新增结论**；所有数字的出处都在本目录的
> `CONCLUSION.md`（判断与排序）、`OPTIMIZATIONS.md`（20 条建议 + 逐条落地记录）、`REPORT.md`（实测口径）。
>
> 建议阅读顺序：§0 → §1 → §2 → §3/§4（纪律）→ §5（提交纪律）→ §6（复跑链路）。

---

## 0. 一句话现状

「第 1 周 + 第 2–4 周」里不依赖架构改造的条目**全部落地并本地提交、未推送**（B1/B5/B2 残留/C6/C1/C4/A4/E3/C2/A2/A2′/D1/D2/B4/D4/D3）。
剩下的工作：**② 结构性** —— E1 的核心（默认 worker 数 / 吞吐判据）与 E2 的隔离部分**均判定不落**，只落了运维护栏（E1：`--workers > 1` 与 `--with-scheduler` 互斥；E2：SSE 超限告警）；**③ 验证缺口**（`tracemalloc`、同一套 JMX 复跑、D1 判据在负载下自抖；**≥4 h 长跑已于 2026-11-06 跑完**，见 §2.3）。
唯一的**未定论风险**是 RSS 长跑爬升 —— B4 已证明它**不是**缓存值驻留；**4 h 长跑（2026-11-06）已把它推进到「非单调累积、能归还、4 h 内无可用性风险」**（8 段 P95 包络 +17.5 MB/h、96 次 >60 MB 归还、尾部斜率衰减），但末段仍在抬升，**不能写「已收敛」**。同轮还**否证了「4 h 无性能退化」**：TPS −42%、p95 +96%，**两条推断（tasks 行数、共享线程池传染）已被 A/B 实验双双证伪**（12 000 行下 options 单发仅 10.9 ms；重接口并发时廉价接口反而更快），**成因未定位**；「Python 客户端 GIL」也已用 JMeter 独立 JVM 排除（12k 行 + 12 并发下 p50 仅 164 ms），只剩 4 h 运行期状态（详见 REPORT §8.8 第 3/4 点）。**2026-11-07 已完成最终归因，并推翻了中间结论（REPORT §8.11）**：§8.10 曾据「重启前 303 / 重启后仍 3xx」判为「接口路径上的持久运行期状态」，**该判断是错的** —— 补测单发后 `tasks/options` 只要 **11 ms（基线 10.9 ms，接口本身根本没退化）**，SQL 直连 5.39 ms、走 `idx_tasks_user_created` 且无临时排序（库 19 019 行、`created_at` 19 019 个互不相同）。3xx/5xx 是**并发放大**：1→12 并发 p50 = **8 → 21 → 99 → 216 → 327 ms**、吞吐 **117 → 36 TPS（负伸缩）**，混合场景再被其余 134 个接口争用放大到 **562.93**（÷327 = 1.72）；同期 `config` 12 并发仍有 **326 TPS**。**所以不要为这条接口加索引或改数据层** —— 唯一对症的是降低单次 CPU（少字段 / 减 limit / 分页流式）或 E1 多进程。
**2026-11-06 已补跑「同一套 JMX」的 9 个阶段**（重读/轻读/写三组）：48/96 并发缺陷率 **3.17% → 0.00%**、轻组 96 并发 P95 **3 893.2 → 835.0**、写组 16 并发 TPS **51.9 → 257.5**、`cache-scan` 96 并发 avg **1 822.8 → 292.5**；原始样本归档在 `results/rerun_*.jsonl.gz`，结论见 REPORT §8。复跑顺带发现一个新瓶颈（`prices/category-comparison` 16 并发 2 433.7 ms），已记入 §2.1。

---

## 1. 已完成条目与提交

| 条目 | 提交 | 一句话结论（详情见 OPTIMIZATIONS 对应「落地记录」） |
|---|---|---|
| B1 缓存单飞 | `8db8a562` | 冷/过期 miss 合并重算；全场最大杠杆 |
| B5 SWR 后台刷新 | `ee8f34d7` | 同步分支真起守护线程、异步分支 `create_task`，两条都并入单飞 |
| B2 残留（config memo） | `90055800` | 原建议 5 条撤回 4 条；只留 `_load_yaml` 文件指纹 memo + 写盘站点配对作废 |
| C6 YAML 读侧 | `4a2bc1a0` | `CSafeLoader`：解析 47.7 → 6.5 ms；**写侧不换**（C dumper 把 emoji 写成 `\U0001F4F7`，产物不再逐字节相同） |
| C1 `_dir_size` 合并 | `2d175392` | 实测 −43%（不是 50%）；`/status` 同步走盘移出事件循环 |
| C4 探测降本 | `3e8de84d` | 只落 ①③：CDP 探测 timeout 1.0→0.1 s + Cookie 判定下推；②短 TTL 需配对失效故不落；「≤30 ms」作废 → 约 190~250 ms |
| A4 status 计数 | `044b1068` | 完整重算 2 235 / 5 205 → 188 / 170 ms；**归因被推翻**：6 张表 COUNT 每张 ~0.1 ms，真凶是漏排除的 `.venv-build/` pycache |
| E3 503 语义 | `fa26395e` | `links/live` 依赖不可用改 200 + 流内 error 事件；长跑 795 次 5xx（占 100%）归零 |
| C2 `/auth/me` | `0143779c` | 每请求 SQL 2 → 1；②③④ 判定不落；HTTP avg 方向在本机噪声下不可判定 |
| A2 `eval.*` 下推 | `dc2eb6ce` | **方向与原文相反**：前缀 `LIKE` → 等值；主人态端点中位 574.7/586.5 → 238.1/228.6 ms；压测身份读到 0 行，195.9 ms 里不含这笔成本 |
| D1 慢接口专用道 | `35fc236e` | 16 条实测最重的同步接口走 `CapacityLimiter(4)`；廉价接口 p50 3 509.6/1 153.9 → 34.3/59.8 ms；`queue_p95` 974.4 → 6.7 ms 而 `work_p50` 恒 ~4 ms |
| D2 cache_scan 解锁 | `8cfa9e9b` | **不是原文的「按路径分锁」**（三个调用点同一个目录）；改为「时间片不持锁 + 同键单推进者」；同路径 8 并发 p95 42.4/36.7 → 9.9/2.8 ms，扫完墙钟不变 |
| B4 缓存容量上限 | `63c2bee2` | 复用 `infra/lru.LRUDict` + `cache_max_entries=2000`，`create_app()` 装配期套用；**RSS 归因被推翻**（全量 GET 扫 106 个 label 只留 7 条 / 23 620 B） |
| D4 真流式导出 | `a6f05d48` | 3 处导出改按块产出 / 游标取数：5 万行 items 峰值 38.27→2.52 MB、40 MB 日志 190.48→9.29 MB、18 403 行 events 导出 2 433→285 ms；**原文「db-admin 也是全内存拼接」被推翻**（它本来就流式，慢在逐行 yield × 线程池往返） |
| D3 写侧批量 | `550e299b` | `recompute` 的 payload 改单事务批量写：500 条 **137.5 → 25.4 ms（5.41×）**、UPDATE 500 → 1 条；**写组 TPS 判据不成立**（61.6% 样本是夹具 4xx、真写单发全 <30 ms 且每请求 1 条），UPSERT 分支 / `busy_timeout` 调小 / 扩池判定不落 |
| E1 多 worker（护栏） | `6e1930a1` | `--workers > 1` 与 `--with-scheduler` 改为互斥、直接 `Exit(2)`（此前会各起一套浏览器/调度器）；默认 worker 数与「80 → ≥150 rps」判定不落（缓存按 worker 分裂；~~本机无原生 JMeter~~ **2026-11-06 纠正：JMeter 5.6.3 在 `D:\code\Jmeter\`，只是不在 PATH，缺的是复跑不是工具**） |
| E2 SSE 告警 | `6e1930a1` | 连接超限时补 WARNING（此前服务端完全无记录）；`/api/metrics` 新端点、单独进程、塞进 `CapacityLimiter` 三条判定不落 |
| C3 残余 模板文件 memo | cb6cd5a8 | 指纹 memo + 写盘配对作废；进程内处理器 2.23 → 0.53~0.58 ms，deepcopy 方案被实测否掉（1.14 ms ≈ 重新 `json.load`）→ 返回浅拷贝；详见 OPTIMIZATIONS §C3 |
| A2′ 品牌归一化 | `88e614c0` | 正则预编译 + 别名表压缩结果预计算；`_compact_text` 73 251 → 2 367 次、`_enrich_eval_record` 706.9 → 256.3 ms；**请求内 memo 被实测推翻**（377 条只有 363 个唯一 title，memo 版慢 15%） |
| A1 过滤列索引 | `7665e83e` | 5 条候选落 3 条：`items.price` 15.09 → 7.17 ms、`evaluations.risk_level` 变覆盖索引 3.45 → 2.17 ms、`orders(type, created_at)` 复合 4.08 → 0.50 ms；`price_history.user_id` / `task_links.updated_at` 实测无收益 |
| C5 市场统计批量 | `a2cb18f5` | 20 个任务 40 条 SQL / 17.1 ms → 2 条 / 4.7 ms；与行情页共用 `_summarize_price_stats` |

历史基线证据在 `results/`（`.jsonl.gz` 原始样本 + 聚合 JSON，均已入库）；`results/*.log` 按 `.gitignore` 不入库，需要时由探针脚本原样复现。

---

## 2. 剩余工作

### 2.1 已取证、改动小、可直接落地（建议先做这一档）

| 条目 | 落点 | 判据 / 预期 | 已知前提与风险 |
|---|---|---|---|
| ~~**A2′** enrich 品牌归一化~~ ✅ **2026-11-06 已落地** | `normalize_display_brand → _compact_text` | `_compact_text` 73 251 次 / 401.1 ms → **2 367 次 / 11.7 ms**；`_enrich_eval_record` 706.9 → **256.3 ms**（占列表 60.8% → 39.5%）；端点中位 148.2 → **121.5 ms** | **「请求内 memo」被微基准推翻**（377 条记录只有 363 个唯一 title，memo 版慢 15%）→ 只落「预编译 pattern + 别名表压缩结果预计算」；详见 OPTIMIZATIONS §A2′ |
| ~~**C3 残余** `api_templates`~~ ✅ **2026-11-06 已落地** | `web/routes/api_templates.py` | 进程内处理器 **2.23 → 0.53~0.58 ms（−76%）**；命中路径残余是两次 `os.stat`（0.28 ms/次） | 指纹 memo + `_save_user` 写盘配对作废 + 读取代次；deepcopy 被实测否掉（1.14 ms ≈ 重新 `json.load`），返回浅拷贝；详见 OPTIMIZATIONS §C3 落地记录 |
| ~~**B3 预热扩展**~~ ❌ **2026-11-06 判定不落** | `web/startup.py:799-828` `_warm_stats_cache` | 现有 5 个 key 与请求侧 `key_fn` 一一对应（有效）；「首页聚合纳入预热」无落点 | `/api/evaluations/distribution` 与 `/api/notifications/unread_count` **都没有 `@cached_ttl`**，预热暖不到；1.1 s 的接口是 `anticrawl/strategy`（C4 已降到约 190~250 ms，残余是 timeout 预算本身）；「调度周期后重暖」会放大陈旧风险。详见 OPTIMIZATIONS §B3 判定记录 |
| ~~**A1 补 5 个过滤列索引**~~ ✅ **2026-11-06 已落地（3/5）** | `infra/db_models.py` | `items.price` 15.09→7.17 ms、`evaluations.risk_level` 覆盖索引 3.45→2.17 ms、`orders.type` 改复合 `(type, created_at)` 4.08→0.50 ms | `price_history.user_id` 与 `task_links.updated_at` 实测无收益，判定不落；接口级 P95 待同一 JMX 复跑。详见 OPTIMIZATIONS §A1 |
| ~~**A5 日志检索**~~ ❌ **2026-11-06 判定不落** | `infra/repo_error_logs.py:63-65` | 原文两半都无落点 | `error_logs` 只有 2 112 行、`%LIKE%` 1.8~10.0 ms，前缀对照反而不快（`LIKE 'api%'` 29.7 ms）；`logs/search` 的 `q` 是 Python 子串匹配、没有 SQL LIKE，下推后延迟 33.7 → 37.7 ms（那是口径改进不是性能收益）。详见 OPTIMIZATIONS §A5 |
| ~~**C5 评估列表去 N+1**~~ ✅ **2026-11-06 已落地** | `web/routes/evaluations_list.py` + `price_dashboard.py` | 20 个任务：40 条 SQL / 17.1 ms → **2 条 / 4.7 ms（−73%）**，SQL 条数与任务数解耦 | 批量取数 + 与行情页共用 `_summarize_price_stats`；接口级 P99 待同一 JMX 复跑。详见 OPTIMIZATIONS §C5 |

| ~~**`prices/category-comparison` 并发爆尾**~~ ✅ **2026-11-06 已落地缓存**（复跑新发现，不在原清单） | `web/routes/price_dashboard.py` `category_comparison` | 单发 24.6 ms，**16 并发 avg 2 433.7 ms / P99 3 299.2、64 并发 3 436.4 ms** → 已挂 `@cached_ttl(60)` + B1 单飞 | 每次请求都全表扫 items（`range_days>0` 再扫一遍），16 并发 = 16 次扫抢同一条 GIL；**A1 索引治不了**（64 并发只比 16 并发再慢 1.4×，不是排队，D1 专用道也不对症）。落地形态：`_compute_category_comparison` 抽函数 + `key_fn=…cmp:{sort_by}:{order}:{limit}:{range_days}`，**归一化必须留在路由层**（否则 `sort_by=bogus` 各占一个 key；变异 L4 实测去掉归一化直接 `KeyError`）。连带修了 `tests/test_price_dashboard.py` 的 fixture（每例新库 → 必须 `invalidate_stats_cache()`，否则串用例）。回归 5 例 + 变异 4 红 2 绿；**落地后实测**（只跑 t16/t64）：该接口 avg **2 433.7 → 103.2 / 3 436.4 → 372.4 ms**、P99 **3 299.2 → 325.3 / 5 268.7 → 712.4**，整组 P99 **2 637.4 / 3 640.1 → 251.1 / 720.8**；整组 p50 反而升（33.9→79.5），缓存只占 2.2% 样本，判机器差异。样本 `results/rerun_cacheverify_*` |

### 2.2 结构性条目（需先补取证，或有硬约束）

| 条目 | 落点 | 预期 | 必须先做的事 / 风险 |
|---|---|---|---|
| ~~**D3 写侧批量 + 池/超时**~~ ✅ **2026-11-06 已落地一半** | `infra/repo_events.py` + `web/routes/evaluations_recompute.py` | `recompute` 的 payload 改单事务批量写：500 条 **137.5 → 25.4 ms（5.41×）**、UPDATE 500 → 1 条 | **写组 TPS 判据不成立**：61.6% 样本是夹具 4xx（不写库），真写接口单发全 <30 ms 且每请求只写 1 条，跨请求批量无处施力。UPSERT 分支、`busy_timeout` 调小、扩池均判定不落（见 OPTIMIZATIONS §D3） |
| ~~**D4 真流式导出**~~ ✅ **2026-11-06 已落地** | `web/routes/api_export.py` / `api_logs.py` / `api_db_admin.py` | 3 处导出改按块产出或游标取数：50 000 行 items 峰值 38.27 → **2.52 MB**、40 MB 日志峰值 190.48 → **9.29 MB**、18 403 行 events 导出 2 433 → **285 ms** | **原文「`db-admin` 也是全内存拼 CSV」被实测推翻**：它本来就是流式，慢在逐行 yield × 线程池往返（1.8 万行 = 2.4 s）。判据 `d4_export_probe.py` 用 774 行 / 5 万行、2 MB / 40 MB 两档比峰值；`api_logs` 的过滤条件下推仍不落（A5 已证）。详见 OPTIMIZATIONS §D4 |
| ~~**E1 多 worker**~~ ⚠️ **2026-11-06 只落运维护栏** | `backend/xianyu_hunter/__main__.py:280-284`（`--workers` 默认 1）、`:309-312`（注释自陈 51→80 req/s） | `--workers > 1` 与 `--with-scheduler`（默认 True）改为互斥、直接 `Exit(2)`；`--no-with-scheduler` 放行并原样透传 | 默认 worker 数与「80 → ≥150 rps」判定不落：进程内 TTL 缓存会按 worker 分裂（B3 判定不落后无共享缓存兜底），且本机无原生 JMeter。见 OPTIMIZATIONS §E1 |
| ~~**E2 SSE 隔离**~~ ⚠️ **2026-11-06 只落告警** | `web/sse_stream.py`（`_MAX_SSE_CONNECTIONS` / `_sse_gate`） | 超限时补 WARNING（此前服务端完全无记录），HTTP 语义不变 | 隔离方案判定不落：`/api/metrics` 不存在、新建会改 openapi 快照（D1 判据）；SSE 单独进程属部署变更；塞进 `CapacityLimiter` 会让长连接永久占槽。见 OPTIMIZATIONS §E2 |

### 2.3 验证缺口（不写代码，但决定前面条目能不能「发布」）

| 缺口 | 具体内容 | 做法 |
|---|---|---|
| ~~**同一套 JMX 复跑**~~ ✅ **2026-11-06 已跑 9 个阶段** | D1/D2/D4/D3/E1 的接口级数字此前都未发布；`results/per_endpoint.json` 等曾是优化前基线 | 已用入库的 `jmeter-config-v2.yml`（只改路径）跑出重读/轻读/写三组共 9 阶段，原始样本按 `results/rerun_*.jsonl.gz` 归档。**核心结果**：48/96 缺陷率 2.03%/3.17% → **0.00%**；轻组 96 并发 P95 3 893.2 → **835.0**、P99 4 296.9 → **1 838.9**；写组 16 并发 TPS 51.9 → **257.5**；`cache-scan` 96 并发 avg 1 822.8 → **292.5**。**仍未覆盖**：重读组并发档基线对照、SUT 资源序列（引擎无 `resource_monitor`；30 min 与 4 h 长跑已由 §8.7 / §8.8 覆盖）。见 REPORT §8 |
| ~~**≥4 h 长跑**~~ ✅ **2026-11-06 已跑满 4 h**（`tracemalloc` 已判不可用） | **内存**：判据成立 —— 8 段 P95 包络 366.4 → 430.8 MB（**+17.5 MB/h**，单调递增）、全局 +20.4 → 后 1/2 +14.0 → 后 1/4 +11.6 MB/h（**有衰减**）、最大单点跌幅 **162.4 MB**、跌超 60 MB **96 次**、按吞吐归一化 **0.19 MB/(TPS·h)**。**性能否证**：TPS **105.8**（30 min 183.1，−42%）、p50/p95/p99 **79.91 / 283.51 / 703.17**（30 min 50.93 / 144.35 / 314.35）；5xx **0**、`000` 24 条（0.002%）。**已否掉的归因**：SQL 索引（最慢的 `tasks/options` SQL 仅 2.24 ms，加 `(user_id, created_at)` 索引 2.18 ms，**判定不落**）。**两条推断均已被 A/B 证伪**（2026-11-06 补测）：把库灌到 **12 000 行**后 `tasks/options` 单发仅 **10.9 ms**、8 并发 p95 255.5 ms；「重接口 8 并发 + 廉价 1 并发」下廉价接口 p95 **60.2 ms**，反而低于对照的 **96.2 ms** → `tasks` 行数与共享池传染都**不成立**（`results/rerun_ab_pool_probe.json`）。**成因未定位**；「Python 客户端 GIL」也已排除 —— JMeter 独立 JVM、12k 行、12 并发下 p50 仅 **164 ms**（`results/rerun_single_endpoint_jmeter.json`），只剩 4 h 运行期累积的服务端状态或当时系统状态。**下一轮唯一能分离的实验**：4 h 跑完立刻在同一 SUT 上用 JMeter 复测该接口（仍慢=服务端累积，恢复=系统/引擎侧）。**保留**：末段 P95 仍在抬升（+13.4）且 `max` 破 500 MB，**不能写「已收敛」**；同机客户端累积 152 万样本的自身劣化**无法排除** | 30 min 干净复跑：183.1 TPS / RSS `202.5 → 387.9 MB`（+219.7 MB/h），归一化 **1.20 vs 基线 1.27 MB/(TPS·h)**；同场景 Python 堆只增 10 MB 量级；`cache_size` 单变量对照**只给方向不给倍数**（探针污染，见 §7） | **判据已订正**：原计划「8 段峰值不破 427.7」被本轮数据证伪（第 4 段即 449.3，且是单点尖刺，相邻采样 418.5/419.6）→ 改用「段 P95 包络斜率 + 尾部斜率衰减 + 回收幅度/次数」，已写进 `rerun_archive_soak4h.py`（并加了「窗口 <100 样本不计」，防采样器多跑 120 s 产生半截窗口）。**下一轮必须补**：外置压测机；以及「4 h 跑完立刻用 JMeter 复测同一接口」以分离服务端运行期累积与系统状态。**`tracemalloc` 不要再试**（两次都让 8011 六分钟不监听、WS 超 960 MB）；**`gc` 探针也不要配在斜率测量上**；**下次长跑必须额外保留「按 label 聚合的 p50/p95/count」**（<100 KB）—— 本轮 152 万行逐请求样本按体积未入库，导致「哪些接口变慢了」无法复算，只能给整体分位数。**2026-11-06 已补做「跑完不重启立即复测」这一实验（REPORT §8.10）**：`tasks/options` 12 并发 p50 **164（冷启动基线）→ 303（冻结恢复后首测）→ 316（5 min 后热态复测）**，同期 `config` p50 **48 / 41**（基线 59.2，反而更快、0.81x/0.69x）；复测时库内 `tasks` 仅 **3 443 行**（基线是 12 000 行）。**判读（已由 REPORT §8.11 订正）**：那 3xx 不是持久状态 —— 单发只要 **11 ms**（基线 10.9 ms），是 12 并发下 **GIL 串行化**（1→12 并发 p50 8→327 ms、吞吐 117→36 TPS），混合场景再叠加 **1.72 倍**竞品争用（562.93÷327）。**不要为这条接口加索引或改数据层。****该轮负载只跑了 1.28 h 就被 Modern Standby 打断**（Kernel-Power 172：`17:52:02` → `21:31:13`，共 3 h 39 min），故该轮 `wall_sec` 17 772.9 与 TPS 23.7 两个字段不可用（按真实负载 4 628 s 算约 **91.1 TPS**）；4 h 重跑已于同日 21:50 启动，并加 `SetThreadExecutionState` + `monitor-timeout-ac 0` 防挂起 |
| ~~**D1 判据在外部负载下自抖**~~ ✅ **2026-11-06 已硬化** | `tests/test_slow_lane.py::test_cheap_route_not_blocked_by_slow_lane` 的 `hold_ms` 30/60 取样窗口与调度延迟同量级；机器被别的会话占住时自红（同一轮**未加变异的基线**也红，51 例从 22 s → 198 s） | 已把 hold 抬到 **150/300 ms**，并加一条**相对**判据（`cheap["finished_at"] < slow["finished_at"]`，不依赖绝对耗时）；改完重跑 `d1_mutation_run.py`：**12 腿全红 + 负对照全绿（ALL LEGS OK）**，`test_slow_lane.py` 15 例 6.8 s 通过 |
| ~~**REPORT 复现链口径**~~ ✅ **2026-11-06 已订正** | `REPORT.md` 的复现链（行号已变为 214）已把 `setup_sandbox.py` / `emit_config.py` / `aggregate.py`（`perf/2026-10-31-full-api/`）、`gen_jmx.py`（`scripts/perf/`）、`python_replay_engine.py`（技能目录，**不在本仓库**）三处真实位置写清，并注明 `D:\tmp\xh-perf` 跑完应删 | 无需再订正 |
| ~~**系统级 I/O / 带宽水位**~~ ✅ **2026-11-07 已补本机侧** | 此前只采 SUT 进程侧 RSS/私有内存/CPU/句柄 | 新增 `rerun_sample_sysio.py`（psutil，5 s 差分）：30 min 混合负载下**磁盘写 p50 2.29 MB/s / 538 IOPS、读 p50 0、网络 p95 32 KB/s、上下文切换 72k/s** —— 磁盘与网络都有大量裕量，**不是瓶颈**（REPORT §8.12）。**仍缺**：外置压测机侧的真实链路带宽与跨机 RTT |
| ~~**服务端真实极限吞吐**~~ ✅ **2026-11-06 已补测（量级）** | `reads_light` 64 接口、每档 120 s：**16 并发 75.6 TPS（p50 168 / p95 497）→ 48 72.1 → 96 96.7 → 192 99.4 TPS（p95 3 540 / p99 4 857）**。16→192 吞吐仅 **1.31×**、p95 **7.1×** → **拐点 16 并发、天花板约 100 TPS**；四档 18% 错误全是 404/422，**5xx = 0** | `rerun_jmx_for_jmeter.py` 派生（**Host 必须改 127.0.0.1**，否则 IPv6 先试 `::1` 全失败）→ `rerun_jmeter_ladder.py reads_light 16 48 96 192 --duration 120`（已内置 `-Xmx512m`）。**不可与 §8.2 回放引擎直接比**：本轮库是 4 h 后的 12 163 行 tasks、SUT 冷启动、sampler 权重/连接复用不同。仍需：分布式压测机、干净库 + 冷/热两态对照 |

---

## 3. 每条落地的固定配方（不要跳步）

1. **取证探针**：优先真实数据；不可得时用合成数据，但**预算/比例必须复刻生产**（D2 的教训：切片预算写死 → 片子远短于树 → 锁持有根本看不见 → 得出「并发无差别」的假结论；正确做法是「全扫耗时 ÷ 20」）。
2. **最小改动落地**：只改能兑现判据的那一处；不做顺手重构、不加兼容层。
3. **回归测试**：先写会失败的判据（含「装配期真的接上了」这种外部不可观测的接线），再使实现通过。
4. **全量**：`-o "addopts=" -p no:cacheprovider`；不绿先查基线抖动（见 §7），不要靠改实现去迁就环境。
5. **变异验证**：见 §4，逐腿串行，含「接错对象」腿与只改 docstring 的负对照。
6. **文档四处落地**：`OPTIMIZATIONS`（逐条「落地记录」）/ `CONCLUSION`（排序与 Top-3 措辞）/ `REPORT`（工具表与判读）/ `CHANGELOG`。
   **凡实测推翻原文归因，必须写「被推翻」而不是把新数字贴旧标题**（B4、A2、A4、C2、C4、D1、D2 都这么处理）。
7. **本地提交、不推送**：本地 `main` 领先 origin 且含他人的提交，推送会连带别人未完成的工作。

---

## 4. 变异验证纪律（写死在 `*_mutation_run.py` 里，别凭记忆）

- **逐条串行**，绝不并发（并发会把变异互相覆盖，也会让时序用例互扰）。
- **基线必须全绿**，否则整轮无效；基线红先修基线。
- **只认 `pytest exit == 1` 为「红」**；exit 2/3/4/5（收集错误、内部错、超时）一律判 **INVALID** 并中止整轮。
  （B4 第一轮 M01「退回普通 dict」就栽在这里：4 个收集错误 → exit=2，必须重写成「写入绕过 LRU」的合法变异。）
- **锚点唯一**（`count == 1`），注入前 `ast.parse` 校验变异文本。
- 每条腿**无条件按原始字节恢复**，结束逐文件 sha256 核对；CRLF 文件的多行锚点先做换行归一化。
- 必含两条**应为绿**的腿：①「接错对象」（改了个看似相关其实不管用的地方）；②只改 docstring 措辞。缺了这两条，全红只能说明脚本自嗨。
- 摘要要保留 `FAILED` / `ERROR` 与 `E   ` 断言行，否则只剩 「N failed」不知为何红。
- **大文本断言必须先比长度再定位首个差异**：1 MB 级 `assert got == expected` 失败时 pytest 会渲染整份 diff，单腿从 9 s 拖到 9 分钟（D4 实测踩到，`tests/test_export.py::_assert_same_text` 是正确写法）。
- **变异脚本自己写日志文件**，不要靠 PowerShell 管道落盘（见 §7）。
- `tests/test_slow_lane.py`（时序用例）在机器有外部负载时会自抖：D2 探针为此提供 `--drop-slow-lane`（摘掉它，维护侧 3 文件 51 例判据一条不少，文档里要写明这一点）；B4 的跑批（capacity / swr / single_flight / order_stats）本身不含它，但仍可能被同一波环境抖动波及（见 §7）。

---

## 5. 仓库现状与提交纪律（**最容易出事的一段**）

### 5.1 工作树里有一批**不属于性能线**的在途改动

仓库改名线（`wangnan05563/xianyu-hunter` → `wangnan05563/XianyuHunter`）留下的脏文件，**不要提交、不要还原、不要顺手格式化**：

```
 M .gitignore                      M pyproject.toml
 M CHANGELOG.md                    M requirements-lock.txt
 M README.md                       M scripts/bump_version.py
 M backend/xianyu_hunter/__init__.py
 M backend/xianyu_hunter/web/app.py            ← 仅 app.py:488 的 contact URL 一行
 M backend/xianyu_hunter/web/routes/api_about.py
 M cleanup-config.yaml
 M docs/...（关于页操作手册、详细设计、VERSIONING、目录结构、概要设计文档）
 M frontend/src/pages/About/AboutMenuList.tsx(+test)
 M scripts/config.publish.json
 M tests/test_about.py
 M tests/test_version.py
?? assets/github-social-preview.png
?? backend/xianyu_hunter/data/
?? data/
?? docs/系统架构设计文档.md
```

`backend/xianyu_hunter/data/`、`data/`、`assets/github-social-preview.png` 是随包资源/数据，别删、别加进提交。

### 5.2 只提交自己那段 hunk 的做法

`app.py` 与 `CHANGELOG.md` 同时含「我的性能改动」和「别人的改名改动」，不能整文件 `git add`。已验证可用的方法：

1. `git diff -- <file>` 导出，人工删掉不属于自己的 hunk，存成 patch；
2. `git apply --cached <patch>`（只进索引，不动工作树）；
3. `git show :<file>` / `git diff --cached -- <file>` 复核暂存内容只有自己那段；
4. `git commit` 后 `git show HEAD:<file> | grep <别人的标记>` 确认没被带进去；
5. 提交后工作树里应仍保留别人的改动（`git status` 里该文件仍是 ` M`）。

B4 就是这么把 `app.py:488` 的 contact URL 挡在 `63c2bee2` 之外的。

### 5.3 其他

- **不推送**。需要推送时先确认本地 `main` 与 origin 的关系。
- 新脚本/文档里**不要写死数据主人的账号 ID**（形如手机号）；需要身份时动态解析并脱敏。
- 探针不读任何 cookie 文件。
- `results/*.log` 被 `.gitignore` 忽略；需要留证就把结构化结果写成 `results/*.json`（入库），日志只作现场参考。
- **多轮产物文件名必须逐轮唯一**（`_r2`、`_r3`…），固定名会静默覆盖，最后只剩一轮。

---

## 6. 压测复现链路（现在的真实状态）

```
perf/2026-10-31-full-api/
  setup_sandbox.py        建 D:\tmp\xh-perf\sandbox（独立端口 8011 + DB 副本 + 关调度器/穿透），dump openapi.json
  build_scenarios.py      按 OpenAPI + tier-rules.json 分层 → coverage_matrix.json / probe_plan.json / jmeter-config.yml
  patch_and_v2.py         把「工具口径污染」的端点移出负载轮 → groups.json / mixed.json + jmeter-config-v2.yml
  emit_config.py          读 D:\tmp\xh-perf\{groups,mixed,sut.pid} → 重写 jmeter-config.yml（含 token/端口）
  jmeter-config.yml       基线配置（scenarios + phases，已入库）
  jmeter-config-v2.yml    第二轮配置（阶段时长改为引擎实测墙钟，已入库）
  jmx/*.jmx               4 份原生 JMX（mixed_biz / reads_heavy / reads_light / writes_idem），已入库
  aggregate.py            读 results/*.jsonl(.gz) + sut_resource.csv → 接口级基线/拐点/长跑退化表（就地可复算）
```

- 回放引擎在**技能目录**，不在仓库：`C:\Users\hspcadmin\.qoder-cn\skills\jmeter-performance-test\scripts\python_replay_engine.py`（与基线同构的复跑用它）。**原生 JMeter 在本机可用**：`D:\code\Jmeter\apache-jmeter-5.6.3`（不在 PATH），用 `java -jar bin\ApacheJMeter.jar`（`jmeter.bat` 结尾会 `pause`，别用）；先跑 `rerun_jmx_for_jmeter.py` 派生带鉴权的 jmx。
- `D:\tmp\xh-perf` 是**临时工作区，跑完应删除**：沙箱里有 `.env` 的完整副本（含明文 token）。若中途崩溃留下目录，删掉并按需轮换 token。
- 沙箱探针**不要用固定目录名无锁并发跑**：D1 的 `d1_lane_arms_c.json` 整轮 `server not ready` 就是两个探针互相 `rmtree` 同一目录的事故现场；临时目录带 PID 后缀。
- `aggregate.py` 支持 `XH_PERF_DIR` 指向临时工作区；仓库内归档是 gzip，两处都能就地复算。

---

## 7. 已知坑（上一轮真实踩过的）

| 坑 | 表现 | 处置 |
|---|---|---|
| 后台任务 exit code 不可信 | 重定向到不存在的目录，命令失败但通知报 exit 0 | 后台跑完**读日志尾部**确认；不用退出码代替证据 |
| 并发下的「全量不绿」 | B4 那轮 7 failed / 46:12（正常 17:28）；同一轮基线复跑也红 | 先看**墙钟**：明显变长就是机器被外部负载占住（其它会话的 uvicorn/JMeter/MCP、pip 安装）；隔离子集重跑，通过则判环境抖动并**如实记录**，不要为它改实现 |
| 按用例 vs 按文件的耗时 | pytest 的 `--durations` 文件级汇总会掩盖单条用例超时 | 归因阶梯：比单条用例耗时 → 放宽只是 CLI 侧的参数 → 隔离 → 未改动复跑 |
| `from xianyu_hunter.web import app` 拿到的是 FastAPI 实例 | `AttributeError: 'FastAPI' object has no attribute 'create_app'` | 用 `from xianyu_hunter.web.app import create_app` |
| 日志按 GBK 落盘 | 用 UTF-8 读成乱码/二进制 | 用 `encoding='gbk'` 读日志 |
| 不能用 `python3` | 本机是坏桩（exit 49） | 一律 `.venv/Scripts/python.exe` |
| `taskkill /IM node.exe` 之类 | 会杀掉别的会话的进程 | 按 PID，且先看命令行确认归属 |
| pytest 日志里 `N passed` 混着 warning 行 | 摘要抓错行 | `-q --no-header -rf`，摘要只留 `passed/failed/error/FAILED/ERROR/E   ` |
| 长命令套 `grep` 管道 | 吞退出码 + 丢证据 | 先落文件，再用 Read/Grep 看 |
| `TMP` 没跟 `TEMP` 一起改到 D 盘 | 临时 SQLite 仍写 C 盘，报 `database or disk is full`（C 盘 0 可用） | 跑 pytest 前 **`TEMP` 和 `TMP` 都要设**到 D 盘，且目录先存在（D4 实测踩到） |
| PowerShell 管道缓冲外部进程输出 | `... \| Tee-Object log` 时输出要等进程退出才落盘，中途被杀只剩半份进度 | 让脚本自己开文件写日志（`d4_mutation_run.py` 就这么做） |
| 随机性用例在全量里偶发翻红 | `test_anticrawl_integration.py::TestFreqDisguiseIntegration::test_request_pattern_not_robot_like` 断言请求间隔分布 `< 0.3`，机器负载变化时偶发 `0.353`；隔离复跑 3 次全绿 | 先隔离复跑；全绿即判环境抖动，不要为它改随机阈值 |
| 回放引擎没有资源采样实现 | 配置里写了 `resource_monitor`（含 `output_csv`），跑完却没有 `sut_resource.csv` | 当前技能版 `python_replay_engine.py` 源码里搜不到该配置键的处理 —— 是工具能力缺失，不是配置写错。内存判据改用进程内 `rerun_sample_resource.py`（psutil 每 5 s 一条）或 `rerun_run_sut_heap.py`（堆口径） |
| **`tracemalloc` 在本应用上会让服务起不来** | 2026-11-06 两次实测：`start(25)` → 6 分钟不监听 8011、WS 966 MB；`start(10)` + 延迟 90 s 再开 → t=207 s 时 traced 只 76 MB 而进程 WS 已 1 034 MB、8011 仍未监听 | 帧追踪把 import 阶段的每次分配都按帧回溯，启动路径从秒级变成分钟级。**不要再试**；判「Python 堆在不在涨」用 `sys.getallocatedblocks()` + `gc.get_objects()` 按类型分组（`rerun_run_sut_heap.py`，O(1)/O(存活对象)、不碰分配快路径），判页缓存用 `cache_size` 单变量对照（A/B 两组同夹具各跑 5 min，`rerun_analyze_heap.py` 汇总） |
| **`gc` 探针本身会抬高 RSS 斜率** | 2026-11-06：带探针的 A 组与**无探针**的 30 min 轮同 t 比高出 **+31 / +124 / +221 MB**（t=180/240/300），差值随时间增长 —— 探针污染的是斜率不是基线 | 每 60 s 的 `gc.get_objects()` 要临时分配「40 万引用」的 list，反复冲击 pymalloc arena。**测 RSS 斜率时不能用它**；它只配同一次测量内的 Python 堆对比。4 h 长跑用 `run_sut.py`（无探针）+ `rerun_sample_resource.py`，归档用 `rerun_archive_soak4h.py` |
| **JMeter 的 `HeaderManager` guiclass 写错会静默丢掉整个元素** | 2026-11-06：派生 jmx 里用 `guiclass="HeaderManagerGui"`，JMeter 5.6.3 解析成功、日志零 ERROR，但请求不带 `Authorization` —— 业务全 401（91.2%），`sentBytes` 与无 header 时完全相同（123） | 必须是 `guiclass="HeaderPanel"`（值抄自 `bin/templates/build-adv-web-test-plan.jmx`）。判定手段：起一个本地回显请求头的服务（8012）对照 `sentBytes`/响应体，别只看 JMeter summary 的 Err 计数。修完 401 归零（200×52 / 422×5 / 404×7） |
| 引擎跑完会定向关停 SUT | 每组配置 `--all` 结束后日志出现「定向关停外部 SUT pid=[…]」，下一组直接连不上 | 这是 `sandbox.lifecycle` 的预期行为；每组之间重启 `run_sut.py` 并确认 `/healthz` 200 |
| `Start-Process` 起的 SUT 活不过 shell 退出 | healthz 检查通过，但下一条命令就 `无法连接到远程服务器` | 用 `run_in_background` 的 shell 任务跑 `run_sut.py`（前台阻塞），进程才常驻 |
| **JMeter 连 `localhost` 会先试 IPv6** | 2026-11-06：派生 jmx 里 Host 写 `localhost`，jtl 全是 `HttpHostConnectException: Connect to localhost:8011 [localhost/127.0.0.1, localhost/0:0:0:0:0:0:0:1] failed`，`err=100%` 且 p50 仅 5 ms（快速失败，极易被误读成「服务端拒绝」）。根因：uvicorn 只绑 `127.0.0.1`，而 JMeter 会同时解析出 `::1` 并优先尝试 | 派生 jmx 后把 Host 统一换成 `127.0.0.1`（`reads_light_native.jmx` 共 64 处）；jtl 里出现成对 `127.0.0.1` + `0:0:0:0:0:0:0:1` 就是这个坑 |
| **E3 之后混合场景里的 `links/live` 会把回放引擎挂死** | 2026-11-06 第一次 soak：13139 样本 / 1800 s = **7.3 TPS**（基线 59.6），几乎所有接口 `max ≈ 1 632 s`（正好等于窗口时长），句柄 423 → 531 | 该端点 E3 后返回 200 + `text/event-stream`、流不结束，而 `python_replay` 引擎不给流式响应设读超时 → sampler 挂到窗口结束；12 线程 × 135 sampler 顺序轮转下 TPS 崩到 1/8，且**挂起连接本身污染 RSS/句柄序列**（那轮 +34.1 MB/h 不可用作泄漏证据）。修法：`rerun_prepare_soak.py` 从原 jmx 派生 `mixed_biz_no_sse.jmx`，只把该 sampler 的 `enabled` 置 false（其余 134 个不动，保住同构性） |

---

## 8. 命令速查

**复跑一轮压测（2026-11-06 实测可用，负载约 15 分钟）：**

1. `setup_sandbox.py` 建沙箱（119 MB 的 sqlite backup 副本 → `D:\tmp\xh-perf\sandbox`）
2. 从 `.env` 取 `WEB_TOKEN` 写 `D:\tmp\xh-perf\token.txt`（**不打印明文**）
3. 用 `run_in_background` 跑 `D:\tmp\xh-perf\run_sut.py`（cwd=沙箱，`PYTHONPATH=backend`，**不要** `XH_WITH_SCHEDULER`）；脚本自己写 `sut.pid=os.getpid()`
4. `prepare_rerun.py` / `prepare_heavy.py` 生成两份配置（只改路径，**不重新生成 scenarios**，否则端点集与基线不同构）；**soak 用 `rerun_prepare_soak.py`** —— 它会派生 `mixed_biz_no_sse.jmx`（`links/live` 在 E3 后是永不结束的 SSE 流，会把引擎线程挂死，见 §7）。时长从命令行读：30 min 不传，**4 h 传 `14400`**，并用 `rerun_sample_resource.py 14520` 采资源序列（比负载长 120 s）
5. 引擎逐组跑：`python_replay_engine.py --config … --all`（**每组之间重启 SUT**，引擎跑完会定向关停它）
6. `XH_PERF_DIR=D:\tmp\xh-perf` + `aggregate.py` → `compare_rerun.py` → `archive_rerun.py`（归档成 `results/rerun_*`，不覆盖基线）

脚本本体：`rerun_run_sut.py` / `rerun_prepare_light_write.py` / `rerun_prepare_heavy.py` / `rerun_prep_aggregate.py` / `rerun_merge_cfg.py` / `rerun_compare.py` / `rerun_archive.py`（都在本目录；与 `setup_sandbox.py` 一样写死 `D:\tmp\xh-perf`）。踩过的坑见 §7 最后四行。

**原生 JMeter 测极限吞吐（2026-11-06 验证可用，Java 25.0.1 + JMeter 5.6.3）：**

```powershell
$jh='D:\code\Jmeter\apache-jmeter-5.6.3'
$tok=(Get-Content D:\tmp\xh-perf\token.txt -Raw).Trim()
python perf/2026-10-31-full-api/rerun_jmx_for_jmeter.py     # 派生带鉴权的 jmx
& 'D:\code\Java\jdk-25.0.1\bin\java.exe' "-Djmeter.home=$jh" -jar "$jh\bin\ApacheJMeter.jar" `
  -n -t D:\tmp\xh-perf\jmx_native\reads_light_native.jmx `
  -Jthreads=96 -Jramp=10 -Jduration=120 -Jport=8011 "-Jtoken=$tok" `
  -l D:\tmp\xh-perf\output\jmeter_light_t96.jtl
python perf/2026-10-31-full-api/rerun_jmeter_summary.py D:\tmp\xh-perf\output\jmeter_light_t96.jtl
```

不要用 `jmeter.bat`（结尾 `pause` 会在非交互里挂住）；`-Djmeter.save.saveservice.*` 在 5.6.3 上不生效（jtl 仍是 CSV），要改 jtl 格式只能改 `jmeter.properties`。

```bash
# 单文件/子集
.venv/Scripts/python.exe -m pytest tests/test_cache_capacity.py -o "addopts=" -p no:cacheprovider -q

# 缓存族（B1/B4/B5/单飞/SWR 一起；B4 变异验证用的就是这组，55 例）
.venv/Scripts/python.exe -m pytest tests/test_cache_capacity.py tests/test_cache_single_flight.py \
  tests/test_cache_swr_refresh.py tests/test_order_stats_cache_invalidation.py \
  -o "addopts=" -p no:cacheprovider -q

# 全量（安静机器上约 7~18 min；2 868 passed / 0 failed 是 E1/E2 时的基线）
$env:TEMP='D:\tmp\pytest-temp'; $env:TMP='D:\tmp\pytest-temp'
.venv/Scripts/python.exe -m pytest -o "addopts=" -p no:cacheprovider -q --basetemp=D:/tmp/full

# 变异验证（逐腿串行；先确认基线绿）
.venv/Scripts/python.exe perf/2026-10-31-full-api/b4_mutation_run.py
```

环境事实：Python 3.12 / FastAPI 0.136.3 / Starlette 1.2.1 / anyio 4.13.0；Windows + Git Bash；全量用例数会随新增用例增长（B4 时是 2 818），**不要把它当常量写进判据**。

---

## 9. 未决问题（不是待办，是「还没有答案」）

1. **RSS 爬升的根因**：缓存已排除；句柄无泄漏。**4 h（2026-11-06）已把 30 min 无法区分的两半分开**：既非单调累积也非「完全不涨」—— 8 段 P95 包络 +17.5 MB/h 且单调递增，但 96 次 >60 MB 的大幅归还（最大 162.4 MB）+ 尾部斜率衰减（20.4 → 11.6 MB/h）说明**能归还、形态是分配器/页缓存水位缓慢上行**；末段仍在抬升，故不能写「已收敛」。`tracemalloc` 已判不可用，不要再试。
2. **服务端真实吞吐上限**：客户端同机且 `python_replay` 96 并发已近顶，现有 P95/P99 只是上界。
3. **`/api/vector-admin/snapshots` 的 `≤120 ms`**：C1 落地后未复跑证实。
4. **`config/raw` 稳态 ~90 ms**：C6 只换读侧，dump 那 21~27 ms 留在原地，已判定不换（emoji 转义）。
5. ~~**A2 的 230 ms 残余**：已定位到 enrich（A2′），但尚未量化「预编译 + 请求内 memo」能收回多少。~~ **已于 2026-11-06 量化**：预编译 + 别名预计算收回约 64% 的 enrich 累计时间（706.9 → 256.3 ms），但「请求内 memo」被实测推翻；A2 原文的「≤60 ms」仍不成立。

---

## 10. 建议的第一个动作

§2.1 的「已取证、改动小」档已清空：A2′/C3/B3/A1/A5/C5 全部处置完毕（**4 条落地、2 条判定不落**）。**下一步应转验证缺口**：按 §6 重建压测链路、订正 `REPORT.md:205` 的三处路径，再做一轮同机同数据的 JMX 复跑，把 A2′/C3/A1/C5 的接口级收益与 D1/D2/B4 的并发数字一次兑现。若要先开新条目，从 §2.2 的 D4（真流式导出）或 D3（写侧批量）开始 —— 但两者都要先补取证。

若目标是「把已落地的 D1/D2/B4 换成可发布的数字」，则先按 §6 重建压测链路、订正 `REPORT.md:205` 的路径，再做一轮同机同数据的 JMX 复跑——这比继续开新条目更能回答「优化到底兑现了多少」。
