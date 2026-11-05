# Changelog

本文件记录 XianyuHunter（闲鱼猎人）的所有版本变化，遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式与 [Semantic Versioning](https://semver.org/lang/zh-CN/) 2.0.0 规范。

版本号变更规则见 [VERSIONING.md](docs/standards/VERSIONING.md)。

---

## [Unreleased]

### Changed

- **根目录整理（2026-09-30）**：散落文件归类，根目录文件数 27 → 24
  - 移动：`run_sonar.bat` → `scripts/`；`VERSIONING.md` → `docs/standards/`（同步更新 CHANGELOG、概要设计文档、目录结构.md 中的引用链接）
  - 删除（维护者确认的一次性 Sonar 调试快照，可从 git 历史找回）：`issues.json`、`qg.json`、`sonar-scan-log.txt`
  - 删除（维护者确认）：`.cleanup-quarantine/`（过期隔离区）、`cleanup-logs/`（第五轮清理审计记录，含 frontend/data 删除取证，经确认不留档）；保留 `cleanup-config.yaml` 供周期性维护复用
  - 保留根目录（工具约定/相对路径引用）：`xianyu-hunter.spec`、`installer.iss`、`.build-counter.json`、`coverage.xml`/`.coverage`、各构建与配置文件

### Removed

- **彻底移除 Docker 部署功能（2026-09-30，维护者确认）**
  - 删除 `Dockerfile`、`docker-compose.yml`、`.dockerignore`
  - 改写 `app.py` healthz 注释、`setup-env.ps1` 版本对齐注释
  - 保留 `security.py`/`logger.py` 中 `/docker-compose` 探测屏蔽条目（防外部扫描，与项目自身部署无关）
  - 文档同步：README、部署指南（删 Docker 章节、重编号、命令改原生部署）、目录结构.md、docs/README.md、cleanup-config.yaml 白名单
- **工作空间第六轮清理（2026-07-09）**：根目录文件数 49 → 17，删除 32 个调试/日志/一次性脚本产物，释放约 10.13 MB 空间。备份日志 `logs/cleanup-20260709-210649.log`。
  - 误重定向：`17_xianyu`、`ubprocess; r=...`（特殊字符文件，使用 .NET `File.Delete` 绕过 `Remove-Item` 限制）
  - SonarQube 一次性 Python：`check_s6759.py`、`list_issues.py`、`merge_sonar.py`、`parse_sonar.py`
  - SonarQube 调试输出：`sonar-scan-output.txt`、`sonar_issues_filtered.txt`、`sonar_open_issues.json`、`sonar_scan.log`、`sonar_scan_err.log`、`sonar-scan-log.txt`、`sonar_fix_guide.md`、`pytest-quick-results.txt`
  - 运行时/构建/调试日志：`run.stdout.log`、`run.stdout.2026-07-05_00-45-22_759023.log`（9.77 MB 历史日志）、`debug.log`、`build.log`、`build2.log`、`tsc.log`、`tsc2.log`、`t1.log`、`t2.log`
  - skill 集成/git 调试：`stash-full.patch`、`final_status.txt`、`git_op_log.txt`、`git_status_check.txt`、`tmp_skill_new.md`、`dist_list.txt`
  - 临时脚本：`test-parse.ps1`、`verify_msg.ps1`
  - 截图：`mobile-blank.png`
  - frontend 调试：`frontend/.env`、`frontend/frontend-build.log`、`frontend/tsconfig.tsbuildinfo`、`frontend/vitest-result.json`
  - 稳定性检查二次清理：`b1.log`、`b2.log`、`check_status.ps1`、`_temp_overview.html`（其他 AI session/后台进程在首次稳定性检查后继续创建）
  - 新增 `.gitignore` 规则：`/check-*.ps1`、`/_temp_*.html`、`/check_s*.py`、`/list_*.py`、`/merge_sonar.py`、`/parse_sonar.py`、`/*_sonar.py`
  - 扩展 `forbid-root-temp-files` pre-commit 钩子，覆盖上述新模式
  - 验证：`import xianyu_hunter` 退出码 0，二次稳定性检查（10s）无新文件
  - 注意事项：`Remove-Item -LiteralPath` 对含 `;()` 等特殊字符的 PowerShell 误重定向文件返回 SUCCESS 但实际未删除，必须用 `[System.IO.File]::Delete($path)` 兜底

### Fixed

- **进程内 TTL 缓存的并发击穿（single-flight）**：`web/cache.py` 的冷/过期 miss 路径没有任何合并，TTL 到期的瞬间 N 个并发请求各自重算同一份聚合 —— 全量 API 性能测试实测「单用户 avg 50 ms 的接口，12 并发长跑变 200.5 ms」，重算还占住 anyio 默认 40 token 线程池，把 8.4 ms 级廉价接口在 96 并发拖到 2 s 以上。现按 key 登记在途重算：抢到 lead 的调用执行并写缓存，其余等待同一结果（同步用 `Event`、协程用同循环内的 `Future`，跨事件循环不合并）；`bypass_cache=True` 保持不读缓存、不并入他人在途重算的原语义；异常不入缓存并原样传给跟随者。回归见 `tests/test_cache_single_flight.py`（9 例，含变异验证）。证据与量化预期：`perf/2026-10-31-full-api/OPTIMIZATIONS.md` B1（本文档所有压测数字仍是修复前基线，待复跑）。
- **`stale_while_revalidate` 的「后台刷新」并不在后台**（落地 B1 时复核出来的，不是压测观测到的）：`_maybe_refresh_stale` 的 docstring 写「启动守护线程异步刷新」，函数体却在请求线程里直接重算完再把**旧值**返回 —— 于是 `/api/maintenance/status` 每 60 s 窗口后的那一个请求付全款（专项探针实测 0.602 s ≈ 完整重算耗时），客户端还拿到过期数据。`_maybe_refresh_stale_async` 更糟：它在已运行的事件循环里调 `asyncio.run(...)`，必然抛 `RuntimeError` 并被 `except Exception: pass` 吞掉，缓存从此冻结在第一个旧值上（探针里过期后连续 6 次返回同一个值）。现同步分支真起 `cache-swr-refresh` 守护线程、异步分支改 `loop.create_task(...)`（任务强引用挂在 `_bg_tasks`，asyncio 对任务只持弱引用）；两条分支的重算都并入上面的单飞，所以后台刷新与同时到达的冷请求互并而不各算一次；刷新失败只记 WARNING、保留旧值并释放名额，下一个 TTL 窗口可重试。回归见 `tests/test_cache_swr_refresh.py`（6 例：过期不阻塞、后台写回、同 key 只刷一次、失败保留旧值且可重试、SWR 下 `bypass_cache` 仍强制重算、异步不再冻结）；`perf/2026-10-31-full-api/swr_probe.py` 的两条「判定」现均为 False。详见 OPTIMIZATIONS B5 / REPORT 表 B9。
- **配置 API 每次请求重复全量解析 YAML（B2 复核后的安全残留）**：`web/routes/api_config.py` 的 `_load_yaml()` 每个请求都重读 `config.yaml` 并 `yaml.safe_load` 整份配置，绕过了 `infra/yaml_config.py` 已有的单例缓存。实测（`perf/2026-10-31-full-api/config_memo_probe.py`）7.2 KB / 35 个顶层键的一份配置，读文件 0.36 ms 而**解析 52.5 ms** —— `/api/config/raw`、`/share`、`/preview`、`/save`、`/import`、`/export` 六个端点每个请求都在付这 52 ms。现按文件指纹（路径 + `st_mtime_ns` + `st_size`）memo 解析结果并返回 `copy.deepcopy`（调用方会就地合并/脱敏，共享对象等于把缓存写脏），6 处 `CONFIG_FILE.write_text` 后配对 `_invalidate_yaml_memo()`：只靠指纹不够，Windows 文件系统时间粒度约 15.6 ms，「POST /save 后前端立刻读」恰好可能落在同一个 tick，且同尺寸改动（等长字符串/数字）是常见形态。另加**存储侧代次护栏**（`_yaml_epoch`）：请求可能在「stat → 解析」中间被另一个请求的写盘插队，解析完仍会把写盘前的旧值存回 memo，因此只在写回时比对代次（读侧比对是无效的——坏值已经进了 memo）。用文件指纹而**不用 TTL**是为了零陈旧语义：手工编辑配置文件下一次读就跟上。残余边界已钉成用例（`test_out_of_band_same_fingerprint_write_is_the_residual_gap`）：绕过本模块的跨模块写盘（`api_tunnel`/`api_notifier`/`api_batch_refresh`/`infra/ai_usage`）只有指纹在挡。回归见 `tests/test_config_yaml_memo.py`（13 例：只解析一次、外部编辑立刻生效、同尺寸+同时间戳的写盘仍读到新值、备份恢复路径、并发读不把写盘前的旧值存回 memo、切换文件不串味、返回值不被写脏、连续两次保存不互相覆盖、`/raw` 端到端、跨模块写盘的残余边界、AST 源码门禁「每个写盘点必须配对作废」+「写盘点数量下限」）；五条退化变异各自翻红（冷/命中两条 return 都去深拷贝 1 例、指纹不参与判定 2 例、去掉 6 处作废 3 例、指纹去掉路径 1 例、去掉代次护栏 1 例）。
- **同一轮复核撤回了 B2 原建议的 4/5 条**（只改文档，不改代码）：`maintenance/cache-scan` 是「每请求推进一片」的状态机，缓存会让进度永远停在 `scanning`、前端「统计中禁止清理」的护栏把清理功能永久锁死；`vector-admin/snapshots` 与 `anticrawl/strategy` 的旧值会在用户自己的下一步操作（建/删/恢复快照、点初始化、开 CDP 浏览器）后被当场看见；`config/raw` 与 `config/share` 在 `frontend/**` 零调用，把它们放进 `_cache_store` 等于让整份明文凭据驻留进程 30~120 s，与 B4「导出类不进缓存」相反。反证与修订后的降本口径见 `perf/2026-10-31-full-api/OPTIMIZATIONS.md` §B2、§C3、新增 §C6，以及 REPORT 表 A 对应三行。
- **新发现（未落地，已给判据）：PyYAML 在用纯 Python 后端而本机 libyaml 已装**。同一份配置 `CSafeLoader` 解析 4.7 ms（`safe_load` 52.5 ms）、`CSafeDumper` 序列化 6.3 ms（`safe_dump` 32.9 ms），在 `config.yaml` 这一份上输出逐字节相同。这比给前端不消费的接口挂 TTL 缓存对症得多，列为 OPTIMIZATIONS C6（高优先、成本极低）；本轮按「只做安全残留、不引入真实失败」的口径**没有**切换后端，先补等价性用例再逐文件切。（该条当时另外两处判断已在下一轮复核订正：「逐字节相同」只在 BMP 字符上成立；`/import` 收的是 JSON body，全仓并无 `YAMLError→400` 的语义。）
- **配置读取切到 libyaml C 后端（OPTIMIZATIONS C6，只落读侧）**：`yaml.safe_load` 绑的是 PyYAML 的**纯 Python** `SafeLoader`，而本机 libyaml 已在依赖里 —— 同一份 7.2 KB `config.yaml` 解析 52.5 ms（落地后同机复测 `yaml.safe_load` 47.7 ms → `CSafeLoader` 6.5 ms，**7.4×**）。新增 `infra/yaml_io.safe_load`（`_LOADER = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader`：PyPI 的 PyYAML 是否带 `_yaml` 取决于安装平台，精简镜像里纯 Python 后端是真实形态），9 个读盘点改走它 —— `api_config._load_yaml`、`infra/yaml_config.load_yaml`（启动与每次 `reload_config()` 都要读）、`repo_param_rules`、`ai_usage`、`menu_manager`、`api_templates`、`api_tunnel`、`api_notifier`、`api_batch_refresh`。**写侧刻意不换**：`CSafeDumper` 对超出 BMP 的字符（模板图标 `📷`（码位 U+1F4F7））不遵守 `allow_unicode=True`，会写成 `"\U0001F4F7"` 而纯 Python 写原字符 —— 两者读回同一个值，但磁盘产物变了，而写盘/备份/`_diff` 都建立在「同一个 dict 两次 dump 结果一致」上，所以序列化那 21~27 ms 不拿；`config/raw` 的稳态因此是 ~90 ms 而不是先前估的 60~70 ms。前置判据 `tests/test_yaml_io_equivalence.py`（56 例：10 种真实形状 × 仓库在用的 3 组 dump 参数 × 交叉读写、空文档返回 `None`、坏 YAML 抛**同一个异常类**（`menu_manager` 的 `except yaml.YAMLError` 覆盖面不能缩）、`!!python/object` 照旧被拒、文件对象入参仍支持、仓库 7 份真实 YAML 逐份读出相等、后端选择跟 `hasattr(yaml, "CSafeLoader")` 走、以及一条钉住「C dumper 的 emoji 写法确实不一致」的用例挡住将来「顺手把 dump 也换过去」）；`tests/test_config_yaml_memo.py` 的计数 shim 随之内移到 `api_config.yaml_io`。变异验证两腿（`_LOADER` 硬编码成纯 Python → 1 例；换成 `FullLoader` → 3 例）；第二腿最初只翻 1 例，因为 `!!python/object/apply` 连 FullLoader 都拒 —— 据此补了 `test_rejection_is_not_vacuous`，正面断言「FullLoader 接受 `!!python/name`、我们拒」，这条安全判据才真的有牙。
- **快照目录体积被重复遍历（OPTIMIZATIONS C1）**：`web/routes/api_vector_admin.py` 的 `_list_snapshots` 为了分别填 `size_bytes` 与 `size_display`，对同一个快照目录连调两次 `_dir_size(d)`，等于把每棵子树走两遍（`GET /api/vector-admin/snapshots` 基线 avg 233.8 ms / 长跑 450.7 ms 的主因，且该接口按 B2 的结论**不能**挂缓存 —— 建/删/恢复快照后列表必须立刻反映磁盘现状）。现改为一次求值后复用；主目录体积抽成 `_main_data_size()`，`GET /status` 的「主目录 + 全部快照」两段遍历合并成同一次 `asyncio.to_thread` 跳转。判据 `perf/2026-10-31-full-api/dir_size_probe.py`：同一棵合成树（8 目录 × 400 文件）两遍 11 811 ms → 一遍 6 777 ms，**降到 57%（-43%，不是干净的减半** —— 单遍里 `iterdir`/`st_mtime`/排序是固定开销，约占 26%），两侧逐字段相同；同探针还量出「遍历期间事件循环的心跳推进次数」修前中位 **1 次** vs 修后 **376 次**：`get_status` 是 `async def`，原先在事件循环里同步 `rglob`，TTL 到点后连抢到单飞 lead 的那一跳都会把整个循环冻住（B1 只合并重算，救不了这一段）。**订正 C1 原文**：「`rglob` 跑在 async handler 内会阻塞事件循环」对 `/snapshots` 不成立 —— 它是同步 `def`，FastAPI 投进 anyio 线程池，占的是线程令牌（记在 D1 的账上），占事件循环的是 `/status`。顺带钉一个环境量级：本机单次 `Path.stat()` ≈ **1.6 ms**（安全软件逐文件扫描），凡「glob 目录 + 逐个 stat」的接口（A4 的日志统计、`_cache_scan` 的用户数据目录扫描）都按这个量级估成本。回归 `tests/test_vector_admin_snapshot_size.py`（19 例：每个目录只遍历一次 ×2 条调用路径、display 与 bytes 同源、两个快照不串体积、嵌套目录计入而目录本身不计、缺根目录返回空、`_main_data_size` 排除 `snapshots`、`/status` 的遍历不在事件循环线程上、`/status` 全字段与空快照两种终态）；三条退化变异各自翻红（改回双调用 → 2 例、去掉 `to_thread` → 1 例、`_main_data_size` 不再排除 snapshots → 2 例）。表 A 的 `≤120 ms` 本轮**不宣布达成**：真实快照目录里遍历在 233.8 ms 中占多少并未测过，须用同一 JMX 复跑替换。
- **反爬策略接口每请求吃满 1 s 的 CDP 探测超时（OPTIMIZATIONS C4 ①③；②短 TTL 刻意不落）**：`modules/login_strategy.py` 判断「系统浏览器是否开了 CDP」用 `socket.create_connection(..., timeout=1.0)`，而本机对回环上**没监听的端口不发 RST 而是静默丢包** —— 于是「CDP 未开」这一最常见形态每次都吃满 1 s（`GET /api/anticrawl/strategy` 基线 avg 1 094.7 ms 的来源）。动手前先用 `perf/2026-10-31-full-api/loopback_connect_control.py` 四格对照证实了这条前提（空闲端口 1.0 s 上限实测 ~1 010 ms、0.1 s 上限 ~109 ms；真正在 accept 的端口 0.76 ms 完成握手），而不是照文档假设。现改为类常量 `CDP_PROBE_TIMEOUT = 0.1`，调用处引用它；验收探针 `c4_after_probe.py`（9 次采样取中位）实测这一跳 **1 014.2 → 109.1 ms（省 905.1 ms，9.3×）**，同一轮里真在 accept 的端口 **0.60 ms** 仍判可用（预算还留 ~160× 余量），完整 `evaluate()` 地板 ~1 s → 109.5 ms。**③ 的落地形态与原文不同**：原文建议「`host_key = ?` 精确匹配，该表有 `idx_user_cookies_user_host` 类比索引」—— 那个索引属于**我们自己的** `user_cookies` 表，Chromium 的 `Network/Cookies` 文件里没有它；且等价匹配会漏掉同一站点的 `.goofish.com` / `www.goofish.com` / `s.taobao.com` 形态。实际改为**关键名下推 + 命中即停**：`SELECT 1 FROM cookies WHERE (host_key LIKE ? OR host_key LIKE ?) AND name IN (...) LIMIT 1`，谓词集合不变（`anticrawl_probe_cost.py`：5 000 行 8.24 → 3.44 ms，布尔逐形状相同；走真实代码路径：命中 0.87 ms、全表无关键名的最坏情况 2.47 ms）—— 这项本来就只占毫秒级，不是那一秒的来源，收益小但语义零风险。**②「探测结果短 TTL 缓存」仍不落**：`evaluate()` 的定义就是「当前环境」，缓存必须配对失效（`/initialize`、登录态变更、会话失效、用户刚打开带 CDP 的浏览器），否则页面会在用户当场改完环境后继续推荐旧策略；这轮不引入这种真实失败。**本条「avg ≤30 ms」的预期经实测作废**：残余的 109 ms 就是超时预算本身（静默丢包 ⇒ 耗时恰等于 timeout），①③ 相加只能到约 **190~250 ms**，接口级与 96 并发的数字仍待同一 JMX 复跑。回归 `tests/test_anticrawl_strategy_cost.py`（19 例：探测取类常量、预算 ≤0.2 s、关闭端口整次探测 <0.5 s、真 accept 端口仍判可用、探测失败回落到扫码；Cookie 侧 8 个形状同时对照内联的改写前参照实现、host/name 两个过滤都还活着、`name IN`+`LIMIT 1` 确实进了 SQL、缺库/无表/非 SQLite 文件照旧 False；全部用例 chdir 到 tmp 并打桩配置与 browser-data 路径，不读真实登录态）；变异验证四腿 + 负对照（timeout 写回 1.0 → 2 例红、SQL 退回原句 → 1 例红、去掉 `name IN` → 5 例红、host 图案接错 → 1 例红、改 `infra/browser.py` 另一处超时 → 全绿），还原后 sha256 与基线一致。
- **`maintenance/status` 1.6 s 的底噪（OPTIMIZATIONS A4；原文「表计数慢」的归因被实测推翻）**：`GET /api/maintenance/status` 基线单发 1.6 s，v1 负载轮里因 60 s TTL 到期 × 线程池排队出现 P95 60 033 ms（客户端超时判 000，超时率 37.5%）。落地前用 `perf/2026-10-31-full-api/maintenance_cost_probe.py` 在真实库（`mode=ro`）逐段拆成本，结论与原文 ①② 相反：**6 张表逐张 `SELECT COUNT(*)` 每张仅 ~0.1 ms**（最大 events 18 386 行），增量扫描 `snapshot(0.5)` 冷 19~32 ms、热 0 ms —— 所以「换 `sqlite_stat1` 近似行数 / 维护计数器表」不但没用，还会白丢精确性。真正的成本是 `__pycache__` 全树遍历：`_EXCLUDE_SCAN_DIRS` 漏了打包用的构建虚拟环境 `.venv-build/`，把第二套 site-packages 的 1 578 个 `__pycache__`（131.7 MB）算进「应用编译缓存」（这同时是正确性 bug：界面上的数字几乎全是它，清理也够不着）。现做三件事：① 排除 `.venv-build`（1 596 → **18 个目录**、1 860 → **167 ms**、131.7 → **9.4 MB**）；② 日志统计把「`is_file()` + 两次 `stat()`」合并为**每文件 1 次 `stat()`**（8.5 → 2.9 ms；同时单个文件读不到不再让整段统计失败）；③ `@cached_ttl` **60 → 300 + SWR**（失效点本来就在：三个清理入口写盘后各调 `invalidate_stats_cache()`，增量扫描 `/cache-scan` 到 `done` 时也失效一次），并在 `_warm_stats_cache` 加第 5 个预热 key、**走 `asyncio.to_thread`**（同步 `def` 直接 await 会卡住启动期事件循环）。完整重算（`bypass_cache=True`）**2 235 / 5 205 ms → 188 / 170 ms**，缓存命中 ≈0 —— 「≤200 ms」在重算路径成立。回归 `tests/test_maintenance_status_cost.py`（TTL/SWR AST 门禁、三清理入口失效门禁、每文件恰 1 次 `stat`、空/缺目录、预热落在线程池且失败隔离）+ `tests/test_maintenance_cleanup.py` 补 `.venv-build` 不统计/不清理断言；变异验证 5 腿各自翻红（去掉排除 → 4 例、日志退回 3 次 `stat` → 1 例、TTL 写回 60 → 1 例、去掉预热 → 2 例、预热不走 `to_thread` → 1 例），负对照（改同一集合里的 `dist`）全绿，两个源文件按 sha256 还原。残余边界：`_iter_pycache_dirs` 仍是全树 `os.walk`，每个 TTL 窗口仍会付 ~170 ms；将来再引入构建产物目录须同步补进排除集合。
- **`/api/auth/me` 每请求两条 SQL 中的一条 + 身份解析两次（OPTIMIZATIONS C2 ①⑤′；②③④判定不落；本条原文的归因被实测推翻）**：`GET /api/auth/me` 基线 avg 152.6 ms / 长跑 414.3 ms 原先记为「路由体重复文件解析 + 多余 SQL」。落地前用四条探针复核（`c2_me_probe.py` 进程内直调、`c2_server_probe.py` 真 uvicorn 隔离沙箱、`c2_ladder.py` 阶梯 + 每请求 SQL 计数、`c2_scan_cost.py` 被省掉的扫描按行数阶梯计价）：**处理器本身只有 3.6~7.2 ms（进程内）/ p50 ≈ 10 ms（安静服务端）**；`get_userinfo()` 走 `AuthManager` 的 300 s 内存缓存、`_sync_nick_to_users_table` 先比对才 UPDATE，所以 ③④ 描述的重复并不存在；回读原始样本 `results/baseline_light.jsonl.gz`，那 56 个 `/me` 样本实为 **p50 85.6 / p95 490.1 / max 548.6 ms** —— **152.6 是被长尾抬起来的平均值**，同场景慢接口（`anticrawl/strategy` ~1 s、`maintenance/status` ~1.6 s）占住 anyio 40 线程与 StaticPool 单连接造成的排队是主要嫌疑，但 85.6 ms 中位数在本机隔离环境复现不出来，**「已解释」这句不下，留给同一 JMX 复跑 + 关闭后台线程的对照**。真正成立的两处改动：**① 身份只解析一次** —— `/me` 里 `_resolve_current_user_id` 被调两次（`auth_query.py:339` 与原先的 `:406`），而 `UserManager.verify_session` **整个方法在进程级 `self._lock` 下执行、锁范围盖住查库**，同请求两次等于两个全局串行化点，纯白付；现开头解析一次并复用 `current_uid`。**⑤ 的原方案不可行、改成按用户节流** —— 原文要「`scan_and_notify` 移出 `/me`、改调度触发」，但 `H-05` 注释里指向的替代触发点 `/api/notifications/scan` **在代码里不存在**，而该模块的原则是「零侵入、不引入新 cron/定时器」，移出等于砍掉订单超时提醒的唯一生产者。现改为 `/me` 显式带窗口：`notification_engine.ORDER_SCAN_MIN_INTERVAL_SEC = 30.0` → `scan_and_notify(min_scan_interval=…)` → `scan_order_timeouts(min_interval=…)`，同一用户 30 s 内只扫一次（窗口内成本 0.002 ms 的闸门判定），默认值仍是 `0.0` = 不节流以保持显式调用方语义，分桶键 `user_id or "*"` 保住 T02618「扫描范围 = 通知归属」的口径。**效果以每请求 SQL 往返计：2.0 → 1.0 条**（`SELECT * FROM users` + `SELECT orders.… LIMIT 200` → 只剩前者）；省掉那条的量级随该用户订单规模增长：**0 行 0.48 ms / 50 行 2.77 ms / 200 行 8.85 ms / 2 000 行 10.34 ms**（临时库合成数据，通知写入走替身仓储只计扫描侧），且这是**独占全进程那一条 SQLite 连接**的时间。**② 刻意不落**：`_check_cookies` 每请求 `invalidate_cache` 不是疏漏而是跨进程新鲜度契约（浏览器登录子进程独立进程写完 `cookies_{uid}.json`，主进程吃 30 s TTL 会最长 30 s 认不出新登录的 user_id），该行为被 `tests/test_cookie_layer_sync_fix.py` 显式钉住（≥6 例），而它的实测成本只有 1.7 ms。**users 展示名 memo 同样判定不落**：`users.nickname/custom_alias` 有多个写入方（前端改别名、重复账号合并、建号），失效点配不齐就会造出「改了别名界面不刷新」这种真实故障，而收益是 1 条 0.3~0.5 ms 的往返。**本条不发布修复后的 HTTP avg**：同一份未节流基线在 c=48 三轮实测 256.2 / 117.7 / 233.5 ms，空处理器地板自己在轮间摆 57.9~178.4 ms，摆幅大于期望收益 —— 方向不可判定，高并发那段归 **D1**。回归 `tests/test_auth_me_scan_throttle.py`（12 例：窗口内不查库、默认不节流、窗口到期恢复、按用户分桶、`None` 独占桶、16 线程同时到达只放行一次、首次调用仍产出 5 min 通知且 `dedup_key` 逐字节不变、窗口内第二个请求不重复写、掉线告警不被连带节流、`/me` 只解析一次身份与必须透传窗口常量两条 AST 门禁、同一个 uid 既用于读 cookie 也用于通知归属、无会话身份时仍回退 `owner_user_id`）；`tests/test_regress_notification_user_scope.py` 的聚合入口用例改为把 `min_interval` 一并断言 —— 起因是 `scan_and_notify` 对每条规则包了 `except Exception → 记 WARNING、该档计 0`，替身签名漂移会被**静默吞掉**（本轮就是这么暴露的：`stats` 照常、只有 `seen` 少一项），把入参写进断言才能把这类漂移变成显式失败；两处 `scan_and_notify` 替身签名同步放宽。变异验证 6 腿 + 负对照（登记挪到扫描之后 → 2 例红、默认窗口写成 30 s → 1 例红、分桶塌成一个 → 2 例红、聚合入口不透传 → 2 例红、路由不传窗口 → 2 例红、路由第二次解析身份 → 2 例红；改**同文件相邻**的掉线 dedup_key → 33 例全绿），两份源文件按 sha256 逐字节还原。判据脚本 `perf/2026-10-31-full-api/c2_*.py`（阶梯 runner 加了 `--rebuild`：复用旧沙箱会被启动期 `token_renewer`/`cookie_rotator` 把 session 层标失效，改的是 `/me` 走的分支而不是性能）。
- **评估族只读接口改按 `eval.scored` 等值取数（OPTIMIZATIONS A2；原文建议的 `LIKE 'eval.%'` 恰是实测更慢的形态，「缓存命中态」与「≤60 ms」两处被实测推翻）**：`GET /api/evaluations` 原先经 `list_events_by_type_prefix("eval.")` 把整段评估事件读进 Python 再过滤/分页，并附发一条 `count(*)`。落地前用四份判据脚本（`a2_evals_probe.py` 逐阶段计时 + `EXPLAIN QUERY PLAN`、`a2_enrich_probe.py` 双身份 + cProfile、`a2_ab.py` 前后各两轮、`a2_growth.py` 5× 副本前后腿交替；都在生产库的 sqlite `backup` 一致快照副本上跑，生产文件从不被打开）复核，三条结论与原文相反：**① 这四个只读 GET 根本没挂 `@cached_ttl`**，「基线 19.8 ms（缓存命中态）」里那句「缓存命中」不成立；**② 压测身份一行评估数据都没读到** —— 压测走 WEB_TOKEN 直通，`web/middleware/auth.py:123` 把 `request.state.user_id` 置为 `"default"`，而真实库 1 867 条 `eval.*` 全属另一个数据主人账号（其 `user_id` 形如手机号，明文不落本文档），同一条请求沙箱态 median 4.8 ms/`total=0`、主人态 306.1 ms/`total=377`，所以长跑那 195.9 ms 里**不含**「多读 5× 行」的代价（它是并发排队，归 D1），本条的收益只落在账号里有评估数据的真实用户路径上；**③ 前缀 `LIKE 'eval.%' ORDER BY created_at DESC LIMIT n` 让 SQLite 走 `SCAN ix_events_created_type` 全表扫**，等值才走 `SEARCH ix_events_type_created (type=?)` —— 原文建议的 SQL 形态方向反了。真实库里 1 867 行中 **1 490 行是 `eval.passed`**（NotifierHub 推钉钉时写入，`payload=None`），路由紧接着在 Python 端按 `type != "eval.scored"` 丢弃，等于白读 5.0× 行且每行多一次 `json.loads`；那条 `count(*)` 在 4 个调用点全部以 `rows, _ =` 丢弃。现新增 `infra/repo_events.list_events_by_type()`（等值、`user_id is not None` 才过滤、返回 `list` 而非 `(rows, total)`、不附发 COUNT）与 `evaluations_common.load_eval_scored_events()` 统一入口，`list_evaluations`、`list_evaluation_brands`、`evaluations_distribution`、`feedback_stats` 四个只读端点接线（最后那个原先把精确类型当**前缀**传进去，即 `LIKE 'eval.scored%'`，顺带修掉「`eval.scored_extra` 会被读进来」的潜在口径漏洞）。**主人态实测（每轮 7 次取中位、两轮同向）：端点中位 574.7 / 586.5 → 238.1 / 228.6 ms，取数段 218.7 / 256.2 ms @1 867 行 → 35.9 / 65.3 ms @377 行，每请求 SQL 6 → 5 条**；5× 副本（events 25 856 / `eval.%` 9 335）下取数段 **699.8 / 820.9 → 143.0 / 175.6 ms**、端点中位 **1 208.9 / 1 705.4 → 609.1 / 1 027.2 ms**、每请求 SQL 8 → 5 条 —— 抗增长是本条真正买到的东西。**「avg → ≤60 ms」不发布**：cProfile 显示取数收窄后剩余大头在 enrich 而非 SQL（3 次主人态调用共 2.356 s 里 `_enrich_eval_record` 累计 1.267 s ≈54%，原取数只占 0.521 s ≈22%；热点是 `normalize_display_brand → _compact_text` 的 73 251 次 `re.sub`，单请求 24 000+ 次），那一刀记为 **A2′** 未做。**语义等价性另有数据侧判据**：生产库 `mode=ro` 复核确认 `eval.passed` 覆盖的 item_id 中没有任何一个缺 `eval.scored` 行（`NOT EXISTS` = 0），所以收窄范围不会让品牌候选集或列表少条目。**刻意不落**的三件：SQL 侧 `LIMIT/OFFSET` 分页与 `COUNT(*) OVER ()`（列表过滤依赖 join items 后的价格/标题字段、`total` 也在过滤后才成立，下推会改语义）、中位数/分位下推或物化快照（收窄后聚合是否仍贵未测，不为未测成本引入 SQL 侧口径漂移）、两个 POST 路径 `evaluations_batch._collect_evaluated_ids` 与 `evaluations_recompute` **保留 `eval.` 宽口径**（它们问的是「该 item 是否被评估流程触碰过」，改窄会让 `eval.passed` 覆盖的条目从「已评估」范围消失）；`list_events_by_type_prefix` 的形状（元组 + COUNT）因此一字未动。回归 `tests/test_eval_events_query_pushdown.py`（14 例：等值不顺带命中同前缀类型且保留前缀对照组、恰 1 条 SQL 不含 `count(`、`user_id` 隔离与 `None=不过滤`、共享入口返回 list 且入参须为精确类型、跨账号记录混不进列表、混入 `eval.passed` 结果不变、把前缀方法打成炸弹后 `/api/evaluations` 仍 200、brands/distribution/feedback 各取数一次且类型与 user 都对、通知事件不再被误算成 `insufficient_count`、两条 AST 门禁分别钉「只读用共享入口」与「POST 继续用前缀」）；判据集 66 例（本文件 + 评估族 5 个既有文件）基线全绿，全量后端 2 781 例通过、0 失败。变异验证 9 腿各自按预期翻红（等值退回前缀 → 1 例、加回 COUNT 并改回元组 → 33 例、丢掉 `user_id` 隔离 → 2 例、共享入口换前缀字面量 → 30 例、四个端点逐一回退前缀 → 各 2 例、batch 宽口径改窄 → 1 例），负对照（只把等值方法的 `limit` 默认值 50 000 → 500 000）66 例全绿，6 个源文件按 sha256 逐字节还原。**首轮变异跑作废重跑过一次**：L5b 的锚点少写一行缩进、拼出孤立的 `)`，pytest 以 `exit=2`（收集期 SyntaxError）「翻红」，而脚本原先把 `exit != 0` 一律当「这条腿有判据」—— 据此给探针加了两道闸（注入前 `ast.parse` 变异文本、判定只认 `exit == 1`），这类「文件坏了」的红不再可能被读成「用例认这行改动」。详见 OPTIMIZATIONS §A2 落地记录与 REPORT 表 B4。
- **`links/live` 依赖不可用时不再回 HTTP 5xx（OPTIMIZATIONS E3）**：采集器未启动（`scheduler_off`）或浏览器未初始化（`browser_unavailable`）时，`GET /api/tasks/{id}/links/live` 原先在开流前直接抛 503 —— 30 min 耐久里全部 **795 次 5xx 都来自这个前置检查**（占本轮 5xx 的 100%），它既是缺陷率的全部来源，也会在线上把真实故障淹没。这两种状态是部署期/启动态，不是请求错误，现改为先以 **HTTP 200** 建立 `text/event-stream`，再发一条 `{"stage":"error","status":503,"code":…,"detail":…}` 后结束。事件字段与旧 HTTP 错误体逐字段一致：前端 `dispatchSSEEvent` 本就按 `data.status || 502` 还原成 `makeHttpError`，`liveSearchError.ts` 又按 `code` 优先分类，所以提示文案、重新登录入口与「浏览器不可用」降级展示全部不变，变化的只是 HTTP 层不再记 5xx。任务不存在（404）与无关键词（400）仍是真实 HTTP 错误；`/links/refresh` 的同类检查不动（POST 返回 JSON，且不在本轮 503 来源里）。回归 `tests/test_task_links_units.py::test_live_links_endpoint` 改为断言「200 + `text/event-stream` + 单条 error 事件」并保留 404/400；变异验证 3 腿各自翻红（退回 503、事件丢 `code`、媒体类型不是 SSE），负对照（改相邻 400 的 detail）全绿，源文件按 sha256 还原。缺陷率 0.74% → 0 仍待同一 JMX 复跑确认。
- **慢同步接口与快接口隔离线程池（OPTIMIZATIONS D1；原文建议的 API 装机不存在，名单按实测成本重划）**：本项目 277 个路由处理器里 **231 个是同步 `def`**（`d1_inventory.py` 纯 AST 清点），FastAPI 一律经 `starlette.concurrency.run_in_threadpool` 投进 anyio 的**默认 40 令牌池**且调用时不传 limiter —— 于是「占用 1 s 的导出」和「占用 8 ms 的读配置」排同一条队。压测里 96 并发档 `GET /api/notifier/quiet-hours`（本身 8.4 ms）avg 被抬到 2 096.8 ms、60 s 完成样本 55 → 7。动手前用 `d1_lane_probe.py` 把每次请求拆成 `queue_ms`（等令牌+派发）与 `work_ms`（处理器自己算）：生产同形臂（同步 `Depends` + 3 000 行响应体、IO hog×40 走共享池）廉价接口 p50 **3 509.6 / 1 153.9 ms** 而 `queue_p95` **974.4 / 978.6 ms**、`work_p50` 恒 **~4 ms**、峰值借用 **40/40**、完成数 **16 / 34**；同一条 hog 走 `CapacityLimiter(4)` 专用道后 p50 **34.3 / 59.8 ms**、`queue_p95` **6.7 / 8.9 ms**、完成数 **504 / 334**（两轮同向，`results/d1_lane_arms_c2.json`）。**两条更省事的替代方案实测更差、判定不落**：只把全局令牌抬到 128 → p50 49.1 / 174.0 ms、完成数 257 / 62；另开一条**不限容**的 limiter → p50 113.5 / 341.6 ms、完成数 104 / 24 —— 起作用的变量是**上限**，不是「有第二条道」。**CPU 型另记一笔**：CPU hog×40 吃满共享池时 p50 7~11 s 而 `queue_p95` 只有 69~289 ms，排队拆不出这个量级，是 GIL 把事件循环一起冻住（`d1_hog_nature.py` 的 `ticker_ratio`：纯 Python 忙等 0.492 vs `time.sleep` 1.105）；同一条 hog 走专用道 p50 41.4~66.9 ms。落地形态：新增 `backend/xianyu_hunter/web/slow_lane.py`，在 `create_app()` 的路由注册之后按 **16 条**名单（12 读接口 + 4 条结构性长任务：`maintenance/{cache,database,logs}`、`vector-admin/snapshots/{id}` DELETE）把 `route.dependant.call` 换成「`anyio.to_thread.run_sync(..., limiter=CapacityLimiter(cap))`」的协程包装，再清 `Dependant` 的 `is_coroutine_callable` 等 `cached_property` 并 `route.app = request_response(...)` 重建 —— **必须是 `fastapi.routing.request_response`**（FastAPI vendored 的那份额外往 scope 塞 `fastapi_inner_astack`，误用 Starlette 版会让每个请求撞 `AssertionError`）。**不用装饰器**是因为 `startup.py:835` 与十余处测试把这些处理器按同步函数直接调用（`test_db_admin_edit_guard.py:212` 还 `inspect.signature(admin.export_rows)`），装饰器会把它们变成协程函数连带崩掉；集中改写保住处理器对象、签名与 openapi（562 条路由 / 480 个 path 逐字节不变）。容量 4 由 `d1_lane_budget.py` 按 Little's law 定价：最坏的 96 并发档慢族在途需求 **2.14**、4 槽吞吐上限 **9.16 rps > 实测 4.9 rps**。配置项 `slow_lane_tokens`（默认 4，`<=0` 整体关闭）。**原文两处修正**：① 建议的 `anyio.to_thread.RunVars` 在装机 anyio 4.13.0 里**不存在**，公开 API 没有 per-route 落点；② 点名接口里 `logs/search` **不入道** —— 它压测样本是 `422×286`，从未真正执行、没有占用数据（同理 `logs/export`、`logs/request/{id}`、`error-logs` 族），`db-admin/tables/{table}/rows`（34.2 ms）低于 90 ms 判据也不入道。回归 `tests/test_slow_lane.py`（15 例：名单逐条钉住 16 条与 `/xianyu` 两份挂载、`is_coroutine_callable` 已清、openapi 逐字节相同、Query/Request/同步依赖/请求体/`Response` 直通、在途恰为 cap 且真的排队、廉价接口并发不受限、限流器按事件循环键控、失配报错、`capacity<=0` 关闭）；变异验证 **12 腿 + 负对照** 各自按预期翻红/全绿（首版 L01 只改构造、保留 `elif total_tokens != capacity` 的夹逼而**自愈变绿**，那轮另存 `results/d1_mutation_first_pass_selfheal.log`（`*.log` 按 `.gitignore` 不入库，判据可由脚本原样复现）），`slow_lane.py` 与 `config.py` 按 sha256 逐字节还原。受影响族回归 370 例通过，全量后端 **2 796 passed / 0 failed**（20:22）。~~48/96 并发轻组 P95 ≤1 000 ms、缺陷率 <0.5%~~ **本轮不发布**，须同一 JMX 复跑。

- **目录统计的在飞时间片不再挡别的请求（OPTIMIZATIONS D2；原文建议的「按路径分锁」判定不落）**：`web/services/cache_scan.py` 的 `snapshot()` 原先整片持住进程级 `RLock`，而那片是**按墙钟**切预算的（生产档位 `STATUS/PREVIEW = 0.5 s`、`POLL = 1.0 s`、`MAX_POLL = 2.0 s`）—— 于是迟到的请求排的不是 CPU 而是「别人那一片还剩多久」，`/cache-scan` 与 `/maintenance/status` 互相串行（压测里这条串行写在 CONCLUSION ① 的根因链上）。现把遍历挪到锁外走，并用 `_Scan.advancing` 保证同一路径同一时刻只有一个推进者：抢到名额的调用执行该片，其余调用方**立刻拿到当前进度**（`state: scanning` + 已遍历条目数），片结束后回读进度表而不是交出自家那份局部值。两处正确性硬判据：① 单推进者下的终值与「一次扫完」的参照逐字段相同；② 一片在飞时若发生 `invalidate()`（真实删除后清进度），该次结果不复活被删掉的旧数字，改回「重新统计中」。判据 `perf/2026-10-31-full-api/d2_lock_probe.py`（合成树 60 目录 × 300 文件 = 18 000 文件 / 17.58 MB，一次全扫 50.3 ms，切片预算 = 全扫/20 = 2.51 ms，两轮 R1/R2 同向）：同路径 8 次并发到达的调用 p95 **42.4 / 36.7 → 9.9 / 2.8 ms**、总墙钟 **44.4 / 38.0 → 11.9 / 4.6 ms**；「扫到 done 的波数与墙钟」基本不变（1 波 42.6 / 39.8 → 3 波 44.4 / 41.1 ms），说明买到的是**迟到者不再排队**，不是把树扫得更快。「共享工作队列（只 pop 时持锁）」候选实测更差并判定不落：到 done 要 4~6 波、墙钟 **83.8 / 66.7 ms**（比现状长一倍），因为预算是墙钟 —— GIL 下 K 个并发切片只是把一片切碎，而干了活的手/调用从 0.12 涨到 1.0（人人都在跑，每人分到的进度更少）；单到达吞吐也更低（同一片扫到 6 660 条目 vs 5 460）。「按路径分锁」同样不落：清点过三个调用点（`/maintenance/status`、`/cache-scan`、清理预览）都用同一个 `_browser_data_dir()`、且全进程只有一个 `_cache_scan` 实例，分锁要解决的是并不存在的形态；跨路径的并行是这次改动的**副产品**（两路径 2×8 并发总墙钟 **81.6 / 83.6 → 16.4 / 17.1 ms**），不是机制。前端契约已核：`Cleanup.tsx` 本就按 `state === 'scanning'` 渲染「浏览器数据统计中（已遍历 N 项）」并在 `done` 前禁止清理，语义不变。**接口级 avg 与 96 并发数字本轮不发布**，须同一 JMX 复跑。回归 `tests/test_maintenance_cache_scan.py` 新增 4 例（迟到者拿到进度而非排队且 `slice_keys` 只有一条同路径片、`invalidate()` 不被在飞片挡住、一片只挡自己那条路径别的目录照常推进、在飞片不回写已被作废的进度），该文件 17 例通过、维护族 + 慢车道族 66 例通过、全量后端 **2 810 passed / 0 failed**（18:58）；变异验证 8 腿（时间片请回锁内 → 3 例红、去掉同键名额 → 1 例红、名额置了不清 → 3 例红、名额改全局 → 1 例红、去掉 TTL 过期重建 → 1 例红、在飞那次不回读进度表 → 1 例红）+ 「接错对象」腿（名额守卫加在 `invalidate()` 上）与只改 docstring 的负对照各自全绿，`cache_scan.py` 按 sha256 逐字节还原。一轮负对照曾翻红，红的是 D1 的 `test_slow_lane.py::test_cheap_route_not_blocked_by_slow_lane` —— 同一轮基线（未加任何变异）也红且 51 例从 22 s 涨到 198 s，判为机器被外部负载占住时那条「在途峰值取样」用例自抖（`hold_ms` 30/60 的取样窗口与调度延迟同量级），与本条无因果；探针据此加 `--drop-slow-lane` 口径，其余判据一条不少。详见 OPTIMIZATIONS §D2 落地记录。
- **读缓存加上容量上限与 LRU 兜底（OPTIMIZATIONS B4；原文「RSS 爬升来自无上限缓存」的归因被实测推翻）**：`web/cache.py` 的 `_cache_store` 原先是个没有上限的普通 dict，过期条目也只在被同 key 覆盖时才消失。取证分两步：① `b4_cache_census.py` 清点全仓 8 个 `@cached_ttl` 站点（其中 4 个 key 直接拼查询参数），再用 ASGI transport 把 `results/per_endpoint.json` 里 **106** 个 GET label 全扫一遍 —— 缓存里只落下 **7 条 / 23 620 B（0.02 MB）**，单条最深 8 139 B；② `b4_key_ceiling_probe.py` 用沙箱库里 60 个真实 task 各打 `trend` + `histogram`（**+120 条 / 539 865 B**，每 task 2.0 条 / 8 997.75 B），再伪 200 个前端从不发送的 `metric`（**+200 条 / 128 090 B**，单条 640.45 B）；终点 327 条、深量 **0.66 MB** 而同场 RSS 涨 **26.4 MB**（40 倍），沙箱 248 个 task 全打一遍外推也只有 **2.1 MB** —— 所以「缓存驻留」解释不了 RSS 爬升，真正的问题是**键空间无界**：`metric` / `range_hours` / `bins` / `task_id` 路由不约束，任何调用方都能线性加条目且永不自清。落地：`_cache_store` 换成 `infra/lru.py::LRUDict`（dict 子类，调用方零改动），新增 `cache_max_entries`（默认 2000，按每 task 8 997.75 B 外推最坏约 8.8 MB），由 `create_app()` 在装配期调 `configure_cache_max_entries()` 套用 —— 不在模块导入期读配置（`get_settings()` 会读 `.env`、取 keyring），也不在写入路径上读（不让缓存可写性依赖配置读取）。`_try_get_cache` / `_try_get_stale` 都走 `LRUDict.get`，读命中即续命，因此 `maintenance_status`（TTL 300 s + SWR）的旧值不会被正常流量挤掉 —— 否则 A4 的 188/170 ms 重算会重新落到过期请求头上。明确不做：字节计量（驻留只有 0.02 MB，深遍历成本大于收益）与大 payload 黑名单（8 个站点里没有大响应，`config/raw`/`share` 本就不挂缓存）。回归 `tests/test_cache_capacity.py` 8 例、缓存族 5 文件 55 例、后端全量 **2 818 passed / 0 failed**（17:28）；变异验证 7 腿（写入绕过 LRU / 上限套用空操作 / 读命中不续命 / 非正上限放行 / 装配期漏接 = 5 红，「接错对象」等价改写 + 只改 docstring = 2 绿），`web/cache.py` + `web/app.py` + `infra/lru.py` 按 sha256 逐字节还原。RSS 斜率仍须 ≥4 h 长跑 + `tracemalloc` 定论，本条不宣布解决。详见 OPTIMIZATIONS §B4 落地记录。
- **评估列表品牌归一化不再重复压缩常量别名表（OPTIMIZATIONS A2′；原文建议的「请求内 memo」被实测推翻）**：A2 落地后评估列表剩下的大头已从 SQL 转到 enrich —— 3 次主人态调用共 2.356 s 里 `_enrich_eval_record` 占 1.267 s（54%），而 `normalize_display_brand → _compact_text` 单请求要跑 **73 251 次 `re.sub`**：每条 display 都把 title、candidate 与**整张品牌别名表（~70 组 / 200 个别名）**各压一遍。现做两处：① `_compact_text` 改用模块级预编译的 `_COMPACT_TEXT_RE`（此前每次调用都要过一次 `re._compile` 缓存查找，实测 73 740 次）；② `_BRAND_ALIASES` 是模块级常量、压缩结果只依赖常量内容，因此在导入期预计算成 `_COMPACT_BRAND_ALIASES`，比对时不再逐别名重跑正则。品牌比对同时改成「按 canonical 组求交集」—— 原实现逐组判断「candidate 命中该组某别名 and title 命中该组某别名」，若直接合并成「任意别名 + 任意别名」，「联想 + 尼康 Z9」会被误判成匹配（重构时真实踩到，已钉成用例）。**「按 (candidate, title) 做请求内 memo」刻意不落**：真实库 377 条 `eval.scored` 只有 **363 个唯一 title**（重复 3.7%），2 000 条 `task_links.display` 重复更低；同进程 A/B 里 memo 版比无 memo 版慢 **15%**（键构造与查表开销大于命中收益）。判据（同一份 `a2_enrich_probe.py` 前后各一轮；原探针缺 `DATA_UID` 赋值使主人态永远读 0 行，已就地补上）：`_compact_text` **73 251 次 / 401.1 ms → 2 367 次 / 11.7 ms**（−97%）、`_brand_candidate_matches_title` **360.2 → 110.3 ms**、`_enrich_eval_record` **706.9 → 256.3 ms**（占 `list_evaluations` **60.8% → 39.5%**）、端点中位 **148.2 → 121.5 ms**、冷启动 **200.1 → 151.6 ms**。**A2 原文的「推向 60 ms」仍不成立**：enrich 仍是列表里最大的单项，剩余成本转到 `_row_to_dict`/`json.loads`/成色标签扫描。另一条同进程 A/B（2 000 条真实 `task_links.display`、9 轮交错）上旧实现复刻 **423.7 → 175.0 ms（0.41×）**，用来压掉机器外部负载对「前后两轮探针」的干扰。语义等价：22 427 组（2 427 组真实数据 + 20 000 组 fuzz）逐条对比旧实现复刻，`_brand_candidate_matches_title` / `infer_brand_from_title` / `normalize_display_brand` 差异全 0。回归 `tests/test_brand_display_normalize.py` 新增 4 例（`"om-d"` 必须靠压缩后才命中无连字符标题、跨组别名不得匹配、两处 `_compact_text` 调用次数上界）+ 变异验证 `a2_prime_mutation_run.py` 2 红（别名表退回未压缩 / 组交集退化成 `or`）2 绿（接错对象 / 只改 docstring），`collector_utils.py` 按 sha256 逐字节还原。详见 OPTIMIZATIONS §A2′ 落地记录。
- **模板列表的 presets YAML / user JSON 不再每请求重解析（OPTIMIZATIONS C3 残余）**：`GET /api/templates` 原先每次请求都 `open + yaml_io.safe_load(preset_templates.yaml)` 再 `open + json.load(user_templates.json)`，`POST/DELETE` 还要「读整表 → 改 → 整表写回」。现按 `(mtime_ns, size, 读取代次)` 做文件指纹 memo：`_load_presets` / `_load_user` 都走 `_load_cached()`，命中时只付一次 `os.stat`（本机中位 0.28 ms），进程内处理器实测 **2.23 → 0.53~0.58 ms（−76%）**（presets 1.32 → 0.64~0.91 ms、user 1.15 → 0.34~0.65 ms）。写侧 `_save_user` 在写盘后 `_bump_epoch(USER_FILE)` + `_MEMO.pop(USER_FILE)` 配对作废 —— Windows 文件系统时间粒度约 15.6 ms，同尺寸改写会落在同一 mtime tick，单靠指纹会把「保存后立刻读」读成旧值；读取代次则防「stat 与 parse 之间被写盘插队」的在飞请求把旧值存回 memo（与 `api_config` 的 `_yaml_epoch` 同一手法）。**deepcopy 方案被实测否掉**：112 条用户模板 deepcopy 1.14 ms，与重新 `json.load` 的 1.15 ms 持平，等于把收益吃光，因此返回浅拷贝列表（现有调用方只做拼接/过滤/append 新元素，不就地改元素）。**接口级不发布**：基线 HTTP `GET /api/templates` 115.8 ms 里能归到模板解析的只有约 2 ms，其余是中间件/线程池排队（D1 的账），须同一 JMX 复跑。回归 `tests/test_templates_memo.py` 9 例（文件没变只解析一次、外部编辑下一次读跟上、同尺寸 + 同一 mtime tick 写盘后仍读到新值、返回副本不被 append 污染、缺文件与坏 JSON 的降级与恢复、写盘配对作废的 AST 门禁）；变异验证 `c3_mutation_run.py` 3 红（指纹不参与命中 / 写盘不作废 / 命中分支直接返回缓存对象）2 绿（异常元组顺序、只改 docstring），`api_templates.py` 按 sha256 逐字节还原。详见 OPTIMIZATIONS §C3 落地记录。
- **按实测补 3 个过滤/聚合列索引，另 2 个候选判定不落（OPTIMIZATIONS A1；原文 5 条里 1 条改复合形态、2 条无收益）**：原文列了 `items.price` / `evaluations.risk_level` / `price_history.user_id` / `orders.type` / `task_links.updated_at` 五个单列索引。落地前用 `perf/2026-10-31-full-api/a1_index_probe.py` 在合成库（每表 2 万行，约为真实 items 774 / task_links 2 050 的 10~25×）逐条验证「`EXPLAIN QUERY PLAN` 是否真的改用它 + 15 次中位耗时 + 写侧 3 000 行插入代价」，结论三落两不落：① **`items.price`** —— `stats_price_trend` 的 `price between ? and ? order by publish_time desc limit 2000` 从 `publish_time` 索引全扫改为 price 区间 + 临时排序，**15.09 → 7.17 ms**，写代价 3 000 行 12.5 → 16.6 ms；② **`evaluations.risk_level`** —— `stats_overview` 的无 WHERE `SUM(CASE risk_level='low')` 变成覆盖索引扫描（`COVERING INDEX`），**3.45 → 2.17 ms**，写代价 7.9 → 14.1 ms（每行 +2 µs）；③ **`orders.type` 落地形态改为 `(type, created_at)` 复合** —— 原文的单列实测反而更慢（4.85 → 5.49 ms，仍要临时排序），复合后 **4.08 → 0.50 ms** 且计划里没有 `TEMP B-TREE`，写代价 +4%；④ **`price_history.user_id` 判定不落** —— 两个读点都以 `item_id` 等值前缀走既有 `(item_id, captured_at)`，加索引前后计划与耗时都不变（0.31 → 0.33 ms）；⑤ **`task_links.updated_at` 判定不落** —— 单列不改变计划（49.91 → 40.60 ms 是缓存预热，不可归因），复合 `(link_type, updated_at)` 只 −4%（成本在逐行 `json_extract`）而写代价 +46%。落地两条路径：ORM `__table_args__` 三处 `Index(...)`（新建库）+ `init_db` 三条 `_migrate_create_index`（旧库，`create_all` 不会给已存在的表补索引）。回归 `tests/test_perf_a1_indexes.py` 8 例（三个索引存在、三条查询计划断言、两条「判定不落」的反向门禁防将来「顺手补齐 5 条」、ORM 声明与迁移一致、`init_db` 幂等、旧库 drop 三个索引后迁移回填）；变异验证 `a1_mutation_run.py` 4 红（删 items/orders 迁移行、ORM 改错列、把 `price_history.user_id` 加回来）2 绿（重复 seller 索引 / 只改 docstring），`db_models.py` 按 sha256 逐字节还原。接口级 P95 仍待同一 JMX 复跑。详见 OPTIMIZATIONS §A1 落地记录。
- **评估列表的市场统计不再逐 task N+1（OPTIMIZATIONS C5）**：多任务用户的评估列表在 cache miss 时会按 task_id 逐个调用 `price_dashboard._compute_sold_range`，每个任务至少 2~3 条 SQL（任务价格区间 + 已售样本 + 回退样本），任务越多代价线性放大。现新增 `_compute_sold_range_batch()`：区间 1 条（`task_id IN (...)`）、已售样本 1 条（`LIMIT 5000 × 任务数`，与原单任务 `LIMIT 5000` 的「任意截断」语义对齐）、回退样本 0~1 条（只对已售样本不足的任务查）；`evaluations_list.compute_market_stats_batch` 的 miss 集合改走它，批量整段失败时退回逐任务、单个任务失败只丢它自己，`task_id=None`（全局统计）仍保持单条语义。**统计口径没有复制实现**：把原 `_compute_sold_range` 里的 mixed / all_fallback / 分位数逻辑抽成 `_summarize_price_stats()`，逐条版与批量版共用同一段，行情页/直方图/趋势页的入口不变。判据 `perf/2026-10-31-full-api/c5_market_stats_probe.py`（合成库 20 个任务 ×（60 条已售 link + 60 条 items），7 轮中位）：逐条 **40 条 SQL / 17.1 ms** → 批量 **2 条 SQL / 4.7 ms（0.27×，−73%）**，SQL 条数与任务数解耦。**接口级 P99 858 → ≤300 ms 本轮不发布**：进程内直调只证明每次冷算的 SQL 与耗时降了 4~7 倍，HTTP 层还有 D1 的线程池排队，须同一 JMX 复跑。回归 `tests/test_evaluations_market_stats_batch.py` 7 例（批量结果与逐任务逐字段相同且覆盖 sold/mixed/all_fallback 三种 source、SQL 次数与任务数解耦、`None` 仍走单条、cache 命中不查库、批量失败退回逐条、单任务失败只丢自己、接线门禁）；变异验证 `c5_mutation_run.py` 4 红（退回逐 task 循环 / 每 task 套用第一个任务的区间 / 批量回退丢掉 range_days 过滤 / 回退判定反向）2 绿（区间查询多带一列 / 只改 docstring），`price_dashboard.py` 与 `evaluations_list.py` 按 sha256 逐字节还原。详见 OPTIMIZATIONS §C5 落地记录。
- **日志检索的前后通配与「1 000 条截断」判定不落（OPTIMIZATIONS A5；原文两半经实测都没有落点）**：用 `perf/2026-10-31-full-api/a5_like_probe.py` 在一致性快照副本上复核 —— ① `repo_error_logs.list_error_logs` 的 `%LIKE%`（`/api/error-logs`）在真实库 `error_logs` 只有 **2 112 行**，直接 SQL 4.5~7.8 ms、走仓储 1.8~10.0 ms；前缀匹配对照**没有更快**（`request_path LIKE 'api%'` 反而 **29.7 ms**，`ix_error_logs_request_path` 只能做前缀过滤，`ORDER BY timestamp` 还要回表排序），而且会改语义（前端提示「按状态与错误类型搜索」，用户输入的是部分类型名/路径片段），FTS5 的收益 <10 ms 不抵虚表 + 触发器的迁移成本；② `/api/logs/search` 的 `q` 是 **Python 端子串匹配**，SQL 侧本来就没有 `%LIKE%`，所以「前缀 / FTS5」对它无落点：进程内「取 1 000 行候选 31.9 ms + Python 过滤 6.2 ms」≈38 ms，把 `q` 下推 SQL 要 21.8 ms 再 + 准确 `COUNT(*)` 14.0 ms = **37.7 ms** —— **延迟没降**，换来的是 `total_estimate` 从「最近 1 000 条里的命中」变成「全部历史命中」（同关键词 14 → 52 条）。这是产品口径改进（前端确实把 `total_estimate` 当命中总数，见 T02678），要不要改语义需要产品确认，不在性能条目里落。**本条「132.9 → ≤40 ms」不成立**：`logs/search` 接口自身只有 ~38 ms，HTTP 长跑 132.9 ms 的其余近 100 ms 是中间件/线程池排队（D1 的账）。
- **`--workers > 1` 与 `--with-scheduler` 改为互斥拒绝（OPTIMIZATIONS E1 运维护栏；默认 worker 数与吞吐判据不落）**：`__main__.web` 的注释一直写着「多 worker + 调度器会让每个 worker 各起一套浏览器/调度器」，但代码不拦 —— 用户执行 `--workers 4`（默认 `--with-scheduler`）会起 4 个 Chromium + 4 份轮询，既抢浏览器锁也重复采集。现加互斥校验：`workers > 1` 且 `with_scheduler` 时直接 `Exit(2)` 并提示改用 `--no-with-scheduler`；**不静默降级**（静默降级会让用户以为多 worker 生效，实际仍是单进程）。**判定不落**：把 `--workers` 默认值改为 CPU 核数会让进程内 TTL 缓存按 worker 分裂成 N 份（B3 判定不落后没有共享缓存兜底），单机用户会直接看到命中率下降；「80 → ≥150 rps」需同一 JMX 复跑，本机无原生 JMeter 且 96 并发已近回放引擎上限。回归 `tests/test_web_workers_guard.py` 3 例（默认拒绝且不启动 uvicorn、`--no-with-scheduler` 放行并原样透传、`--reload` 仍强制降为 1）。详见 OPTIMIZATIONS §E1。
- **SSE 连接超限时补服务端告警（OPTIMIZATIONS E2；隔离方案判定不落）**：`/api/events/stream` 在并发连接达到 `_MAX_SSE_CONNECTIONS=10` 时只给客户端发一条流内 error 事件，**服务端没有任何记录** —— 运维侧看不到「容量已打满」。现于 `_acquire_sse_slot()` 的拒绝分支补一条 WARNING（HTTP 语义不变，仍是 200 + 流内事件）。**判定不落**：新建 `/api/metrics` 会新增对外契约并改动 openapi 快照（D1 的 562 路由逐字节判据要连带更新），而 E2 没有量化判据；SSE 单独进程/端口属部署形态变更；把 SSE 塞进 `CapacityLimiter` 会让 10 条长连接变成 10 个永久占用的槽，比共享池更糟。回归 `tests/test_sse_limit_alert.py` 3 例（超限恰好一条、未超限不打、释放后可重新获取）。详见 OPTIMIZATIONS §E2。
- **`recompute` 的 payload 改成单事务批量写（OPTIMIZATIONS D3；原文的写组 TPS 判据不成立，「批量 UPSERT」没有跨请求落点）**：先按响应码拆 `writes_idem` 的 26 个接口（`perf/2026-10-31-full-api/d3_write_inventory.py`，数据源是那轮压测的逐接口聚合）：16 并发 3 170 个样本里 **61.6% 是压测夹具的 4xx**（合成 query/body 没补齐必填参数，在 FastAPI 校验层返回、一次库都不碰），真写只有 38.4%；真写接口单发全部 <30 ms（`tasks` 18.7、`batch-evaluate` 17.4、`recompute` 16.4、`preferences` 9.7），且**每请求只写 1 条** —— 除了改响应语义，跨请求合并写无处施力。真实存在的 N+1 在 `POST /api/evaluations/recompute`：它按事件逐条 `update_event_payload`，每条一个 `engine.begin()`（500 条 = 500 事务）。现新增 `repo_events.batch_update_event_payloads(updates, user_id=None)`（单事务 executemany、按 500 条切片、`user_id` 过滤语义与单条版一致），recompute 主循环改为「循环内只登记 `(event_id, payload)`」，结束后一次批量写；`notify=True` 的通知有顺序语义，仍留在循环内发。判据 `d3_write_probe.py`（仓储层，5 轮中位）：events UPDATE 500 条 **99.4 → 6.0 ms（16.5×）**、2 000 条 402.2 → 32.9 ms；items UPSERT 500 条 405.3 → 95.1 ms（4.3×）。端到端 `d3_recompute_probe.py`（500 条 `eval.scored`、两臂共用同一份数据与同一段重算循环）：**137.5 → 25.4 ms（5.41×），UPDATE 500 → 1 条**。**判定不落**：`recompute` 无评估事件时的 UPSERT 分支（`upsert_item` 的 `user_id or "default"` 归属、items 列默认值、逐条 try/except 的部分成功语义要一起改，风险高于收益）、`busy_timeout` 10 s→2 s（不提升吞吐，只是把等待换成报错，原文自己要求配等锁指标）、扩池（SQLite WAL 单写者，原文已否）。原文的「16 并发写 TPS 51.9 → ≥66」不发布：写组 61.6% 的样本不写库。回归 `tests/test_recompute_batch_payload.py` 5 例（单事务、逐行落库、`user_id` 隔离、空列表 no-op、端到端 200 条只发 1 条 UPDATE + AST 接线门禁）；变异验证 `d3_mutation_run.py` 4 红 2 绿、2 个源文件按 sha256 逐字节还原。接口级写吞吐仍须同一 JMX 复跑。详见 OPTIMIZATIONS §D3。
- **三处导出改为真正的边生成边发（OPTIMIZATIONS D4；原文「`db-admin` 也是全内存拼 CSV」的归因被实测推翻）**：`api_export._build_csv_response` 先把最多 5 万行整份 CSV 拼进 `io.StringIO` 再 `iter([buf.getvalue()])` 单元素「流式」发出，`api_logs.export_logs` 的 CSV 与 `_export_log_file`（整份 `read_text` + `"\n".join`）同一模式。现改为：`api_export` / `api_logs` 的 CSV 按 **256 KB 攒块**产出（BOM 与表头在首块），原始日志改成 `TextIOWrapper.read(1M 字符)` 分块读，`api_db_admin.export_rows` 由 `.all()` 全表读入改为 **`SELECT COUNT(*)` + 游标逐行取**、并把原来的逐行 yield 攒成块。为什么必须攒块而不是逐行 yield：`StreamingResponse` 对同步迭代器走 `iterate_in_threadpool`，每次 `next()` 一次线程切换 —— `db-admin` 导出 18 403 行 events 实测 **2.43 s**，其中绝大部分是 1.8 万次线程池往返。**逐字节语义有两条不能丢**：原 `Path.read_text` 默认 `newline=None` 会把 `\r\n` 转成 `\n`（二进制读会留下 `\r\n`），原 `"\n".join([...])` 的元素间分隔且空文件也算一个元素。判据 `perf/2026-10-31-full-api/d4_export_probe.py`（真实 items 774 → 循环放大到 5 万行、真实 events 18 403 行、2 MB / 40 MB 日志文件；`db-admin` 跑在 `sqlite3 backup` 副本上；峰值增量在数据构造完成之后才开 `tracemalloc`，真实耗时另跑一遍不开它）：**50 000 行 items 峰值 38.27 → 2.52 MB（−93.4%）、首块/总耗时 0.98 → 0.01；40 MB 日志峰值 190.48 → 9.29 MB（−95.1%）、首块/总耗时 0.66 → 0.02；18 403 行 events 导出 2 433 → 285 ms（−88.3%）、峰值 17.71 → 2.02 MB（−88.6%）**。原文的 `api_logs` 「过滤条件下推」**判定不落**（A5 已证下推更慢且改 `total_estimate` 口径），`api/config/export` 不动（7 KB 配置对象，耗时来自 `_load_yaml` + 序列化，不是大结果集）。回归 `tests/test_export.py` 20 例（新增 11 例：逐字节参照在测试里独立重写、首块产出时未取过数据行、单块 ≤ 2×块大小、多字节字符跨块、BOM/换行/空文件边界、db-admin CSV+JSON 分块）；变异验证 `d4_mutation_run.py` **8 红 2 绿**、3 个源文件按 sha256 逐字节还原；全量后端 **2 857 passed / 0 failed**。接口级 P99 1 827.7 ms 仍待同一 JMX 复跑。详见 OPTIMIZATIONS §D4。

## [0.4.0] - 2026-07-06

## [0.3.1] - 2026-07-05

_本次发布周期的变更已封版至 [0.3.0]，新变更请在此段落积累。_

### Fixed

- **实时搜索 500 内部服务器错误**：`Repository.get_task(task_id, user_id=...)` 方法签名缺失 `user_id` 参数，路由层多用户隔离调用抛 `TypeError`，被全局异常处理器兜底为 500。补齐参数并按 `user_id` 过滤，与 `list_tasks` 行为一致。影响实时搜索、关联列表/刷新、任务依赖等 11 处端点。
- **打包版登录跳转 404**：浏览器原生 `location.replace/href` 不走 react-router，不会自动补 `basename` 前缀。`client.ts` 401 拦截器、`useSSEChat.ts` SSE 401 跳转、`App.tsx` 移动端 UA 检测共 4 处的 `/login` 改为 `/app/login`，与 SPA 挂载路径 `/app/` 对齐。

---

## [0.3.0] - 2026-07-03

### Summary

本次发布聚焦多用户体系、任务调度自动化、多 Sheet 页工作区与远程访问能力。从单用户工具升级为支持多账号隔离与团队协作的捡漏平台，并补齐任务自动实时搜索、Cloudflare Tunnel 远程访问、SonarQube 代码质量整改等关键能力。

### Added

#### 多用户体系（MU1 + MU2）
- **多用户数据层**：新增 6 张多用户 ORM 表模型，TaskRow 增加 `user_id` 字段与索引迁移。
- **UserManager**：身份识别 `identify_or_create`、会话签发 `issue_session`、滑动续期 `verify_session`、会话注销 `revoke_session`、用户列表/状态切换/删除/账号探测等完整生命周期管理。
- **数据迁移**：`migrate_to_multi_user` 启动时自动迁移历史 Cookie 与任务到默认用户。
- **CookieStore 用户隔离**：按 `user_id` 维度隔离 JSON 文件与内存缓存。
- **认证中间件三路校验**：WEB_TOKEN 直通 + session_token 校验 + 401 降级，注入 `user_id` 到请求上下文。
- **登录流程接入**：`unified_login` 集成 UserManager，登录成功后自动签发会话并导出 Cookie。
- **向后兼容**：`cookie_inject` / `auth_query` 调用自动补充 `user_id=default`。

#### 任务管理菜单实时搜索与采集周期配置
- **useAutoLiveSearch Hook**：任务自动实时搜索，支持倒计时、页面可见性感知、并发控制。
- **TaskList 集成**：新增「下次搜索」倒计时列、「自动搜索」开关、「采集周期」只读列。
- **手动/自动互斥**：折叠面板「实时查询」按钮与自动搜索互斥，避免冲突。
- **TaskSchedulerConfig**：新增 `default_interval_seconds` / `auto_search_enabled` / `auto_search_concurrency` 配置块，支持全局默认与任务级覆盖。
- **SearchConfig 分区**：新增「任务调度默认值」分区。

#### 多 Sheet 页工作区
- **useIsMobile**：响应式断点检测 Hook。
- **sheetRegistry**：路径到懒加载页面组件的映射注册表。
- **sheetStore**：Sheet 状态管理与持久化，支持 navigator 注入。
- **useSheetSync**：URL 与当前 Sheet 双向同步。
- **Sheet 组件套件**：SheetContent、SheetTabs、SheetPreferences、SheetWorkspace 容器。
- **MainLayout 改造**：用 SheetWorkspace 替换 Outlet，登出时清空 Sheet 栈。

#### Cloudflare Tunnel 远程访问
- **内置 TunnelService**：零配置启动 Cloudflare Tunnel，支持远程访问本地服务。
- **Tunnel API**：`/api/tunnel/start` / `/api/tunnel/stop` 一键开关。

#### 其他
- **全局流水号中间件**：每个请求分配 `request_id`，贯穿日志链路。
- **钉钉通知渠道**：新增钉钉机器人推送。
- **采集服务**：统一搜索服务标准化。
- **前端用户菜单**：右上角用户头像下拉菜单。

### Changed
- **搜索服务标准化**：评估价格覆盖逻辑统一，钉钉通知时机修正。
- **MainLayout 重构**：Outlet → SheetWorkspace，登出清理 Sheet 栈。

### Fixed

#### Scheduler 与 API 修复
- **ResumeBlockedError ImportError**：main 分支上 `scheduler.py` 引用未定义的 `ResumeBlockedError`。
- **api_tasks await 不匹配**：移除 `pause/resume/start` 的 `await` 调用，匹配 scheduler 同步方法签名。
- **api_anticrawl 返回类型**：`/freq/delay` 返回 `dict | JSONResponse` Union 类型不被 Pydantic 支持，改为 `response_model=None`。
- **5 分钟冷却**：会话过期暂停后 5 分钟内禁止重复恢复，避免雪崩。

#### 多用户模块修复
- **Cookie 文件迁移异常保护**：迁移失败不中断启动。
- **verify_session 竞态条件**：滑动续期测试补充。
- **issue_session 隔离**：`_log_event` 失败不影响会话签发。
- **get_user_manager 初始化**：确保 `init_db` 在 engine 使用前调用。
- **时间字段类型**：与现有表模式保持一致。
- **路径遍历校验**：`_cookie_json_path` 防路径遍历攻击。
- **代码评审整改**：2 Critical + 7 Important 问题修复。

#### 任务管理与前端修复
- **类型不一致**：`api_tasks.py` 的 `interval_seconds` 类型与 DB schema 对齐。
- **递归调用栈**：`useAutoLiveSearch` 递归调用 `processQueue` 改为迭代。
- **Switch 关闭未暂停**：`TaskList.tsx` Switch 关闭时未调用 `pauseAll`。
- **defineProperty writable**：测试 L58 补齐 `writable: true`。
- **aria-label**：Switch 补充无障碍标签。
- **警告文本**：`SearchConfig` 自动并发警告文本优化。

#### 其他修复
- **TunnelService**：清理未使用导入，添加 `stdout=None` 防御。
- **test_app_config**：修复 `test_app_config_loads_task_scheduler_from_yaml` 全局 `_config` 污染。
- **SonarQube 整改**：79 个跟踪文件的代码质量问题全面修复。

### Security
- **路径遍历防护**：`_cookie_json_path` 增加路径遍历校验。
- **认证中间件**：三方校验防止会话伪造。

---

## [0.2.0] - 2026-06-29

### Summary

本次发布包含 Sprint A 至 About 菜单 sprint 的全部功能与优化，从 MVP 升级为功能完整的闲鱼捡漏平台。涉及任务管理、价格策略、AI 评估、通知渠道、反检测、浏览器自动化、配置版本管理、PWA、Docker 部署、数据导出、About 菜单等 14 个模块。

### Added

#### 任务管理
- **任务 CRUD**：创建/编辑/删除/启停，支持 Cron 表达式调度。
- **任务编辑向导**：6 步骤（基础信息、价格与过滤、搜索参数、AI 评估、反检测、调度与确认），任务级配置覆盖全局。
- **批量任务操作**：批量启停、删除、立即执行。
- **AI 自然语言建任务**：输入自然语言描述自动解析为任务配置。
- **任务依赖（F-16）**：任务间依赖关系配置。
- **模板市场（F-11）**：任务模板一键应用。

#### 价格策略
- **4 种策略独立开关**：硬性上限/下限、低于市场参考价、同类低价 TopN。
- **价格直方图**：ECharts 可视化当前商品价格分布与过滤区间。
- **30 天价格趋势基线**：PriceHistogramCard 增加 last30d 对比。

#### 评估规则
- **4 维权重雷达图**：滑块联动 ECharts 雷达图，权重总和指示器。
- **职业卖家检测**：3 维度（在售数/30 天发帖数/主营品类占比）+ 图像盗用检测（SQLite LIKE 查询）。
- **深度鉴伪**：`/api/ai/deep` 端点 + 前端 Tabs 展示单维度分析。
- **评估漏斗**：EvalFunnelCard 5 阶段转化率可视化。
- **AI 多轮趋势建议**：基于历史评估数据 LLM 生成优化建议。

#### 通知渠道
- **7 个渠道**：Server酱、PushPlus、Bark、Telegram、企业微信、钉钉、自定义 Webhook。
- **拖拽排序**：dnd-kit 实现通道顺序拖拽。
- **免打扰时段**：时间范围滑块 + 跨午夜 + 周末开关。
- **事件订阅规则**：12 种事件勾选 + 级别映射。

#### 反检测与浏览器
- **QPS 限流 + 延迟范围 + 失败熔断**：三道防线。
- **WAF 熔断器**：自动检测 WAF 触发并熔断。
- **WebView2 持久化**：`private_mode=False` + 唯一 `storage_path`，cookie 持久化。
- **CREATE_NEW_CONSOLE**：子进程使用新控制台，防止 GUI 闪退。

#### 反爬登录管理
- **统一登录入口**：扫码/浏览器登录/Cookie 注入/浏览器导入 4 路径收敛。
- **会话管理自动启动**：登录成功后自动启动 TokenRenewer 后台续期，无需手动点击。
- **Cookie 三层状态机**：identity / session / tracking 独立判定 + 自动恢复。
- **force_restore_layers**：依赖层校验，避免在底层 cookie 失效时误恢复上层。

#### 数据导出与采集
- **CSV 导出**：商品/评估/订单/事件 4 个数据集，支持 task_id/状态/时间过滤。
- **批量数据刷新**：APScheduler + asyncio 调度，重试 + 熔断机制。
- **已售商品过滤**：后端 SQL WHERE 推送 + 前端状态持久化。

#### About 菜单
- **品牌卡**：版本/构建日期/Git SHA + 5 态检查更新按钮。
- **开源软件声明**：41 个依赖（21 前端 + 20 后端）+ 搜索过滤。
- **构建元信息**：`scripts/build_info.py` 自动生成 `_build_info.py`。
- **5 个入口**：顶栏按钮、Sider、Command Palette、快捷键 `g+a`、Swagger UI。
- **GitHub 自动更新检查**：后端 `/api/about/check-update` 接入 GitHub Releases API（仓库 `wangnan05563/xianyu-hunter`），5 分钟缓存避免限速，失败安全降级。
- **前端自动定期检查**：About 页面挂载 5 秒后自动检查一次，每 60 分钟复查；卸载时清理所有定时器，杜绝内存泄漏。
- **BrandCard 发布时间展示**：newer 态 Tooltip 展示 release 发布日期。

#### 系统优化
- **PWA 支持**：VitePWA + manifest + workbox（排除 SSE/auth/export）。
- **SSE 断线重连**：Dashboard localStorage 持久化 lastEventId，重连后重放。
- **Docker 部署**：3 阶段 Dockerfile + 多 profile docker-compose。
- **全局快捷键**：Command Palette（`g+*` 路由前缀）。
- **利润计算器**：Orders 页面 1.6% 鱼小铺手续费。

### Changed

- **数据库索引优化**：task_id/seller_id/first_seen/publish_time/created_at 索引 + 复合索引。
- **COUNT 查询合并**：`_overview()` 使用 CASE WHEN 聚合，DB 调用减少 67%。
- **SQLite StaticPool**：单连接共享 + busy_timeout + cache_size，解决锁竞争。
- **dealer_detector 查询合并**：循环 LIKE → 单次 OR 查询。
- **配置覆盖语义**：任务级 `model_copy(update=...)` 不污染全局 singleton。
- **401 响应统一 JSON**：`{"detail": "Unauthorized"}` 替代纯文本。
- **token 比较**：`hmac.compare_digest()` 防止时序攻击。
- **`_is_newer` 保守策略**：任一端版本号无法解析时返回 False，避免 current='unknown' 时误判有更新。

### Fixed

- **评估明细点击「通过」查不到记录**：统计卡片显示全量计数，但前端仅过滤当前页。修复为后端 `result_category` 参数精确分类 + 分页返回。
- **`eval_threshold` NOT NULL 约束失败**：改为 nullable，worker 优先读取任务级阈值。
- **`antidetect_config` 死字段**：UI 改为全局快捷 Modal，避免配置保存但不生效。
- **编辑模式清空 overrides 失败**：`model_dump(exclude_unset=True)` 修复 null 语义断层。
- **TokenRenewer 启动遗漏**：4 个登录入口统一接入 `trigger_session_start`。
- **`_m_h5_tk` 续期失败**：导航到 `h5.m.taobao.com` 触发（用户自然行为，风控压力低）。
- **useUpdateChecker 内存泄漏**：setTimeout 句柄 useRef 持有 + useEffect 卸载清理。
- **PriceHistogramCard `token.colorPurple`**：替换为 `#722ED1` 修复 TS2339。

### Security

- **认证白名单**：`/api/about`、`/api/auth/cookie`、`/api/auth/me`、`/api/events/stream` 等加入 PUBLIC_PREFIXES。
- **敏感字段过滤**：token 长度不记录日志，仅记录布尔匹配结果。
- **Token 写入失败告警**：`logging.warning()` 提示。
- **导入补全**：`re`/`threading`/`logging` 模块级导入，防止运行时 NameError。
- **GitHub API 请求零隐私**：HTTP 头仅含 User-Agent + Accept，不携带 Authorization/token；不发送用户身份、cookie、设备指纹；release_url 始终指向 github.com。

### Compatibility

- **Python**：>=3.10（开发环境 3.14）。
- **Node.js**：>=18（构建环境 24）。
- **浏览器**：Chrome 90+（PWA 需 Service Worker 支持）。
- **数据库**：SQLite（无需额外服务）。
- **操作系统**：Windows 10+（WebView2 依赖）/ Linux / macOS。

---

## [0.1.0] - 2026-06-06

### Summary

初始 MVP 版本，包含基础任务管理、闲鱼搜索、商品采集、评估通知核心流程。

### Added

- **任务管理**：关键词、价格区间、地域、排除词、闲鱼筛选标签、任务模式（AUTO/SEMI_AUTO/CONFIRM/NOTIFY_ONLY）。
- **闲鱼搜索**：Playwright 浏览器自动化，支持 Cookie 注入。
- **商品采集**：标题、价格、卖家、图片、发布时间。
- **基础评估**：4 维权重（价格/卖家/信用/历史）+ pass_score 阈值。
- **通知渠道**：Server酱、PushPlus、Bark（3 个基础渠道）。
- **FastAPI 后端**：SQLAlchemy + SQLite，SSR 模板渲染。
- **htmx 前端**：服务端渲染 + htmx 局部刷新。
- **Swagger UI**：API 文档 + 顶栏品牌块。

### Compatibility

- **Python**：>=3.10。
- **浏览器**：Chrome 90+。
- **数据库**：SQLite。

---

[Unreleased]: https://github.com/wangnan05563/xianyu-hunter/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/wangnan05563/xianyu-hunter/releases/tag/v0.4.0
[0.3.1]: https://github.com/wangnan05563/xianyu-hunter/releases/tag/v0.3.1
[0.3.0]: https://github.com/wangnan05563/xianyu-hunter/releases/tag/v0.3.0
[0.2.0]: https://github.com/wangnan05563/xianyu-hunter/releases/tag/v0.2.0
[0.1.0]: https://github.com/wangnan05563/xianyu-hunter/releases/tag/v0.1.0
