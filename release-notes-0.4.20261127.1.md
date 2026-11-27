# XianyuHunter v0.4.20261127.1

本版是针对 2026-11-26 会话代码走查报告的修复版，重点修复 Full/Lite 形态判定、构建/发布门禁与 Chromium 完整性校验。

## 修复

- **Full/Lite 身份判定**：应用根目录新增 `bundle-manifest.json` 作为唯一身份源；即使 `playwright_browsers` 被整体删除，Full 仍会报“安装包不完整”，不会误判 Lite 去联网下载。旧的 `.xh-full-bundle` 标记保留兼容。
- **Chromium 完整性**：`chrome.exe` 必须同时具备非空 `chrome.dll`、`resources.pak`、`icudtl.dat`；空壳目录/被杀软隔离的文件不再算完整内核。
- **构建门禁**：`-SkipBuild` 也必须通过 `release\XianyuHunter-Setup-v<版本>.build.json` 侧车校验（版本、形态、manifest 哈希、Chromium 关键文件 size/sha256、主资产 size/sha256）。
- **主资产选择**：严格按 `pyproject.toml` 当前版本取 `XianyuHunter-Setup-v<版本>.exe`，不再按修改时间取“最新”，旧包不会顶替新包。
- **原子发布**：Full/Lite 作为同一次 Release 会话，先草稿上传并校验 `state/size/digest`，全部通过后再公开；已公开 Release 替换同名资产时先上传临时名，再切换正式名并保留回滚。
- **Release 标签绑定**：创建 Release 前先确保版本 tag 指向目标分支，公开时显式写回 `tag_name`，避免 draft Release 公开后仍是 `untagged-...` URL。
- **发布校验**：远端 `digest=sha256:...` 与本地 SHA256 必须一致，不再只比字节数。
- **构建期 smoke**：新增 `chrome --headless=new --version` 与真实 Playwright driver 启动 bundled Chromium 的页面加载 smoke；本次实测 Chrome for Testing `159.0.8154.6`。
- **Lite 正确性**：Lite 暂存目录使用 `robocopy /MIR` 强制镜像，复制后校验不含 `playwright_browsers`，并把 manifest 改写为 `bundle_browser=false`；安装包体积仍保持明显小于 Full。

## 产物

- Full：`XianyuHunter-Setup-v0.4.20261127.1.exe`
  - 291,790,871 字节（278.3 MB）
  - SHA256 `7b15bcc4216bdc44f3e52fde062ae82e102d0cc52847a5b8a8b76ac418816d22`
- Lite：`XianyuHunter-Setup-v0.4.20261127.1-Lite.exe`
  - 135,731,740 字节（129.4 MB）
  - SHA256 `fd01772d3d3726455145fbebaeaff68ad70b7c98f20ffa4019a556f4f5ec1b88`

## 已知事项

- 本版尚未在全新、离线 Windows 环境完成真实 GUI 登录验收；构建期的 Chromium/Playwright smoke 已通过，但 T05717 仍保持 `verified=false`。
- Full 包内 Chromium 为 `chromium-1255`（Chrome 159），不是 Playwright 默认 1223；构建期已用真实 driver 验证兼容性，建议首次安装后先做一次断网登录验证。
