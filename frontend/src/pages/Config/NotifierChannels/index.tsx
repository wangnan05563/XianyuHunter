import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Checkbox,
  Divider,
  Space,
  Spin,
  Switch,
  Tag,
  TimePicker,
  theme,
} from 'antd'
import { BellOutlined, SaveOutlined, UndoOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { TipButton } from '@/components/TipButton'
import { useConfigStore } from '../../../stores/configStore'
import { SCOPE_NOTIFIER } from '../../../stores/configDirty'
import { extractApiError } from '../../../utils/apiError'
import type { DiffChange } from '../../../stores/configStore'
import api from '../../../api/client'
import type { AppConfig } from '../../../api'
import {
  DB_ONLY_EVENT_KEYS,
  defaultChannels,
  notifiableEventTypes,
  pricingMeta,
  severityColors,
  severityLabels,
  type ChannelDef,
  type Pricing,
} from './constants'
import {
  buildTestCredentials,
  filterSubscribedEvents,
  formatQuietWindow,
  getLegacySubscribedEvents,
  getNotifierConfigIssue,
  getNotifierReadyConfig,
  getNotifierValidationError,
  getNotifierWarnings,
  getEnabledChannels,
  parseTimeToMinutes,
} from './notifierModel'
import ChannelCard from './components/ChannelCard'
import QuietHoursTimeline from './components/QuietHoursTimeline'
import { DiffPreviewModal } from '../../../components/DiffPreviewModal'
import './NotifierChannels.css'

const PRICING_ORDER: Pricing[] = ['free', 'freemium', 'paid']
const BRAND_COLOR = '#FF6200'
const CARD_STYLES = { body: { padding: 16 } } as const

/** AntD TimePicker 需要 Dayjs；用 dayjs() 偏移而不是字符串解析，避免依赖 customParseFormat。 */
function timeToPickerValue(value: string): Dayjs | null {
  const minutes = parseTimeToMinutes(value)
  if (minutes === null) return null
  return dayjs().startOf('day').add(minutes, 'minute')
}

/** 页面外壳提供 AntdApp 上下文，避免静态 message 在暗色主题下告警。 */
export default function NotifierChannels() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  return (
    <AntdApp component={false}>
      <NotifierChannelsContent />
    </AntdApp>
  )
}

