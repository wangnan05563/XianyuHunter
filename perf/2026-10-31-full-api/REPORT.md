# XianyuHunter 全量 API 性能测试报告（2026-10-31）

> 执行方式：`jmeter-performance-test` 技能，`mode: python_replay`（本机无 Java/JMeter，技能自动回退等价回放引擎）
> 完整阶梯、原始样本、聚合数据、JMX 与配置均留存在本目录，见「可追溯性」

## 1. 被测环境与口径

| 项 | 值 |
|---|---|
| 被测实例 | 隔离沙箱 `D:\tmp\xh-perf\sandbox`，端口 **8011**（生产控制台 8001、截图实例 8010 均不触碰） |
| 启动方式 | `python -m xianyu_hunter web --port 8011 --no-with-scheduler`（**调度器关闭**，不会拉起浏览器/采集任务） |
| 数据库 | `data/xianyu.db` 的 **SQLite backup API 一致性副本**（114.1 MB，items 774 / tasks 170 / evaluations·events·price_history 全量），与生产库物理隔离 |
| 配置 | 沙箱副本内 `tunnel.auto_start` 置 false；仓库 `config/config.yaml` 未被改动 |
| 服务端 | FastAPI + Uvicorn 单进程单 worker，SQLAlchemy 2.0 + SQLite(WAL, busy_timeout=10000, synchronous=NORMAL, QueuePool pool_size=20/max_overflow=10)，进程内 TTL 缓存 |
| 压测机 | Windows 10 / Python 3.12 venv；负载生成器与被测服务**同机**（见 §6 偏差声明） |
| 鉴权 | `Authorization: Bearer $WEB_TOKEN`（令牌仅存在于临时目录，压测后随目录删除） |
| 端点总数 | `/api/*` 共 **272** 个（另 272 个 `/xianyu/api/*` 为同一路由的前缀别名，不重复计） |

### 覆盖率（约束「不可遗漏」的对账口径）

| 分层 | 数量 | 处置 | 判据 |
|---|---|---|---|
| `load_read` | 109 | 进入基准 + 压力阶梯 | 本地只读接口 |
| `load_write` | 26 | 进入基准 + 压力阶梯 | 幂等/低代价写接口，落在沙箱副本库 |
| `probe_single` | 67 | 单发探针（记录真实响应码与延迟，不进负载轮） | 非幂等/破坏性/批处理类写接口；重复压测会把「夹具耗尽」伪装成服务缺陷 |
| `blocked` | 70 | 不压测，逐条给出原因 | 出站点（外部大模型/GitHub/推送渠道/真实站点抓取）、真实下单、真实登录态、SSE 长连接、内网穿透 |
| 合计 | **272** | 负载实测量 136 个接口 + 单发实测量 67 个 = 203 个拿到真实响应；69 个按安全红线不出击 | |

`blocked` 的代表项（完整原因见 `endpoint-coverage.json` 的 `reason` 字段）：

- `POST /api/orders/*`（真实下单）、`/api/batch-refresh/{start,run}`、`/api/items/{id}/refresh`、`/api/evaluations/{id}/collect-official`、`/api/tasks/{id}/start`（真实抓取 + 拉起浏览器）
- `/api/ai*`、`/api/chatbot/{chat,messages}`（外部大模型，计费且延迟由第三方支配）
- `/api/tunnel/*`（对外暴露本机端口）、`/api/auth/{qr,browser,cookie,session,import}*`（真实登录链路）
- `*/stream`、`/api/events/stream`（SSE 长连接，请求级吞吐口径不适用）
- `/api/notifier/test`（向真实钉钉/Server酱/Telegram 发消息）、`/api/about/check-update`（出网）

## 2. 基准测试（Baseline，1 并发）

| 组 | 采样器 | 时长 | 样本 | TPS | 平均 | P50 | P95 | P99 | 最大 | 真实缺陷率 | 4xx 率 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 重组 `reads_heavy` | 46 | 300s | 13 156 | 43.9 | 22.7 | 11.1 | 79.4 | 263.6 | 1602.6 | 0.00% | 21.74% |
| 轻组 `reads_light` | 64 | 180s | 3 547 | 19.5 | 50.9 | 11.1 | 180.6 | 1065.4 | 1372.5 | 1.58% | 18.72% |
| 写组 `writes_idem` | 26 | 180s | 5 975 | 32.8 | 30.0 | 10.0 | 148.7 | 253.6 | 4387.0 | 0.02% | 61.52% |

资源基线（SUT 存活期 2 085 个有效采样 / 原始 2 132 行，2 s 间隔；关停后的 rss=0 行已剔除，见 §6.8）：RSS `180.9 MB → 峰值 414.5 MB`，私有内存峰值 `617.7 MB`，句柄 `482 → 峰值 858`，CPU `平均 56.1% / 峰值 269.1%`（多核折算，单进程受 GIL 支配）。磁盘侧：SQLite 主库 + `-wal`，写阶梯期间 WAL  checkpoint 持续回收，未观察到数据库文件无界增长。

**基线结论**：绝大多数接口在单用户下 P95 低于 200 ms，符合本地工具体验；但有 4 个接口在**零并发**下就已经很慢（见 §4 表 A），属于固有成本而非容量问题。

## 3. 压力测试（阶梯并发与拐点）

### 3.1 重组 `reads_heavy`（控制台首屏/统计/评估/日志类聚合读，45+1 个接口）

| 并发 | 样本 | TPS | 平均 | P95 | P99 | 缺陷率 | 4xx |
|---|---|---|---|---|---|---|---|
| 1 | 13 156 | 43.9 | 22.7 | 79.4 | 263.6 | 0.00% | 21.7% |
| 2 | 4 307 | 71.8 | 27.7 | 95.1 | 176.9 | 0.00% | 21.7% |
| 4 | 4 874 | **81.2** | 49.0 | 152.9 | 281.6 | 0.00% | 21.7% |
| 8 | 4 784 | 79.7 | 99.4 | 250.8 | 499.3 | 0.00% | 21.6% |
| 16 | 4 420 | 73.7 | 213.7 | 440.0 | 677.1 | 0.00% | 21.4% |

拐点描述：**拐点出现在 4 并发**。TPS 从 43.9 → 81.2（4 并发）后彻底持平并在 16 并发回落至 73.7，而 P95 从 79 ms 线性攀升至 440 ms（×5.5），平均耗时 ×9.4 —— 典型的「吞吐封顶 + 排队增长」饱和曲线，且**没有任何 5xx**，说明不是逻辑回归而是单进程算力/调度饱和。

> 口径提示：本组数据来自 v1 轮（46 个采样器，含 1 个 SSE 接口 `/api/logs/stream`）。修正分层后该接口被判为 `blocked`，为保持同一集合的 apple-to-apple，未按新集合补跑 32/64 并发档（见 §6）。

### 3.2 轻组 `reads_light`（配置/菜单/账号/看板/维护/向量状态等 64 个接口）

