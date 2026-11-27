import type { AppConfig } from '../../api'

/** 搜索段完整字段；max_pages 是后端已有但前端此前漏掉的翻页深度。 */
export interface SearchConfigSegment {
  page_size: number
  sort_type: string
  timeout: number
  regions: string
  filter_tags: string[]
  max_pages: number
}

/** 本页读写并必须在渲染前校验的四个配置段。 */
export interface SearchConfigReady {
  search: SearchConfigSegment
  antidetect: AppConfig['antidetect']
  task_scheduler: AppConfig['task_scheduler']
  waf: AppConfig['waf']
}

export const SEARCH_SORT_OPTIONS = [
  { label: '默认综合', value: 'default' },
  { label: '最新发布', value: 'newest' },
  { label: '价格最低', value: 'price_asc' },
  { label: '价格最高', value: 'price_desc' },
  { label: '想要最多', value: 'want_count' },
] as const

export const SEARCH_PAGE_SIZE_OPTIONS = [
  { label: '20 条/页', value: 20 },
  { label: '40 条/页', value: 40 },
  { label: '50 条/页', value: 50 },
  { label: '60 条/页', value: 60 },
  { label: '100 条/页', value: 100 },
] as const

export const SEARCH_MAX_PAGES_MIN = 1
export const SEARCH_MAX_PAGES_MAX = 20
export const SEARCH_TIMEOUT_MIN = 10
export const SEARCH_TIMEOUT_MAX = 120
export const OPERATION_DELAY_MIN = 1
export const OPERATION_DELAY_MAX = 30
export const FAIL_PAUSE_THRESHOLD_MIN = 0
export const FAIL_PAUSE_THRESHOLD_MAX = 10
export const DEFAULT_INTERVAL_MIN = 30
export const DEFAULT_INTERVAL_MAX = 3600
export const WAF_INTERVAL_MIN = 5
export const WAF_INTERVAL_MAX = 1440

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isIntInRange(value: unknown, min: number, max: number): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= min && value <= max
}

function collectSearchMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.search)) return ['search']
  const search = root.search
  const invalid: string[] = []
  if (!isFiniteNumber(search.page_size)) invalid.push('page_size')
  if (typeof search.sort_type !== 'string') invalid.push('sort_type')
  if (!isFiniteNumber(search.timeout)) invalid.push('timeout')
  if (typeof search.regions !== 'string') invalid.push('regions')
  if (!isStringArray(search.filter_tags)) invalid.push('filter_tags')
  if (!isFiniteNumber(search.max_pages)) invalid.push('max_pages')
  return invalid.length > 0 ? [`search.${invalid.join('/')}`] : []
}

function collectAntidetectMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.antidetect)) return ['antidetect']
  const antidetect = root.antidetect
  const valid =
    isFiniteNumber(antidetect.min_delay_ms)
    && isFiniteNumber(antidetect.max_delay_ms)
    && isFiniteNumber(antidetect.qps)
    && isFiniteNumber(antidetect.fail_pause_threshold)
    && isFiniteNumber(antidetect.fail_window_sec)
  return valid ? [] : ['antidetect']
}

function collectTaskSchedulerMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.task_scheduler)) return ['task_scheduler']
  const taskScheduler = root.task_scheduler
  const valid =
    isFiniteNumber(taskScheduler.default_interval_seconds)
    && typeof taskScheduler.auto_search_enabled === 'boolean'
    && isFiniteNumber(taskScheduler.auto_search_concurrency)
  return valid ? [] : ['task_scheduler']
}

function collectWafMissing(root: Record<string, unknown>): string[] {
  if (!isRecord(root.waf)) return ['waf']
  const waf = root.waf
  const valid = typeof waf.enabled === 'boolean' && isFiniteNumber(waf.login_check_interval_min)
  return valid ? [] : ['waf']
}

/**
 * 配置段完整性守卫：本页同时写 search / antidetect / task_scheduler / waf，
 * 缺任一段都不能渲染默认值表单，否则用户会在默认值上覆盖真实配置。
 */
export function getSearchConfigIssue(config: AppConfig | null): string | null {
  if (!config) return '配置尚未加载'
  const root = config as unknown as Record<string, unknown>
  const missing = [
    ...collectSearchMissing(root),
    ...collectAntidetectMissing(root),
    ...collectTaskSchedulerMissing(root),
    ...collectWafMissing(root),
  ]
  return missing.length > 0 ? `当前配置缺少或不完整：${missing.join('、')}` : null
}