function NotifierChannelsContent() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  const { message } = AntdApp.useApp()
  const { token } = theme.useToken()
  const {
    config,
    loading: configLoading,
    error: configError,
    load,
    dirtyPathsInScope,
    reset,
    update,
    previewSave,
    confirmSave,
  } = useConfigStore()

  // StrictMode 下 effect 会执行两次；本页只允许首屏请求一次。
  const didAutoLoadRef = useRef(false)
  useEffect(() => {
    if (didAutoLoadRef.current) return
    didAutoLoadRef.current = true
    void load()
  }, [load])

  const [saving, setSaving] = useState(false)
  const [testingChannel, setTestingChannel] = useState<string | null>(null)
  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])

  const ready = getNotifierReadyConfig(config)
  const configIssue = getNotifierConfigIssue(config)
  const dirtyCount = config ? dirtyPathsInScope(SCOPE_NOTIFIER).length : 0
  const validationError = ready ? getNotifierValidationError(ready) : null
  const warnings = ready ? getNotifierWarnings(ready) : []
  const canSave = Boolean(ready) && dirtyCount > 0 && !validationError && !saving
  const canReset = Boolean(ready) && dirtyCount > 0 && !saving

  const channelList = useMemo<ChannelDef[]>(() => defaultChannels.map((channel) => ({
    ...channel,
    enabled: ready?.notifier.channels[channel.key as keyof AppConfig['notifier']['channels']] ?? false,
  })), [ready])

  const enabledChannels = ready ? getEnabledChannels(ready.notifier) : []
  const displayEvents = ready ? filterSubscribedEvents(ready.notifier.subscribed_events) : []
  const legacyEvents = ready ? getLegacySubscribedEvents(ready.notifier.subscribed_events) : []

  const groups = useMemo(() => {
    const result: Record<Pricing, ChannelDef[]> = { free: [], freemium: [], paid: [] }
    for (const channel of channelList) {
      result[channel.pricing].push(channel)
    }
    return result
  }, [channelList])

  const headerStyle: CSSProperties = {
    position: 'sticky',
    top: 0,
    zIndex: 20,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    flexWrap: 'wrap',
    padding: '14px 0 12px',
    marginBottom: 16,
    background: token.colorBgLayout,
    borderBottom: `1px solid ${token.colorBorderSecondary}`,
  }

  const handleReset = () => {
    reset(SCOPE_NOTIFIER)
    message.info('已恢复本页未保存的修改')
  }

  const handleSave = async () => {
    if (!ready) return
    if (validationError) {
      message.error(validationError, 5)
      return
    }
    if (dirtyCount === 0) {
      message.info('配置未变更')
      return
    }
    try {
      setSaving(true)
      const changes = await previewSave(SCOPE_NOTIFIER)
      if (changes.length === 0) {
        message.info('配置未变更')
        return
      }
      setDiffChanges(changes)
      setDiffModalOpen(true)
    } catch (e: unknown) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const handleConfirmSave = async () => {
    try {
      setSaving(true)
      await confirmSave(SCOPE_NOTIFIER)
      setDiffModalOpen(false)
      message.success('通知配置已保存')
    } catch (e: unknown) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const toggleChannel = (key: string, enabled: boolean) => {
    if (!ready) return
    update({
      notifier: {
        ...ready.notifier,
        channels: {
          ...ready.notifier.channels,
          [key]: enabled,
        },
      },
    })
  }

  const updateChannelField = (_channelKey: string, fieldKey: string, value: string) => {
    if (!ready) return
    // 13 个凭据字段在 AppConfig 顶层；页段提交由 SCOPE_NOTIFIER 覆盖。
    update({ [fieldKey]: value } as Partial<AppConfig>)
  }

  const updateQuietHours = (patch: Partial<AppConfig['notifier']['quiet_hours']>) => {
    if (!ready) return
    update({
      notifier: {
        ...ready.notifier,
        quiet_hours: { ...ready.notifier.quiet_hours, ...patch },
      },
    })
  }

  const handleEventChange = (values: Array<string | number>) => {
    if (!ready) return
    const next = filterSubscribedEvents(values.map(String))
    update({
      notifier: {
        ...ready.notifier,
        subscribed_events: next,
      },
    })
  }

  const handleTest = async (channelKey: string) => {
    if (!ready) return
    const channel = channelList.find((item) => item.key === channelKey)
    if (!channel) return
    setTestingChannel(channelKey)
    try {
      const credentials = buildTestCredentials(ready.credentials, channel.fields)
      const { data } = await api.post('/api/notifier/test', {
        channel: channelKey,
        credentials,
      })
      if (data?.ok) {
        message.success(`${channel.name} 测试推送成功，请检查是否收到消息`)
      } else {
        const reason = data?.error || data?.detail || '未知错误'
        message.error(`${channel.name} 推送失败：${reason}`, 5)
      }
    } catch (err: unknown) {
      message.error(`${channel.name} 测试失败：${extractApiError(err, '网络错误')}`, 5)
    } finally {
      setTestingChannel(null)
    }
  }

  const header = (
    <div style={headerStyle} data-testid="notifier-config-header">
      <div style={{ minWidth: 280 }}>
        <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>通知渠道</h1>
        <div style={{ marginTop: 3, fontSize: 12, color: token.colorTextTertiary }}>
          配置渠道、免打扰与事件订阅；并发推送，不做顺序降级
        </div>
      </div>
      <Space wrap align="center">
        {dirtyCount > 0 && (
          <Tag color="orange" data-testid="notifier-config-dirty">
            未保存 {dirtyCount} 项
          </Tag>
        )}
        <TipButton
          tip="放弃本页未保存的修改并重置"
          icon={<UndoOutlined />}
          onClick={handleReset}
          disabled={!canReset}
          data-testid="notifier-config-reset"
        >
          重置
        </TipButton>
        <TipButton
          tip={validationError ?? (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存通知渠道配置')}
          type="primary"
          style={canSave ? { backgroundColor: BRAND_COLOR, borderColor: BRAND_COLOR } : undefined}
          icon={<SaveOutlined />}
          onClick={() => { void handleSave() }}
          loading={saving}
          disabled={!canSave}
          data-testid="notifier-config-save"
        >
          保存配置
        </TipButton>
      </Space>
    </div>
  )

  let body: ReactNode
  if (!ready) {
    if (configError) {
      body = (
        <Alert
          type="error"
          showIcon
          message="通知渠道配置加载失败"
          description={`${configError}。请检查后端服务或网络后重试，避免在默认值上覆盖真实配置。`}
          action={(
            <TipButton
              tip="重新加载通知渠道配置"
              size="small"
              onClick={() => { void load() }}
              loading={configLoading}
              data-testid="notifier-config-retry"
            >
              重试
            </TipButton>
          )}
          data-testid="notifier-config-config-error"
        />
      )
    } else if (config) {
      body = (
        <Alert
          type="error"
          showIcon
          message="通知渠道配置不完整，无法编辑"
          description={`${configIssue}。本页同时读写 notifier 段与 13 个顶层凭据字段；请重新加载，确认后端返回完整数据。`}
          action={(
            <TipButton
              tip="重新加载通知渠道配置"
              size="small"
              onClick={() => { void load() }}
              loading={configLoading}
              data-testid="notifier-config-retry"
            >
              重试
            </TipButton>
          )}
          data-testid="notifier-config-config-missing"
        />
      )
    } else {
      body = (
        <div
          data-testid="notifier-config-loading"
          style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          <Spin tip="正在加载通知渠道配置…" size="large">
            <div style={{ width: 260, height: 90 }} />
          </Spin>
        </div>
      )
    }

    return (
      <div className="page-container" data-testid="notifier-config-page">
        {header}
        {body}
      </div>
    )
  }

  const activeConfig = config as AppConfig

  return (
    <div className="page-container" data-testid="notifier-config-page">
      {header}

      {validationError && (
        <Alert
          type="error"
          showIcon
          message="配置校验未通过"
          description={validationError}
          style={{ marginBottom: 12 }}
          data-testid="notifier-config-validation"
        />
      )}

      {warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="配置提醒（不阻止保存）"
          description={(
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          )}
          style={{ marginBottom: 12 }}
          data-testid="notifier-config-warning"
        />
      )}

      <div className="notifier-layout">
        <div className="notifier-main">
          <Card
            title={(
              <span className="notifier-summary">
                <span>推送渠道</span>
                <Tag>{channelList.length} 个渠道</Tag>
                <Tag color={enabledChannels.length > 0 ? 'green' : 'default'}>{enabledChannels.length} 个已启用</Tag>
              </span>
            )}
            styles={CARD_STYLES}
            data-testid="notifier-channels-card"
          >
            {PRICING_ORDER.map((pricing) => groups[pricing].length > 0 && (
              <div key={pricing} className="notifier-channel-group">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 10px' }}>
                  <Tag color={pricingMeta[pricing].color}>{pricingMeta[pricing].label}</Tag>
                  <span style={{ fontSize: 12, color: token.colorTextTertiary }}>
                    {groups[pricing].length} 个渠道
                  </span>
                </div>
                <div className="notifier-channel-grid">
                  {groups[pricing].map((channel) => (
                    <ChannelCard
                      key={channel.key}
                      channel={channel}
                      config={activeConfig}
                      testing={testingChannel === channel.key}
                      anyTesting={testingChannel !== null}
                      onToggle={toggleChannel}
                      onFieldChange={updateChannelField}
                      onTest={(key) => { void handleTest(key) }}
                    />
                  ))}
                </div>
              </div>
            ))}
          </Card>

          <Alert
            type="info"
            showIcon
            message="推送方式"
            description="后端会向所有已启用渠道并发发送同一事件，不做失败降级；全部渠道关闭时，重启后回退到 default_channels 兜底。"
            data-testid="notifier-push-semantics"
          />
        </div>

        <div className="notifier-side">
          <Card
            title="免打扰时段"
            styles={CARD_STYLES}
            data-testid="notifier-quiet-card"
          >
            <div className="notifier-quiet-head">
              <div>
                <div style={{ fontWeight: 500 }}>启用免打扰</div>
                <div style={{ marginTop: 2, fontSize: 12, color: token.colorTextTertiary }}>
                  静默期内仅 critical 事件可按策略放行
                </div>
              </div>
              <Switch
                checked={ready.notifier.quiet_hours.enabled}
                onChange={(v) => updateQuietHours({ enabled: v })}
                aria-label="启用免打扰"
                data-testid="notifier-quiet-switch"
              />
            </div>

            <div style={{ marginTop: 14 }}>
              <div className="notifier-quiet-times">
                <div className="notifier-quiet-time-field">
                  <label htmlFor="notifier-quiet-start" className="notifier-field-label">
                    开始
                  </label>
                  <TimePicker
                    id="notifier-quiet-start"
                    aria-label="免打扰开始时间"
                    value={timeToPickerValue(ready.notifier.quiet_hours.start)}
                    format="HH:mm"
                    minuteStep={1}
                    allowClear={false}
                    disabled={!ready.notifier.quiet_hours.enabled}
                    onChange={(v) => {
                      if (v) updateQuietHours({ start: v.format('HH:mm') })
                    }}
                    data-testid="notifier-quiet-start"
                  />
                </div>
                <span className="notifier-quiet-arrow" aria-hidden="true">→</span>
                <div className="notifier-quiet-time-field">
                  <label htmlFor="notifier-quiet-end" className="notifier-field-label">
                    结束
                  </label>
                  <TimePicker
                    id="notifier-quiet-end"
                    aria-label="免打扰结束时间"
                    value={timeToPickerValue(ready.notifier.quiet_hours.end)}
                    format="HH:mm"
                    minuteStep={1}
                    allowClear={false}
                    disabled={!ready.notifier.quiet_hours.enabled}
                    onChange={(v) => {
                      if (v) updateQuietHours({ end: v.format('HH:mm') })
                    }}
                    data-testid="notifier-quiet-end"
                  />
                </div>
              </div>
              <div style={{ marginTop: 6, fontSize: 12, color: token.colorTextTertiary }}>
                {formatQuietWindow(ready.notifier.quiet_hours.start, ready.notifier.quiet_hours.end)}
              </div>
            </div>

            <div style={{ marginTop: 16 }}>
              <QuietHoursTimeline
                start={ready.notifier.quiet_hours.start}
                end={ready.notifier.quiet_hours.end}
              />
            </div>

            <Divider />

            <Space direction="vertical" size={8}>
              <Checkbox
                checked={ready.notifier.quiet_hours.critical_only}
                disabled={!ready.notifier.quiet_hours.enabled}
                onChange={(e) => updateQuietHours({ critical_only: e.target.checked })}
              >
                仅 critical 级别可推送（其他事件落本地“待发摘要”）
              </Checkbox>
              <Checkbox
                checked={ready.notifier.quiet_hours.weekend_only}
                disabled={!ready.notifier.quiet_hours.enabled}
                onChange={(e) => updateQuietHours({ weekend_only: e.target.checked })}
              >
                仅周末启用（工作日保持全量推送）
              </Checkbox>
            </Space>
          </Card>

          <Card
            title={`事件订阅规则（${notifiableEventTypes.length} 种可推送事件）`}
            styles={CARD_STYLES}
            data-testid="notifier-events-card"
          >
            <div style={{ marginBottom: 10, fontSize: 12, color: token.colorTextTertiary }}>
              勾选需要推送的事件；空订阅会被后端回退成默认订阅，因此至少保留 1 项。
            </div>
            <fieldset aria-label="事件订阅规则" style={{ border: 0, padding: 0, margin: 0 }}>
              <Checkbox.Group
                value={displayEvents}
                onChange={handleEventChange}
                style={{ width: '100%' }}
              >
                <div className="notifier-event-grid">
                  {notifiableEventTypes.map((event) => (
                    <Checkbox key={event.key} value={event.key} className="notifier-event-checkbox">
                      <Space size={6}>
                        <Tag color={severityColors[event.severity]} style={{ fontSize: 10, margin: 0 }}>
                          {severityLabels[event.severity] ?? event.severity}
                        </Tag>
                        <span>{event.label}</span>
                      </Space>
                    </Checkbox>
                  ))}
                </div>
              </Checkbox.Group>
            </fieldset>

            <Divider />

            <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
              <BellOutlined /> 已订阅 {displayEvents.length} / {notifiableEventTypes.length} 种可推送事件
            </div>
            {legacyEvents.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: token.colorWarning }}>
                当前配置含 {legacyEvents.length} 个仅写入时间线的事件；重新勾选订阅后会自动清理。
              </div>
            )}
            <div className="notifier-event-note">
              另有 {DB_ONLY_EVENT_KEYS.length} 种维护/生命周期事件只写入时间线，不会触发通知，已从本列表隐藏。
            </div>
          </Card>
        </div>
      </div>

      <DiffPreviewModal
        open={diffModalOpen}
        diffChanges={diffChanges}
        config={config}
        loading={saving}
        onCancel={() => setDiffModalOpen(false)}
        onConfirm={() => { void handleConfirmSave() }}
      />
    </div>
  )
}
