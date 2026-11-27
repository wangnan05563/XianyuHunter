import type { HistogramData } from '../../api'

/** 页面可编辑的价格策略字段（与后端 PriceStrategyConfig 对齐） */
export interface PriceStrategyConfig {
  enabled_max: boolean
  max_price: number
  enabled_min: boolean
  min_price: number
  enabled_market_ratio: boolean
  market_ratio: number
  enabled_top_n: boolean
  top_n: number
}

/** 仅用于配置缺失时的兜底；已加载的真实配置永远优先 */
export const DEFAULT_PRICE_STRATEGY: PriceStrategyConfig = {
  enabled_max: true,
  max_price: 10000,
  enabled_min: true,
  min_price: 100,
  enabled_market_ratio: false,
  market_ratio: 0.8,
  enabled_top_n: false,
  top_n: 5,
}

export const MAX_PRICE_LIMIT = 50000
export const MIN_PRICE_LIMIT = 5000

export interface PriceBinVerdict {
  index: number
  min: number
  max: number | null
  count: number
  pass: number
  reject: number
  status: 'pass' | 'reject' | 'boundary'
}

export interface PriceHitEstimate {
  total: number
  pass: number
  reject: number
  /** 跨越价格阈值、只能按区间占比估算的桶内商品数 */
  boundary: number
  /** 存在跨阈值桶时为 true，界面必须标注“估算” */
  estimated: boolean
  bins: PriceBinVerdict[]
}

function finiteNumber(value: unknown, fallback: number): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 合并默认值，同时把接口里的字符串数字收敛为 number */
export function normalizePriceStrategy(
  value: Partial<PriceStrategyConfig> | null | undefined,
): PriceStrategyConfig {
  return {
    enabled_max: value?.enabled_max ?? DEFAULT_PRICE_STRATEGY.enabled_max,
    max_price: finiteNumber(value?.max_price, DEFAULT_PRICE_STRATEGY.max_price),
    enabled_min: value?.enabled_min ?? DEFAULT_PRICE_STRATEGY.enabled_min,
    min_price: finiteNumber(value?.min_price, DEFAULT_PRICE_STRATEGY.min_price),
    enabled_market_ratio:
      value?.enabled_market_ratio ?? DEFAULT_PRICE_STRATEGY.enabled_market_ratio,
    market_ratio: finiteNumber(value?.market_ratio, DEFAULT_PRICE_STRATEGY.market_ratio),
    enabled_top_n: value?.enabled_top_n ?? DEFAULT_PRICE_STRATEGY.enabled_top_n,
    top_n: finiteNumber(value?.top_n, DEFAULT_PRICE_STRATEGY.top_n),
  }
}

/** 保存前校验；返回 null 表示可以提交 */
export function getPriceStrategyError(strategy: PriceStrategyConfig): string | null {
  if (strategy.max_price < 0 || strategy.max_price > MAX_PRICE_LIMIT) {
    return `硬性上限需在 ¥0–¥${MAX_PRICE_LIMIT} 之间`
  }
  if (strategy.min_price < 0 || strategy.min_price > MIN_PRICE_LIMIT) {
    return `硬性下限需在 ¥0–¥${MIN_PRICE_LIMIT} 之间`
  }
  if (
    strategy.enabled_max &&
    strategy.enabled_min &&
    strategy.min_price >= strategy.max_price
  ) {
    return '硬性下限必须小于硬性上限，否则所有商品都会被过滤'
  }
  if (strategy.market_ratio < 0.3 || strategy.market_ratio > 1) {
    return '市场参考价比例需在 30%–100% 之间'
  }
  if (!Number.isInteger(strategy.top_n) || strategy.top_n < 1 || strategy.top_n > 20) {
    return '同类低价 TopN 需为 1–20 的整数'
  }
  return null
}

function statusForBin(pass: number, reject: number, count: number): PriceBinVerdict['status'] {
  if (count <= 0) return 'pass'
  if (pass <= 0) return 'reject'
  if (reject <= 0) return 'pass'
  return 'boundary'
}

/**
 * 按价格桶估算硬性上限/下限的命中数量。
 *
 * 直方图只提供“桶内总数”，无法还原每个商品的价格。因此：
 * - 桶完全在区间内/外时精确计入；
 * - 桶跨越阈值时按价格跨度占比分摊，并标记为 boundary，界面不得宣称精确。
 *
 * 市场参考价与 TopN 需要逐商品/逐类目比较，不在本估算范围内。
 */
