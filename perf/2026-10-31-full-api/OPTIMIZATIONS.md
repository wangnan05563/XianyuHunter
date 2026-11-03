# 优化建议清单（基于 2026-10-31 全量 API 压测数据）

> 每条给出：**具体优化内容** → **证据**（实测数字 + 源码位置） → **量化预期收益** → **优先级** → **验证方式**。
> 收益口径说明：下文所有「目标值」以本次实测的**同类廉价接口**为地板参照 —— `GET /api/anticrawl/fingerprint` 9.0 ms、`GET /api/notifier/quiet-hours` 8.4 ms、`GET /api/anticrawl/freq/delay` 7.4 ms（单用户基线，纯配置/内存读取）。凡「缓存命中」类预测均按该地板 + 少量序列化开销（10~20 ms）估算，未采用无参照的外推。
> 优先级 = 影响面（该接口在真实控制台中的调用频率 × 劣化幅度）÷ 实现成本。

## 0. 汇总表（按优先级）

| 编号 | 优化项 | 优先级 | 关键指标预期变化 | 成本 |
|---|---|---|---|---|
| B1 | 缓存单飞（single-flight） | **高** | 长跑混合场景 avg 200.5 → ≤80 ms；长跑整体 P95 544.5 → ≤250 ms | 低（`web/cache.py` 约 20 行）✅ **已落地** |
| B5 | SWR「后台刷新」实为同步 + 异步分支静默失效 | **高** ✅ **已落地** | `maintenance/status` 过期后首次调用 ≈1.6 s → ≈0；消除 SWR 冻结隐患 | 低 |
| C2 | `/api/auth/me` 去重复文件解析 | **高** | avg 152.6 → ≤50 ms（长跑 414.3 → ≤120 ms） | 低 |
| C4 | 反爬策略探测降本 + 缓存 | **高** | avg 1 094.7 → ≤30 ms；96 并发 2 511.8 → ≤100 ms | 低 ✅ **已落地（①③；②不落地，且「≤30 ms」经实测作废 → 约 190~250 ms）** |
| D1 | 慢同步接口独立线程池 | **高** | 48/96 并发轻组 P95 2 064/3 893 → ≤1 000 ms；缺陷率 2.03/3.17% → <0.5% | 中 |
| A2 | `eval.*` 事件查询下推 | **高** | `/api/evaluations` avg 195.9 → ≤60 ms；P99 858 → ≤250 ms | 中 |
| C6 | PyYAML 走纯 Python 后端，libyaml 已在而未用 | **高**（新发现，非压测得出）✅ **已落地（读侧）** | 同一份 config.yaml：解析 **47.7 → 6.5 ms（7.4×，实测落地值）**；写侧不换（C dumper 把 emoji 写成 `\U0001F4F7` 转义，产物不再逐字节相同），序列化那 21~27 ms 留在原地 | 极低（新增 `infra/yaml_io.safe_load` + 9 个读盘点） |
| B2 | 慢接口纳入 TTL 缓存（raw/share/snapshots/preview） | ~~高~~ → **撤回 4/5 条** | 轻组基线 avg 50.9 → ≤25 ms 的预期**不成立**（见 §B2 反证）；只做安全残留 = config memo | 低 |
| A4 | `maintenance/status` 计数降本 | **高** ✅ **已落地（2026-11-03）** | 完整重算 **2 235 / 5 205 ms → 188 / 170 ms**（「≤200 ms」在重算路径成立），消除 60 s 超时（曾致 P95 60 033 ms / 37.5%）。**归因修正**：6 张表 `COUNT(*)` 每张仅 ~0.1 ms，真正成本是漏排除的 `.venv-build/` pycache（1 578 个目录 / 131.7 MB）；原文 ①②（近似行数/后台计数）因此不成立 | 低 |
| A1 | 补齐 5 个过滤列索引 | 中 | `prices/category-comparison` 长跑 avg 580.2 → ≤200 ms | 低 |
| C3 | `_load_yaml` 走单例缓存 | 中 ✅ **部分落地（config memo）** | `config/raw` 132.4 → ≤15 ms 的预期**过高**：实测解析 52 ms、脱敏+序列化另占 ~33 ms，memo 只砍前者（→ 约 80~90 ms）。写侧 `preview/save` 各去掉一次全量读 | 低 |
| C1 | `_dir_size` 双调用合并 | 中 | ✅ 已落地（2026-11-03）：实测降幅 **43%**（不是 50%，一遍里还有 26% 固定开销），另把 `/status` 的同步走盘移出事件循环；`vector-admin/snapshots` 233.8 → 待复跑（≤120 ms 未证实） | 极低 |
| D2 | 打破 `cache_scan` 进程级 RLock 串行 | 中 | `maintenance/cache-scan` 96 并发 avg 1 822.8 → ≤400 ms | 中 |
| D3 | 写侧批量 UPSERT + 池/超时调优 | 中 | 16 并发写 TPS 51.9 → ≥66（回到峰值档）；P95 635.8 → ≤400 ms | 中 |
| D4 | 大结果集流式导出 | 中 | `db-admin/tables/items/export` P99 1 827.7 → ≤600 ms；私有内存峰值 617.7 MB 下降 | 中 |
| E1 | 多 worker 部署（纯 Web） | 中 | 吞吐 80 → ≥150 rps；96 并发 P99 4 296.9 → ≤1 500 ms | 中（需配合 B1/B3） |
| A5 | 日志/错误检索改前缀匹配或 FTS5 | 中 | `logs/search` 132.9 → ≤40 ms | 中 |
| B3 | 启动预热扩展 | 中 | 冷启动首请求 1.1 s → ≤100 ms | 低 |
| E3 | 依赖不可用的 503 语义改造 | 中 | 30 min 内 795 次 503 → 0，长跑缺陷率 0.74% → 0 | 低 |
| C5 | 评估列表 N+1 批量化 | 中 | 评估列表 P99 858 → ≤300 ms | 中 |
| B4 | 缓存键粒度 + 容量上限/LRU | 中↑（原低） | RSS 峰值 414.5 → ≤350 MB；长跑斜率 +75.9 MB/h → ≈0 | 中 |
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