| 并发 | 样本 | TPS | 平均 | P95 | P99 | 真实缺陷率 | 4xx |
|---|---|---|---|---|---|---|---|
| 1 | 3 547 | 19.5 | 50.9 | 180.6 | 1065.4 | 1.58% | 18.7% |
| 16 | 3 530 | **58.1** | 266.3 | 782.7 | 1275.5 | 1.81% | 18.4% |
| 48 | 2 507 | 41.3 | 1096.2 | 2064.0 | 3566.5 | 2.03% | 15.9% |
| 96 | 3 027 | 49.4 | 1825.8 | **3893.2** | 4296.9 | **3.17%** | 13.8% |

拐点描述：**拐点在 16 并发**（吞吐增益 2.98×，P95 相对基线劣化 4.3×）。16 并发后 TPS 不再上升（58.1 → 41.3 → 49.4，吞吐回落且抖动），P95 在 48→96 并发之间再劣化 ×5，同时真实缺陷率单调上升 1.81% → 2.03% → 3.17%。96 并发的 P99 4.3 s 已进入「控制台不可用」区间。

### 3.3 写组 `writes_idem`（26 个幂等写接口，含配置保存/评估重算/任务创建）

| 并发 | 样本 | TPS | 平均 | P95 | P99 | 真实缺陷率 | 4xx（夹具口径） |
|---|---|---|---|---|---|---|---|
| 1 | 5 975 | 32.8 | 30.0 | 148.7 | 253.6 | 0.02% | 61.5% |
| 4 | 3 523 | 57.3 | 67.2 | 259.3 | 441.6 | 0.03% | 61.5% |
| 8 | 4 042 | **66.4** | 118.0 | 378.6 | 577.3 | 0.00% | 61.6% |
| 16 | 3 170 | 51.9 | 291.9 | 635.8 | 816.0 | 0.00% | 61.6% |

拐点描述：**吞吐上限在 8 并发（66.4）**，8→16 并发吞吐不仅不涨反而回落到 78%（51.9），同时 P95 已达基线 ×4.3 —— 机械判据（`ladder_table.md`）把饱和档记为 16，实际可用容量应按「峰值档 8」取。该形态符合 SQLite WAL 单写者 + 连接池（20）在写侧的串行化特征。写侧 5xx 全程 ≈0，说明写路径在容量范围内是正确的。

### 3.4 最大并发能力（以 P95 < 1 s 为体验红线）

| 场景 | 可接受并发 | 依据 |
|---|---|---|
| 首屏/统计聚合读 | **≈4** | 4 并发后 TPS 不再增长 |
| 混合控制台读（含配置/看板） | **≈16** | 48 并发时 P95 2.06 s 越线 |
| 幂等写（配置保存、重算） | **≈8** | 16 并发吞吐回落 |

## 4. 稳定性 / 耐久测试

- 场景：`mixed_biz`（135 个接口，读:写 ≈ 9:1，贴近控制台真实比例）
- 配置：**12 并发恒定负载，连续 1800 s（30 分钟，实测墙钟 1 801.4 s）**，107 324 个样本
- 分窗趋势（每 5 分钟一窗 ×6，样本数完全相等说明节流稳定）：

| 窗口 | 样本 | TPS | 平均 | P95 | P99 | 真实缺陷率 | 4xx |
|---|---|---|---|---|---|---|---|
| 1（0–5min） | 17 887 | 59.6 | 198.3 | 545.0 | 1194.5 | 0.76% | 27.7% |
| 2（5–10min） | 17 887 | 59.6 | 201.4 | 521.2 | 1157.6 | 0.75% | 28.2% |
| 3（10–15min） | 17 887 | 59.6 | 194.9 | 548.2 | 1182.7 | 0.74% | 28.1% |
| 4（15–20min） | 17 887 | 59.6 | 220.7 | 624.4 | 1229.1 | 0.75% | 28.2% |
| 5（20–25min） | 17 887 | 59.6 | 223.3 | 585.8 | 1166.5 | 0.74% | 28.1% |
| 6（25–30min） | 17 887 | 59.6 | 164.7 | 398.6 | 1063.5 | 0.73% | 28.3% |

判定：
- **无吞吐/延迟退化**：TPS 六窗恒为 59.6（12 并发 × avg 200 ms 的理论值 60，说明客户端无空转），P95 在第 6 窗反而最优（398.6 ms），末窗相对首窗改善 27% —— 曲线在 30 分钟尺度上平稳。
- **内存**：长跑窗口（末 1 800 s 存活样本）内 RSS 由 `346.8 → 378.4 MB` 六段递增，线性拟合斜率 **+75.9 MB/h**；但增势在后半程趋平（第 4/5/6 段 `374.5 / 373.4 / 378.4`，波动 < 5 MB），且负载停止后未回落（终止时仍 375.6 MB）。全程 8 段为 `203 → 217 → 311 → 322 → 332 → 357 → 372 → 377 MB`（前 4 段是预热爬升），峰值 414.5 MB 出现在长跑窗口内；私有内存峰值 617.7 MB（约 RSS 的 1.5 倍，含 SQLite 页缓存 `cache_size=-20000` ≈ 20 MB/连接 × 连接数）。**判读：形态更像「无容量上限的进程内缓存 + 分配器 arena 预热」（见 B4），但在 30 min 尺度上与慢泄漏不可区分 —— 这是本次唯一未能定论的稳定性风险。** **⚠️ 2026-11-05 已把「缓存」这一半排除**：`b4_cache_census.py` 把 106 个 GET label 全扫一遍，缓存里只有 **7 条 / 23 620 B（0.02 MB）**；合成到 327 条时深量 0.66 MB，而同场 RSS 涨 26.4 MB（40 倍）。所以 RSS 爬升不再是「缓存驻留」能解释的，**仍需 ≥4 h 长跑 + `tracemalloc` 才能定论**
- **连接/句柄**：长跑窗口内句柄恒处 `627~631`（斜率 +7.2/h，无爬升），峰值 858 出现在压力阶段的高并发档、随后回落；负载停止后未缩回空载的 482，说明句柄稳定但不主动收缩 —— **未见连接泄漏证据**；未出现 `database is locked`（服务端日志中该关键字命中 0 次）。
- **残余缺陷**：30 分钟内 795 次 503 **全部**来自 `GET /api/tasks/{id}/links/live`（100% 失败），根因是沙箱关闭调度器后采集器不可用；另有 4 次 `000` 超时（分布在 4 个写接口，单发，属长尾抖动）。扣除该项后，本轮真实缺陷率为 0.00%。（E3 已于 2026-11-03 把该前置检查改为 200 + 流内 error 事件；复跑后这 795 次 503 应归零，见 OPTIMIZATIONS §E3。）
- **未达成项**：目标要求 ≥4 h。本次执行 30 min（会话预算限制），已把耐久长跑的参数留存在 `jmeter-config-v2.yml`（把 `soak_mixed.duration_override` 改为 `14400` 即可原样复跑 4 h），本报告不将 30 min 结果外推为 4 h 结论。**该缺口不只是时长问题**：B8 的内存爬升在 30 min 尺度上无法与慢泄漏区分，4 h 复跑是排除它的必要证据。

