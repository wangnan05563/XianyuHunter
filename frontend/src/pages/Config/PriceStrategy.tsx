import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Switch,
  Slider,
  InputNumber,
  Space,
  Divider,
  Statistic,
  Tag,
  Empty,
  Spin,
  theme,
} from 'antd'
import { TipButton } from '@/components/TipButton'
import { SaveOutlined, ReloadOutlined } from '@ant-design/icons'
import ReactECharts from '../../components/charts/EChart'
import { useConfigStore } from '../../stores/configStore'
import { SCOPE_PRICE } from '../../stores/configDirty'
import { extractApiError } from '../../utils/apiError'
import type { DiffChange } from '../../stores/configStore'
import type { HistogramData } from '../../api'
import { priceApi } from '../../api'
import { DiffPreviewModal } from '../../components/DiffPreviewModal'
import {
  PriceCeilingIcon,
  PriceFloorIcon,
  MarketRatioIcon,
  TopNIcon,
  TrendPreviewIcon,
  TargetHitIcon,
  RevertIcon,
} from '../../components/icons/GeometricIcons'
import {
  DEFAULT_PRICE_STRATEGY,
  MAX_PRICE_LIMIT,
  MIN_PRICE_LIMIT,
  estimatePriceHits,
  formatBinRange,
  formatPrice,
  formatPriceAxis,
  getPriceStrategyError,
  isHistogramPayload,
  normalizePriceStrategy,
  type PriceStrategyConfig,
} from './priceStrategyModel'

/** Switch 未在 antd 类型里声明 aria-label，但 rc-switch 会把剩余属性透传给 button */
function switchA11y(label: string): Record<string, string> {
  return { 'aria-label': label }
}

const stickyHeaderStyle: CSSProperties = {
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
  background: 'var(--xh-bg-layout)',
  borderBottom: '1px solid var(--xh-border)',
}

const BIN_STATUS_TEXT: Record<string, string> = { reject: '过滤', boundary: '跨阈值估算' }

export default function PriceStrategy() {
  return (
    <AntdApp component={false}>
      <PriceStrategyContent />
    </AntdApp>
  )
}