function verdictForOpenBin(
  base: { index: number; min: number; max: number | null; count: number },
  lower: number,
  upper: number,
): PriceBinVerdict {
  const { index, min, max, count } = base
  if (Number.isFinite(upper) && min >= upper) {
    return { index, min, max, count, pass: 0, reject: count, status: statusForBin(0, count, count) }
  }
  const cutsLower = Number.isFinite(lower) && min < lower
  const cutsUpper = Number.isFinite(upper) && min < upper
  if (cutsLower || cutsUpper) {
    return { index, min, max, count, pass: count, reject: 0, status: 'boundary' }
  }
  return { index, min, max, count, pass: count, reject: 0, status: statusForBin(count, 0, count) }
}

function verdictForClosedBin(
  base: { index: number; min: number; max: number; count: number },
  lower: number,
  upper: number,
): PriceBinVerdict {
  const { index, min, max, count } = base
  const span = max - min
  const overlap = Math.max(0, Math.min(max, upper) - Math.max(min, lower))
  if (span <= 0) {
    const pass = min >= lower && min <= upper ? count : 0
    return { index, min, max, count, pass, reject: count - pass, status: statusForBin(pass, count - pass, count) }
  }

  const pass = Math.max(0, Math.min(count, Math.round((count * overlap) / span)))
  const reject = count - pass
  let status: PriceBinVerdict['status']
  if (overlap > 0 && overlap < span) {
    status = count > 0 ? 'boundary' : 'pass'
  } else {
    status = statusForBin(pass, reject, count)
  }
  return { index, min, max, count, pass, reject, status }
}

function verdictForBin(
  bin: Readonly<{ min: number; max: number | null; count: number }>,
  index: number,
  lower: number,
  upper: number,
): PriceBinVerdict {
  const min = Number.isFinite(bin.min) ? bin.min : 0
  const count = Math.max(0, Math.trunc(Number(bin.count) || 0))
  const max = bin.max == null ? null : Number(bin.max)
  if (max == null || !Number.isFinite(max)) {
    return verdictForOpenBin({ index, min, max, count }, lower, upper)
  }
  return verdictForClosedBin({ index, min, max, count }, lower, upper)
}

export function estimatePriceHits(
  bins: ReadonlyArray<{ min: number; max: number | null; count: number }>,
  strategy: PriceStrategyConfig,
): PriceHitEstimate {
  const lower = strategy.enabled_min ? strategy.min_price : Number.NEGATIVE_INFINITY
  const upper = strategy.enabled_max ? strategy.max_price : Number.POSITIVE_INFINITY

  const verdicts = bins.map((bin, index): PriceBinVerdict => verdictForBin(bin, index, lower, upper))

  return verdicts.reduce<PriceHitEstimate>(
    (acc, bin) => ({
      total: acc.total + bin.count,
      pass: acc.pass + bin.pass,
      reject: acc.reject + bin.reject,
      boundary: acc.boundary + (bin.status === 'boundary' ? bin.count : 0),
      estimated: acc.estimated || bin.status === 'boundary',
      bins: acc.bins,
    }),
    { total: 0, pass: 0, reject: 0, boundary: 0, estimated: false, bins: verdicts },
  )
}

export function formatPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const n = Number(value)
  if (Math.abs(n) >= 10000) {
    const wan = n / 10000
    return `¥${Number.isInteger(wan) ? wan : wan.toFixed(1)}万`
  }
  return `¥${n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

export function formatPriceAxis(value: number): string {
  if (!Number.isFinite(value)) return ''
  if (Math.abs(value) >= 10000) {
    const wan = value / 10000
    return `${Number.isInteger(wan) ? wan : wan.toFixed(1)}万`
  }
  if (Math.abs(value) >= 1000) {
    const k = value / 1000
    return `${Number.isInteger(k) ? k : k.toFixed(1)}k`
  }
  return `${value}`
}

export function formatBinRange(bin: { min: number; max: number | null }): string {
  if (bin.max == null) return `${formatPrice(bin.min)} 以上`
  if (bin.max <= bin.min) return formatPrice(bin.min)
  return `${formatPrice(bin.min)}–${formatPrice(bin.max)}`
}

/** 运行时校验接口载荷，避免旧 counts/字符串 bins 契约再次静默污染页面 */
export function isHistogramPayload(value: unknown): value is HistogramData {
  if (value == null || typeof value !== 'object') return false
  const data = value as { bins?: unknown; summary?: unknown }
  if (!Array.isArray(data.bins)) return false
  if (data.summary == null || typeof data.summary !== 'object') return false
  return data.bins.every((bin) => {
    if (bin == null || typeof bin !== 'object') return false
    const b = bin as { min?: unknown; max?: unknown; count?: unknown }
    return (
      typeof b.min === 'number' &&
      Number.isFinite(b.min) &&
      (b.max == null || (typeof b.max === 'number' && Number.isFinite(b.max))) &&
      typeof b.count === 'number' &&
      Number.isFinite(b.count) &&
      b.count >= 0
    )
  })
}