## 5. 瓶颈定位（测量 + 源码双向对齐）

### 表 A：零并发就慢的接口（固有成本，非容量问题）

| 接口 | 单用户 avg | 根因（源码位置） |
|---|---|---|
| `GET /api/anticrawl/strategy` | **1095 ms**（30 min 长跑中 avg 1300 ms） | 同步 `def` → 线程池；`login_strategy.py:144` 对 CDP 端口做 `socket.create_connection(timeout=1.0)`，端口未开即吃满 1 s；`login_strategy.py:150-184` 再打开 Chromium `Network/Cookies` SQLite 做全表 `LIKE '%goofish%'` 扫描。**原写法「结果秒级稳定却完全未缓存」已在复核中撤回**：`LoginStrategySelector.evaluate` 的两个输入（CDP 端口是否已开、Cookie 是否仍有效）恰恰是用户在页面上会当场改变的东西，`AntiCrawl/index.tsx` 又是 `runInitialize()` → `fetchAll()`，直接挂 TTL 会在用户刚开 CDP/刚登录后继续推荐旧策略。**✅ C4 已于 2026-11-03 落地 ①③**：探测超时改 `CDP_PROBE_TIMEOUT = 0.1`（本机实测关闭态 1 014.2 → 109.1 ms，省 905 ms；真 accept 的端口 0.60 ms 仍判可用），Cookie 判定改为关键名下推 + `LIMIT 1`（5 000 行 8.24 → 3.44 ms，语义逐形状等价）；②的短 TTL 仍不落（需 `/initialize`、登录态变更、会话失效、刚开 CDP 四类配对失效）。**「avg ≤30 ms」经实测作废** —— 残余 109 ms 就是预算本身，接口级预期约 190~250 ms，本行 1095 ms 仍是修复前基线，待同一 JMX 复跑 |
| `GET /api/vector-admin/snapshots` | 451 ms | ✅ **C1 已于 2026-11-03 落地**：`_dir_size` 双调用合并为一次求值（`api_vector_admin.py:101-107`），实测同类树降到 57%（-43%，不是干净的 50% —— 一遍遍历里 `iterdir`/`st_mtime`/排序是固定开销）；同时把 `async def get_status` 里的同步走盘改 `asyncio.to_thread`（遍历期间事件循环心跳 1 次 → 376 次）。表里的 451 ms 仍是修复前基线，待同一 JMX 复跑。原先「未缓存」的定性不变：**降本走合并遍历，不走缓存** —— `VectorAdminPanel.loadAll()` 在创建/删除/恢复后立刻重拉列表，TTL 会让「刚删的快照还在列表里」这类用户可感知错数据出现（见 OPTIMIZATIONS B2 第 2 条反证） |
| `GET /api/auth/me` | 153 ms（长跑 414 ms）—— **该行根因已被实测推翻，数字仍是修复前基线** | 原先记为「`_resolve_current_user_id` 调用两次 + `_check_cookies` 每请求重读两份 cookie 文件 + `get_userinfo()` 再读文件 + `_sync_nick_to_users_table` 写库 + `scan_and_notify` 查库」。**2026-11-03 C2 落地前的三条探针复核（`c2_me_probe.py` / `c2_server_probe.py` / `c2_ladder.py`）证明这个归属不成立**：进程内直调 `/me` 稳态 **3.6~7.2 ms**，真 uvicorn 安静服务端 **p50 ≈ 10 ms**；`get_userinfo()` 走 `AuthManager` 的 300 s 内存缓存（不是每请求读文件），`_sync_nick_to_users_table` 先比对才 UPDATE（昵称未变时不写库）。真实成因两条：① **压测沙箱没有 cookie 文件**（`setup_sandbox.py` 不生成任何 cookie / userinfo / xh_token 工件），那批样本走未登录分支且无会话身份；② 混合负载下的**排队**结果——`/me` 是 `def` 同步处理器，要经 anyio 40 线程池转一跳，同一阶梯里「处理器近乎为空」的 `POST /me/refresh` 在 c=48 地板就有 **57.9~178.4 ms**，「无处理器的 401」才 ~30 ms。**但 ① 解释不了量级差**：隔离沙箱测的是**更重的已登录分支**（多一次 users 查询 + 昵称同步 + 会话解析），c=1 仍只有 8~10 ms。回读原始样本 `results/baseline_light.jsonl.gz`，`/me` 的 56 个样本实为 **p50 85.6 / p90 370.3 / p95 490.1 / max 548.6 ms**——**152.6 是被长尾抬起来的平均值，不是「一次调用的成本」**；`soak_mixed` 的 794 个样本 p50 316.2 / p95 1 058.7 / max 2 620.1。85.6 ms 的中位数在本机隔离环境复现不出来，最可信的机制是同场景里排在 `/me` 前面的慢接口（`anticrawl/strategy` ~1 s、`maintenance/status` ~1.6 s）占住线程与单连接造成排队，**但这是推断而非实测：定论要等同一 JMX 复跑 + 关闭后台线程的对照组，本轮不下「已解释」的结论**。可归给路由体的边际成本在 c=48 约 55 ms，其中一半是那 **2 条 SQL 抢 StaticPool 单连接**（`db_models.py:872`）。鉴权本身（`verify_session` sha256 + 300 s `_verify_cache`）确实不是瓶颈，但它也不是本行的解释项。**✅ C2 已落地 ①（身份只解析一次）+ ⑤′（订单超时扫描按用户节流 30 s）**，效果是每请求 SQL **2.0 → 1.0 条**；HTTP avg 在本机轮间噪声（同一份基线代码 c=48 实测 117.7~256.2 ms）**下方向不可判定**，故本行不发布修复后 avg，并发墙归 D1。详见 OPTIMIZATIONS §C2 落地记录 |
| `GET /api/config/raw` `/api/config/share` | 132 / 105 ms | `api_config._load_yaml()` 每次调用都重读并 `yaml.safe_load` 整个配置文件（`infra/yaml_config.py` 的单例缓存在这条路径上被绕过）。**落地后复核（`config_memo_probe.py`）把根因再下钻一层**：同一份 7.2 KB 配置纯 Python `safe_load` 中位 **52.5 ms**、`_dump_yaml` 中位 **32.9 ms**，两者都用的是 PyYAML 纯 Python 后端，而本机 `yaml.__with_libyaml__=True`（libyaml 在而未用）。memo 已砍掉重复解析（→ 命中约 80~90 ms）；**2026-11-01 C6 已落地读侧**（`infra/yaml_io.safe_load` 走 `CSafeLoader`，同机实测 47.7 → 6.5 ms），但**写侧不换** —— 逐份比对仓库 7 份 YAML 后 `preset_templates.yaml` 的 dump 产物不一致（`CSafeDumper` 对 emoji 不遵守 `allow_unicode`，写成 `"\U0001F4F7"`），原先那句「输出逐字节相同」只在 BMP 字符上成立，故序列化那 27~33 ms 留在原地，80~90 ms 是稳态而非过渡值；「缓存后 ≤15 ms」的原预期本就不成立 |
| `POST /api/config/preview` `/api/templates` | 419 / 377 ms（长跑） | 同一 `_load_yaml` 成本 + `_dump_yaml`/`_validate`/`_diff`；`templates` 另加读用户文件 + 写文件 + 读 presets |
| `GET /api/prices/category-comparison` | 580 ms（长跑） | `price_dashboard.py:95-98` 在 `task_id=None` 时无 WHERE 条件全表扫 `items`，`_build_item_select` 被重复执行（`range_days>0` 时读两遍）；未缓存 |
| `GET /api/maintenance/status` | 单发 1.6 s | **A4 落地时的成本拆解推翻了本行原来的归因**（`maintenance_cost_probe.py`，真实库 `mode=ro`）：6 张表的 `SELECT COUNT(*)` 每张仅 **~0.1 ms**（最大 events 18 386 行），**不是**成本项；增量扫描 `snapshot(0.5)` 冷 19~32 ms、热 0 ms，预算只是上限。真正的 1.6 s+ 是 **`__pycache__` 全树遍历**：`_EXCLUDE_SCAN_DIRS` 漏了构建 venv `.venv-build/`，把第二套 site-packages 的 1 578 个 `__pycache__`（131.7 MB）算进「应用编译缓存」，`os.walk` 3 442 ms + 逐目录求和 375 ms（本机单次 `stat` ≈1.6 ms）。**✅ A4 已于 2026-11-03 落地**：排除 `.venv-build`（1 596 → 18 个目录、1 860 → 167 ms、131.7 → 9.4 MB）、日志统计每文件 stat 3 次 → 1 次（8.5 → 2.9 ms）、TTL 60 → 300 + SWR（失效点本来就在：三个清理入口与 `/cache-scan` 到 done 都会 `invalidate_stats_cache()`）、启动时用 `to_thread` 预热。**完整重算 2 235/5 205 → 188/170 ms，命中 ≈0**；`≤200 ms` 在「重算」这条路径上达成（命中路径远低于它）。原先建议的「`sqlite_stat1` 近似行数 / 计数器表」（①）在实测面前不成立 —— 精确计数只值 0.1 ms，换近似等于白丢正确性 |

