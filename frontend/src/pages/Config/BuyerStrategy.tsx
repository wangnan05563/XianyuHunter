import { type CSSProperties, useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Col,
  InputNumber,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  theme,
} from 'antd'
import { AimOutlined, BellOutlined, SaveOutlined, ThunderboltOutlined, UndoOutlined, WarningOutlined } from '@ant-design/icons'
import { TipButton } from '@/components/TipButton'
import { useConfigStore } from '../../stores/configStore'
import type { DiffChange } from '../../stores/configStore'
import { SCOPE_BUYER } from '../../stores/configDirty'
import { extractApiError } from '../../utils/apiError'
import { DiffPreviewModal } from '../../components/DiffPreviewModal'
import {
  ACCOUNT_ROTATOR_FIELDS,
  BUYER_RUNTIME_FIELDS,
  getAutoBuyThresholdWarning,
  getBuyerStrategyConfigIssue,
  getBuyerStrategyReadyConfig,
  getBuyerStrategyValidationError,
  getDailyOrderGuardText,
  toFiniteNumberOrNull,
  type AccountRotatorConfig,
  type BuyerRuntimeConfig,
} from './buyerStrategyModel'

const { Text, Paragraph } = Typography

// 三种任务执行模式。背景统一用主题容器色，只保留品牌色描边；
// 原实现把浅色 pastel 写死，暗色主题下会变成亮块且白字不可读。
const MODES = [
  {
    key: 'notify',
    icon: <BellOutlined />,
    label: 'notify',
    desc: '仅推送通知，不抢单（最安全）',
    color: '#1677ff',
  },
  {
    key: 'confirm',
    icon: <AimOutlined />,
    label: 'confirm',
    desc: '推送后等待用户确认（推荐日常）',
    color: '#faad14',
  },
  {
    key: 'auto',
    icon: <ThunderboltOutlined />,
    label: 'auto',
    desc: '全自动抢单（评分达到阈值且低风险）',
    color: '#FF6200',
  },
] as const

interface NumericFieldProps {
  readonly id: string
  readonly label: string
  readonly value: number
  readonly min: number
  readonly max: number
  readonly step?: number
  readonly precision?: number
  readonly description: string
  readonly onChange: (value: number) => void
  readonly suffix?: string
  readonly status?: 'error' | 'warning'
  readonly ariaInvalid?: boolean
  readonly testId?: string
}

/**
 * 统一数值字段：label 通过 htmlFor 关联 input，读屏名称和 description 一并可访问。
 * 清空输入时不写回 0 —— InputNumber 的 null 直接忽略，避免“擦掉重输”被静默改配置。
 */
function NumericField({
  id,
  label,
  value,
  min,
  max,
  step,
  precision,
  description,
  onChange,
  suffix,
  status,
  ariaInvalid,
  testId,
}: NumericFieldProps) {
  const descId = `${id}-desc`
  const input = (
    <InputNumber
      id={id}
      aria-label={label}
      aria-describedby={descId}
      aria-invalid={ariaInvalid ? 'true' : undefined}
      data-testid={testId}
      min={min}
      max={max}
      step={step}
      precision={precision}
      status={status}
      value={value}
      style={suffix ? { flex: 1 } : { width: '100%' }}
      onChange={(next) => {
        const finite = toFiniteNumberOrNull(next)
        if (finite !== null) onChange(finite)
      }}
    />
  )

  return (
    <div style={{ marginBottom: 8 }}>
      <label htmlFor={id} style={{ display: 'block', marginBottom: 8, cursor: 'text' }}>
        <strong>{label}</strong>
      </label>
      {suffix ? (
        <Space.Compact style={{ width: '100%' }}>
          {input}
          <span
            aria-hidden="true"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '0 12px',
              background: 'var(--xh-bg-spotlight)',
              border: '1px solid var(--xh-border)',
              borderLeft: 'none',
            }}
          >
            {suffix}
          </span>
        </Space.Compact>
      ) : input}
      <Paragraph id={descId} type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
        {description}
      </Paragraph>
    </div>
  )
}

