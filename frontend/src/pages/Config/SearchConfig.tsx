import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Col,
  Divider,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  theme,
} from 'antd'
import { SaveOutlined, UndoOutlined } from '@ant-design/icons'
import { TipButton } from '@/components/TipButton'
import { useConfigStore } from '../../stores/configStore'
import type { DiffChange } from '../../stores/configStore'
import { SCOPE_SEARCH } from '../../stores/configDirty'
import { extractApiError } from '../../utils/apiError'
import TagEditor from '../../components/editors/TagEditor'
import { DiffPreviewModal } from '../../components/DiffPreviewModal'
import {
  DEFAULT_INTERVAL_MAX,
  DEFAULT_INTERVAL_MIN,
  FAIL_PAUSE_THRESHOLD_MAX,
  FAIL_PAUSE_THRESHOLD_MIN,
  OPERATION_DELAY_MAX,
  OPERATION_DELAY_MIN,
  SEARCH_MAX_PAGES_MAX,
  SEARCH_MAX_PAGES_MIN,
  SEARCH_SORT_OPTIONS,
  SEARCH_TIMEOUT_MAX,
  SEARCH_TIMEOUT_MIN,
  WAF_INTERVAL_MAX,
  WAF_INTERVAL_MIN,
  getMaxDelayMs,
  getOperationDelaySeconds,
  getPageSizeOptions,
  getQpsFromDelay,
  getSearchConfigIssue,
  getSearchConfigReadyConfig,
  getSearchConfigValidationError,
  toFiniteNumberOrNull,
  type SearchConfigReady,
  type SearchConfigSegment,
} from './searchConfigModel'

const { Text, Paragraph } = Typography
/** 1024 双列下压缩卡片内边距，把宽度让给输入控件。 */
const CARD_STYLES = { body: { padding: 16 } } as const

interface FieldProps {
  readonly id: string
  readonly label: string
  readonly description?: string
  readonly children: ReactNode
}

/** 统一的字段容器：label 通过 htmlFor 绑定控件，说明通过 aria-describedby 关联。 */
function Field({ id, label, description, children }: FieldProps) {
  const descId = `${id}-desc`
  return (
    <div style={{ marginBottom: 14 }}>
      <label htmlFor={id} style={{ display: 'block', marginBottom: 8, cursor: 'text' }}>
        <strong>{label}</strong>
      </label>
      {children}
      {description && (
        <Paragraph id={descId} type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
          {description}
        </Paragraph>
      )}
    </div>
  )
}

interface NumericFieldProps {
  readonly id: string
  readonly label: string
  readonly value: number
  readonly min: number
  readonly max: number
  readonly step?: number
  readonly precision?: number
  readonly description: string
  readonly suffix?: string
  readonly testId?: string
  readonly onChange: (value: number | null) => void
}

function NumericField({
  id,
  label,
  value,
  min,
  max,
  step,
  precision,
  description,
  suffix,
  testId,
  onChange,
}: NumericFieldProps) {
  const descId = `${id}-desc`
  const input = (
    <InputNumber
      id={id}
      aria-label={label}
      aria-describedby={descId}
      data-testid={testId}
      min={min}
      max={max}
      step={step}
      precision={precision}
      value={value}
      onChange={onChange}
      style={suffix ? { flex: 1 } : { width: '100%' }}
    />
  )

  return (
    <Field id={id} label={label} description={description}>
      {suffix ? (
        <Space.Compact style={{ width: '100%' }}>
          {input}
          <span
            aria-hidden="true"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '0 8px',
              background: 'var(--xh-bg-spotlight)',
              border: '1px solid var(--xh-border)',
              borderLeft: 'none',
              color: 'var(--xh-text-tertiary)',
            }}
          >
            {suffix}
          </span>
        </Space.Compact>
      ) : input}
    </Field>
  )
}

/** 页面外壳负责提供 AntdApp 上下文，避免静态 message 在暗色主题下产生警告。 */
export default function SearchConfig() {
  return (
    <AntdApp component={false}>
      <SearchConfigContent />
    </AntdApp>
  )
}

