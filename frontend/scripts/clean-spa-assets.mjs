/**
 * 构建前清理 release/spa 的历史产物
 *
 * 背景（2026-11-20 UI 走查）：vite.config.ts 里 outDir 指到仓库外，为避免 vite
 * 「拒绝清空外部目录」的报错设置了 emptyOutDir:false。副作用是每次构建都把上一版
 * 的 hash 文件留在 assets/ 里 —— 实测堆到 481 个文件 / 13.7 MB / 225 份 index-*.js，
 * 而 PWA 的 precache 会把这堆死文件全部列进清单（494 条 / 14 MB），新用户首次进入
 * 就要多下十几 MB 永远用不到的字节。
 *
 * 这里只删「vite 每次都会重新生成」的东西：
 * - release/spa/assets/        整目录（全部是带 hash 的构建产物）
 * - release/spa/sw.js          每次由 vite-plugin-pwa 重新生成
 * - release/spa/workbox-*.js   同上
 * 保留 favicon / icon.svg / index.html 等静态资源（它们来自 public/，会被重新拷贝）。
 */
import { mkdirSync, readdirSync, rmSync, unlinkSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const spaDir = resolve(here, '..', '..', 'release', 'spa')

// 删除是不可逆操作：先确认路径确实是 <repo>/release/spa，避免 cwd/相对路径意外导致误删
if (basename(spaDir) !== 'spa' || basename(dirname(spaDir)) !== 'release') {
  console.error(`[clean-spa-assets] 目标目录异常，拒绝执行：${spaDir}`)
  process.exit(1)
}

let removedFiles = 0
const assetsDir = join(spaDir, 'assets')

// 只清 assets 里的内容、保留目录本身：目录被整块删掉时，vite-plugin-pwa 的
// generateSW 在 outDir 不存在的情况下可能长时间卡在 build 阶段（2026-11-20 实测）。
mkdirSync(assetsDir, { recursive: true })
for (const name of readdirSync(assetsDir)) {
  rmSync(join(assetsDir, name), { recursive: true, force: true })
  removedFiles += 1
}

for (const name of readdirSync(spaDir)) {
  if (name === 'sw.js' || name.startsWith('workbox-')) {
    unlinkSync(join(spaDir, name))
    removedFiles += 1
  }
}

console.log(`[clean-spa-assets] 已清理 ${spaDir} 下的 assets/ 内容与 PWA 旧产物（共 ${removedFiles} 项）`)
