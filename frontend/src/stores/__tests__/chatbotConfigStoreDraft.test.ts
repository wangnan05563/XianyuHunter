import { beforeEach, describe, expect, it, vi } from 'vitest'

// T05072：客服配置统一保存 store 的草稿/脏检测/dry-run 契约测试。
// 与 configStoreDraft.test.ts 同口径，但保存通道是 chatbotApi.saveConfig
// （POST /api/chatbot/config/batch，整份提交 + 白名单 diff）。

vi.mock('../../pages/Chatbot/api', () => ({
  chatbotApi: { getConfig: vi.fn(), saveConfig: vi.fn() },
}))

import { chatbotApi } from '../../pages/Chatbot/api'
import type { ChatbotConfig } from '../../pages/Chatbot/types'
import { useChatbotConfigStore } from '../chatbotConfigStore'

const server = () =>
  ({
    enabled: true,
    max_history_turns: 10,
    session_timeout_min: 30,
    welcome_message: null,
    rag: { top_k: 5, similarity_threshold: 0.65, max_context_chars: 8000 },
    llm: { model: 'gpt-4o-mini', temperature: 0.3, max_tokens: 1024 },
    agent: { enable_tools: true, max_tool_rounds: 5, tool_trigger_mode: 'function_calling' },
    kb: { auto_update_enabled: true, update_interval_hours: 6 },
    faq: { similarity_threshold: 0.6, confirm_threshold: 0.85 },
    escalation: { feedback_threshold: 2, feedback_window_min: 30, contact: '', sanitize_pii: true },
    updatable_keys: [],
  }) as unknown as ChatbotConfig

describe('chatbotConfigStore 草稿与加载（T05072）', () => {
  beforeEach(() => {
    useChatbotConfigStore.setState({ config: null, original: null, loading: false, error: null })
    vi.mocked(chatbotApi.getConfig).mockReset()
    vi.mocked(chatbotApi.saveConfig).mockReset()
  })

  it('load 拉取服务端配置并建立 original 快照', async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    const cfg = await useChatbotConfigStore.getState().load()
    expect(cfg).not.toBeNull()
    expect(useChatbotConfigStore.getState().dirtyPaths()).toEqual([])
  })

  it('update 点号路径只写草稿，不改 original', async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    await useChatbotConfigStore.getState().load()

    useChatbotConfigStore.getState().update('rag.top_k', 8)
    useChatbotConfigStore.getState().update('llm.model', 'qwen-plus')

    const { config, original } = useChatbotConfigStore.getState()
    expect((config as ChatbotConfig).rag.top_k).toBe(8)
    expect((config as ChatbotConfig).llm.model).toBe('qwen-plus')
    expect((original as ChatbotConfig).rag.top_k).toBe(5)
    expect(useChatbotConfigStore.getState().dirtyPaths()).toEqual([
      'llm.model',
      'rag.top_k',
    ])
  })

  it('再次 load 时未保存草稿不被服务端新值抹掉（同 configStore.applyDrafts 口径）', async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    await useChatbotConfigStore.getState().load()
    useChatbotConfigStore.getState().update('rag.top_k', 8)

    const changed = server()
    ;(changed.llm as { max_tokens: number }).max_tokens = 2048
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(changed)
    await useChatbotConfigStore.getState().load()

    const config = useChatbotConfigStore.getState().config as ChatbotConfig
    expect(config.rag.top_k).toBe(8)
    expect(config.llm.max_tokens).toBe(2048)
    expect((useChatbotConfigStore.getState().original as ChatbotConfig).llm.max_tokens).toBe(2048)
  })

  it('reset 放弃全部草稿', async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    await useChatbotConfigStore.getState().load()
    useChatbotConfigStore.getState().update('rag.top_k', 8)

    useChatbotConfigStore.getState().reset()

    expect((useChatbotConfigStore.getState().config as ChatbotConfig).rag.top_k).toBe(5)
    expect(useChatbotConfigStore.getState().hasChanges()).toBe(false)
  })
})

describe('chatbotConfigStore 统一保存（T05072）', () => {
  beforeEach(() => {
    useChatbotConfigStore.setState({ config: null, original: null, loading: false, error: null })
    vi.mocked(chatbotApi.getConfig).mockReset()
    vi.mocked(chatbotApi.saveConfig).mockReset()
  })

  const makeDirty = async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    await useChatbotConfigStore.getState().load()
    useChatbotConfigStore.getState().update('rag.top_k', 8)
    useChatbotConfigStore.getState().update('llm.model', 'qwen-plus')
  }

  it('previewSave 走 dry-run 并映射后端 diffs', async () => {
    await makeDirty()
    vi.mocked(chatbotApi.saveConfig).mockResolvedValue({
      ok: true,
      dry_run: true,
      diffs: [
        { path: 'rag.top_k', op: 'modify', old: 5, new: 8 },
        { path: 'llm.model', op: 'modify', old: 'gpt-4o-mini', new: 'qwen-plus' },
        { path: 'welcome_message', op: 'remove', old: 'x', new: null },
      ],
    })

    const changes = await useChatbotConfigStore.getState().previewSave()
    expect(vi.mocked(chatbotApi.saveConfig)).toHaveBeenCalledWith(expect.anything(), true)
    expect(changes).toHaveLength(3)
    expect(changes[2]).toEqual({ path: 'welcome_message', op: 'delete', old_value: 'x', new_value: null })
  })

  it('confirmSave 落库并把 original 前移，脏数清零', async () => {
    await makeDirty()
    vi.mocked(chatbotApi.saveConfig).mockResolvedValue({ ok: true, dry_run: false, diffs: [] })

    await useChatbotConfigStore.getState().confirmSave()

    const payload = vi.mocked(chatbotApi.saveConfig).mock.calls[0][0] as ChatbotConfig
    expect(payload.rag.top_k).toBe(8)
    expect(vi.mocked(chatbotApi.saveConfig).mock.calls[0][1]).toBe(false)
    expect(useChatbotConfigStore.getState().dirtyPaths()).toEqual([])
    expect((useChatbotConfigStore.getState().original as ChatbotConfig).rag.top_k).toBe(8)
  })

  it('confirmSave 失败时草稿保留且错误上账', async () => {
    await makeDirty()
    vi.mocked(chatbotApi.saveConfig).mockRejectedValue(new Error('保存失败'))

    await expect(useChatbotConfigStore.getState().confirmSave()).rejects.toThrow('保存失败')

    expect(useChatbotConfigStore.getState().dirtyPaths()).toEqual(['llm.model', 'rag.top_k'])
    expect(useChatbotConfigStore.getState().error).toBe('保存失败')
  })

  it('previewSave 无草稿时仍会发起 dry-run（由后端返回空 diffs 判定「未变更」）', async () => {
    vi.mocked(chatbotApi.getConfig).mockResolvedValue(server())
    await useChatbotConfigStore.getState().load()
    vi.mocked(chatbotApi.saveConfig).mockResolvedValue({ ok: true, dry_run: true, diffs: [] })

    const changes = await useChatbotConfigStore.getState().previewSave()
    expect(changes).toEqual([])
    expect(vi.mocked(chatbotApi.saveConfig)).toHaveBeenCalledTimes(1)
  })
})