function SearchConfigContent() {
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

  const didAutoLoadRef = useRef(false)
  useEffect(() => {
    if (didAutoLoadRef.current) return
    didAutoLoadRef.current = true
    void load()
  }, [load])

  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])
  const [saving, setSaving] = useState(false)

  const ready = getSearchConfigReadyConfig(config)
  const configIssue = getSearchConfigIssue(config)
  const dirtyCount = config ? dirtyPathsInScope(SCOPE_SEARCH).length : 0
  const validationError = ready ? getSearchConfigValidationError(ready) : null
  const canSave = Boolean(ready) && dirtyCount > 0 && !validationError && !saving
  const canReset = Boolean(ready) && dirtyCount > 0 && !saving

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

  const handleReset = () => {
    reset(SCOPE_SEARCH)
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
      const changes = await previewSave(SCOPE_SEARCH)
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
      await confirmSave(SCOPE_SEARCH)
      setDiffModalOpen(false)
      message.success('搜索参数配置已保存')
    } catch (e) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const header = (
    <div style={headerStyle} data-testid="search-config-header">
      <div style={{ minWidth: 280 }}>
        <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>搜索参数</h1>
        <div style={{ marginTop: 2, fontSize: 12, color: token.colorTextTertiary }}>
          控制搜索分页、排序、地区/标签过滤、超时与调度默认值
        </div>
      </div>
      <Space wrap align="center">
        {dirtyCount > 0 && (
          <Tag color="orange" data-testid="search-config-dirty">
            未保存 {dirtyCount} 项
          </Tag>
        )}
        <TipButton
          tip="放弃本页未保存的修改并重置"
          icon={<UndoOutlined />}
          onClick={handleReset}
          disabled={!canReset}
          data-testid="search-config-reset"
        >
          重置
        </TipButton>
        <TipButton
          tip={
            validationError
              ?? (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存搜索参数配置')
          }
          type="primary"
          style={canSave ? { backgroundColor: '#FF6200', borderColor: '#FF6200' } : undefined}
          icon={<SaveOutlined />}
          onClick={() => { void handleSave() }}
          loading={saving}
          disabled={!canSave}
          data-testid="search-config-save"
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
          message="搜索参数配置加载失败"
          description={`${configError}。请检查后端服务或网络后重试，避免在默认值基础上误保存。`}
          action={(
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading} data-testid="search-config-retry">
              重试
            </TipButton>
          )}
          data-testid="search-config-config-error"
        />
      )
    } else if (config) {
      body = (
        <Alert
          type="error"
          showIcon
          message="搜索参数配置不完整，无法编辑"
          description={`${configIssue}。本页同时读写 search、antidetect、task_scheduler、waf 四段；请重新加载配置，确认后端返回完整数据。`}
          action={(
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading} data-testid="search-config-retry">
              重试
            </TipButton>
          )}
          data-testid="search-config-config-missing"
        />
      )
    } else {
      body = (
        <div
          data-testid="search-config-loading"
          style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          <Spin tip="正在加载搜索参数配置…" size="large">
            <div style={{ width: 260, height: 90 }} />
          </Spin>
        </div>
      )
    }

    return (
      <div className="page-container" data-testid="search-config-page">
        {header}
        {body}
      </div>
    )
  }

  const search = ready.search
  const antidetect = ready.antidetect
  const taskScheduler = ready.task_scheduler
  const waf = ready.waf
  const delaySec = getOperationDelaySeconds(antidetect.min_delay_ms)
  const requestRate = getQpsFromDelay(delaySec)
  const pageSizeOptions = getPageSizeOptions(search.page_size)
  const sortLabel = SEARCH_SORT_OPTIONS.find((option) => option.value === search.sort_type)?.label ?? search.sort_type

  const updateSearch = <K extends keyof SearchConfigSegment>(key: K, value: SearchConfigSegment[K]) => {
    update({ search: { ...search, [key]: value } })
  }

  const updateAntidetect = (patch: Partial<SearchConfigReady['antidetect']>) => {
    update({ antidetect: { ...antidetect, ...patch } })
  }

  const updateTaskScheduler = (patch: Partial<SearchConfigReady['task_scheduler']>) => {
    update({ task_scheduler: { ...taskScheduler, ...patch } })
  }

  const updateWaf = (patch: Partial<SearchConfigReady['waf']>) => {
    update({ waf: { ...waf, ...patch } })
  }

  const setOperationDelay = (value: number | null) => {
    const finite = toFiniteNumberOrNull(value)
    if (finite === null) return
    const next = Math.min(OPERATION_DELAY_MAX, Math.max(OPERATION_DELAY_MIN, Math.round(finite)))
    updateAntidetect({
      min_delay_ms: next * 1000,
      max_delay_ms: getMaxDelayMs(next),
      qps: getQpsFromDelay(next),
    })
  }

  const setIntField = (value: number | null, min: number, max: number, apply: (next: number) => void) => {
    const finite = toFiniteNumberOrNull(value)
    if (finite === null) return
    apply(Math.min(max, Math.max(min, Math.round(finite))))
  }

  const toggleFilterTag = (tag: string) => {
    const next = search.filter_tags.includes(tag)
      ? search.filter_tags.filter((item) => item !== tag)
      : [...search.filter_tags, tag]
    updateSearch('filter_tags', next)
  }

  body = (
    <>
      {validationError && (
        <Alert
          type="error"
          showIcon
          message="配置校验未通过"
          description={validationError}
          data-testid="search-config-validation"
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="基础搜索参数" styles={CARD_STYLES} style={{ marginBottom: 16 }} data-testid="search-config-basic-card">
            <NumericField
              id="search-operation-delay"
              label="操作延迟"
              value={delaySec}
              min={OPERATION_DELAY_MIN}
              max={OPERATION_DELAY_MAX}
              step={1}
              precision={0}
              description="模拟人类操作的间隔时间（非任务循环间隔），避免被风控（建议 ≥2 秒）"
              suffix="秒"
              testId="search-operation-delay"
              onChange={setOperationDelay}
            />

            <Field id="search-page-size" label="每页结果数量" description="单次请求返回的商品条目数">
              <Select
                id="search-page-size"
                aria-label="每页结果数量"
                aria-describedby="search-page-size-desc"
                value={search.page_size}
                onChange={(value) => updateSearch('page_size', value)}
                options={pageSizeOptions}
                style={{ width: '100%' }}
                data-testid="search-page-size"
              />
            </Field>

            <Field id="search-sort-type" label="排序方式" description="搜索结果的默认排序规则">
              <Select
                id="search-sort-type"
                aria-label="排序方式"
                aria-describedby="search-sort-type-desc"
                value={search.sort_type}
                onChange={(value) => updateSearch('sort_type', value)}
                options={SEARCH_SORT_OPTIONS.map((option) => ({ ...option }))}
                style={{ width: '100%' }}
                data-testid="search-sort-type"
              />
            </Field>

            <NumericField
              id="search-timeout"
              label="搜索超时"
              value={search.timeout}
              min={SEARCH_TIMEOUT_MIN}
              max={SEARCH_TIMEOUT_MAX}
              step={5}
              precision={0}
              description="单次搜索请求的最大等待时间"
              suffix="秒"
              testId="search-timeout"
              onChange={(value) => setIntField(value, SEARCH_TIMEOUT_MIN, SEARCH_TIMEOUT_MAX, (next) => updateSearch('timeout', next))}
            />

            <NumericField
              id="search-max-pages"
              label="翻页深度"
              value={search.max_pages}
              min={SEARCH_MAX_PAGES_MIN}
              max={SEARCH_MAX_PAGES_MAX}
              step={1}
              precision={0}
              description={`最多翻多少屏；每屏约 20–26 条，5 屏约 100–130 条。越大覆盖越全但耗时更长（${SEARCH_MAX_PAGES_MIN}–${SEARCH_MAX_PAGES_MAX} 屏）`}
              testId="search-max-pages"
              onChange={(value) => setIntField(value, SEARCH_MAX_PAGES_MIN, SEARCH_MAX_PAGES_MAX, (next) => updateSearch('max_pages', next))}
            />
          </Card>

          <Card title="容错与暂停" styles={CARD_STYLES} style={{ marginBottom: 16 }} data-testid="search-config-fault-card">
            <NumericField
              id="search-fail-threshold"
              label="失败暂停阈值"
              value={antidetect.fail_pause_threshold}
              min={FAIL_PAUSE_THRESHOLD_MIN}
              max={FAIL_PAUSE_THRESHOLD_MAX}
              step={1}
              precision={0}
              description={`连续失败达到 ${antidetect.fail_pause_threshold} 次后将暂停任务（防触发风控）`}
              testId="search-fail-threshold"
              onChange={(value) => setIntField(value, FAIL_PAUSE_THRESHOLD_MIN, FAIL_PAUSE_THRESHOLD_MAX, (next) => updateAntidetect({ fail_pause_threshold: next }))}
            />
            <div data-testid="search-request-rate">
              <Text strong>等效请求频率：</Text>
              <Text code>{requestRate} 次/分钟</Text>
              <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
                根据操作延迟自动计算的理论值（60 秒 / 每次延迟秒）
              </Paragraph>
            </div>
          </Card>

          <Card title="任务调度默认值" styles={CARD_STYLES} style={{ marginBottom: 16 }} data-testid="search-config-task-card">
            <Alert
              type="info"
              showIcon
              message="新建任务未指定采集周期时使用此默认值"
              description="已存在的任务不受此配置影响，需在任务编辑页面单独修改。"
              style={{ marginBottom: 16 }}
            />
            <NumericField
              id="search-default-interval"
              label="默认采集周期"
              value={taskScheduler.default_interval_seconds}
              min={DEFAULT_INTERVAL_MIN}
              max={DEFAULT_INTERVAL_MAX}
              step={10}
              precision={0}
              description={`范围 ${DEFAULT_INTERVAL_MIN}–${DEFAULT_INTERVAL_MAX} 秒；过短易触发反爬，过长错过抢单窗口`}
              suffix="秒"
              testId="search-default-interval"
              onChange={(value) => setIntField(value, DEFAULT_INTERVAL_MIN, DEFAULT_INTERVAL_MAX, (next) => updateTaskScheduler({ default_interval_seconds: next }))}
            />
            <Field
              id="search-auto-search"
              label="任务列表自动搜索默认开关"
              description="开启后，用户首次访问任务管理页时自动启用倒计时搜索（用户可在页面内手动关闭）"
            >
              <Switch
                id="search-auto-search"
                aria-label="任务列表自动搜索默认开关"
                aria-describedby="search-auto-search-desc"
                checked={taskScheduler.auto_search_enabled}
                onChange={(checked) => updateTaskScheduler({ auto_search_enabled: checked })}
                checkedChildren="开"
                unCheckedChildren="关"
                data-testid="search-auto-search"
              />
            </Field>
          </Card>

          <Card title="反爬检测（WAF）" styles={CARD_STYLES} style={{ marginBottom: 16 }} data-testid="search-config-waf-card">
            <Alert
              type="info"
              showIcon
              message="定期检测闲鱼登录状态，失效时自动暂停任务并通知用户"
              style={{ marginBottom: 16 }}
            />
            <Field
              id="search-waf-enabled"
              label="启用 WAF 检测"
              description="关闭后不再自动检测登录状态，任务可能因 Cookie 失效而持续失败"
            >
              <Switch
                id="search-waf-enabled"
                aria-label="启用 WAF 检测"
                aria-describedby="search-waf-enabled-desc"
                checked={waf.enabled}
                onChange={(checked) => updateWaf({ enabled: checked })}
                checkedChildren="开"
                unCheckedChildren="关"
                data-testid="search-waf-enabled"
              />
            </Field>
            <NumericField
              id="search-waf-interval"
              label="登录检查间隔"
              value={waf.login_check_interval_min}
              min={WAF_INTERVAL_MIN}
              max={WAF_INTERVAL_MAX}
              step={5}
              precision={0}
              description={`多久检测一次闲鱼登录状态（${WAF_INTERVAL_MIN}–${WAF_INTERVAL_MAX} 分钟）；过短增加服务器压力，过长延迟发现失效`}
              suffix="分钟"
              testId="search-waf-interval"
              onChange={(value) => setIntField(value, WAF_INTERVAL_MIN, WAF_INTERVAL_MAX, (next) => updateWaf({ login_check_interval_min: next }))}
            />
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="地区与过滤" styles={CARD_STYLES} style={{ marginBottom: 16 }} data-testid="search-config-filter-card">
            <Field
              id="search-regions"
              label="地区过滤"
              description='限制搜索结果的发货地区，多个地区用英文逗号分隔（如 "北京,上海,广州"）'
            >
              <Input
                id="search-regions"
                aria-label="地区过滤"
                aria-describedby="search-regions-desc"
                value={search.regions}
                onChange={(e) => updateSearch('regions', e.target.value)}
                placeholder="例：北京,上海,广州"
                allowClear
                data-testid="search-regions"
              />
            </Field>

            <Divider plain>筛选标签</Divider>

            <Field
              id="search-filter-tags"
              label="筛选标签"
              description="仅显示带有以下标签的商品（如包邮、信用极好等同款筛选条件）"
            >
              <TagEditor
                id="search-filter-tags"
                value={search.filter_tags}
                onChange={(tags) => updateSearch('filter_tags', tags)}
                placeholder="输入筛选标签后回车"
                ariaLabel="筛选标签"
                color="orange"
              />
            </Field>

            <div style={{ marginTop: 14 }}>
              <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 8 }}>常用预设：</Text>
              <Space size={[4, 8]} wrap>
                {['包邮', '信用极好', '同款', '全新', '可小刀'].map((tag) => (
                  <TipButton
                    tip={`点击切换筛选标签「${tag}」`}
                    key={tag}
                    size="small"
                    type={search.filter_tags.includes(tag) ? 'primary' : 'default'}
                    onClick={() => toggleFilterTag(tag)}
                    style={{
                      fontSize: 12,
                      borderColor: search.filter_tags.includes(tag) ? undefined : '#FF6200',
                      color: search.filter_tags.includes(tag) ? undefined : '#FF6200',
                    }}
                    data-testid={`search-preset-${tag}`}
                  >
                    {tag}
                  </TipButton>
                ))}
              </Space>
            </div>
          </Card>

          <Card title="当前配置摘要" styles={CARD_STYLES} data-testid="search-config-summary">
            <div style={{ fontSize: 13, lineHeight: 2 }}>
              <div>
                <Text strong>操作延迟：</Text>
                <Text>{delaySec}s（最大 {Math.round(delaySec * 1.5)}s）</Text>
              </div>
              <div>
                <Text strong>每页数量：</Text>
                <Text>{search.page_size} 条</Text>
              </div>
              <div>
                <Text strong>翻页深度：</Text>
                <Text>{search.max_pages} 屏</Text>
              </div>
              <div>
                <Text strong>排序方式：</Text>
                <Text>{sortLabel}</Text>
              </div>
              <div>
                <Text strong>超时设置：</Text>
                <Text>{search.timeout}s</Text>
              </div>
              <div>
                <Text strong>失败暂停阈值：</Text>
                <Text>连续失败 {antidetect.fail_pause_threshold} 次后暂停</Text>
              </div>
              <div>
                <Text strong>地区过滤：</Text>
                <Text>{search.regions || '未设置（全国）'}</Text>
              </div>
              <div>
                <Text strong>筛选标签：</Text>
                <Text>{search.filter_tags.length > 0 ? search.filter_tags.join('、') : '未设置'}</Text>
              </div>
            </div>
          </Card>
        </Col>
      </Row>

      <DiffPreviewModal
        open={diffModalOpen}
        diffChanges={diffChanges}
        config={config}
        loading={saving}
        onCancel={() => setDiffModalOpen(false)}
        onConfirm={() => { void handleConfirmSave() }}
      />
    </>
  )

  return (
    <div className="page-container" data-testid="search-config-page">
      {header}
      {body}
    </div>
  )
}