### A2. `eval.*` 事件全量重载下推到 SQL — 优先级 **高**
**具体优化内容**：`GET /api/evaluations` 目前经 `web/routes/evaluations_list.py:732` 调 `container.repo.list_events_by_type_prefix(...)`，其默认 `limit=50000`（`infra/repo_events.py:337`，注释自陈「评估事件量级的上限」），把事件整段读进 Python 再过滤 / 分页 / 求中位数；另在同函数额外发一次 COUNT（`repo_events.py:333-365`）。改为：SQL 侧 `WHERE type LIKE 'eval.%' AND …` + `LIMIT/OFFSET`（或 keyset 分页）+ `COUNT(*) OVER ()` 单趟取总数，聚合（中位数/分位）改为 SQL 侧或物化到快照表。
**证据**：基线 1 并发 19.8 ms（缓存命中态）→ 12 并发长跑 **195.9 ms / P95 456.9 / P99 858.0**；同族 `evaluations/distribution` 190.4、`evaluations/threshold-suggestion` 180.2、`evaluations/brands` 173.1 —— 说明整族都被同一重载支配。
**量化预期收益**：长跑该族 avg **195.9 → ≤60 ms**（-70%），P99 **858 → ≤250 ms**；由于评估列表是控制台首页的核心调用，混合场景整体 avg 200.5 可下移约 5~8%。
**验证方式**：同一 JMX 复跑 + `sqlite3 .stats` 或 `EXPLAIN QUERY PLAN`；比对事件表行数翻倍后的 P95（抗增长才是真收益）。

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

### B4. 缓存容量上限与淘汰 — 优先级 **中**（原列「低」，因资源口径修正后证据变强而上调，见 REPORT §6.8）
**具体优化内容**：`web/cache.py` 的 `_cache_store` 无容量上限/淘汰策略；加 `max_entries` + LRU，并对大 payload（导出类、`snapshots`、`logs/search` 响应）显式不进缓存；同时给缓存加「条目数 + 字节数」可观测指标。
**证据**：只统计 SUT 存活样本后，长跑 30 min 窗口内 RSS 六段为 `346.8 → 354.7 → 368.5 → 374.5 → 373.4 → 378.4 MB`（拟合 **+75.9 MB/h**），全程 8 段 `203 → 217 → 311 → 322 → 332 → 357 → 372 → 377 MB`，峰值 **414.5 MB**、私有内存峰值 **617.7 MB**，且**负载停止后不回落**（终止时仍 375.6 MB）。同期句柄恒定（`627~631`），因此不像连接/FD 泄漏；最可解释的来源就是这份无上限的进程内缓存（叠加 SQLite 页缓存与分配器 arena）。**在 30 min 尺度上仍无法与慢泄漏区分** —— 本条既是优化，也是将问题变为可判定的手段。
**量化预期收益**：RSS 峰值 **414.5 → ≤350 MB**，长跑窗口斜率 **+75.9 MB/h → ≈0**（有上限后应收敛为平台）；把「内存增长是缓存还是泄漏」从不可归因变成可判定。
**验证方式**：4 h 长跑（见 §E4 与 CONCLUSION 的未达成项）分段对比 RSS 斜率 —— 若加限制后斜率归零即确认原因为缓存，若仍爬升则为泄漏，需转 `tracemalloc` 定位。

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