### 表 B：并发下的系统性瓶颈

| # | 瓶颈 | 证据 | 根因 |
|---|---|---|---|
| B1 | **吞吐封顶 ~80 rps 且与并发无关**（重组 4 并发即到顶） | §3.1：TPS 43.9→81.2→79.7→73.7，缺陷率恒 0 | 单进程 Uvicorn + Python GIL：CPU avg 56%、峰值 269% 说明多核未用满，但同步 handler 全挤在 anyio 线程池。`__main__.py:280-284` 的 `--workers` 默认 **1**，且 `__main__.py:309-312` 注释自陈「已实测单 worker 51 → 多 worker 80 req/s，生产建议纯 Web 多 worker + 独立调度进程」——本次沙箱正是默认单 worker |
| B2 | **高并发尾延迟爆炸 + 超时** | 轻组 96 并发 P99 4296.9 ms、缺陷率 3.17%；v1 轮 `maintenance/status` 出现 P95 60 033 ms（= 客户端 60 s 超时判 000）而单发仅 1.6 s | 慢同步接口与快接口**共享 anyio 线程池（默认 40 token，代码未覆写）**；`CacheScanService` 进程级 `RLock`（`services/cache_scan.py:52,66`）把 `/cache-scan` 与 `/maintenance/status` 串行化，一个全树遍历就能让后续请求排队 → 排队即超时。**✅ D1 已于 2026-11-04 落地第一半**（16 条实测最重的同步接口改走 `CapacityLimiter(4)` 专用道，`web/slow_lane.py`）：同形沙箱里廉价接口 p50 **3 509.6 / 1 153.9 → 34.3 / 59.8 ms**、60 s 完成样本 **16 / 34 → 504 / 334**，且 `queue_p95` **974 → 6.7 ms** 而 `work_p50` 恒 ~4 ms —— 排队这一环被证实（拆分计时见 `d1_lane_probe.py`）。第二半（`cache_scan` 的进程级 `RLock`，D2）**✅ 已于 2026-11-05 落地**：时间片改到锁外走 + 同一路径只留一个推进者，迟到者立刻拿当前进度而不再排「别人那片还剩多久」（合成树同路径 8 并发到达 p95 **42.4 / 36.7 → 9.9 / 2.8 ms**、跨路径总墙钟 **81.6 / 83.6 → 16.4 / 17.1 ms**，扫到 done 的墙钟与终值不变 —— 见 `d2_lock_probe.py`）；原文建议的「按路径分锁」判定不落（三个调用点共用同一个 `_browser_data_dir()`、全进程只有一个实例），「共享工作队列」实测更差（到 done 4~6 波、墙钟 83.8 / 66.7 ms）。接口级 96 并发 P99/缺陷率仍待同一 JMX 复跑，见 OPTIMIZATIONS §D1 / §D2 落地记录 |
| B3 | **缓存无单飞（击穿）** | 长跑 12 并发下 avg 200 ms，而单用户仅 50 ms；`stats` 类接口 300 s TTL 到期时并发全量重算 | 被测实例（测时版本）的 `web/cache.py` 中 `_make_sync_wrapper` / `_make_async_wrapper` 冷/过期 miss 无 `single-flight`，N 个并发请求各自重算同一聚合（只有 `stale_while_revalidate` 分支有 `_refreshing` 标志）；缓存字典 `_cache_store` 无容量上限/淘汰，`invalidate_stats_cache()` 为全清。**本条已于 2026-10-31 按 OPTIMIZATIONS B1 落地修复，故本文所有数字均为「修复前」基线** |
| B4 | **重活放在请求路径上** | `GET /api/evaluations` 每请求全量拉 `eval.*` 事件（`limit=50000`）+ JSON 解析 + Python 侧过滤/分页/中位数；`/api/logs/search` 取 1000 条事件再 Python 子串过滤；`/api/export` 最多 5 万行全内存拼 CSV。**⚠️ `evaluations` 那半条在 A2 落地复核（2026-11-04）里被拆成两件事**：① 「全量拉 `eval.*`」里 **80% 的行（1 867 中的 1 490 条 `eval.passed`）读完即丢**，且前缀 `LIKE` 走全表扫 —— 改等值后取数段 218.7/256.2 → 35.9/65.3 ms；② 剩下的 230 ms 不在 SQL，而在逐条 enrich 的品牌归一化（`_compact_text` 单请求 24 000+ 次 `re.sub`，占 CPU 累计 54%）。另注意**本行数字测的是 `user_id="default"` 身份，该身份读到 0 行**，所以 195.9 ms 里不含这笔成本 —— 收益证据在主人态与 5× 副本上，见 OPTIMIZATIONS §A2 与 `a2_*.py` | `evaluations_list.py:676`、`repo_events.py:333-365`（另发一次 COUNT）、`api_logs.py:127-162`、`api_export.py:76-98`；`compute_market_stats_batch`（`evaluations_list.py:69-80`）在缓存 miss 时**按 task_id 逐个**跑 `_compute_sold_range`（N+1） |
| B5 | **写侧 8 并发饱和** | §3.3：TPS 66.4（8 并发，峰值）→ 51.9（16 并发，−22%） | SQLite WAL 单写者；连接池 20 但 `busy_timeout=10000` 会把写冲突变成「等待」而非报错，因此表现为吞吐回落而不是 5xx —— 与技能判据「饱和非逻辑回归」一致 |
| B6 | **无索引的过滤列** | 代码核对（`db_models.py` 的 `Index(...)` 与 `init_db` 的 `_migrate_create_index`）后确认下列被查询列**无索引**：`items.price`（`stats_price_trend.py:49-51` 区间过滤）、`evaluations.risk_level`（`stats_overview.py:134-141` 全表 `SUM(CASE)`）、`price_history.user_id`（`repo_price_history.py:126,137` 过滤但该表不在 `_MU2_TABLES`）、`orders.type`（`repo_orders.py:90`）、`task_links.updated_at`（`price_dashboard.py:421`）、`events.level` / 单独 `events.stage`；`error_logs.error_type/request_path` 用 `%LIKE%` 前后通配（`repo_error_logs.py:63-65`，索引不可用） | 数据量小时不明显，本轮 items 774/evaluations·events 数万，全表扫已是百毫秒级；随采集累积会线性恶化 |
| B7 | **503 语义污染可观测性** | 30 min 内 795 次 503 全部来自 `links/live`（100%） | 依赖（采集器）不可用时直接 5xx。它既是本次缺陷率的全部来源，也说明生产中该状态会淹没真实故障信号。**✅ 2026-11-03 E3 已落地**：`scheduler_off` / `browser_unavailable` 改为 HTTP 200 + 单条 SSE `{"stage":"error","status":503,"code":…,"detail":…}`，前端本来就按事件里的 status/code 还原错误，UI 行为不变；HTTP 5xx 不再被这类部署态灌满。404/400 与 `/links/refresh` 的 HTTP 语义保持不变 |
| B8 | **长跑内存呈爬升形态，但 30 min 不足以定论泄漏** | 长跑窗口 RSS `346.8 → 378.4 MB`（拟合 +75.9 MB/h），后半程趋平、负载停止后不回落；句柄同期恒定（627~631） | ~~与「进程内缓存 `_cache_store` 无容量上限/淘汰 + `invalidate_stats_cache()` 全清」（`web/cache.py`）以及分配器 arena 预热一致~~ **← 缓存这一半已被 B4 取证推翻（2026-11-05）**：全量 GET 扫一遍只留 7 条 / 0.02 MB，逐 task 打满外推全库 248 个 task 也只有 2.1 MB；合成 327 条时深量 0.66 MB 而 RSS Δ 26.4 MB。剩下的是分配器 arena / SQLite 页缓存 / 别处。若是真泄漏，句柄也应上涨，而它没有。**排除需 ≥4 h 复跑 + `tracemalloc`**（见 §4 未达成项与 OPTIMIZATIONS B4 落地记录） |
| B9 | **`stale_while_revalidate` 的「后台刷新」并未在后台**（来源：B1 落地时的实现复核 + 专项探针，**非负载观测**） | 探针（重算人为 0.6 s、TTL 1 s）：过期后首次调用墙钟 `0.602 s` 且返回**旧值**，第二次才 `0.000 s` 拿到新值；异步分支 6 次调用恒返回同一旧值、底层重算次数恒为 1 | `_maybe_refresh_stale` docstring 承诺守护线程刷新，函数体却在请求线程内直接重算后才返回旧值；`_maybe_refresh_stale_async` 在已运行的事件循环里 `asyncio.run(...)` → `RuntimeError` 被 `except Exception: pass` 吞掉，缓存从此不再刷新。全仓现仅 `maintenance_status`（同步 `def`；A4 后 TTL 300 s）在用，故本轮只表现为「过期请求付全款」；异步那条是给下一个 SWR 端点挖的坑。详见 OPTIMIZATIONS B5 与 `swr_probe.py`。**本条已于 2026-10-31 随 B1 之后的 B5 落地修复**（同步分支真起线程、异步分支改 `create_task`），修复后探针打印「过期请求被重算阻塞 = False / 值被冻结 = False」；表中其余数字仍为修复前基线 |