/** 通过完整性守卫后才返回强类型配置，避免把 undefined 带进渲染层。 */
export function getSearchConfigReadyConfig(config: AppConfig | null): SearchConfigReady | null {
  return getSearchConfigIssue(config) ? null : config as unknown as SearchConfigReady
}

export function getSearchConfigValidationError(ready: SearchConfigReady | null): string | null {
  if (!ready) return '配置尚未加载'
  const { search, antidetect, task_scheduler: taskScheduler, waf } = ready

  if (!isIntInRange(search.page_size, 1, 200)) return '每页结果数量必须是 1–200 的整数'
  if (!SEARCH_SORT_OPTIONS.some((option) => option.value === search.sort_type)) return '排序方式不在支持列表'
  if (!isIntInRange(search.timeout, SEARCH_TIMEOUT_MIN, SEARCH_TIMEOUT_MAX)) {
    return `搜索超时必须为 ${SEARCH_TIMEOUT_MIN}–${SEARCH_TIMEOUT_MAX} 秒`
  }
  if (!isIntInRange(search.max_pages, SEARCH_MAX_PAGES_MIN, SEARCH_MAX_PAGES_MAX)) {
    return `翻页深度必须为 ${SEARCH_MAX_PAGES_MIN}–${SEARCH_MAX_PAGES_MAX} 屏`
  }

  const delaySec = getOperationDelaySeconds(antidetect.min_delay_ms)
  if (delaySec < OPERATION_DELAY_MIN || delaySec > OPERATION_DELAY_MAX) {
    return `操作延迟必须为 ${OPERATION_DELAY_MIN}–${OPERATION_DELAY_MAX} 秒`
  }
  if (antidetect.max_delay_ms < antidetect.min_delay_ms) return '最大操作延迟不能小于最小操作延迟'
  if (!isIntInRange(antidetect.fail_pause_threshold, FAIL_PAUSE_THRESHOLD_MIN, FAIL_PAUSE_THRESHOLD_MAX)) {
    return `失败暂停阈值必须为 ${FAIL_PAUSE_THRESHOLD_MIN}–${FAIL_PAUSE_THRESHOLD_MAX} 次`
  }

  if (!isIntInRange(taskScheduler.default_interval_seconds, DEFAULT_INTERVAL_MIN, DEFAULT_INTERVAL_MAX)) {
    return `默认采集周期必须为 ${DEFAULT_INTERVAL_MIN}–${DEFAULT_INTERVAL_MAX} 秒`
  }
  if (!isIntInRange(waf.login_check_interval_min, WAF_INTERVAL_MIN, WAF_INTERVAL_MAX)) {
    return `登录检查间隔必须为 ${WAF_INTERVAL_MIN}–${WAF_INTERVAL_MAX} 分钟`
  }
  return null
}

/** 每页选项要能容纳后端默认值 50，也要能显示历史配置里的非标准正整数。 */
export function getPageSizeOptions(current: number): { label: string; value: number }[] {
  const base: { label: string; value: number }[] = SEARCH_PAGE_SIZE_OPTIONS.map((option) => ({ ...option }))
  if (Number.isFinite(current) && current > 0 && !base.some((option) => option.value === current)) {
    base.push({ label: `${current} 条/页`, value: current })
  }
  return base
}

/** 操作延迟以秒展示；后端存的是毫秒。 */
export function getOperationDelaySeconds(minDelayMs: number): number {
  if (!Number.isFinite(minDelayMs) || minDelayMs <= 0) return OPERATION_DELAY_MIN
  return Math.min(OPERATION_DELAY_MAX, Math.max(OPERATION_DELAY_MIN, Math.round(minDelayMs / 1000)))
}

/** 操作延迟换算理论请求频率：60 秒 / 每次延迟秒。 */
export function getQpsFromDelay(delaySec: number): number {
  if (!Number.isFinite(delaySec) || delaySec <= 0) return 60
  return Math.max(1, Math.round(60 / delaySec))
}

export function getMaxDelayMs(delaySec: number): number {
  if (!Number.isFinite(delaySec) || delaySec <= 0) return 1500
  return Math.round(delaySec * 1500)
}

/** InputNumber 清空时不静默写 0；只有有限数字才写回。 */
export function toFiniteNumberOrNull(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
