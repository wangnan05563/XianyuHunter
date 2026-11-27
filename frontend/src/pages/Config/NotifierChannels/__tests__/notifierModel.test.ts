import { describe, expect, it } from 'vitest'
import type { AppConfig } from '../../../api'
import { defaultChannels } from '../constants'
import {
  buildTestCredentials,
  filterSubscribedEvents,
  getCredentialDisplayValue,
  getCredentialPlaceholder,
  getLegacySubscribedEvents,
  getMissingRequiredFields,
  getNotifierConfigIssue,
  getNotifierReadyConfig,
  getNotifierValidationError,
  getNotifierWarnings,
  getQuietHoursSlots,
  isRedactedCredential,
  parseTimeToMinutes,
  formatQuietWindow,
} from '../notifierModel'

const NOTIFIER: AppConfig['notifier'] = {
  default_channels: ['serverchan'],
  channels: {
    serverchan: true,
    pushplus: true,
    bark: true,
    telegram: false,
    wecom: false,
    dingtalk: false,
    webhook: false,
    ntfy: false,
  },
  quiet_hours: {
    enabled: false,
    start: '23:00',
    end: '07:00',
    critical_only: true,
    weekend_only: false,
  },
  subscribed_events: ['EVAL_PASSED', 'BUY_SUCCEEDED'],
}

function config(overrides: Partial<Record<string, unknown>> = {}): AppConfig {
  return {
    notifier: NOTIFIER,
    serverchan_send_key: '',
    pushplus_token: '',
    bark_server: '',
    bark_key: '',
    telegram_bot_token: '',
    telegram_chat_id: '',
    wecom_webhook: '',
    dingtalk_webhook: '',
    dingtalk_secret: '',
    webhook_url: '',
    ntfy_server: '',
    ntfy_topic: '',
    ntfy_token: '',
    ...overrides,
  } as unknown as AppConfig
}

const ntfy = defaultChannels.find((channel) => channel.key === 'ntfy')!

describe('getNotifierConfigIssue', () => {
  it('配置尚未加载时返回提示', () => {
    expect(getNotifierConfigIssue(null)).toBe('配置尚未加载')
  })

  it('缺少 notifier 段时阻止渲染', () => {
    expect(getNotifierConfigIssue({} as AppConfig)).toContain('notifier')
  })

  it('缺少 channels 段或渠道布尔值时列出具体路径', () => {
    const missingChannels = { ...NOTIFIER, channels: {} } as unknown as AppConfig['notifier']
    expect(getNotifierConfigIssue(config({ notifier: missingChannels }))).toContain('notifier.channels.serverchan')
  })

  it('缺 quiet_hours 或时间格式非法时阻止渲染', () => {
    expect(getNotifierConfigIssue(config({ notifier: { ...NOTIFIER, quiet_hours: undefined } })))
      .toContain('notifier.quiet_hours')
    const invalid = { ...NOTIFIER, quiet_hours: { ...NOTIFIER.quiet_hours, start: '25:99' } }
    expect(getNotifierConfigIssue(config({ notifier: invalid }))).toContain('notifier.quiet_hours.start')
  })

  it('完整配置通过守卫并规整 13 个凭据为空字符串', () => {
    const ready = getNotifierReadyConfig(config())
    expect(ready).not.toBeNull()
    expect(ready?.credentials.bark_server).toBe('')
    expect(getNotifierConfigIssue(config())).toBeNull()
  })
})

