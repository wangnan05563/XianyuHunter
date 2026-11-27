import { describe, expect, it } from 'vitest'
import type { AIConfig, AIUsage } from '../../../../api'
import {
  canTestEmbedding,
  canTestLLM,
  diffAIConfig,
  getAIConfigValidationError,
  isBudgetDirty,
  normalizeAIConfig,
} from '../aiConfigModel'

const base: AIConfig = normalizeAIConfig({
  ai_enabled: true,
  base_url: 'https://api.openai.com/v1',
  api_key: '****abcd',
  model: 'gpt-4o-mini',
  vision_model: 'gpt-4o',
  preset_id: 'openai',
})

const budget: AIUsage['budget'] = {
  daily_token_limit: 500000,
  daily_cost_limit_usd: 5,
  rate_limit_per_min: 20,
  token_usage_pct: 25,
  cost_usage_pct: 8,
}

describe('aiConfigModel', () => {
  it('把 null/缺失字段规范化为可渲染的具体值', () => {
    const value = normalizeAIConfig({
      ai_enabled: undefined,
      base_url: null as unknown as string,
      api_key: null as unknown as string,
      embedding_dimensions: Number.NaN,
      preset_id: '',
    })
    expect(value.ai_enabled).toBe(true)
    expect(value.base_url).toBe('')
    expect(value.api_key).toBe('')
    expect(value.embedding_dimensions).toBe(0)
    expect(value.preset_id).toBeNull()
  })

  it('逐字段统计未保存修改，不把等价空值算成脏', () => {
    const current = { ...base, base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash', preset_id: 'deepseek' }
    expect(diffAIConfig(base, current)).toEqual(['base_url', 'model', 'preset_id'])
    expect(diffAIConfig(base, normalizeAIConfig(base))).toEqual([])
    expect(diffAIConfig(null, current)).toEqual([])
  })

  it('Embedding 字段变化同样计入未保存修改', () => {
    const current = { ...base, embedding_model: 'BAAI/bge-small-zh-v1.5', embedding_dimensions: 512 }
    expect(diffAIConfig(base, current)).toEqual(['embedding_model', 'embedding_dimensions'])
  })

  it('拒绝非 http(s) 或缺少主机名的 Base URL', () => {
    expect(getAIConfigValidationError({ ...base, base_url: 'not-a-url' })).toContain('API Base URL 格式无效')
    expect(getAIConfigValidationError({ ...base, base_url: 'ftp://example.com/v1' })).toContain('API Base URL 格式无效')
    expect(getAIConfigValidationError({ ...base, base_url: 'http://' })).toContain('API Base URL 格式无效')
    expect(getAIConfigValidationError(base)).toBeNull()
  })

  it('Base URL 与文本模型必须成对填写', () => {
    expect(getAIConfigValidationError({ ...base, base_url: '', model: 'gpt-4o-mini' })).toContain('请先填写 API Base URL')
    expect(getAIConfigValidationError({ ...base, model: '' })).toContain('请同时填写文本解析模型')
    expect(getAIConfigValidationError({ ...base, base_url: '', model: '' })).toBeNull()
  })

  it('远程 Embedding 必须有模型，维度限制在 0–3072', () => {
    expect(getAIConfigValidationError({ ...base, embedding_base_url: 'https://api.openai.com/v1', embedding_model: '' })).toContain('Embedding 模型')
    expect(getAIConfigValidationError({ ...base, embedding_dimensions: -1 })).toContain('0–3072')
    expect(getAIConfigValidationError({ ...base, embedding_dimensions: 3073 })).toContain('0–3072')
  })

  it('测试连接要求 AI 开启、URL 与模型齐备', () => {
    expect(canTestLLM(base)).toBe(true)
    expect(canTestLLM({ ...base, ai_enabled: false })).toBe(false)
    expect(canTestLLM({ ...base, model: '' })).toBe(false)
    expect(canTestLLM({ ...base, base_url: 'bad' })).toBe(false)
    expect(canTestLLM(null)).toBe(false)
  })

  it('LLM 测试不被未完成的 Embedding 配置连坐', () => {
    const incompleteEmbedding = {
      ...base,
      embedding_base_url: 'https://api.openai.com/v1',
      embedding_model: '',
    }
    expect(canTestLLM(incompleteEmbedding)).toBe(true)
    expect(canTestEmbedding(incompleteEmbedding)).toBe(false)
  })

  it('Embedding 三种模式分别判断可测性', () => {
    expect(canTestEmbedding({ ...base, embedding_base_url: '', embedding_model: 'BAAI/bge-small-zh-v1.5', embedding_dimensions: 512 })).toBe(true)
    expect(canTestEmbedding({ ...base, embedding_base_url: 'https://api.openai.com/v1', embedding_model: 'text-embedding-3-small' })).toBe(true)
    expect(canTestEmbedding({ ...base, embedding_base_url: 'bad', embedding_model: 'text-embedding-3-small' })).toBe(false)
    // 全空 = 复用 LLM，LLM 可用时才可测
    expect(canTestEmbedding({ ...base, embedding_base_url: '', embedding_model: '', embedding_dimensions: 0 })).toBe(true)
    expect(canTestEmbedding({ ...base, ai_enabled: false, embedding_base_url: '', embedding_model: '', embedding_dimensions: 0 })).toBe(false)
  })

  it('预算只比较三个可编辑字段，忽略服务端百分比刷新', () => {
    expect(isBudgetDirty(budget, { ...budget, token_usage_pct: 31, cost_usage_pct: 12 })).toBe(false)
    expect(isBudgetDirty(budget, { ...budget, daily_token_limit: 600000 })).toBe(true)
    expect(isBudgetDirty(budget, { ...budget, daily_cost_limit_usd: 6 })).toBe(true)
    expect(isBudgetDirty(budget, { ...budget, rate_limit_per_min: 30 })).toBe(true)
    expect(isBudgetDirty(null, budget)).toBe(false)
  })
})