### 表 C：必须与「服务缺陷」区分开的环境/工具口径

| 现象 | 判定 | 依据 |
|---|---|---|
| `POST /api/tasks/{id}/links/refresh`、`/api/evaluations/batch-collect-official` 100% 503（v1 写基线） | 环境口径（非容量缺陷），已从负载轮剔除 | 沙箱关闭调度器 → 采集器不可用 |
| 写组 4xx 率 61.5%、长跑 422/400 共 20 068 次（`logs/search`、`logs/export`、`items/batch`、`error-logs`、`links/lookup` 等 8 类各 795~799 次全量 422） | 夹具口径（合成 query/body 未补齐必填参数），**不计入服务缺陷** | 422 = FastAPI 必填参数校验失败；技能规定错误率与缺陷率必须区分 |
| 轻组基线受一次旁路实验干扰（17:28 打开 45 路 SSE 共 25 s） | 该 25 s 落在 `baseline_light` 窗口内，占比 <1% 样本；已知偏差，报告中如实标注 | 副作用是我造成的，非被测系统行为 |

## 6. 偏差与局限（不粉饰）

1. **负载生成器与被测服务同机**（Windows、D 盘剩余 3 GB）。技能判据明确这会让尾延迟虚高；因此本文的 P95/P99 是**上界**，而「拐点相对位置」仍可比。旁证：轻组在 16 并发下 P50 仍健康，说明非纯机器饱和。
2. **`python_replay` 模式的绝对 TPS 低于真实 JMeter 分布式能力**（GIL + 线程模型，96 并发已近客户端上限）。因此「最大并发能力」三档数字应读作「在本客户端下观察到的饱和点」，不是服务端极限。
3. 重组阶梯（§3.1）使用 v1 分组（含 1 个 SSE），修正分组后未补跑 32/64 档，避免与基线不同构对比 —— 该组结论止步于 16 并发。
4. 耐久仅 30 min，未满足 ≥4 h；长跑中 `threads` 指标在本机采样接口返回空值，故未纳入泄漏判据（句柄数与 RSS 双指标已覆盖同类风险）。
5. v1 轮重组阶梯（2/4/8/16 并发）的**逐请求原始样本**在修正分层时被清理，因此 `results/agg_phases.json` 中不含这四档，只有本文件 §3.1 抄录的汇总数字（当时按配置时长 60 s 计算，与该轮引擎实测墙钟一致）。v2 轮 10 个阶段的原始样本全部留存。
6. 写压测在沙箱副本库留下了种子数据（约 230 次任务创建等）。副本已随临时目录删除，生产库未被写入。
7. **口径修正（复核时发现并已改正）**：首版聚合器按 v1 配置的时长去除 v2 轮的样本数（`baseline_light` 用 300 s、`soak_mixed` 用 3600 s），而这两阶段实跑 180 s / 1800 s，导致吞吐被系统性低估一半（曾误报 11.8 / 29.8）。现 `aggregate.py` 改为从引擎日志取每阶段**实测墙钟秒数**，本文件与 `results/*` 均为修正后数字；百分位、缺陷率、4xx 率不受影响。
8. **口径修正之二（资源采样）**：`sut_resource.csv` 末尾有 47 行是 SUT 被定向关停之后写入的（`rss_mb=0`），首版把这些 0 值行一并计入统计，于是得出「末段 RSS 回落到 350 MB、CPU 均值 55.4%、2 111 点」——其中「回落」是 0 值稀释造成的**假象**，且当时被当作「无泄漏」的证据。现 `aggregate.py` 只统计存活样本（2 085 行，原始 2 132 行），得到 RSS 不回落、长跑窗口内 +75.9 MB/h 的爬升形态，据此把该风险改判为「未定论」（见 §4 与 B8），并把缓存容量上限（OPTIMIZATIONS B4）从低优先级提为中优先级。