### C2. `/api/auth/me` 去除重复文件解析与多余 SQL — 优先级 **高**
**具体优化内容**：`web/routes/auth_query.py` 中：① `_resolve_current_user_id(request)` 在一次请求里被调用两次（`:336` 与 `:406`），合并为一次并透传；② `_check_cookies`（`:341` → 其内部 `:83`）每次都 `invalidate_cache` 后重读 cookies JSON 与 `last_login_cookies.json`，改为带 TTL 的进程内 memo；③ `get_userinfo()`（`:344`）再次读文件，复用 ② 的结果；④ `_sync_nick_to_users_table` 的写库（`:369`）仅在昵称变化时才执行（先比对）；⑤ `scan_and_notify`（`:403`）移出 `/me` 请求路径（改调度触发）。
**证据**：基线 avg **152.6 ms**（P95 394.7 / P99 492.7），长跑 **414.3 ms**（P95 1 049.5），96 并发 2 365.2 ms。同时须澄清：**鉴权本身不是瓶颈** —— `web/middleware/auth.py` 的 `verify_session` 走 sha256 + `_verify_cache`（TTL 300 s），非 bcrypt；同族 `POST /api/auth/me/refresh` 仅 17.9 ms，可作「去掉文件重读后的落点」参照。
**量化预期收益**：avg **152.6 → ≤50 ms**（对标 `me/refresh` 17.9 ms + 用户信息查询）；长跑 **414.3 → ≤120 ms**。该接口是控制台每个页面加载的必经调用，收益覆盖全站请求，故列为高优先级。
**验证方式**：`/me` 单接口阶梯（1/16/48 并发）复跑；统计每请求的文件 open 次数。

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
**未落**：`api_templates.list_templates` / `save_template` 的同类 memo 化（每次请求读 YAML + JSON 再整表写回）——形态相同但没做过成本占比测量，且写侧模板是用户内容，失效点要单独设计，留作后续。（读侧的 C loader 已随 C6 覆盖 `api_templates._load_presets`。）


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

---

## D. 连接池配置调优与线程模型

### D1. 慢同步接口与快接口隔离线程池 — 优先级 **高**
**具体优化内容**：本项目大量 handler 是同步 `def`，FastAPI 一律丢进 anyio 线程池（默认 **40 token**，代码未覆写）。给已知慢接口（`maintenance/*`、`export*`、`logs/search`、`vector-admin/*`、`anticrawl/strategy`）单独设置 `anyio.to_thread.RunVars`/`CapacityLimiter`（如 4），并把 `vector-admin` 的 `rglob`、`_dir_size` 移出 async handler。
**证据（饥饿的直接形态）**：96 并发时，**本身只要 8.4 ms 的** `GET /api/notifier/quiet-hours` 变成 avg 2 096.8 ms；60 s 窗口内它的完成样本数从基线的 55 掉到 **7**，`vector-admin/snapshots` 只剩 **3**，`maintenance/cache-scan` 剩 **6** —— 完成数骤降 + 廉价接口延迟被抬到 2 s，是「排队等线程」而非「服务算得慢」的教科书特征。轻组整体：16 并发 P95 782.7 → 48 并发 2 064.0 → 96 并发 3 893.2 ms，真实缺陷率 1.81% → 2.03% → **3.17%**。
**量化预期收益**：48/96 并发轻组 P95 **2 064/3 893 → ≤1 000 ms**；缺陷率 **2.03/3.17% → <0.5%**；廉价接口（`quiet-hours`、`fingerprint`）在高并发下回到 ≤100 ms。
**验证方式**：同一 `reads_light` JMX 在 48/96 并发复跑，比较 `notifier/quiet-hours` 的 avg 与完成样本数（样本数恢复即证明隔离生效）。