describe('凭据展示与测试载荷', () => {
  it('*** 脱敏值不展示也不提交', () => {
    expect(isRedactedCredential('***')).toBe(true)
    expect(getCredentialDisplayValue('***')).toBe('')
    expect(getCredentialDisplayValue('real-key')).toBe('real-key')
    expect(getCredentialPlaceholder(ntfy.fields[0], '***')).toContain('已配置')
  })

  it('测试载荷跳过空值和 ***，保留可选 token', () => {
    const credentials = { ntfy_server: 'https://ntfy.sh', ntfy_topic: 'topic', ntfy_token: 'tk_1' }
    expect(buildTestCredentials(credentials, ntfy.fields)).toEqual(credentials)
    expect(buildTestCredentials({ ntfy_topic: 'topic', ntfy_token: '***' }, ntfy.fields)).toEqual({ ntfy_topic: 'topic' })
  })

  it('可选字段为空不算缺失，topic 为空才算缺失', () => {
    expect(getMissingRequiredFields({ ntfy_topic: 'topic' }, ntfy).map((field) => field.key)).toEqual([])
    expect(getMissingRequiredFields({ ntfy_server: 'https://ntfy.sh' }, ntfy).map((field) => field.key)).toEqual(['ntfy_topic'])
    expect(getMissingRequiredFields({ ntfy_token: '***' }, ntfy).map((field) => field.key)).toEqual(['ntfy_topic'])
  })
})

describe('时间轴与免打扰校验', () => {
  it('解析合法与非法时间', () => {
    expect(parseTimeToMinutes('23:59')).toBe(1439)
    expect(parseTimeToMinutes('25:99')).toBeNull()
    expect(parseTimeToMinutes('')).toBeNull()
  })

  it('生成 48 个半小时格，23:30 与 07:00 边界准确', () => {
    const slots = getQuietHoursSlots('23:30', '07:30')
    expect(slots).toHaveLength(48)
    expect(slots.find((slot) => slot.title.includes('23:00–23:30'))?.quiet).toBe(false)
    expect(slots.find((slot) => slot.title.includes('23:30–24:00'))?.quiet).toBe(true)
    expect(slots.find((slot) => slot.title.includes('07:00–07:30'))?.quiet).toBe(true)
    expect(slots.find((slot) => slot.title.includes('07:30–08:00'))?.quiet).toBe(false)
  })

  it('非跨午夜窗口按分钟筛选', () => {
    expect(getQuietHoursSlots('09:30', '11:00').find((slot) => slot.title.includes('09:00–09:30'))?.quiet).toBe(false)
    expect(getQuietHoursSlots('09:30', '11:00').find((slot) => slot.title.includes('09:30–10:00'))?.quiet).toBe(true)
  })

  it('起止相同在启用时阻断保存', () => {
    const ready = getNotifierReadyConfig(config({
      notifier: {
        ...NOTIFIER,
        quiet_hours: { ...NOTIFIER.quiet_hours, enabled: true, start: '09:00', end: '09:00' },
      },
    }))
    expect(getNotifierValidationError(ready)).toContain('不能相同')
  })

  it('订阅全为空会阻断保存', () => {
    const ready = getNotifierReadyConfig(config({ notifier: { ...NOTIFIER, subscribed_events: [] } }))
    expect(getNotifierValidationError(ready)).toContain('至少订阅')
  })

  it('时间窗口文案标注跨午夜', () => {
    expect(formatQuietWindow('23:30', '07:30')).toContain('跨午夜')
  })
})

describe('事件和渠道提示', () => {
  it('只保留可推送事件；仅时间线事件单独识别', () => {
    expect(filterSubscribedEvents(['EVAL_PASSED', 'ITEM_FOUND', 'MAINTENANCE_CACHE'])).toEqual(['EVAL_PASSED'])
    expect(getLegacySubscribedEvents(['EVAL_PASSED', 'ITEM_FOUND', 'MAINTENANCE_CACHE'])).toEqual(['ITEM_FOUND', 'MAINTENANCE_CACHE'])
  })

  it('全部关闭与凭据缺失给出非阻断提示', () => {
    const allOff = {
      ...NOTIFIER,
      channels: Object.fromEntries(Object.keys(NOTIFIER.channels).map((key) => [key, false])),
    } as AppConfig['notifier']
    const ready = getNotifierReadyConfig(config({ notifier: allOff }))
    expect(getNotifierWarnings(ready).join(' ')).toContain('未启用任何推送渠道')

    const missingKey = getNotifierWarnings(getNotifierReadyConfig(config()))
    expect(missingKey.join(' ')).toContain('Server酱')
  })
})
