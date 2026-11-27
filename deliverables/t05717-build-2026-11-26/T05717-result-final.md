## 根因

T05717 的原始报错不是「Chromium 没打进安装包」这么简单，实机复现后是两层问题叠加：

1. **主因：Playwright 新版本把 Windows 完整内核目录从 `chrome-win` 改成了 `chrome-win64`。**
   安装目录实际结构是 `playwright_browsers\chromium-1223\chrome-win64\chrome.exe`，而旧版运行时
   `bundled_chromium_dir()` / `_bundled_kernel()` 与构建校验 `Test-FullChromium` 都硬编码
   `chrome-win\chrome.exe`。结果随包内核明明完整，程序却始终判定「找不到」，下载兜底安装成功后
   仍会误报「未找到 chromium-*」。
2. **次因：一键发布默认产 Lite 包。**
   `scripts/config.publish.json` 的 `build_pipeline.bundle_browser` 曾为 `false`，默认产物不含
   Chromium，与「离线可用」的硬约束冲突，也是重装后目录没有完整内核的诱因之一。

## 影响范围

- 所有浏览器启动入口最终都走 `infra/browser_kernel.resolve_kernel`：Worker 抓取
  `infra/browser.py`、扫码登录 `scripts/browser_login.py`、二维码/昵称抓取
  `scripts/auth_helper.py`。一处修复即可覆盖全链路。
- 仅影响打包态（`sys.frozen=True`）下的随包 Chromium 解析；dev 模式仍走系统 Edge/Chrome
  或下载兜底，不受影响。
- 不影响闲鱼抓取、登录态维持、订单处理等业务逻辑。

## 修复（绝对路径 + 改动点）

### commit `17f44040`：Full 包缺失内核不再自动下载

- `D:\code\otherProjects\XianyuHunter\backend\xianyu_hunter\infra\browser_kernel.py`
  - 新增 `is_bundled_build()`（frozen 且安装目录存在 `playwright_browsers/`）；
  - 新增 `_bundled_integrity_error()`；
  - `resolve_kernel` 的 `auto` / `bundled` 分支在 Full 构建缺失内核时直接抛出完整性错误，
    Lite/dev 仍保留下载兜底。
- `D:\code\otherProjects\XianyuHunter\scripts\build-exe.ps1` 5.4 节
  - 完整内核判据收紧为 `chromium-*/chrome-win/chrome.exe`，不匹配 headless-shell；
  - 复制后二次校验，缺内核直接 `throw` 终止构建。

### commit `95ed1d4b`：兼容 `chrome-win64` 布局（核心修复）

- `backend\xianyu_hunter\infra\browser_kernel.py`
  - 新增 `chromium_executable()` 单一真相源：兼容 `chrome-win64`、`chrome-win`、
    `chrome-linux64`、`chrome-linux`，末尾带 `*/chrome.exe` / `*/chrome` glob 兜底；
  - `bundled_chromium_dir()` 与 `_bundled_kernel()` 统一经此解析，显式排除
    `chromium_headless_shell-*`；
  - `is_bundled_build()` 优先读构建期标记 `.xh-full-bundle`，兼容旧包看 `chromium-*` 子目录，
    Lite 空目录不再误判 Full；
  - 报错附带实际目录内容；`/api/about` 增加 `bundled_executable` 与
    `browsers_dir_contents` 诊断字段。
- `scripts\build-exe.ps1` 5.4 节
  - `Test-FullChromium` 与运行时同口径（兼容新旧布局、排除 headless-shell）；
  - 复制校验通过后写入 `.xh-full-bundle` 标记。

### commit `1e5376ae`：默认 Full 包 + 发布前完整性门禁

- `scripts\config.publish.json`
  - `build_pipeline.bundle_browser` 由 `false` 改为 `true`，一键发布默认产 Full，同时额外产
    `-Lite` 包。
- `scripts\build-publish.ps1`
  - 构建后、定位产物前新增 Full 包 Chromium 完整性门禁：
    必须存在 `chromium-*/chrome-win64|chrome-win/chrome.exe`（排除 headless_shell）且存在
    `.xh-full-bundle` 标记，否则 `throw` 终止发布，杜绝空壳 Full 包流出。

## 验证（实测证据）

- `D:\code\otherProjects\XianyuHunter\tests\test_browser_kernel.py`
  - 2026-11-25 复跑：**27 passed，4 warnings，194.18s**；
  - 覆盖真实缓存结构 `chrome-win64` + `headless_shell` 共存、新旧布局、未知布局 glob 兜底、
    混合版本、`.xh-full-bundle` 标记判据、诊断字段。
