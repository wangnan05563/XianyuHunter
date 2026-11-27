import type { AppConfig } from '../../api'
import { getEvalValidationError, sumWeights } from './evalRulesModel'

/** 抢单运行时参数，与后端 BuyerRuntimeConfig 对齐 */
export interface BuyerRuntimeConfig {
  click_retry_times: number
  click_retry_interval: number
  confirm_button_timeout: number
  price_tolerance: number
  min_interval_between_orders: number
  takeover_timeout_min: number
  max_orders_per_day: number
}

/** 账号轮换调度参数，与后端 AccountRotatorConfig 对齐 */
export interface AccountRotatorConfig {
  cooldown_sec: number
  fail_threshold: number
}

/** 本页真正读取并可编辑的配置子集 */
export interface BuyerStrategyConfig {
  eval: AppConfig['eval']
  buyer: AppConfig['buyer']
  account_rotator: AppConfig['account_rotator']
}

export interface NumericFieldSpec<K extends string = string> {
  key: K
  label: string
  min: number
  max: number
  step?: number
  precision?: number
  description: string
}

const BUYER_RUNTIME_KEYS: readonly (keyof BuyerRuntimeConfig)[] = [
  'click_retry_times',
  'click_retry_interval',
  'confirm_button_timeout',
  'price_tolerance',
  'min_interval_between_orders',
  'takeover_timeout_min',
  'max_orders_per_day',
]

const ACCOUNT_ROTATOR_KEYS: readonly (keyof AccountRotatorConfig)[] = [
  'cooldown_sec',
  'fail_threshold',
]

/** 运行时参数表单元数据：页面渲染与模型断言共用，避免标签/边界各写一份 */
export const BUYER_RUNTIME_FIELDS: readonly NumericFieldSpec<keyof BuyerRuntimeConfig>[] = [
  {
    key: 'click_retry_times',
    label: '点击重试次数',
    min: 0,
    max: 10,
    precision: 0,
    description: '点击“立即购买”按钮失败时的重试次数',
  },
  {
    key: 'click_retry_interval',
    label: '点击重试间隔（秒）',
    min: 0,
    max: 10,
    step: 0.1,
    description: '两次点击之间的退避秒数',
  },
  {
    key: 'confirm_button_timeout',
    label: '确认按钮超时（秒）',
    min: 1,
    max: 60,
    precision: 0,
    description: '等待“提交订单”按钮出现的超时',
  },
  {
    key: 'price_tolerance',
    label: '价格容差',
    min: 0,
    max: 1,
    step: 0.01,
    description: '拍下价格相对预期价格的允许偏差（0.05=5%）',
  },
  {
    key: 'min_interval_between_orders',
    label: '两次落单最小间隔（秒）',
    min: 0,
    max: 3600,
    precision: 0,
    description: '防手抖：两次落单间的最小间隔，0=不限制',
  },
  {
    key: 'takeover_timeout_min',
    label: '人工接管超时（分钟）',
    min: 1,
    max: 120,
    precision: 0,
    description: '与闲鱼订单关闭时间对齐，超时未接管则订单流转到下一状态',
  },
  {
    key: 'max_orders_per_day',
    label: '单日真实落单上限（次）',
    min: 0,
    max: 10000,
    precision: 0,
    description: '0＝不限制（默认）。非 0 时在提交订单前拦截：按 UTC 自然日计数、所有账号合计，与仪表盘「今日订单」同一口径；只统计真实落单，演练（dry_run）不占额度。保存后即生效，无需重启。',
  },
]

