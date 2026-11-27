# XianyuHunter v0.4.20261127.2

本版接续 v0.4.20261127.1，修复在 Full 包离线验收中发现的一个错误路径缺陷，并补充可复现的回归测试。

## 修复

- **导航失败错误被掩盖**：`scripts/browser_login.py` 的轮询心跳变量只在“打开闲鱼成功”后创建，但 `finally` 无条件引用。断网或导航失败时，`page.goto()` 抛出的真实错误会先被 `UnboundLocalError: _poll_hb_stop` 覆盖，用户看到的是 `cannot access local variable '_poll_hb_stop'`，而不是网络原因。
- **修复方式**：进入 Playwright 前将 `_poll_hb_stop` / `_poll_hb_thread` 初始化为 `None`，`finally` 判空后再停止；同时停止启动期心跳线程，避免错误退出后仍有线程写状态文件。
- **回归测试**：`tests/test_browser_login_launch.py` 新增 `goto` 失败用例，断言状态文件保留真实 `ERR_NETWORK_ACCESS_DENIED`，且不再出现 `local variable`。

## 验收

- 从本版 Full 安装包解包后，在全新用户目录启动；强制 `XH_BROWSER_KERNEL=bundled`：
  - 包内 `chromium-1255\chrome-win64\chrome.exe` 成功启动，版本 `Google Chrome for Testing 159.0.8154.6`；
  - 联网时状态进入 `waiting`，窗口标题“我的闲鱼_闲鱼 - Google Chrome for Testing”；
  - 未出现 `playwright install chromium` 下载提示，也未出现“安装包内的 Chromium 不完整或缺失”。
- 临时防火墙阻断包内 `chrome.exe` 出站流量后重试：
  - Chromium 仍能启动；
  - 状态文件正确记录 `Page.goto: net::ERR_NETWORK_ACCESS_DENIED`，旧版 `UnboundLocalError` 不再出现。
- `tests/test_browser_login_launch.py`：12 passed。
- 构建脚本：依赖自检、SPA、PyInstaller、Chromium smoke、Playwright smoke、Lite 暂存、发布侧车全部通过，`EXIT=0`。

## 产物

- Full：`XianyuHunter-Setup-v0.4.20261127.2.exe`
  - 291,786,160 字节（278.3 MB）
  - SHA256 `4b87eda9abed9b30f6ba29d8b1730dfe6f5ee1452c6ae4c25763a462a5062808`
- Lite：`XianyuHunter-Setup-v0.4.20261127.2-Lite.exe`
  - 135,732,079 字节（129.4 MB）
  - SHA256 `22e2c4aa3cdf417acae527eb493819ce2604f4a61c3ccac2fe9ea2e40ce08347`

## 已知事项

- 实际扫码登录仍需要可访问闲鱼登录页的网络与账号，本次验证覆盖“包内 Chromium 启动 + 登录页打开 + 断网错误路径”，未执行真实账号扫码。
