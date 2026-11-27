import type { AIConfig as AIConfigData, AIUsage } from '../../../api'

/** 参与「未保存修改」比对的字段；顺序即页头计数与测试断言的稳定顺序。 */
export const AI_CONFIG_FIELDS = [
  'ai_enabled',
  'base_url',
  'api_key',
  'model',
  'vision_model',
  'preset_id',
  'embedding_base_url',
  'embedding_api_key',
  'embedding_model',
  'embedding_dimensions',
] as const

export type AIConfigField = (typeof AI_CONFIG_FIELDS)[number]

/** 表单与页面基线共用的规范化：后端可能返回 null/缺失字段，页面内部统一成具体值。 */
export function normalizeAIConfig(input: Partial<AIConfigData> | null | undefined): AIConfigData {
  const text = (value: unknown): string => (typeof value === 'string' ? value : '')
  const rawDimensions = Number(input?.embedding_dimensions ?? 0)
  const dimensions = Number.isFinite(rawDimensions) ? Math.trunc(rawDimensions) : 0
  return {
    ai_enabled: input?.ai_enabled !== false,
    base_url: text(input?.base_url),
    api_key: text(input?.api_key),
    model: text(input?.model),
    vision_model: text(input?.vision_model),
    preset_id: typeof input?.preset_id === 'string' && input.preset_id ? input.preset_id : null,
    embedding_base_url: text(input?.embedding_base_url),
    embedding_api_key: text(input?.embedding_api_key),
    embedding_model: text(input?.embedding_model),
    embedding_dimensions: dimensions,
  }
}

/** 逐字段比对，返回发生变化的字段名。用于页头「未保存 N 项」与保存按钮门禁。 */
export function diffAIConfig(base: AIConfigData | null, current: AIConfigData | null): AIConfigField[] {
  if (!base || !current) return []
  const normalizedBase = normalizeAIConfig(base)
  const normalizedCurrent = normalizeAIConfig(current)
  return AI_CONFIG_FIELDS.filter((field) => JSON.stringify(normalizedBase[field]) !== JSON.stringify(normalizedCurrent[field]))
}

function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && Boolean(parsed.hostname)
  } catch {
    return false
  }
}

/**
 * 保存前校验。只拦截会导致运行期连接失败的确定错误；
 * 全部留空仍允许保存（等价于清空配置），由 AI 总开关控制是否实际调用。
 */
export function getAIConfigValidationError(config: AIConfigData | null): string | null {
  if (!config) return null
  const baseUrl = config.base_url.trim()
  const embeddingBaseUrl = (config.embedding_base_url ?? '').trim()
  const embeddingModel = (config.embedding_model ?? '').trim()
  const dimensions = Number(config.embedding_dimensions ?? 0)

  if (baseUrl && !isValidHttpUrl(baseUrl)) {
    return 'API Base URL 格式无效：请填写以 http:// 或 https:// 开头的完整地址'
  }
  if (embeddingBaseUrl && !isValidHttpUrl(embeddingBaseUrl)) {
    return 'Embedding API Base URL 格式无效：请填写以 http:// 或 https:// 开头的完整地址'
  }
  if (!baseUrl && config.model.trim()) {
    return '填写文本解析模型前，请先填写 API Base URL'
  }
  if (baseUrl && !config.model.trim()) {
    return '已填写 API Base URL，请同时填写文本解析模型'
  }
  if (embeddingBaseUrl && !embeddingModel) {
    return '远程 Embedding 模式需要填写 Embedding 模型名称'
  }
  if (!Number.isFinite(dimensions) || dimensions < 0 || dimensions > 3072) {
    return '向量维度需为 0–3072 之间的整数（0 表示由模型决定）'
  }
  return null
}

/** 测试连接按钮是否具备最小可执行条件。 */
export function canTestLLM(config: AIConfigData | null): boolean {
  if (!config) return false
  const baseUrl = config.base_url.trim()
  return config.ai_enabled
    && Boolean(baseUrl)
    && Boolean(config.model.trim())
    && isValidHttpUrl(baseUrl)
}

/** Embedding 可能是独立远程、Python 本地或复用 LLM，三种模式分别判断可测性。 */
export function canTestEmbedding(config: AIConfigData | null): boolean {
  if (!config) return false
  if (!config.ai_enabled) return false
  const baseUrl = (config.embedding_base_url ?? '').trim()
  const model = (config.embedding_model ?? '').trim()
  const dimensions = Number(config.embedding_dimensions ?? 0)
  if (baseUrl) return isValidHttpUrl(baseUrl) && Boolean(model)
  if (model || dimensions > 0) return true
  // 全部为空 = 复用 LLM；LLM 自身不可用时无法继承出可测端点。
  return Boolean(config.base_url.trim() && config.model.trim() && isValidHttpUrl(config.base_url.trim()))
}

export const BUDGET_FIELDS = ['daily_token_limit', 'daily_cost_limit_usd', 'rate_limit_per_min'] as const
export type BudgetField = (typeof BUDGET_FIELDS)[number]

/** 预算是否有未保存修改：usage 中的百分比字段随服务端刷新，不参与比对。 */
export function isBudgetDirty(base: AIUsage['budget'] | null, current: AIUsage['budget'] | null): boolean {
  if (!base || !current) return false
  return BUDGET_FIELDS.some((field) => Number(base[field] ?? 0) !== Number(current[field] ?? 0))
}