export default function BuyerStrategy() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  return (
    <AntdApp component={false}>
      <BuyerStrategyContent />
    </AntdApp>
  )
}

function BuyerStrategyContent() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
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

  // React StrictMode 会把 mount effect 跑两遍；不挡住就会首屏请求配置两次。
  // 这里只防自动加载，失败后的“重试”仍然显式调用 load()。
  const didAutoLoadRef = useRef(false)
  useEffect(() => {
    if (didAutoLoadRef.current) return
    didAutoLoadRef.current = true
    void load()
  }, [load])

  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])
  const [saving, setSaving] = useState(false)

  const readyConfig = getBuyerStrategyReadyConfig(config)
  const configIssue = getBuyerStrategyConfigIssue(config)
  const evalConfig = readyConfig?.eval
  const buyer = readyConfig?.buyer
  const accountRotator = readyConfig?.account_rotator
  const dirtyCount = config ? dirtyPathsInScope(SCOPE_BUYER).length : 0
  const validationError = readyConfig ? getBuyerStrategyValidationError(config) : null
  const scoreOrderInvalid = Boolean(evalConfig && evalConfig.pass_score > evalConfig.auto_buy_score)
  const thresholdWarning = evalConfig ? getAutoBuyThresholdWarning(evalConfig.auto_buy_score) : null
  const canSave = Boolean(readyConfig) && dirtyCount > 0 && !validationError && !saving
  const canReset = Boolean(readyConfig) && dirtyCount > 0 && !saving

  const headerStyle: CSSProperties = {
    position: 'sticky',
    top: 0,
    zIndex: 20,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    flexWrap: 'wrap',
    padding: '16px 0 12px',
    marginBottom: 16,
    background: token.colorBgLayout,
    borderBottom: `1px solid ${token.colorBorderSecondary}`,
  }

  const updateScore = (key: 'pass_score' | 'auto_buy_score', value: number) => {
    if (!evalConfig) return
    update({ eval: { ...evalConfig, [key]: value } })
  }

  const updateBuyer = <K extends keyof BuyerRuntimeConfig>(key: K, value: BuyerRuntimeConfig[K]) => {
    if (!buyer) return
    update({ buyer: { ...buyer, [key]: value } })
  }

  const updateRotator = <K extends keyof AccountRotatorConfig>(key: K, value: AccountRotatorConfig[K]) => {
    if (!accountRotator) return
    update({ account_rotator: { ...accountRotator, [key]: value } })
  }

  const handleReset = () => {
    reset(SCOPE_BUYER)
    message.info('已恢复本页未保存的修改')
  }

  const handleSave = async () => {
    if (!readyConfig) return
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
      const changes = await previewSave(SCOPE_BUYER)
      if (changes.length === 0) {
        message.info('配置未变更')
        return
      }
      setDiffChanges(changes)
      setDiffModalOpen(true)
    } catch (e) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const handleConfirmSave = async () => {
    try {
      setSaving(true)
      await confirmSave(SCOPE_BUYER)
      setDiffModalOpen(false)
      message.success('抢单策略已保存')
    } catch (e) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const header = (
    <div style={headerStyle} data-testid="buyer-strategy-header">
      <div style={{ minWidth: 260 }}>
        <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>抢单策略</h1>
        <div style={{ marginTop: 2, fontSize: 12, color: token.colorTextTertiary }}>
          控制 auto 模式下的抢单行为；notify / confirm 模式不受影响
        </div>
      </div>
      <Space wrap align="center">
        {dirtyCount > 0 && (
          <Tag color="orange" data-testid="buyer-strategy-dirty">
            未保存 {dirtyCount} 项
          </Tag>
        )}
        <TipButton
          tip="放弃本页未保存的修改并重置"
          icon={<UndoOutlined />}
          onClick={handleReset}
          disabled={!canReset}
          data-testid="buyer-strategy-reset"
        >
          重置
        </TipButton>
        <TipButton
          tip={
            validationError
              ?? (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存抢单策略配置')
          }
          type="primary"
          style={canSave ? { backgroundColor: '#FF6200', borderColor: '#FF6200' } : undefined}
          icon={<SaveOutlined />}
          onClick={() => { void handleSave() }}
          loading={saving}
          disabled={!canSave}
          data-testid="buyer-strategy-save"
        >
          保存
        </TipButton>
      </Space>
    </div>
  )

  let body
  if (!readyConfig || !evalConfig || !buyer || !accountRotator) {
    if (configError) {
      body = (
        <Alert
          type="error"
          showIcon
          message="抢单策略配置加载失败"
          description={`${configError}。请检查后端服务或网络后重试，避免在默认值基础上误保存。`}
          action={(
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading} data-testid="buyer-strategy-retry">
              重试
            </TipButton>
          )}
          data-testid="buyer-strategy-config-error"
        />
      )
    } else if (config) {
      body = (
        <Alert
          type="error"
          showIcon
          message="抢单策略配置不完整，无法编辑"
          description={`${configIssue}。本页同时读写 eval、buyer、account_rotator 三段；请重新加载配置，确认后端返回完整数据。`}
          action={(
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading} data-testid="buyer-strategy-retry">
              重试
            </TipButton>
          )}
          data-testid="buyer-strategy-config-missing"
        />
      )
    } else {
      body = (
        <div
          data-testid="buyer-strategy-config-loading"
          style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          <Spin tip="正在加载抢单策略配置…" size="large">
            <div style={{ width: 260, height: 90 }} />
          </Spin>
        </div>
      )
    }

    return (
      <div className="page-container" data-testid="buyer-strategy-page">
        {header}
        {body}
      </div>
    )
  }

  body = (
    <>
      {validationError && (
        <Alert
          type="error"
          showIcon
          message="配置校验未通过"
          description={validationError}
          data-testid="buyer-strategy-validation"
          style={{ marginBottom: 16 }}
        />
      )}

      <Card title="分数阈值" style={{ marginBottom: 24 }}>
        <Row gutter={[24, 16]}>
          <Col xs={24} md={12}>
            <NumericField
              id="buyer-auto-score"
              label="自动抢单最低分数（auto_buy_score）"
              value={evalConfig.auto_buy_score}
              min={0}
              max={100}
              precision={0}
              description="≥ 此分数才会自动拍下（在「评估规则」页面设置评分规则）"
              onChange={(value) => updateScore('auto_buy_score', value)}
              suffix="分"
              status={scoreOrderInvalid ? 'error' : undefined}
              ariaInvalid={scoreOrderInvalid}
              testId="buyer-strategy-auto-score"
            />
          </Col>
          <Col xs={24} md={12}>
            <NumericField
              id="buyer-pass-score"
              label="通过分数（pass_score）"
              value={evalConfig.pass_score}
              min={0}
              max={100}
              precision={0}
              description="≥ 此分数会推送给用户确认"
              onChange={(value) => updateScore('pass_score', value)}
              suffix="分"
              status={scoreOrderInvalid ? 'error' : undefined}
              ariaInvalid={scoreOrderInvalid}
              testId="buyer-strategy-pass-score"
            />
          </Col>
        </Row>
        {scoreOrderInvalid && (
          <div
            role="alert"
            data-testid="buyer-score-error"
            style={{ color: token.colorError, fontSize: 12, marginTop: 4 }}
          >
            通过分数不能大于自动抢单分数
          </div>
        )}
        {thresholdWarning && (
          <Alert
            type="warning"
            showIcon
            icon={<WarningOutlined />}
            message={thresholdWarning}
            data-testid="buyer-strategy-threshold-warning"
            style={{ marginTop: 12 }}
          />
        )}
      </Card>

      <Card title="抢单运行时参数" style={{ marginBottom: 24 }}>
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 16 }}>
          控制自动抢单流程的细节参数。修改后保存即可生效，无需重启服务。
        </Paragraph>
        <Row gutter={[16, 16]} data-testid="buyer-strategy-runtime-grid">
          {BUYER_RUNTIME_FIELDS.map((field) => (
            <Col xs={24} sm={12} xl={8} key={field.key}>
              <NumericField
                id={`buyer-runtime-${field.key}`}
                label={field.label}
                value={buyer[field.key]}
                min={field.min}
                max={field.max}
                step={field.step}
                precision={field.precision}
                description={field.description}
                onChange={(value) => updateBuyer(field.key, value)}
                testId={`buyer-runtime-${field.key}`}
              />
            </Col>
          ))}
        </Row>
      </Card>

      <Card title="账号轮换调度" style={{ marginBottom: 24 }}>
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 16 }}>
          多账号轮换调度器的冷却与失败阈值。触发风控后账号进入冷却，连续失败超阈值则自动禁用。
          修改后保存即生效（已创建的调度器实例会在下次启动时读取新值）。
        </Paragraph>
        <Row gutter={[16, 16]}>
          {ACCOUNT_ROTATOR_FIELDS.map((field) => (
            <Col xs={24} md={12} key={field.key}>
              <NumericField
                id={`buyer-rotator-${field.key}`}
                label={field.label}
                value={accountRotator[field.key]}
                min={field.min}
                max={field.max}
                precision={field.precision}
                description={field.description}
                onChange={(value) => updateRotator(field.key, value)}
                testId={`buyer-rotator-${field.key}`}
              />
            </Col>
          ))}
        </Row>
      </Card>

      <Card title="任务级模式" style={{ marginBottom: 24 }}>
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          每个任务可独立设置执行模式（在「任务管理」中）：
        </Text>
        <Row gutter={[16, 16]}>
          {MODES.map((mode) => (
            <Col xs={24} md={12} xl={8} key={mode.key}>
              <Card
                size="small"
                data-testid={`buyer-mode-${mode.key}`}
                style={{
                  height: '100%',
                  borderColor: mode.color,
                  backgroundColor: token.colorBgContainer,
                  textAlign: 'center',
                }}
              >
                <Tag
                  icon={mode.icon}
                  color={mode.color}
                  style={{ fontSize: 14, padding: '4px 12px' }}
                >
                  {mode.label}
                </Tag>
                <p style={{ margin: '8px 0 0', color: token.colorTextSecondary, fontSize: 13 }}>{mode.desc}</p>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      <Card title="安全护栏" style={{ marginBottom: 24 }} data-testid="buyer-strategy-safety">
        <ul style={{ paddingLeft: 20, lineHeight: 2.2, margin: 0 }}>
          <li>
            <Text>
              auto 模式下，评分 ≥ 当前 <strong>{evalConfig.auto_buy_score}</strong> 分且风险等级为 LOW 才会自动拍下；
              建议阈值不低于 80。
            </Text>
          </li>
          <li>
            <Text>单日抢单数量上限：</Text>
            {(buyer.max_orders_per_day ?? 0) > 0 ? (
              <Text>{getDailyOrderGuardText(buyer.max_orders_per_day)}</Text>
            ) : (
              <Text type="warning">{getDailyOrderGuardText(buyer.max_orders_per_day)}</Text>
            )}
          </li>
          <li>
            <Text>
              系统只拍下不代付：订单停在「待支付」，需在闲鱼 App 手动完成支付；Dashboard 可人工接管。
            </Text>
          </li>
        </ul>

        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message={(
            <span>
              <strong>谨慎使用 auto 模式</strong>：自动拍下会立即锁定你的资金，请确认已充分测试。
            </span>
          )}
          style={{ marginTop: 16 }}
        />
      </Card>
    </>
  )

  return (
    <div className="page-container" data-testid="buyer-strategy-page">
      {header}
      {body}
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