function PriceStrategyContent() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  const { message } = AntdApp.useApp()
  const {
    config,
    loading: configLoading,
    error: configError,
    load,
    update,
    dirtyPathsInScope,
    reset,
    previewSave,
    confirmSave,
    getFieldOriginal,
  } = useConfigStore()
  const { token } = theme.useToken()

  // 本地态只是编辑缓冲：改动即刻写回 store 草稿。
  // 初始 null，等真实配置到达后再渲染控件，避免默认值闪现与“未加载先编辑”。
  const [strategyState, setStrategyState] = useState<PriceStrategyConfig | null>(null)
  const [histogram, setHistogram] = useState<HistogramData | null>(null)
  const [histogramLoading, setHistogramLoading] = useState(false)
  const [histogramError, setHistogramError] = useState<string | null>(null)
  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])
  const [saving, setSaving] = useState(false)

  // 计算与渲染使用非空值；是否真正可编辑仍由 strategyState/isReady 控制
  const strategy = strategyState ?? DEFAULT_PRICE_STRATEGY

  useEffect(() => {
    void load()
  }, [load])

  // 配置加载完成后同步到本地编辑态；若配置段缺失，用安全默认值兜底并在页面上提示
  useEffect(() => {
    if (config) setStrategyState(normalizePriceStrategy(config.price_strategy))
  }, [config])

  const loadHistogram = useCallback(async (notifySuccess = false) => {
    setHistogramLoading(true)
    setHistogramError(null)
    try {
      // 后端只接受 bins / task_id；range_hours 之前被发送但从未被读取，已移除
      const data = await priceApi.histogram({ bins: 20 })
      if (!isHistogramPayload(data)) {
        throw new Error('价格分布接口返回结构异常：需要 bins 为 {min,max,count} 对象数组')
      }
      setHistogram(data)
      if (notifySuccess) message.success('价格分布已刷新')
    } catch (e) {
      const msg = extractApiError(e)
      setHistogramError(msg)
      if (notifySuccess) message.error(`刷新失败：${msg}`, 5)
    } finally {
      setHistogramLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadHistogram()
  }, [loadHistogram])

  const updateStrategy = (next: PriceStrategyConfig) => {
    setStrategyState(next)
    update({ price_strategy: next })
  }

  const updateBargainPercentile = (value: number) => {
    const current = config?.bargain_price ?? { percentile: 0.1 }
    update({ bargain_price: { ...current, percentile: value } })
  }

  const dirtyCount = dirtyPathsInScope(SCOPE_PRICE).length
  const validationError = getPriceStrategyError(strategy)
  const isReady = Boolean(config && strategyState)
  const bargainPercentile = config?.bargain_price?.percentile ?? 0.1

  // 只覆盖硬性上下限；市场参考价与 TopN 需要逐商品/逐类目计算，不能从直方图推导
  const previewResults = useMemo(
    () => estimatePriceHits(histogram?.bins ?? [], strategy),
    [histogram, strategy],
  )

  const priceRangeInvalid = Boolean(
    strategy.enabled_max &&
    strategy.enabled_min &&
    strategy.min_price >= strategy.max_price,
  )
  const maxOutOfRange = strategy.max_price < 0 || strategy.max_price > MAX_PRICE_LIMIT
  const minOutOfRange = strategy.min_price < 0 || strategy.min_price > MIN_PRICE_LIMIT

  const originalMax = getFieldOriginal('price_strategy.max_price')
  const originalMin = getFieldOriginal('price_strategy.min_price')
  const originalMarketRatio = getFieldOriginal('price_strategy.market_ratio')
  const originalTopN = getFieldOriginal('price_strategy.top_n')
  const canRevertMax = typeof originalMax === 'number' && strategy.max_price !== originalMax
  const canRevertMin = typeof originalMin === 'number' && strategy.min_price !== originalMin
  const canRevertMarketRatio =
    typeof originalMarketRatio === 'number' && strategy.market_ratio !== originalMarketRatio
  const canRevertTopN = typeof originalTopN === 'number' && strategy.top_n !== originalTopN

  const chartOption = useMemo(() => {
    if (!histogram?.bins.length) return null
    const bins = histogram.bins
    const axisMin = Math.min(...bins.map((bin) => bin.min))
    const axisMax = Math.max(...bins.map((bin) => bin.max ?? bin.min))
    const tooltipText = (index: number) => {
      const bin = previewResults.bins[index]
      if (!bin) return ''
      const statusText = BIN_STATUS_TEXT[bin.status] ?? '通过'
      return `${formatBinRange(bin)}<br/>商品数：<b>${bin.count}</b><br/>估算结果：${statusText}`
    }
    const markLines: Array<Record<string, unknown>> = []
    if (strategy.enabled_min) {
      markLines.push({
        xAxis: strategy.min_price,
        name: '下限',
        label: { show: false },
        lineStyle: {
          color: token.colorText,
          type: 'dashed',
          width: 1.5,
          shadowColor: token.colorBgContainer,
          shadowBlur: 2,
        },
      })
    }
    if (strategy.enabled_max) {
      markLines.push({
        xAxis: strategy.max_price,
        name: '上限',
        label: { show: false },
        lineStyle: {
          color: token.colorError,
          type: 'dashed',
          width: 1.5,
          shadowColor: token.colorBgContainer,
          shadowBlur: 2,
        },
      })
    }

    return {
      animationDuration: 250,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: token.colorBgElevated,
        borderColor: token.colorBorderSecondary,
        textStyle: { color: token.colorText },
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params]
          const first = list[0] as { dataIndex?: number } | undefined
          return tooltipText(first?.dataIndex ?? 0)
        },
      },
      grid: { left: 52, right: 62, top: 36, bottom: 34 },
      xAxis: {
        type: 'value',
        min: axisMin,
        max: axisMax > axisMin ? axisMax : axisMin + 1,
        name: '价格（¥）',
        nameTextStyle: { color: token.colorTextTertiary, fontSize: 11 },
        axisLabel: {
          color: token.colorTextTertiary,
          fontSize: 11,
          formatter: (value: number) => formatPriceAxis(value),
        },
        splitLine: { show: false },
        boundaryGap: ['2%', '2%'],
      },
      yAxis: {
        type: 'value',
        name: '商品数',
        nameTextStyle: { color: token.colorTextTertiary, fontSize: 11 },
        minInterval: 1,
        axisLabel: { color: token.colorTextTertiary, fontSize: 11 },
        splitLine: { lineStyle: { color: token.colorBorderSecondary } },
      },
      series: [
        {
          name: '商品数',
          type: 'bar',
          barWidth: '78%',
          data: bins.map((bin, index) => {
            const upper = bin.max ?? bin.min
            const mid = (bin.min + upper) / 2
            const verdict = previewResults.bins[index]
            let color = token.colorSuccess
            if (verdict?.status === 'reject') color = token.colorError
            else if (verdict?.status === 'boundary') color = token.colorWarning
            else if (verdict?.count === 0) color = token.colorTextQuaternary
            return {
              value: [mid, bin.count],
              itemStyle: { color, borderRadius: [3, 3, 0, 0] },
            }
          }),
          markLine: markLines.length
            ? { symbol: 'none', silent: true, animation: false, z: 10, data: markLines }
            : undefined,
          // 保证 20 个桶在窄屏也不会因为 0 值被压成不可点击的线
          emphasis: { itemStyle: { shadowBlur: 6, shadowColor: `${token.colorPrimary}44` } },
        },
      ],
    }
  }, [histogram, previewResults, strategy, token])

  const handleSave = async () => {
    if (!config || !strategyState) return
    if (validationError) {
      message.error(validationError)
      return
    }
    if (dirtyCount === 0) {
      message.info('配置未变更')
      return
    }
    try {
      // 本地编辑态已在每次 onChange 写回 store；这里再同步一次，防止异步边界丢最后一次输入
      update({ price_strategy: strategy })
      setSaving(true)
      const changes = await previewSave(SCOPE_PRICE)
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
      await confirmSave(SCOPE_PRICE)
      setDiffModalOpen(false)
      message.success('价格策略已保存')
    } catch (e) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    reset(SCOPE_PRICE)
    message.info('已恢复本页未保存的修改')
  }

  const scopeLabel = histogram?.scope?.label || '全部任务'
  const compactScopeLabel =
    scopeLabel.length > 24 ? `${scopeLabel.slice(0, 24)}…` : scopeLabel
  const hasSamples = previewResults.total > 0
  const sampleText = hasSamples
    ? `样本 ${previewResults.total} 件`
    : '暂无价格样本'
  const medianText = hasSamples && histogram?.summary?.median != null
    ? `中位 ${formatPrice(histogram.summary.median)}`
    : null
  const rangeText =
    hasSamples && histogram?.summary?.min != null && histogram?.summary?.max != null
      ? `${formatPrice(histogram.summary.min)}–${formatPrice(histogram.summary.max)}`
      : null
  const boundaryText = [
    strategy.enabled_min ? `下限 ${formatPrice(strategy.min_price)}` : null,
    strategy.enabled_max ? `上限 ${formatPrice(strategy.max_price)}` : null,
  ].filter((item): item is string => typeof item === 'string').join(' · ')

  const chartAriaLabel = hasSamples
    ? `价格分布直方图，${scopeLabel}，${sampleText}；绿色为通过，红色为过滤，橙色为跨阈值估算。`
    : '价格分布直方图，暂无价格样本'

  let chartBody: ReactNode
  if (chartOption) {
    chartBody = <ReactECharts option={chartOption} style={{ height: 300, width: '100%' }} notMerge lazyUpdate />
  } else if (histogramLoading) {
    chartBody = <div style={{ height: 300 }} />
  } else {
    chartBody = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={histogramError ? '价格分布暂不可用' : '暂无价格分布数据'}
      />
    )
  }

  return (
    <div className="page-container" data-testid="price-strategy-page">
      {/* 吸顶操作区：编辑下方滑块后无需滚回顶部才能保存 */}
      <div style={stickyHeaderStyle} data-testid="price-strategy-header">
        <div style={{ minWidth: 220 }}>
          <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>价格策略</h1>
          <div style={{ marginTop: 2, fontSize: 12, color: token.colorTextTertiary }}>
            四类过滤策略独立开关；修改后先预览差异，再提交保存
          </div>
        </div>
        <Space wrap align="center">
          {dirtyCount > 0 && (
            <Tag color="orange" data-testid="price-strategy-dirty">
              未保存 {dirtyCount} 项
            </Tag>
          )}
          <TipButton
            tip="放弃本页未保存的修改并重置"
            icon={<RevertIcon size={16} />}
            onClick={handleReset}
            disabled={!isReady || dirtyCount === 0}
            data-testid="price-reset"
          >
            重置
          </TipButton>
          <TipButton
            tip={
              validationError ??
              (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存价格策略配置')
            }
            type="primary"
            icon={<SaveOutlined />}
            onClick={() => { void handleSave() }}
            loading={saving}
            disabled={!isReady || dirtyCount === 0 || Boolean(validationError)}
            data-testid="price-save"
          >
            保存
          </TipButton>
        </Space>
      </div>

      {isReady ? (
        <>
          {configError && (
            <Alert
              type="warning"
              showIcon
              message="配置加载异常，当前显示上次加载的内容"
              description={`${configError}。请在保存前重试加载。`}
              action={
                <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading}>
                  重试
                </TipButton>
              }
              style={{ marginBottom: 16 }}
            />
          )}
          {!config?.price_strategy && (
            <Alert
              type="warning"
              showIcon
              message="配置缺少 price_strategy 段，当前使用默认值"
              description="保存后会把默认价格策略写入配置，请确认数值符合预期。"
              style={{ marginBottom: 16 }}
            />
          )}
          {validationError && (
            <Alert
              type="error"
              showIcon
              message={validationError}
              description="修正后保存按钮才会恢复可用。"
              style={{ marginBottom: 16 }}
              data-testid="price-strategy-validation"
            />
          )}

          <div className="config-layout">
            <Card title="策略配置（4 种策略独立开关）">
              {/* 捡漏价格 P10 分位数配置（对应后端 BargainPriceConfig） */}
              <Card
                size="small"
                style={{ marginBottom: 12 }}
                title={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <PriceFloorIcon size={20} /> 捡漏价格分位数
                  </span>
                }
              >
                <Slider
                  min={0.01}
                  max={0.49}
                  step={0.01}
                  value={bargainPercentile}
                  onChange={updateBargainPercentile}
                  marks={{ 0.05: '5%', 0.1: '10%', 0.2: '20%', 0.49: '49%' }}
                  tooltip={{ formatter: (value) => `${((value ?? 0) * 100).toFixed(0)}% (P${Math.round((value ?? 0) * 100)})` }}
                  ariaLabelForHandle="捡漏价格分位数"
                  ariaValueTextFormatterForHandle={(value) => `${((value ?? 0) * 100).toFixed(0)}%（P${Math.round((value ?? 0) * 100)}）`}
                />
                <div style={{ fontSize: 12, color: token.colorTextTertiary, marginTop: 4 }}>
                  取已售价格中最低 {(bargainPercentile * 100).toFixed(0)}% 的边界值（P{Math.round(bargainPercentile * 100)}）作为捡漏基准。
                  调高让更多商品被判定为“可捡漏”，调低则更严格。
                </div>
              </Card>

              {/* 策略 1：硬性上限 */}
              <Card
                size="small"
                style={{ marginBottom: 12 }}
                title={
                  <Space size={8} wrap>
                    <Switch
                      checked={strategy.enabled_max}
                      onChange={(value) => updateStrategy({ ...strategy, enabled_max: value })}
                      {...switchA11y('启用硬性上限策略')}
                    />
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <PriceCeilingIcon size={20} />
                      硬性上限
                    </span>
                    {canRevertMax && (
                      <TipButton
                        tip="恢复最高价原始值"
                        size="small"
                        type="link"
                        aria-label="恢复硬性上限原始值"
                        onClick={() => updateStrategy({ ...strategy, max_price: originalMax })}
                        style={{ padding: 0, fontSize: 12 }}
                      >
                        <RevertIcon size={12} />
                      </TipButton>
                    )}
                  </Space>
                }
              >
                <div style={{ opacity: strategy.enabled_max ? 1 : 0.55, transition: 'opacity 0.2s' }}>
                  <Slider
                    min={0}
                    max={MAX_PRICE_LIMIT}
                    step={100}
                    value={strategy.max_price}
                    onChange={(value) => updateStrategy({ ...strategy, max_price: value })}
                    marks={{ 0: '¥0', 10000: '¥1万', 30000: '¥3万', 50000: '¥5万' }}
                    tooltip={{ formatter: (value) => formatPrice(value) }}
                    disabled={!strategy.enabled_max}
                    ariaLabelForHandle="硬性上限价格"
                    ariaValueTextFormatterForHandle={(value) => formatPrice(value)}
                  />
                  <InputNumber
                    prefix="¥"
                    min={0}
                    max={MAX_PRICE_LIMIT}
                    step={100}
                    precision={0}
                    value={strategy.max_price}
                    disabled={!strategy.enabled_max}
                    status={maxOutOfRange || priceRangeInvalid ? 'error' : undefined}
                    onChange={(value) => updateStrategy({ ...strategy, max_price: value ?? 0 })}
                    style={{ width: '100%' }}
                    aria-label="硬性上限金额"
                    aria-invalid={maxOutOfRange || priceRangeInvalid}
                    aria-describedby={priceRangeInvalid ? 'max-price-desc min-price-error' : 'max-price-desc'}
                  />
                  <div id="max-price-desc" style={{ fontSize: 12, color: token.colorTextTertiary, marginTop: 4 }}>
                    超过此价格的商品将被过滤（¥0–¥{MAX_PRICE_LIMIT}）
                  </div>
                </div>
              </Card>

              {/* 策略 2：硬性下限 */}
              <Card
                size="small"
                style={{ marginBottom: 12 }}
                title={
                  <Space size={8} wrap>
                    <Switch
                      checked={strategy.enabled_min}
                      onChange={(value) => updateStrategy({ ...strategy, enabled_min: value })}
                      {...switchA11y('启用硬性下限策略')}
                    />
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <PriceFloorIcon size={20} />
                      硬性下限（防 1 元引流）
                    </span>
                    {canRevertMin && (
                      <TipButton
                        tip="恢复最低价原始值"
                        size="small"
                        type="link"
                        aria-label="恢复硬性下限原始值"
                        onClick={() => updateStrategy({ ...strategy, min_price: originalMin })}
                        style={{ padding: 0, fontSize: 12 }}
                      >
                        <RevertIcon size={12} />
                      </TipButton>
                    )}
                  </Space>
                }
              >
                <div style={{ opacity: strategy.enabled_min ? 1 : 0.55, transition: 'opacity 0.2s' }}>
                  <Slider
                    min={0}
                    max={MIN_PRICE_LIMIT}
                    step={10}
                    value={strategy.min_price}
                    onChange={(value) => updateStrategy({ ...strategy, min_price: value })}
                    marks={{ 0: '¥0', 100: '¥100', 500: '¥500', 2000: '¥2000', 5000: '¥5000' }}
                    tooltip={{ formatter: (value) => formatPrice(value) }}
                    disabled={!strategy.enabled_min}
                    ariaLabelForHandle="硬性下限价格"
                    ariaValueTextFormatterForHandle={(value) => formatPrice(value)}
                  />
                  <InputNumber
                    prefix="¥"
                    min={0}
                    max={MIN_PRICE_LIMIT}
                    step={10}
                    precision={0}
                    value={strategy.min_price}
                    disabled={!strategy.enabled_min}
                    status={minOutOfRange || priceRangeInvalid ? 'error' : undefined}
                    onChange={(value) => updateStrategy({ ...strategy, min_price: value ?? 0 })}
                    style={{ width: '100%' }}
                    aria-label="硬性下限金额"
                    aria-invalid={priceRangeInvalid || minOutOfRange}
                    aria-describedby={priceRangeInvalid ? 'min-price-desc min-price-error' : 'min-price-desc'}
                  />
                  <div id="min-price-desc" style={{ fontSize: 12, color: token.colorTextTertiary, marginTop: 4 }}>
                    低于此价格视为引流陷阱，过滤（¥0–¥{MIN_PRICE_LIMIT}）
                  </div>
                  {priceRangeInvalid && (
                    <div
                      id="min-price-error"
                      role="alert"
                      style={{ fontSize: 12, color: token.colorError, marginTop: 2 }}
                      data-testid="price-range-inline-error"
                    >
                      下限 {formatPrice(strategy.min_price)} 必须小于上限 {formatPrice(strategy.max_price)}
                    </div>
                  )}
                </div>
              </Card>

              {/* 策略 3：低于市场参考价 */}
              <Card
                size="small"
                style={{ marginBottom: 12 }}
                title={
                  <Space size={8} wrap>
                    <Switch
                      checked={strategy.enabled_market_ratio}
                      onChange={(value) => updateStrategy({ ...strategy, enabled_market_ratio: value })}
                      {...switchA11y('启用市场参考价策略')}
                    />
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <MarketRatioIcon size={20} />
                      低于市场参考价
                    </span>
                    {canRevertMarketRatio && (
                      <TipButton
                        tip="恢复市场价比例原始值"
                        size="small"
                        type="link"
                        aria-label="恢复市场参考价比例原始值"
                        onClick={() => updateStrategy({ ...strategy, market_ratio: originalMarketRatio })}
                        style={{ padding: 0, fontSize: 12 }}
                      >
                        <RevertIcon size={12} />
                      </TipButton>
                    )}
                  </Space>
                }
              >
                <div style={{ opacity: strategy.enabled_market_ratio ? 1 : 0.55, transition: 'opacity 0.2s' }}>
                  <Slider
                    min={0.3}
                    max={1}
                    step={0.05}
                    value={strategy.market_ratio}
                    onChange={(value) => updateStrategy({ ...strategy, market_ratio: value })}
                    marks={{ 0.3: '30%', 0.5: '50%', 0.7: '70%', 1: '100%' }}
                    tooltip={{ formatter: (value) => `${((value ?? 0) * 100).toFixed(0)}%` }}
                    disabled={!strategy.enabled_market_ratio}
                    ariaLabelForHandle="市场参考价比例"
                    ariaValueTextFormatterForHandle={(value) => `${((value ?? 0) * 100).toFixed(0)}%`}
                  />
                  <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
                    价格高于“市场价 × {(strategy.market_ratio * 100).toFixed(0)}%”的商品将被过滤（需 ≥3 个样本）
                  </div>
                </div>
              </Card>

              {/* 策略 4：同类低价 TopN */}
              <Card
                size="small"
                title={
                  <Space size={8} wrap>
                    <Switch
                      checked={strategy.enabled_top_n}
                      onChange={(value) => updateStrategy({ ...strategy, enabled_top_n: value })}
                      {...switchA11y('启用同类低价 TopN 策略')}
                    />
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <TopNIcon size={20} />
                      同类低价 TopN
                    </span>
                    {canRevertTopN && (
                      <TipButton
                        tip="恢复 TopN 原始值"
                        size="small"
                        type="link"
                        aria-label="恢复同类低价 TopN 原始值"
                        onClick={() => updateStrategy({ ...strategy, top_n: originalTopN })}
                        style={{ padding: 0, fontSize: 12 }}
                      >
                        <RevertIcon size={12} />
                      </TipButton>
                    )}
                  </Space>
                }
              >
                <div style={{ opacity: strategy.enabled_top_n ? 1 : 0.55, transition: 'opacity 0.2s' }}>
                  <Slider
                    min={1}
                    max={20}
                    step={1}
                    value={strategy.top_n}
                    onChange={(value) => updateStrategy({ ...strategy, top_n: value })}
                    marks={{ 1: 'Top1', 5: 'Top5', 10: 'Top10', 20: 'Top20' }}
                    tooltip={{ formatter: (value) => `Top${value}` }}
                    disabled={!strategy.enabled_top_n}
                    ariaLabelForHandle="同类低价 TopN"
                    ariaValueTextFormatterForHandle={(value) => `Top${value}`}
                  />
                  <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
                    只保留同类商品中价格最低的前 {strategy.top_n} 个（按类目分别计算）
                  </div>
                </div>
              </Card>
            </Card>

            {/* 右侧：实时预览 */}
            <div className="preview-panel price-strategy-preview">
              <Card
                title={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <TrendPreviewIcon size={20} /> 实时预览：价格分布直方图
                  </span>
                }
                extra={
                  <Tag title={scopeLabel} style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {compactScopeLabel}
                  </Tag>
                }
                style={{ marginBottom: 16 }}
              >
                {hasSamples && (
                  <div style={{ fontSize: 12, color: token.colorTextSecondary, marginBottom: 8 }} data-testid="price-histogram-summary">
                    {sampleText}{medianText ? ` · ${medianText}` : ''}{rangeText ? ` · 区间 ${rangeText}` : ''}
                  </div>
                )}
                {chartOption && boundaryText && (
                  <div style={{ fontSize: 11, color: token.colorTextTertiary, marginBottom: 6 }} data-testid="price-histogram-boundaries">
                    当前策略边界：{boundaryText}
                  </div>
                )}

                {histogramError && (
                  <Alert
                    type="warning"
                    showIcon
                    message="价格分布加载失败"
                    description={
                      histogram
                        ? `${histogramError}。图表与估算仍显示上次成功获取的数据，可重试。`
                        : `${histogramError}。图表暂时无法展示，请重试。`
                    }
                    action={
                      <TipButton tip="重新请求价格分布" size="small" onClick={() => { void loadHistogram(true) }} loading={histogramLoading}>
                        重试
                      </TipButton>
                    }
                    style={{ marginBottom: 12 }}
                    data-testid="price-histogram-error"
                  />
                )}

                <Spin spinning={histogramLoading} tip="正在加载价格分布…">
                  <figure
                    aria-label={chartAriaLabel}
                    data-testid="price-chart-role"
                    style={{ margin: 0, minHeight: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  >
                    {chartBody}
                  </figure>
                </Spin>

                {chartOption && (
                  <div
                    style={{ fontSize: 11, color: token.colorTextTertiary, marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}
                    data-testid="price-histogram-legend"
                  >
                    <span style={{ color: token.colorSuccess }}>■&nbsp;通过</span>
                    <span style={{ color: token.colorError }}>■&nbsp;过滤</span>
                    <span style={{ color: token.colorWarning }}>■&nbsp;跨阈值</span>
                    <span style={{ color: token.colorText }}>┃&nbsp;下限</span>
                    <span style={{ color: token.colorError }}>┃&nbsp;上限</span>
                  </div>
                )}
              </Card>

              <Card
                title={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <TargetHitIcon size={20} /> 策略命中预览
                    <Tag color="gold">仅价格区间</Tag>
                  </span>
                }
              >
                <RowOfStats
                  total={previewResults.total}
                  pass={previewResults.pass}
                  reject={previewResults.reject}
                  successColor={token.colorSuccess}
                  errorColor={token.colorError}
                />

                {previewResults.estimated && (
                  <div style={{ marginTop: 8, fontSize: 12, color: token.colorWarning }} data-testid="price-estimate-boundary">
                    有 {previewResults.boundary} 件落在跨阈值价格桶内，已按桶内价格跨度比例估算。
                  </div>
                )}

                <div style={{ marginTop: 8, fontSize: 12, color: token.colorTextTertiary, lineHeight: 1.6 }}>
                  本预览只按硬性上限/下限对价格分布做估算；市场参考价与同类 TopN 需逐商品比较，实际命中以运行日志为准。
                </div>

                <Divider />

                <TipButton
                  tip="重新请求价格分布，并按当前硬性上下限重新估算；不会保存配置"
                  type="dashed"
                  block
                  icon={<ReloadOutlined />}
                  loading={histogramLoading}
                  onClick={() => { void loadHistogram(true) }}
                  data-testid="price-refresh-preview"
                >
                  刷新价格分布与估算
                </TipButton>
              </Card>
            </div>
          </div>
        </>
      ) : (
        <ConfigUnavailable loading={configLoading} error={configError} onRetry={() => { void load() }} />
      )}

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

function ConfigUnavailable({
  loading,
  error,
  onRetry,
}: Readonly<{ loading: boolean; error: string | null; onRetry: () => void }>) {
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="价格策略配置加载失败"
        description={`${error}。请检查后端服务或网络后重试，避免在默认值基础上误保存。`}
        action={
          <TipButton tip="重新加载配置" size="small" onClick={onRetry} loading={loading}>
            重试
          </TipButton>
        }
        data-testid="price-strategy-config-error"
      />
    )
  }
  return (
    <div style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Spin tip="正在加载价格策略配置…" size="large">
        <div style={{ width: 240, height: 80 }} />
      </Spin>
    </div>
  )
}

function RowOfStats({
  total,
  pass,
  reject,
  successColor,
  errorColor,
}: Readonly<{
  total: number
  pass: number
  reject: number
  successColor: string
  errorColor: string
}>) {
  const suffix = <span style={{ fontSize: 12, marginLeft: 3 }}>件</span>
  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Statistic title="总商品数" value={total} suffix={suffix} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Statistic title="通过" value={pass} suffix={suffix} valueStyle={{ color: successColor }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Statistic title="过滤" value={reject} suffix={suffix} valueStyle={{ color: errorColor }} />
      </div>
    </div>
  )
}