- 真实安装目录直验（项目当前代码，2026-11-25）
  - `F:\Program Files\XianyuHunter\playwright_browsers` 实际存在 `chromium-1223`；
  - 解析结果：`chromium_dir=chromium-1223`，
    `executable=chrome-win64\chrome.exe`，
    `resolve_kernel("bundled")` 返回 `kind=bundled`。
- `scripts\build-exe.ps1` 与 `scripts\build-publish.ps1`
  - PowerShell `Parser.ParseFile` 语法错误均为 **0**。
- `scripts\config.publish.json`
  - `build_pipeline.bundle_browser = true`；构建命令保留 `__BUNDLE_BROWSER__` 占位符。
- 历史回归（本次未重复执行）：内核 / browser_login_launch / about / about_update /
  unified_login_mu2 / browser_data_dir 共 **119 passed**。

### 验证缺口

本机未安装 Inno Setup（`ISCC.exe` 不存在），且现有 `F:\Program Files\XianyuHunter` 里的
`xianyu-hunter.exe` 为 2026-10-30 旧包（早于 2026-11-13 的修复提交）。因此本轮没有重新产出
新的 Full 安装包，也没有执行「干净环境离线安装 → 点浏览器登录」的最终 GUI 实测。
该步已写入构建与验证指南，由实机按指南验收；本任务据此按 **done + verified=false** 收口，
不冒充已验证。

## 本次回写

- 关联 commit：`17f44040`、`95ed1d4b`、`1e5376ae`（已按规则 10 回写 git 链接）。
- 回传后状态：`done`，`verified=false`（实机安装验收待用户执行）。
- 构建与验证指南：
  `D:\code\otherProjects\XianyuHunter\deliverables\t05717-offline-chromium\构建与验证指南.md`

## 下一步建议

1. 在装有 Inno Setup 6 的发布机执行
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-exe.ps1 -BundleBrowser`，
   生成新的 Full 安装包。
2. 在干净、离线 Windows 机器安装 Full 包，点击「浏览器登录」，按指南第 4 节验收。
3. 验收通过后把该任务置为 verified；若失败，新报错会直接列出 `playwright_browsers` 实际目录内容，
   可据此定位是缺内核、误装 Lite 还是布局不符。
## 本次回写（2026-11-26 最终，覆盖上文“未重新产出”缺口）

- 已在发布机实际执行构建：`build-exe.ps1 -EmbeddingEngine onnx -BundleBrowser -SkipSPA -SkipDeps`，`exit_code=0`，日志出现 `[7/7]` 与“构建完成”。
- 版本：`0.4.20261126.2`；Full 安装包 `291,786,283` 字节，SHA256 `2C87BED688B39376873CE085B89353BC374EA50D2242D486514534FE7FEBC703`；Lite 安装包 `135,736,594` 字节，SHA256 `2BDCA24C14156C9D38A2A8F143A8C9A7CDB687232F0B86764ADC22BBC216F5D8`。
- GitHub Release：`https://github.com/wangnan05563/XianyuHunter/releases/tag/0.4.20261126.2`，id `459020223`，`draft=false`；Full/Lite 两个资产均为 `state=uploaded`，体积与本地一致。
- 官方一键发布复跑 `scripts\一键发布.bat -SkipBuild` 退出码 0，输出 `[publish] DONE.`，两个资产远端校验通过。
- Full 包实际内嵌 `chromium-1255/chrome-win64/chrome.exe`（451.92 MB）：Playwright CDN 的 1223 包连续两次下载到 80% 被服务端断开，改用本机已有完整 Chromium；构建与发布门禁均按 `chromium-*/chrome-win64` 布局校验通过，运行时显式传 `executable_path`。
- 首次 Python 上传在创建 Release 时两次出现 GitHub TLS `SSL: UNEXPECTED_EOF_WHILE_READING`，改用同仓库同 token 的 curl 完成首次草稿创建、双资产上传与发布；随后官方一键脚本复跑通过。
- 验证缺口：仍**未**在干净、离线 Windows 实机安装 Full 包并点击“浏览器登录”。因此本次构建/发布成功不等于离线登录 GUI 实测通过，任务继续保持 `done + verified=false`。
- 证据：`deliverables\t05717-build-2026-11-26\构建与发布结果-2026-11-26.md`、`build-exe-resume-2026-11-26.log`、`publish-curl-2026-11-26.log`、`publish-v2-2026-11-26.log`。