export const ACCOUNT_ROTATOR_FIELDS: readonly NumericFieldSpec<keyof AccountRotatorConfig>[] = [
  {
    key: 'cooldown_sec',
    label: '冷却时间（秒）',
    min: 60,
    max: 86400,
    precision: 0,
    description: '触发风控后的冷却秒数（默认 1800=30 分钟），冷却结束后自动恢复',
  },
  {
    key: 'fail_threshold',
    label: '连续失败阈值',
    min: 1,
    max: 50,
    precision: 0,
    description: '连续失败次数超过此值自动禁用账号（默认 5），需人工介入恢复',
  },
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function collectEvalMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.eval)) return ['eval']
  const evalSeg = root.eval
  const missing: string[] = []
  const weights = evalSeg.weights
  const weightKeys = ['professional', 'credit', 'dispute', 'price']
  if (!isRecord(weights) || !weightKeys.every((key) => isFiniteNumber(weights[key]))) {
    missing.push('eval.weights')
  }
  if (!isFiniteNumber(evalSeg.pass_score) || !isFiniteNumber(evalSeg.auto_buy_score)) {
    missing.push('eval.pass_score/auto_buy_score')
  }
  return missing
}

function collectBuyerMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.buyer)) return ['buyer']
  const buyer = root.buyer
  const invalidKeys = BUYER_RUNTIME_KEYS.filter((key) => !isFiniteNumber(buyer[key]))
  return invalidKeys.length > 0 ? [`buyer.${invalidKeys.join('/')}`] : []
}

function collectAccountRotatorMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.account_rotator)) return ['account_rotator']
  const rotator = root.account_rotator
  const invalidKeys = ACCOUNT_ROTATOR_KEYS.filter((key) => !isFiniteNumber(rotator[key]))
  return invalidKeys.length > 0 ? [`account_rotator.${invalidKeys.join('/')}`] : []
}

/**
 * 配置段完整性守卫。
 * 本页会同时读写 eval / buyer / account_rotator 三段，任一缺失都不能渲染默认值表单，
 * 否则用户会在“看似正常的默认值”上保存，覆盖后端真实配置。
 */
export function getBuyerStrategyConfigIssue(config: AppConfig | null): string | null {
  if (!config) return '配置尚未加载'
  const root = config as unknown as Record<string, unknown>
  const missing = [
    ...collectEvalMissing(root),
    ...collectBuyerMissing(root),
    ...collectAccountRotatorMissing(root),
  ]
  return missing.length > 0 ? `当前配置缺少或不完整：${missing.join('、')}` : null
}

/** 通过完整性守卫后再取强类型配置；无守卫直接断言会把缺段崩溃带到渲染层 */
export function getBuyerStrategyReadyConfig(config: AppConfig | null): BuyerStrategyConfig | null {
  return getBuyerStrategyConfigIssue(config) ? null : config as unknown as BuyerStrategyConfig
}

/**
 * 保存前校验。
 * SCOPE_BUYER 包含整个 eval 段，因此即使本页不展示权重，也必须拦住「评估规则」页留下的
 * 权重总和非法 / 分数顺序非法草稿，避免从本页绕过校验写盘。
 */
export function getBuyerStrategyValidationError(config: AppConfig | null): string | null {
  const issue = getBuyerStrategyConfigIssue(config)
  if (issue) return issue
  const ready = config as unknown as BuyerStrategyConfig
  return getEvalValidationError(
    sumWeights(ready.eval.weights),
    ready.eval.pass_score,
    ready.eval.auto_buy_score,
  )
}

/** 低于建议阈值只提示不阻断：后端允许自定义 auto_buy_score，本页不能擅自收紧配置 */
export function getAutoBuyThresholdWarning(autoBuyScore: number): string | null {
  if (!Number.isFinite(autoBuyScore) || autoBuyScore >= 80) return null
  return `auto_buy_score 当前为 ${autoBuyScore} 分，低于建议阈值 80；会扩大自动拍下范围，请确认已充分测试。`
}

/** 单日上限是真实护栏，0 必须明确显示为“不限制”而不是空白 */
export function getDailyOrderGuardText(maxOrdersPerDay: number): string {
  if (!Number.isFinite(maxOrdersPerDay) || maxOrdersPerDay <= 0) {
    return '未设置（不限制）——可在上方「抢单运行时参数」设定'
  }
  return `已启用，当前 ${maxOrdersPerDay} 次/日（UTC 自然日，所有账号合计，仅计真实落单）`
}

/** InputNumber 清空时不要静默折成 0；只有有限数字才写回配置 */
export function toFiniteNumberOrNull(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
