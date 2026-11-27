import type { AppConfig } from '../../../api'
import {
  DB_ONLY_EVENT_KEYS,
  defaultChannels,
  notifiableEventTypes,
  type ChannelDef,
} from './constants'

/** 通知页渲染所需的最小完整配置；只有通过完整性守卫后才允许进入表单。 */
export interface NotifierReady {
  notifier: AppConfig['notifier']
  credentials: Record<string, string>
}

/** 30 分钟粒度的时间轴格子。 */
export interface QuietSlot {
  index: number
  quiet: boolean
  title: string
}

const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/
const REDACTED_VALUE = '***'
const SLOT_MINUTES = 30
const MINUTES_PER_DAY = 24 * 60

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

export function parseTimeToMinutes(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const match = TIME_RE.exec(value)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

export function isValidTime(value: unknown): value is string {
  return parseTimeToMinutes(value) !== null
}

function padTwo(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

export function formatMinuteLabel(minute: number): string {
  if (minute >= MINUTES_PER_DAY) return '24:00'
  const h = Math.floor(minute / 60)
  const m = minute % 60
  return `${padTwo(h)}:${padTwo(m)}`
}

function collectChannelMissing(notifier: Record<string, unknown>): string[] {
  const channels = notifier.channels
  if (!isRecord(channels)) return ['notifier.channels']
  const missingChannels = defaultChannels
    .filter((channel) => typeof channels[channel.key] !== 'boolean')
    .map((channel) => channel.key)
  return missingChannels.length > 0 ? [`notifier.channels.${missingChannels.join('/')}`] : []
}

function collectQuietHoursMissing(notifier: Record<string, unknown>): string[] {
  const quiet = notifier.quiet_hours
  if (!isRecord(quiet)) return ['notifier.quiet_hours']
  const missing: string[] = []
  if (typeof quiet.enabled !== 'boolean') missing.push('notifier.quiet_hours.enabled')
  if (!isValidTime(quiet.start)) missing.push('notifier.quiet_hours.start')
  if (!isValidTime(quiet.end)) missing.push('notifier.quiet_hours.end')
  if (typeof quiet.critical_only !== 'boolean') missing.push('notifier.quiet_hours.critical_only')
  if (typeof quiet.weekend_only !== 'boolean') missing.push('notifier.quiet_hours.weekend_only')
  return missing
}

/**
 * 本页同时读写 notifier 段和 13 个顶层凭据键。缺段时若直接渲染默认值，
 * 用户会在默认值上覆盖真实配置；因此先列出缺失/非法字段，进入只读错误态。
 */
export function getNotifierConfigIssue(config: AppConfig | null): string | null {
  if (!config) return '配置尚未加载'
  const root = config as unknown as Record<string, unknown>
  const notifier = root.notifier

  if (!isRecord(notifier)) {
    return '当前配置缺少或不完整：notifier'
  }

  const missing = [...collectChannelMissing(notifier), ...collectQuietHoursMissing(notifier)]

  if (!isStringArray(notifier.default_channels)) {
    missing.push('notifier.default_channels')
  }

  if (!isStringArray(notifier.subscribed_events)) {
    missing.push('notifier.subscribed_events')
  }

  return missing.length > 0 ? `当前配置缺少或不完整：${missing.join('、')}` : null
}

export function getNotifierReadyConfig(config: AppConfig | null): NotifierReady | null {
  if (getNotifierConfigIssue(config)) return null
  const ready = config as AppConfig
  const root = config as unknown as Record<string, unknown>
  const credentials: Record<string, string> = {}
  for (const channel of defaultChannels) {
    for (const field of channel.fields) {
      const raw = root[field.key]
      credentials[field.key] = typeof raw === 'string' ? raw : ''
    }
  }
  return { notifier: ready.notifier, credentials }
}

export function isRedactedCredential(value: string): boolean {
  return value === REDACTED_VALUE
}

/** GET /config 会把 13 个凭据脱敏为 ***；输入框不能把它当成真实值展示或提交。 */
export function getCredentialDisplayValue(value: string): string {
  return isRedactedCredential(value) ? '' : value
}

export function getCredentialPlaceholder(field: ChannelDef['fields'][number], value: string): string {
  if (isRedactedCredential(value)) {
    return '已配置（服务端脱敏），留空使用已保存凭据'
  }
  return field.placeholder
}

export function isCredentialConfigured(value: string): boolean {
  return value.trim().length > 0
}

/** 测试推送只发送用户可确认的明文；空值和 *** 交给后端回退 keyring/yaml 凭据。 */
export function buildTestCredentials(
  credentials: Record<string, string>,
  fields: ChannelDef['fields'],
): Record<string, string> {
  const payload: Record<string, string> = {}
  for (const field of fields) {
    const value = credentials[field.key] ?? ''
    if (value.trim() && !isRedactedCredential(value)) {
      payload[field.key] = value
    }
  }
  return payload
}

export function getMissingRequiredFields(
  credentials: Record<string, string>,
  channel: ChannelDef,
): ChannelDef['fields'] {
  return channel.fields.filter((field) => !field.optional && !isCredentialConfigured(credentials[field.key] ?? ''))
}

export function getEnabledChannels(notifier: AppConfig['notifier']): ChannelDef[] {
  return defaultChannels.filter((channel) => notifier.channels[channel.key as keyof typeof notifier.channels])
}

export function filterSubscribedEvents(events: readonly string[]): string[] {
  const allowed = new Set(notifiableEventTypes.map((event) => event.key))
  return events.filter((event) => allowed.has(event))
}

export function getLegacySubscribedEvents(events: readonly string[]): string[] {
  return events.filter((event) => (DB_ONLY_EVENT_KEYS as readonly string[]).includes(event))
}

export function getNotifierValidationError(ready: NotifierReady | null): string | null {
  if (!ready) return '配置尚未加载'
  const quiet = ready.notifier.quiet_hours
  if (quiet.enabled && parseTimeToMinutes(quiet.start) === parseTimeToMinutes(quiet.end)) {
    return '免打扰开始与结束时间不能相同'
  }
  if (filterSubscribedEvents(ready.notifier.subscribed_events).length === 0) {
    return '至少订阅 1 种可推送事件；空列表会被后端回退成默认订阅'
  }
  return null
}

/** 非阻断提示：让用户知道当前配置不会立刻触发推送，但不阻止保存。 */
export function getNotifierWarnings(ready: NotifierReady | null): string[] {
  if (!ready) return []
  const warnings: string[] = []
  const enabled = getEnabledChannels(ready.notifier)
  if (enabled.length === 0) {
    const fallback = ready.notifier.default_channels.join('、') || '无'
    warnings.push(`当前未启用任何推送渠道；后端重启后若仍无启用渠道，会回退到默认渠道（${fallback}）`)
  }

  const missing = enabled
    .map((channel) => ({
      name: channel.name,
      fields: getMissingRequiredFields(ready.credentials, channel),
    }))
    .filter((entry) => entry.fields.length > 0)
  if (missing.length > 0) {
    const detail = missing.map((entry) => `${entry.name}（${entry.fields.map((field) => field.label).join('、')}）`).join('；')
    warnings.push(`已启用渠道的必填凭据为空：${detail}。若系统钥匙串已保存对应凭据，可忽略此提示`)
  }

  const legacy = getLegacySubscribedEvents(ready.notifier.subscribed_events)
  if (legacy.length > 0) {
    warnings.push(`当前订阅含 ${legacy.length} 个仅写入时间线、不会触发推送的事件；重新选择订阅事件后会自动清理`)
  }
  return warnings
}

export function formatQuietWindow(start: string, end: string): string {
  const startMinute = parseTimeToMinutes(start)
  const endMinute = parseTimeToMinutes(end)
  if (startMinute === null || endMinute === null) return '时段无效'
  const cross = startMinute > endMinute
  return `${start} → ${end}${cross ? '（跨午夜）' : ''}`
}

/** 48 个半小时格子：分钟精度不再被 parseInt 截成整点。 */
export function getQuietHoursSlots(start: string, end: string): QuietSlot[] {
  const startMinute = parseTimeToMinutes(start)
  const endMinute = parseTimeToMinutes(end)
  if (startMinute === null || endMinute === null) return []

  const cross = startMinute > endMinute
  const emptyWindow = startMinute === endMinute
  const isQuiet = (minute: number) => {
    if (emptyWindow) return false
    return cross
      ? minute >= startMinute || minute < endMinute
      : minute >= startMinute && minute < endMinute
  }

  const slots: QuietSlot[] = []
  for (let index = 0; index < MINUTES_PER_DAY / SLOT_MINUTES; index++) {
    const slotStart = index * SLOT_MINUTES
    const slotEnd = slotStart + SLOT_MINUTES
    const quiet = isQuiet(slotStart)
    slots.push({
      index,
      quiet,
      title: `${formatMinuteLabel(slotStart)}–${formatMinuteLabel(slotEnd)}（${quiet ? '静默' : '推送'}）`,
    })
  }
  return slots
}
