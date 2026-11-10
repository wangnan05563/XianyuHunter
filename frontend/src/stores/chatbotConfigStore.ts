import { create } from 'zustand'
import { collectChangedPaths } from './configDirty'
import type { DiffChange } from './configStore'
import { chatbotApi } from '../pages/Chatbot/api'
import type { ChatbotConfig } from '../pages/Chatbot/types'

/**
 * 客服配置统一保存 store（T05072 / FR-E-4，D-7 裁决：纳入统一保存范式）
 *
 * 为什么不复用 useConfigStore：客服配置与主配置是两套后端体系
 * （ChatbotConfig → /api/chatbot/config + DB 覆盖表 vs AppConfig → /api/config/save + config.yaml），
 * 混进同一个 store 会串数据。本 store 镜像 configStore 的对外契约
 * （草稿 / 字段级脏检测 / previewSave dry-run / confirmSave），保存通道走
 * POST /api/chatbot/config/batch（整份提交 + 白名单 diff），复用 configDirty
 * 的 collectChangedPaths 纯函数，保证脏检测口径与主配置一致。
 *
 * 与 configStore 的差异：
 * - 无 scope 概念：客服配置是单页 9 Tab，配置字段整体一个保存段；
 * - update 走点号路径（页面原 setNestedField 语义），不是顶层段 patch；
 * - FAQ / 知识库重建回滚 / 向量库维护 / 审计日志是动作或只读页，不进草稿（见 Config.tsx 说明）。
 */

function getAtPath(root: unknown, path: string): unknown {
  let cur: unknown = root
  for (const key of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[key]
  }
  return cur
}

function setAtPath(root: Record<string, unknown>, path: string, value: unknown): void {
  const keys = path.split('.')
  let cur: Record<string, unknown> = root
  for (let i = 0; i < keys.length - 1; i++) {
    const step = cur[keys[i]]
    if (step == null || typeof step !== 'object') return
    cur = step as Record<string, unknown>
  }
  const last = keys[keys.length - 1]
  if (value === undefined) delete cur[last]
  else cur[last] = value
}

/** 载入服务端最新配置后，把未保存草稿写回去（与 configStore.applyDrafts 同口径） */
function applyDrafts(
  fresh: ChatbotConfig,
  draft: ChatbotConfig,
  original: ChatbotConfig,
): ChatbotConfig {
  const next = structuredClone(fresh) as unknown as Record<string, unknown>
  for (const path of collectChangedPaths(draft, original)) {
    setAtPath(next, path, getAtPath(draft, path))
  }
  return next as unknown as ChatbotConfig
}

interface ChatbotConfigState {
  config: ChatbotConfig | null
  original: ChatbotConfig | null
  loading: boolean
  error: string | null

  /** 拉取服务端配置并合并未保存草稿；返回加载结果供页面错误隔离使用 */
  load: () => Promise<ChatbotConfig | null>
  /** 点号路径草稿更新（如 'rag.top_k'），即时本地生效，不触发任何请求 */
  update: (path: string, value: unknown) => void
  /** 放弃全部草稿，回滚到 original */
  reset: () => void
  /** 相对 original 的变更路径（点号），字段级脏检测 */
  dirtyPaths: () => string[]
  hasChanges: () => boolean
  /** dry-run：POST /config/batch dry_run=true，返回 diff 供预览弹窗 */
  previewSave: () => Promise<DiffChange[]>
  /** 确认保存：dry_run=false 实际落库，成功后 original 前移；
   *  T05209：返回热加载结果供页面提示（reloaded=已实时生效，restartRequired=需重启） */
  confirmSave: () => Promise<{ reloaded: string[]; restartRequired: string[] }>
}

// 后端 diffs（op: 'add'|'modify'|'delete'）已与前端 DiffChange 同形，仅做 op 兜底映射
function toDiffChanges(
  diffs: Array<{ path: string; op: string; old: unknown; new: unknown }>,
): DiffChange[] {
  return diffs.map((d) => ({
    path: d.path,
    op: (d.op === 'remove' ? 'delete' : d.op) as DiffChange['op'],
    old_value: d.old,
    new_value: d.new,
  }))
}

export const useChatbotConfigStore = create<ChatbotConfigState>((set, get) => ({
  config: null,
  original: null,
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null })
    try {
      const fresh = await chatbotApi.getConfig()
      const { config: draft, original } = get()
      const nextConfig = draft && original ? applyDrafts(fresh, draft, original) : fresh
      set({ config: nextConfig, original: structuredClone(fresh), loading: false })
      return nextConfig
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载客服配置失败'
      set({ error: msg, loading: false })
      return null
    }
  },

  update: (path, value) => {
    const { config } = get()
    if (!config) return
    const next = structuredClone(config) as unknown as Record<string, unknown>
    setAtPath(next, path, value)
    set({ config: next as unknown as ChatbotConfig })
  },

  reset: () => {
    const { original } = get()
    if (!original) return
    set({ config: structuredClone(original) })
  },

  dirtyPaths: () => {
    const { config, original } = get()
    if (!config || !original) return []
    return collectChangedPaths(config, original).sort((a, b) => a.localeCompare(b))
  },

  hasChanges: () => {
    const { config, original } = get()
    if (!config || !original) return false
    return JSON.stringify(config) !== JSON.stringify(original)
  },

  previewSave: async () => {
    const { config } = get()
    if (!config) return []
    const res = await chatbotApi.saveConfig(config, true)
    return toDiffChanges(res?.diffs ?? [])
  },

  confirmSave: async () => {
    const { config } = get()
    if (!config) return { reloaded: [], restartRequired: [] }
    set({ loading: true, error: null })
    try {
      const res = await chatbotApi.saveConfig(config, false)
      set({ original: structuredClone(config), loading: false })
      // T05209：透传后端热加载结果（dry_run=false 响应）；dry-run/旧后端无此字段时兜底空
      return {
        reloaded: res?.hot_reload?.reloaded ?? [],
        restartRequired: res?.hot_reload?.restart_required ?? [],
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '保存客服配置失败'
      set({ error: msg, loading: false })
      throw e
    }
  },
}))