### D2. 解除缓存扫描的进程级互斥 — 优先级 **中**
**具体优化内容**：`web/services/cache_scan.py:52` 的 `self._lock = threading.RLock()` 在 `snapshot()`（`:66`）里对**所有路径**互斥，使 `/maintenance/cache-scan` 与 `/maintenance/status`（内部也调 `snapshot`）在并发下完全串行。改为按扫描路径分锁，或改增量/无锁读。
**证据**：`maintenance/cache-scan` 基线 94.1 ms → 长跑 287.7（P99 1 581.4）→ 96 并发 avg 1 822.8 ms 且窗口内仅完成 6 次；`maintenance/status` 的 60 s 超时（A4）与该锁的串行化叠加。
**量化预期收益**：两接口并发下不再互相阻塞，96 并发 avg **1 822.8 → ≤400 ms**。
**验证方式**：并发两个不同路径的 `cache-scan`，确认总耗时 ≈ 单路径耗时而非两倍。

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

### E4. 尚未覆盖、需在下一轮补的方向
- **≥4 h 耐久**：本次实跑 30 min（墙钟 1 801.4 s），未达目标要求。改 `jmeter-config-v2.yml` 的 `soak_mixed.duration_override: 14400` 即可原样复跑；4 h 才能给出 RSS/句柄斜率的可靠判定（现在只能说「30 min 内未见泄漏」）。
- **CPU/磁盘 IO 逐秒曲线、网络带宽**：`monitor_sut.py` sidecar 只采进程级 RSS/私有内存/CPU%/句柄/线程；系统级磁盘与网络需另接 PerfMon，本报告未据此下任何结论。
- **真实负载画像**：本次「读:写 ≈ 9:1」是按控制台行为的近似，若有真实访问日志（nginx / uvicorn access log），应据此重定权重后再定容量红线。
- **外置压测机**：负载生成器与被测服务同机会让 P95/P99 虚高（技能判据）。当前所有尾延迟应读作**上界**；换机复跑后拐点位置应仍可比。

---

## F. 短期落地路线（建议顺序）

| 阶段 | 内容 | 预期累计效果 |
|---|---|---|
| **第 1 周（低风险高收益）** | ~~B1 单飞~~ ✅ + ~~B2 慢接口缓存~~（4/5 撤回，残留 config memo ✅）+ ~~C6 换 libyaml 后端~~ ✅（2026-11-01 落地读侧；写侧因 emoji 分歧撤回）+ ~~C1 双调用合并~~ ✅（2026-11-03 落地，实测 -43%；顺带把 `/status` 的同步走盘挪出事件循环）+ ~~C4 探测降本~~ ✅（2026-11-03 落地 ①③，关闭态每请求省 905 ms；②短 TTL 因需配对失效不落；「≤30 ms」作废 → 约 190~250 ms，待复跑）+ ~~A4 计数降本~~ ✅（2026-11-03 落地：完整重算 2 235/5 205 → 188/170 ms，归因改为 `.venv-build` pycache 漏排除 + 日志 3 次 stat + TTL 60→300；①② 计数近似不成立）+ E3 503 语义 | 混合场景 avg 200.5 → **≤90 ms**；消除 60 s 超时与 503 噪声；轻组 96 并发 P95 3 893 → **≤1 500 ms**；配置族 132.4 → **约 90 ms**（只有读侧能换 C 后端，dump 留原地；B2 原写的 ≤25 ms 不成立，见 §B2 第 5 条） |
| **第 2–4 周（结构性）** | C2 `/me` 去重 + A2 事件下推 + D1 线程池隔离 + D2 解锁 + ~~C3/YAML 单例~~（config 侧已随 B2 残留落地，剩 `api_templates`）+ **B4 缓存容量上限** + **≥4 h 长跑复测** | 48/96 并发缺陷率 2.03/3.17% → **<0.5%**；`/me` 全站收益；廉价接口高并发不再被拖到 2 s；RSS 斜率 +75.9 MB/h 得到定论（缓存 or 泄漏） |
| **季度内（容量形态）** | D3 写批量 + D4 流式 + A1 索引 + A5 FTS + E1 多 worker（须先完成 B1/B3） | 读吞吐 80 → **≥150 rps**；写侧 16 并发不再回落；内存峰值收敛；随数据增长的劣化斜率变缓 |

> 每条落地后请用同一份 JMX 复跑对比（`jmeter-config-v2.yml` + `aggregate.py`，其阶段时长已改为取引擎日志实测墙钟），并保留 jtl/jsonl 作为前后证据。