## 7. 可追溯性

本目录留存：

| 文件 | 内容 |
|---|---|
| `jmx/reads_heavy.jmx` `reads_light.jmx` `writes_idem.jmx` `mixed_biz.jmx` | 4 份 JMX 脚本（配置驱动生成，主机/端口经 `${__P(host)}/${__P(port)}` 注入） |
| `jmeter-config.yml` / `jmeter-config-v2.yml` | 两轮唯一参数来源（沙箱、阶梯、缺陷规则、报告口径），含改 4 h 长跑的入口 |
| `tier-rules.json` / `build_scenarios.py` / `emit_config.py` | 端点分层规则与场景生成（换业务 = 改 JSON，不改代码） |
| `endpoint-coverage.json` | 272 端点的分层、真实响应码原因、`reason` 逐条可查 |
| `results/*.jsonl.gz` | 10 个阶段的逐请求原始样本（gzip-9） |
| `results/agg_phases.json` | 每阶段 × 每接口的样本数/TPS/P50/P90/P95/P99/最大/缺陷率/4xx/状态码分布 |
| `results/engine_v2.log` | v2 轮引擎逐阶段日志（含每阶段**实测墙钟 `wall_sec`**，是 `aggregate.py` 计算吞吐的分母来源；见 §6.7） |
| `results/ladder_table.md` `endpoint_table.md` `soak_table.md` `resource_summary.json` `probe_probe_single.json` `sut_resource.csv` | 阶梯表 / 136 接口全量表 / 长跑分窗 / 资源采样（原始 2 132 行，存活 2 085 行）/ 67 端点单发探针 |
| `setup_sandbox.py` `coverage_probe.py` `aggregate.py` `patch_and_v2.py` | 复现所需的环境搭建、探针、聚合、口径修正脚本 |
| `soak_trend.py` | §6.8 判定所用的资源趋势脚本（窗口斜率 + 分段，只统计存活样本；直接读归档即可复现 §4 的内存/句柄结论） |
| `swr_probe.py` | 表 B B9 的取证探针（同步 + SWR 的过期调用被重算阻塞；异步 + SWR 值被冻结）。B1 落地时的实现复核产物，非负载测量；B5 修复后它同时是判据——两条「判定」均应打印 False |
| `config_memo_probe.py` | 表 A `config/raw`、`config/share` 那两行的成本拆分判据（读文件 / 解析 / 脱敏 / 序列化各占多少），并顺带测出「libyaml 在而未用」（OPTIMIZATIONS C6）。C6 落地时已把它改成**逐份**比对仓库 7 份 YAML 的 dump 产物 —— emoji 分歧就是这样抓到的（单测一份会误判成可换）。只读配置、只打印量级与耗时，不打印内容 |
| `dir_size_probe.py` | C1 的落地判据：合成快照树量「两遍遍历 vs 一遍」（含逐字段一致性），并量「遍历期间事件循环还能推进几次心跳」——后者是 `to_thread` 那半条的收益证据（修前 1 次 vs 修后 376 次）。只在自建临时目录里造文件，不读真实 `data/chromadb` |
| `loopback_connect_control.py` | C4 ① 的**前提**判据（证明不是「想当然」）：回环上没监听的端口到底是 RST 还是静默丢包。四格对照（监听不 accept / 监听并 accept / 真实 HTTP 往返 / 空闲端口在 1.0 s 与 0.1 s 下）。本机结论：空闲端口吃满 timeout（~1 010 / ~109 ms），accept 的端口 0.76 ms |
| `anticrawl_probe_cost.py` | C4 ③ 的成本判据：造 Chromium 形状的 Cookie 假库（2 000 / 5 000 行），比「全表取 name + Python 求交集」与「name 下推 + `LIMIT 1`」，并核对布尔结果一致；另量 JSON 分支。只在临时目录造库，绝不打开真实 Chromium Cookie 库 |
| `c4_after_probe.py` | C4 落地后的验收探针：走真实代码路径量 CDP 关闭态这次探测（落地 109 ms vs 写回 1.0 s 的 1 014 ms）、真 accept 端口仍判可用、Cookie 分支（命中 0.87 ms / 全表未命中 2.47 ms）、完整 `evaluate()` 地板 |
| `c4_mutation_run.py` | C4 回归用例的变异验证（串行四腿 + 一条负对照：改 `infra/browser.py` 另一处超时应全绿），每腿打印翻红清单并在结束前还原、核对 sha256 |
| `maintenance_cost_probe.py` | A4 的成本拆解判据：真实库以 `mode=ro` 逐张量 `SELECT COUNT(*)`（推翻「计数慢」的归因）、日志两遍/一遍 stat、增量扫描冷热、`_iter_pycache_dirs` 全树（抓出 `.venv-build` 漏排除）、完整 `maintenance_status(bypass_cache=True)`。只读库与目录，不写任何东西 |
| `a4_mutation_run.py` | A4 回归用例的变异验证（5 腿串行 + 负对照：改同一排除集合里的 `dist` 应全绿），每腿翻红 ≥1 例，结束前按 sha256 核对两个源文件已还原 |
| `e3_mutation_run.py` | E3 回归用例的变异验证（3 腿串行：退回 HTTP 503 / 事件丢 `code` / 媒体类型不是 SSE；负对照：改相邻 400 的 detail 应全绿），结束前按 sha256 核对源文件已还原 |
| `a2_evals_probe.py` | A2 的取数判据：在生产库的**一致性快照副本**（sqlite3 `backup` API 拷到临时目录并 `chdir`，生产文件不被打开）上逐阶段拆 `/api/evaluations` 的取数成本（SELECT / `_row_to_dict` / COUNT 各占多少），打 `EXPLAIN QUERY PLAN` 证明前缀 `LIKE` 走 `SCAN ix_events_created_type` 而等值走 `SEARCH ix_events_type_created`，并在 5× 合成副本上看斜率 |
| `a2_enrich_probe.py` | A2 的**身份**判据（本条结论反转的起点）：同一条请求分别以沙箱身份（`user_id="default"`）与数据主人身份直调，打印 median / 冷启动 / `total` —— 前者 `total=0`；再用 cProfile 把主人态那次调用的 CPU 归因摊开（enrich 54% vs 取数 22%）。全程只跑只读 GET，且同样在快照副本上，不碰生产库 |。A2′ 落地后同一探针复跑（原版缺 `DATA_UID` 赋值，主人态按 `user_id=''` 永远读 0 行，已就地补上）：`_enrich_eval_record` 706.9 → 256.3 ms、`_compact_text` 73 251 → 2 367 次（详见 OPTIMIZATIONS §A2′）
| `a2_ab.py` `a2_growth.py` | A2 前后各两轮的 A/B（每轮 7 次取中位，同时打印每请求 SQL 条数与取数段行数/耗时）与 5× 副本的前后腿交替 runner；产物 `results/a2_ab_*.json` `results/a2_growth_5x.json` |
| `a2_mutation_run.py` | A2 回归用例的变异验证（9 腿串行 + 负对照：只改等值方法的 `limit` 默认值应全绿）。含基线全绿前置校验（基线不绿则本轮无效）与逐腿 `stillDiffers` 回读（防并发将变异覆盖回原文），结束前按 sha256 核对 6 个源文件逐字节还原 |
| `a2_prime_mutation_run.py` | A2′ 回归用例的变异验证：2 条应红（别名表退回未压缩 / 组交集退化成 `or`）+ 2 条应绿（接错对象：给通用后缀表加一个用例里不存在的成员；只改 docstring 措辞）；判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置，结束按 sha256 核对 `collector_utils.py` 逐字节还原 |
| `c3_templates_probe.py` | C3 残余的判据：模板文件（presets YAML 2.0 KB / user JSON 29.8 KB）的 read / read+parse / 模块加载函数各 21 次取中位，以及 `GET /api/templates` 处理器的整段耗时。落地前 2.23 ms → 落地后 0.53~0.58 ms；同时量出命中路径的残余就是两次 `os.stat`（本机中位 0.28 ms/次）与 deepcopy 的真实代价（112 条 1.14 ms ≈ 重新 `json.load`，故不采用） |
| `c3_mutation_run.py` | C3 回归用例的变异验证：3 条应红（指纹不参与命中判定 / 写盘后不作废 memo / 命中分支直接返回缓存对象）+ 2 条应绿（异常元组顺序等价改写 / 只改 docstring）；判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置，结束按 sha256 核对 `api_templates.py` 逐字节还原。显式给 `--basetemp`：本机 `C:\pyfix_tmp` 的 pytest basetemp 偶发建不出子目录，属环境问题 |
| `a1_index_probe.py` | A1 的判据：在合成库（每表 2 万行，约为真实 items 774 / task_links 2 050 的 10~25×）上逐条试候选索引 —— 看 `EXPLAIN QUERY PLAN` 是否真的改用它、15 次中位耗时是否下降、以及写侧 3 000 行插入的代价；固定目录带 PID 后缀，可用 `A1_PROBE_DIR` 保留库给复合实验。结论：items.price / evaluations.risk_level / orders(type, created_at) 有效，price_history.user_id 与 task_links.updated_at 判定不落 |
| `a1_index_probe_composite.py` | A1 的追加实验（接主探针保留的库）：单列 `orders(type)` 实测更慢，复合 `(type, created_at)` 才从 4.08 → 0.50 ms 并去掉 `TEMP B-TREE`；`task_links(link_type, updated_at)` 虽被选用但只 −4%，成本在逐行 `json_extract` |
| `a1_mutation_run.py` | A1 回归用例的变异验证：4 条应红（删 items/orders 迁移行、ORM 里把 orders 索引改错列、把判定不落的 `price_history.user_id` 加回来）+ 2 条应绿（重复 seller 索引 / 只改 docstring）；判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置，结束按 sha256 核对 `db_models.py` 逐字节还原 |
| `d1_inventory.py` | D1 的**形状**前提清点（纯 AST，不启动应用、不连库）：277 个路由处理器里 231 个同步 / 46 个 `async def`（83.4%），并逐条查 D1 点名接口的形状 —— 「廉价接口被饿」只有当它自己也占令牌时才是线程池问题（三条廉价接口实测都是同步 `def`，前提成立）。顺带钉出两处清点脚本自身的错：`/delay` 真名是 `/freq/delay`，同路径的 GET 与 PUT 形状不同，所以索引必须含 method |
| `d1_lane_probe.py` | D1 的**机制**判据：起一个与生产同形的最小 uvicorn（同步 `Depends` + 3 000 行响应体），把每次请求拆成 `queue_ms`（等令牌+派发）与 `work_ms`（处理器自己算），跑 6 条臂（空载地板 / IO hog×40 吃满共享池 / IO hog 走专用道 cap=4 / 不限容第二道 / 只把全局令牌抬到 128 / CPU hog 两态）× 2 轮。产物 `results/d1_lane_arms{,_r2,_c,_c2}.json`。「不限容第二道」与「抬到 128」是刻意的**负对照**，两条都比专用道差（p50 113.5~341.6 ms 与 49.1~174.0 ms vs 34.3~59.8 ms）→ 起作用的变量是**上限**，不是「有第二条道」。`_c.json` 整轮 `server not ready` 是固定沙箱目录被并发探针互相 `rmtree` 的事故现场，修法见 OPTIMIZATIONS §D1 落地记录末条 |
| `d1_hog_nature.py` | 慢族的成本**性质**判据：同形功逐条量 per-call 耗时、`cpu_over_wall` 与忙等期间的事件循环心跳比 `ticker_ratio`（纯 Python 忙等 **0.492** vs `time.sleep` 锚点 **1.105**）→ CSV 序列化 5 000 行 181.3 ms、JSON 143.6 ms、`_dir_size` 同形遍历 244.4 ms、rglob 57.8 ms 都是**进程内 CPU 功**：隔离不消除成本，只钉住它能伤害的并发面；这也是 CPU-hog 臂那 ~7~11 s 归因于 GIL 而非排队的出处。产物 `results/d1_hog_nature.json` |
| `d1_lane_budget.py` | 专用道**容量 4** 的依据（不是拍脑袋）：按 `agg_phases.json` 逐接口吞吐 × 基线单次占用做 Little's law 预算（在途需求 = Σ tps×hold，上限 = cap ÷ 平均占用）。最坏的 `stress_light_t96` 档慢族在途 **2.14**、需求 4.9 rps < cap=4 的 **9.16 rps** → 够用且不把慢族自己堵死。产物 `results/d1_lane_budget.json`（首版把 `mean_hold` 误写成 `total_inflight/total_inflight`，恒报 1 000 ms 并误判「堵道」，已订正） |
| `d1_mutation_run.py` | D1 回归用例的变异验证（**12 腿串行**：不限容 / 去 `functools.wraps` / 投递丢 kwargs / 跨循环单例 / 不清 `cached_property` / 不重建 `route.app` / 名单失配不报错 / 去掉 `capacity<=0` 关闭 / 不剥 `/xianyu` / 静默删名单一条 / **忘传 `limiter=`** / 配置默认值 4→0；负对照只改 docstring 措辞必须全绿）。判定**只认 `exit == 1`**，注入前 `ast.parse`、锚点唯一性前置、逐腿超时，结束按 sha256 核对 `slow_lane.py` + `config.py` 逐字节还原。产物 `results/d1_mutation.log`（`*.log` 按 `.gitignore` 不入库，判据可由脚本原样复现）；`results/d1_mutation_first_pass_selfheal.log` 留着首版 L01 **变绿**的现场 —— 它只改了构造、保留了 `elif total_tokens != capacity` 的夹逼，第二次取用时容量自愈，是「腿必须改得动才算证据」的样本 |
| `d2_lock_probe.py` | D2 的**机制与取舍**判据：合成树（60 目录 × 300 文件 = 18 000 文件 / 17.58 MB，一次全扫 50.3 ms）上，切片预算**由全扫耗时自动除以 `--slice-div` 得出**（默认 20 → 2.51 ms），刻意复刻生产「0.5 s 片 vs 大目录」的比例 —— 第一版把预算写死，片子远短于树，锁的持有根本看不见，这是所有「并发下没差别」结论的经典来源。三条臂（现状 / A·单推进者 / B·共享队列）× 三种到达形态：同路径 8 次并发（调用 p95 42.4 / 36.7 → **9.9 / 2.8 ms**）、两路径 2×8（总墙钟 81.6 / 83.6 → **16.4 / 17.1 ms**）、以及**波数 runner**（`run_waves_until_done`：每波所有请求同时到达，整波返回后下一波才发）。波数那条是关键反转 —— 早先用「间隔 sleep + 轮询」的 runner 把「到达稀疏」误读成「丢手」，得出 A 比现状慢 2.4× 的**假结论**；换成确定性整波后结论反过来：A 到 done 3 波 44.4 / 41.1 ms（现状 1 波 42.6 / 39.8 ms，墙钟不变），B 却要 4~6 波 **83.8 / 66.7 ms**，故 B 判定不落。另有正确性两查（单推进终值 == 一次扫完的参照、并发 burst 后再扫终值仍逐字段相同且 `scanned_entries` 不超过条目上限 18 060）与单到达吞吐（同一片 A 扫到 6 660 条目 > B 5 460）。产物 `results/d2_lock_probe.json`；临时树带 PID 后缀，`finally` 里删除 |
| `d2_mutation_run.py` | D2 回归用例的变异验证（**8 腿串行**：时间片请回锁内 / 去掉同键名额 / 名额置了不清 / 名额改全局 / 去掉 TTL 过期重建 / 在飞那次不回读进度表 = 6 条应红，「接错对象」把守卫加在 `invalidate()` 上 + 只改 docstring 措辞 = 2 条应绿）。判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置、逐腿超时，结束按 sha256 核对 `cache_scan.py` 逐字节还原。**这轮暴露了一个环境级事实**：负对照腿红了一次，红的是 D1 的 `test_slow_lane.py::test_cheap_route_not_blocked_by_slow_lane`，而同一轮**未加任何变异的基线**也红且 51 例从 22 s 涨到 198 s（现场 `results/d2_mutation_negctl.log`，`*.log` 按 `.gitignore` 不入库，`--only 负对照` 可原样复现）—— 机器被别的会话的负载（另一项目的 JMeter 探针、uvicorn、pip 安装、多个 MCP 常驻）占住时，那条「在途峰值取样」用例的 `hold_ms` 30/60 窗口与调度延迟同量级，自己会抖。据此加 `--drop-slow-lane` 口径（D1 的用例不参与本条裁决，其余判据一条不少），并给摘要加了 `E   ` 断言行以留住失败原因 |
| `b4_cache_census.py` | B4 的**形状**判据（纯 AST + ASGI transport，不依赖真 uvicorn）：清点全仓 8 个 `@cached_ttl` 站点及其 key 模板，再把 `results/per_endpoint.json` 里 106 个 GET label 全扫一遍，逐请求记录缓存条目数与 `deep_bytes`（`id()` 去重深量）；另做一次「过期条目不回收」实证（拨时间戳 → 读 miss、但条目仍在，保活 44 474 B）。结论是**值体积维度推翻原文归因**：全量扫一遍只留 7 条 / 23 620 B、单条最深 8 139 B。产物 `results/b4_cache_census.json`，控制台落 `results/b4_census_run.log`（`*.log` 不入库） |
| `b4_key_ceiling_probe.py` | B4 的**键基数**判据：A 段用沙箱库（`mode=ro`）里 60 个真实 task 各打 `trend` + `histogram`（→ +120 条 / 539 865 B，每 task 2.0 条 / 8 997.75 B）；B 段伪造 200 个前端从不发送的 `metric`（→ +200 条 / 128 090 B，单条 640.45 B）；终点 327 条、深量 0.66 MB，而同场 RSS 从 144.2 → 170.6 MB（**Δ 26.4 MB ≈ 深量 40 倍**）；最后按沙箱 248 个 task 外推全库只有 2.1 MB。产物 `results/b4_key_ceiling.json` + `results/b4_key_ceiling_run.log`（不入库） |
| `b4_mutation_run.py` | B4 回归用例的变异验证（**7 腿串行**：退回无上限 dict / 上限套用变空操作 / 读命中不续命（LRU→FIFO）/ 非正上限放行 / 装配期漏接 create_app = 5 条应红，「接错对象」把前缀匹配改成等价 `any()` + 只改 docstring 措辞 = 2 条应绿）。判定只认 `exit == 1`、注入前 `ast.parse`、锚点唯一性前置、逐腿超时，结束按 sha256 核对 `web/cache.py` + `web/app.py` + `infra/lru.py` 逐字节还原。产物 `results/b4_mutation.log`（不入库，可由脚本原样复现） |

复现：`python setup_sandbox.py` → 启动沙箱实例并写 `sut.pid` → `python emit_config.py` → `gen_jmx.py --config jmeter-config.yml` → `python_replay_engine.py --config jmeter-config-v2.yml --all` → `python aggregate.py`。
若只重算聚合而不重跑负载：把 `results/*.jsonl.gz` 解回 `results/`（文件名须为 `<phase>.jsonl`）并保留 `results/engine_v2.log` —— `aggregate.py` 的吞吐分母取自该日志的实测墙钟，缺失时会退回配置时长（这正是 §6.7 记录过的坑）。
