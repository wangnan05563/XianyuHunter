import { Card, Switch, Input, Space, Tag, Tooltip, Typography } from 'antd'
import { SendOutlined, StarFilled, LinkOutlined } from '@ant-design/icons'
import { TipButton } from '@/components/TipButton'
import type { AppConfig } from '../../../../api'
import type { ChannelDef } from '../constants'
import { pricingMeta } from '../constants'
import {
  getCredentialDisplayValue,
  getCredentialPlaceholder,
  isRedactedCredential,
} from '../notifierModel'

interface ChannelCardProps {
  // 标记 readonly 以表达「父级传入后子组件不应修改」的契约（SonarQube S6759）
  readonly channel: ChannelDef
  readonly config: AppConfig
  // 当前卡片是否处于测试中
  readonly testing: boolean
  // 是否有任意卡片处于测试中（用于禁用其他卡片的测试按钮）
  readonly anyTesting: boolean
  readonly onToggle: (key: string, enabled: boolean) => void
  readonly onFieldChange: (channelKey: string, fieldKey: string, value: string) => void
  readonly onTest: (channelKey: string) => void
}

export default function ChannelCard({
  channel: ch,
  config,
  testing,
  anyTesting,
  onToggle,
  onFieldChange,
  onTest,
}: ChannelCardProps) {
  const meta = pricingMeta[ch.pricing]
  const root = config as unknown as Record<string, unknown>

  return (
    <Card
      size="small"
      className="channel-card notifier-channel-card"
      data-enabled={ch.enabled ? 'true' : 'false'}
      data-testid={`channel-card-${ch.key}`}
    >
      <div className="notifier-channel-head">
        <Space align="start">
          <span className="notifier-channel-icon" aria-hidden="true">{ch.icon}</span>
          <div>
            <div className="notifier-channel-title">
              <span className="notifier-channel-name">{ch.name}</span>
              <Tag color={meta.color} className="notifier-mini-tag">
                {meta.label}
              </Tag>
              {ch.recommended && (
                <Tooltip title="免费且无每日限制的微信推送渠道，推荐优先使用">
                  <Tag color="gold" icon={<StarFilled />} className="notifier-mini-tag">
                    推荐
                  </Tag>
                </Tooltip>
              )}
            </div>
            <div className="notifier-channel-desc">
              {ch.desc}
              {ch.limits && (
                <span className="notifier-channel-limits">· {ch.limits}</span>
              )}
            </div>
          </div>
        </Space>
        <Switch
          checked={ch.enabled}
          onChange={(v) => onToggle(ch.key, v)}
          aria-label={`${ch.name}启用状态`}
          data-testid={`channel-switch-${ch.key}`}
        />
      </div>

      {ch.obtainUrl && (
        <div className="notifier-channel-obtain">
          <Typography.Link
            href={ch.obtainUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            获取凭据 <LinkOutlined />
          </Typography.Link>
        </div>
      )}

      {ch.enabled && (
        <div className="notifier-channel-fields">
          {ch.fields.map((field) => {
            const raw = typeof root[field.key] === 'string' ? root[field.key] as string : ''
            const displayValue = getCredentialDisplayValue(raw)
            const redacted = isRedactedCredential(raw)
            const inputId = `notifier-field-${field.key}`
            return (
              <div key={field.key} className="notifier-field">
                <label htmlFor={inputId} className="notifier-field-label">
                  {field.label}
                  {field.optional && <span className="notifier-field-optional">（可选）</span>}
                </label>
                {field.secret ? (
                  <Input.Password
                    id={inputId}
                    size="middle"
                    placeholder={getCredentialPlaceholder(field, raw)}
                    value={displayValue}
                    onChange={(e) => onFieldChange(ch.key, field.key, e.target.value)}
                    autoComplete="new-password"
                  />
                ) : (
                  <Input
                    id={inputId}
                    size="middle"
                    placeholder={getCredentialPlaceholder(field, raw)}
                    value={displayValue}
                    onChange={(e) => onFieldChange(ch.key, field.key, e.target.value)}
                    autoComplete="off"
                  />
                )}
                {redacted && (
                  <div className="notifier-field-hint">服务端已脱敏；留空测试会使用已保存凭据</div>
                )}
              </div>
            )
          })}
          <TipButton
            tip="向该渠道发送一条测试推送；未填写字段会使用已保存凭据"
            size="small"
            type="dashed"
            icon={<SendOutlined />}
            onClick={() => onTest(ch.key)}
            block
            loading={testing}
            disabled={anyTesting && !testing}
            aria-label={`${ch.name}发送测试`}
            data-testid={`test-channel-${ch.key}`}
          >
            {testing ? '发送中...' : '发送测试'}
          </TipButton>
        </div>
      )}
    </Card>
  )
}
