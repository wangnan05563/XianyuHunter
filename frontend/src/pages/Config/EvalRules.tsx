import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Divider,
  Empty,
  InputNumber,
  Slider,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  theme,
} from 'antd'
import { TipButton } from '@/components/TipButton'
import {
  CloudDownloadOutlined,
  DollarCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  ThunderboltOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import ReactECharts from '../../components/charts/EChart'
import { useConfigStore } from '../../stores/configStore'
import { SCOPE_EVAL } from '../../stores/configDirty'
import { extractApiError } from '../../utils/apiError'
import type { DiffChange } from '../../stores/configStore'
import { evalApi } from '../../api'
import TagEditor from '../../components/editors/TagEditor'
import { useAutoRefresh } from '../../hooks/useAutoRefresh'
import { formatShortDateTime } from '../../utils/timeFormat'
import { DiffPreviewModal } from '../../components/DiffPreviewModal'
import DataBlock from '../../components/DataBlock'
import { useFailedSources } from '../../hooks/useFailedSources'
import {
  buildHeatmapOption,
  buildRadarOption,
  computeHeatmapData,
  getEvalValidationError,
  isEvalDistributionData,
  normalizeWeights,
  sumWeights,
  type EvalChartPalette,
  type EvalDistributionData,
  type EvalWeights,
} from './evalRulesModel'

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

const priceRangeGradientRules = [
  { range: '< 600', label: '低于任务下限', score: '-25 / -50', color: 'red' },
  { range: '600-640', label: '低价边缘', score: '-10', color: 'orange' },
  { range: '640-730', label: '推荐核心区', score: '不扣分', color: 'green' },
  { range: '730-760', label: '偏高观察区', score: '-5', color: 'gold' },
  { range: '760-800', label: '高价边缘', score: '-12', color: 'orange' },
  { range: '> 800', label: '高于任务上限', score: '-25 / -50', color: 'red' },
]

// 本页独立请求的数据源（T02899）：分布热力图失败必须显示错误态，
// 不能被 `<Empty description="暂无评估分布数据" />` 说成「近 7 天没有评估」。
type EvalRulesSource = 'dist'

const SOURCE_LABELS: Record<EvalRulesSource, string> = {
  dist: '评估分布',
}

type CollectStats = {
  total: number
  success: number
  failed: number
  success_rate: number
  is_paused: boolean
  fail_pause_threshold: number
  top_failures: Array<{ reason: string; count: number }>
}

export default function EvalRules() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  return (
    <AntdApp component={false}>
      <EvalRulesContent />
    </AntdApp>
  )
}

function EvalRulesContent() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
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
    getFieldOriginal,
    revertField,
  } = useConfigStore()

  const evalConfig = config?.eval
  const weights = evalConfig?.weights
  const thresholds = evalConfig?.thresholds
  const isReady = Boolean(evalConfig && weights && thresholds)
  const weightsTotal = weights ? sumWeights(weights) : 0
  const scoreOrderError = Boolean(evalConfig && evalConfig.pass_score > evalConfig.auto_buy_score)
  const validationError = (evalConfig && weights)
    ? getEvalValidationError(weightsTotal, evalConfig.pass_score, evalConfig.auto_buy_score)
    : null
  const dirtyCount = config ? dirtyPathsInScope(SCOPE_EVAL).length : 0

  const [distData, setDistData] = useState<EvalDistributionData | null>(null)
  const [distLoading, setDistLoading] = useState(true)
  const [distError, setDistError] = useState<string | null>(null)
  const distInFlightRef = useRef(false)
  const { isFailed, mark } = useFailedSources<EvalRulesSource>()

  const [collectStats, setCollectStats] = useState<CollectStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [statsUpdatedAt, setStatsUpdatedAt] = useState<number | null>(null)
  const statsInFlightRef = useRef(false)

  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])
  const [saving, setSaving] = useState(false)

  const originalPassScore = getFieldOriginal('eval.pass_score')
  const originalAutoBuyScore = getFieldOriginal('eval.auto_buy_score')

  const canSave = isReady && dirtyCount > 0 && !validationError && !saving
  const canReset = isReady && dirtyCount > 0 && !saving

  const loadDist = useCallback(async (notifySuccess = false): Promise<void> => {
    // React StrictMode 会重复执行 mount effect；用同步 ref 去重，
    // 避免首屏对同一个分布接口发两次请求（正式构建同样受益于手动刷新保护）。
    if (distInFlightRef.current) return
    distInFlightRef.current = true
    setDistLoading(true)
    setDistError(null)
    try {
      const res = await evalApi.distribution({
        range_hours: 168,
        price_bin_count: 5,
        score_bin_count: 5,
      })
      if (!isEvalDistributionData(res)) {
        throw new Error('评估分布接口返回结构异常：缺少 buckets / price_range / total')
      }
      setDistData(res)
      mark('dist', true)
      if (notifySuccess) message.success('评估分布已刷新')
    } catch (e) {
      const text = extractApiError(e, '评估分布加载失败')
      setDistError(text)
      mark('dist', false)
      if (notifySuccess) message.error(`刷新失败：${text}`, 5)
    } finally {
      distInFlightRef.current = false
      setDistLoading(false)
    }
  }, [mark, message])

  const loadCollectStats = useCallback(async (): Promise<void> => {
    if (!config?.eval?.auto_collect_official) {
      setCollectStats(null)
      setStatsError(null)
      setStatsUpdatedAt(null)
      return
    }
    if (statsInFlightRef.current) return
    statsInFlightRef.current = true
    setStatsLoading(true)
    setStatsError(null)
    try {
      const res = await evalApi.autoCollectStats(24)
      setCollectStats(res)
      setStatsUpdatedAt(Date.now())
    } catch (e) {
      setStatsError(extractApiError(e, '采集统计加载失败'))
    } finally {
      statsInFlightRef.current = false
      setStatsLoading(false)
    }
  }, [config?.eval?.auto_collect_official])

  // 只在开关开启时按 60s 兜底刷新；首次与开关切换由下面的 loadCollectStats effect 立即请求，
  // 不再通过 deps 触发一次延迟请求（原实现会与首次 effect 叠加成两次）。
  const { nextRefreshAt: statsNextRefresh } = useAutoRefresh({
    enabled: Boolean(config?.eval?.auto_collect_official),
    intervalSec: 60,
    paused: !config?.eval?.auto_collect_official,
    refresh: loadCollectStats,
  })

  useEffect(() => {
    void loadCollectStats()
  }, [loadCollectStats])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    void loadDist()
  }, [loadDist])

  const statsLastRefreshStr = statsUpdatedAt
    ? formatShortDateTime(statsUpdatedAt, 'time')
    : '-'
  const statsNextRefreshStr = statsNextRefresh
    ? formatShortDateTime(statsNextRefresh, 'time')
    : null

  const handleReset = () => {
    reset(SCOPE_EVAL)
    message.info('已恢复本页未保存的修改')
  }

  const handleSave = async () => {
    if (!isReady) return
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
      const changes = await previewSave(SCOPE_EVAL)
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
      await confirmSave(SCOPE_EVAL)
      setDiffModalOpen(false)
      message.success('评估规则已保存')
    } catch (e) {
      message.error(extractApiError(e), 5)
    } finally {
      setSaving(false)
    }
  }

  const header = (
    <div style={stickyHeaderStyle} data-testid="eval-rules-header">
      <div style={{ minWidth: 240 }}>
        <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>评估规则</h1>
        <div style={{ marginTop: 2, fontSize: 12, color: token.colorTextTertiary }}>
          调整四维权重、阈值与自动抢单分数；权重总和须为 100
        </div>
      </div>
      <Space wrap align="center">
        {dirtyCount > 0 && (
          <Tag color="orange" data-testid="eval-rules-dirty">
            未保存 {dirtyCount} 项
          </Tag>
        )}
        <TipButton
          tip="放弃本页未保存的修改并重置"
          icon={<UndoOutlined />}
          onClick={handleReset}
          disabled={!canReset}
          data-testid="eval-reset"
        >
          重置
        </TipButton>
        <TipButton
          tip={
            validationError
              ?? (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存评估规则配置')
          }
          type="primary"
          icon={<SaveOutlined />}
          onClick={() => { void handleSave() }}
          loading={saving}
          disabled={!canSave}
          data-testid="eval-save"
        >
          保存
        </TipButton>
      </Space>
    </div>
  )

  if (!config || !evalConfig || !weights || !thresholds) {
    let body
    if (configError) {
      body = (
        <Alert
          type="error"
          showIcon
          message="评估规则配置加载失败"
          description={`${configError}。请检查后端服务或网络后重试，避免在默认值基础上误保存。`}
          action={
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading}>
              重试
            </TipButton>
          }
          data-testid="eval-config-error"
        />
      )
    } else if (config) {
      body = (
        <Alert
          type="error"
          showIcon
          message="评估配置不完整，无法编辑"
          description="当前配置缺少 eval.weights 或 eval.thresholds 段。请重新加载配置，确认后端返回完整的评估规则。"
          action={
            <TipButton tip="重新加载配置" size="small" onClick={() => { void load() }} loading={configLoading}>
              重试
            </TipButton>
          }
          data-testid="eval-config-error"
        />
      )
    } else {
      body = (
        <div
          data-testid="eval-config-loading"
          style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          <Spin tip="正在加载评估规则配置…" size="large">
            <div style={{ width: 260, height: 90 }} />
          </Spin>
        </div>
      )
    }
    return (
      <div className="page-container" data-testid="eval-rules-page">
        {header}
        {body}
      </div>
    )
  }

  const updateWeight = (key: keyof EvalWeights, value: number) => {
    if (!Number.isFinite(value)) return
    update({ eval: { ...evalConfig, weights: { ...weights, [key]: value } } })
  }
  const updateThreshold = (key: keyof typeof thresholds, value: number) => {
    if (!Number.isFinite(value)) return
    update({ eval: { ...evalConfig, thresholds: { ...thresholds, [key]: value } } })
  }

  const updateScore = (key: 'pass_score' | 'auto_buy_score', value: number) => {
    if (!Number.isFinite(value)) return
    update({ eval: { ...evalConfig, [key]: value } })
  }

  const normalize = () => {
    const next = normalizeWeights(weights)
    if (!next) {
      message.warning('权重总和为 0，无法归一化')
      return
    }
    update({ eval: { ...evalConfig, weights: next } })
    message.success('已归一化为 100')
  }

  const chartPalette: EvalChartPalette = {
    axis: token.colorBorderSecondary,
    // 9–11px 的图表轴文字用 tertiary（暗色下仅 45% 白）对比度偏低，统一用 secondary
    axisLabel: token.colorTextSecondary,
    splitAreaA: token.colorFillQuaternary,
    splitAreaB: token.colorBgContainer,
    tooltipBg: token.colorBgElevated,
    tooltipBorder: token.colorBorderSecondary,
    tooltipText: token.colorText,
    cellLabel: token.colorText,
    cellLabelBorder: token.colorBgContainer,
    primary: token.colorPrimary,
    // ECharts 的 visualMap 对 rgba 首色会按白色处理，导致暗色低值格发白；
    // 改用不透明 token：低值=卡片底色，高值=品牌主色。
    visual: [
      token.colorBgContainer,
      token.colorPrimaryBg,
      token.colorPrimary,
    ],
  }
  const radarOption = buildRadarOption(weights, chartPalette)
  const heatmap = computeHeatmapData(distData)
  const heatmapOption = buildHeatmapOption(distData, heatmap, chartPalette)
  const radarAriaLabel = `四维权重雷达图：职业度 ${weights.professional}、信誉 ${weights.credit}、纠纷 ${weights.dispute}、价格 ${weights.price}，总和 ${weightsTotal}。`
  const heatmapAriaLabel = heatmap.hasPriceData
    ? `评估分布热力图。近 7 天共 ${distData?.total ?? 0} 条评估，其中 ${heatmap.pricedTotal} 件有价格。横轴为价格，纵轴为评分；颜色越深表示商品数越多。`
    : '评估分布热力图，暂无可用于绘图的价格样本。'

  const renderCollectStats = evalConfig.auto_collect_official ? (
    <CollectStatsPanel
      stats={collectStats}
      loading={statsLoading || (!collectStats && !statsError)}
      error={statsError}
      lastRefreshStr={statsLastRefreshStr}
      nextRefreshStr={statsNextRefreshStr}
      onRetry={() => { void loadCollectStats() }}
    />
  ) : null

  return (
    <div className="page-container" data-testid="eval-rules-page">
      {header}

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

      {weightsTotal !== 100 && (
        <Alert
          type="error"
          showIcon
          message={`权重总和为 ${weightsTotal}，不能保存`}
          description="四项权重之和必须精确等于 100；可点击右侧按钮按当前比例自动归一化。"
          action={
            <TipButton
              tip={weightsTotal === 0 ? '权重全为 0，无法归一化' : '将权重总和自动归一化为 100'}
              size="small"
              onClick={normalize}
              disabled={weightsTotal === 0}
            >
              一键归一化
            </TipButton>
          }
          style={{ marginBottom: 16 }}
          data-testid="eval-weights-error"
          id="eval-weights-error"
        />
      )}

      <div className="config-layout" data-testid="eval-rules-layout">
        {/* 左侧：权重雷达图 + 权重滑块 + 阈值 */}
        <div>
          <Card title="4 维权重（雷达图 + 滑块联动）" style={{ marginBottom: 16 }}>
            <div data-testid="eval-radar">
              <figure data-testid="eval-radar-role" aria-label={radarAriaLabel} style={{ margin: 0 }}>
                <ReactECharts option={radarOption} style={{ height: 280 }} />
              </figure>
            </div>

            <Divider />

            <WeightSlider
              label="职业度权重"
              value={weights.professional}
              onChange={(v) => updateWeight('professional', v)}
              help="在售商品 / 30天发布 / 描述中含职业关键词 → 扣分"
              revertPath="eval.weights.professional"
              originalValue={getFieldOriginal('eval.weights.professional')}
              onRevert={revertField}
              invalid={weightsTotal !== 100}
              errorId="eval-weights-error"
            />
            <WeightSlider
              label="信誉权重"
              value={weights.credit}
              onChange={(v) => updateWeight('credit', v)}
              help="芝麻信用 / 实名认证 / 注册天数 → 加分"
              revertPath="eval.weights.credit"
              originalValue={getFieldOriginal('eval.weights.credit')}
              onRevert={revertField}
              invalid={weightsTotal !== 100}
              errorId="eval-weights-error"
            />
            <WeightSlider
              label="纠纷权重"
              value={weights.dispute}
              onChange={(v) => updateWeight('dispute', v)}
              help="差评数 / 退款率 / 投诉 → 减分"
              revertPath="eval.weights.dispute"
              originalValue={getFieldOriginal('eval.weights.dispute')}
              onRevert={revertField}
              invalid={weightsTotal !== 100}
              errorId="eval-weights-error"
            />
            <WeightSlider
              label="价格权重"
              value={weights.price}
              onChange={(v) => updateWeight('price', v)}
              help="商品价 / 市场价比例 → 加分"
              revertPath="eval.weights.price"
              originalValue={getFieldOriginal('eval.weights.price')}
              onRevert={revertField}
              invalid={weightsTotal !== 100}
              errorId="eval-weights-error"
            />

            <Divider />
            <div style={{ textAlign: 'center' }}>
              <span
                className={`weight-total-indicator ${weightsTotal === 100 ? 'weight-total-ok' : 'weight-total-warn'}`}
                aria-live="polite"
              >
                权重总和：{weightsTotal}
                {weightsTotal === 100 ? ' ✓' : ' ⚠'}
              </span>
            </div>
          </Card>

          <Card title="阈值配置（一票否决标识）">
            <WeightSlider
              label="在售数阈值"
              value={thresholds.on_sale_count}
              onChange={(v) => updateThreshold('on_sale_count', v)}
              min={0}
              max={200}
              help="超过此值视为职业卖家"
            />
            <WeightSlider
              label="30 天发帖数阈值"
              value={thresholds.post_count_30d}
              onChange={(v) => updateThreshold('post_count_30d', v)}
              min={0}
              max={100}
              help="月发布超过此值视为职业"
            />
            <WeightSlider
              label="主营品类占比阈值"
              value={thresholds.top_category_ratio}
              onChange={(v) => updateThreshold('top_category_ratio', v)}
              min={0}
              max={1}
              step={0.05}
              help="单一类目占比超过此值视为职业"
              formatter={(v) => `${(v * 100).toFixed(0)}%`}
            />

            <Divider />

            <div style={{ marginBottom: 8 }}>
              <Tag color="red" icon={<ThunderboltOutlined />}>一票否决</Tag>
            </div>
            <WeightSlider
              label="信用分下限"
              value={thresholds.credit_score_min}
              onChange={(v) => updateThreshold('credit_score_min', v)}
              min={0}
              max={1000}
              help="低于此分一票否决（直接拒绝）"
            />
            <WeightSlider
              label="差评数上限"
              value={thresholds.bad_review_max}
              onChange={(v) => updateThreshold('bad_review_max', v)}
              min={0}
              max={20}
              help="超过此值一票否决"
            />
            <WeightSlider
              label="注册天数下限"
              value={thresholds.register_days_min}
              onChange={(v) => updateThreshold('register_days_min', v)}
              min={0}
              max={365}
              help="注册不满此天数加风险分"
            />
          </Card>
        </div>

        {/* 右侧：分数阈值、AI 采集、分布与关键词 */}
        <div className="eval-rules-preview">
          <Card title="通过阈值（双滑块）" style={{ marginBottom: 16 }}>
            {scoreOrderError && (
              <Alert
                type="error"
                showIcon
                message={`通过分数（${evalConfig.pass_score}）不能大于自动抢单分数（${evalConfig.auto_buy_score}）`}
                description="请下调通过分数或上调自动抢单分数；修正前保存按钮保持禁用。"
                style={{ marginBottom: 12 }}
                data-testid="eval-score-error"
                id="eval-score-error"
              />
            )}

            <div style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Space wrap size={6}>
                  <strong>通过分数（pass_score）</strong>
                  <Tag color="green">{evalConfig.pass_score}</Tag>
                  {typeof originalPassScore === 'number' && evalConfig.pass_score !== originalPassScore && (
                    <TipButton
                      tip="恢复 pass_score 原始值"
                      size="small"
                      type="link"
                      aria-label="恢复通过分数原始值"
                      onClick={() => revertField('eval.pass_score')}
                      style={{ padding: 0, fontSize: 12 }}
                    >
                      ⏪
                    </TipButton>
                  )}
                </Space>
                <InputNumber
                  min={0}
                  max={100}
                  precision={0}
                  value={evalConfig.pass_score}
                  onChange={(v) => { if (typeof v === 'number') updateScore('pass_score', v) }}
                  style={{ width: 88 }}
                  aria-label="通过分数"
                  status={scoreOrderError ? 'error' : undefined}
                  aria-invalid={scoreOrderError}
                  aria-describedby={scoreOrderError ? 'eval-score-error' : undefined}
                />
              </div>
              <span style={{ fontSize: 12, color: token.colorTextTertiary }}>≥ 此分数：通知用户</span>
              <Slider
                min={0}
                max={100}
                value={evalConfig.pass_score}
                onChange={(v) => updateScore('pass_score', v)}
                marks={{ 0: '0', 60: '60', 80: '80', 100: '100' }}
                ariaLabelForHandle="通过分数滑块"
                ariaValueTextFormatterForHandle={(v) => `${v} 分`}
                tooltip={{ formatter: (v) => `${v ?? 0} 分` }}
                style={{ marginTop: 8 }}
              />
            </div>

            <div>
              <div style={{ marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Space wrap size={6}>
                  <strong>自动抢单分数（auto_buy_score）</strong>
                  <Tag color="orange">{evalConfig.auto_buy_score}</Tag>
                  {typeof originalAutoBuyScore === 'number' && evalConfig.auto_buy_score !== originalAutoBuyScore && (
                    <TipButton
                      tip="恢复 auto_buy_score 原始值"
                      size="small"
                      type="link"
                      aria-label="恢复自动抢单分数原始值"
                      onClick={() => revertField('eval.auto_buy_score')}
                      style={{ padding: 0, fontSize: 12 }}
                    >
                      ⏪
                    </TipButton>
                  )}
                </Space>
                <InputNumber
                  min={0}
                  max={100}
                  precision={0}
                  value={evalConfig.auto_buy_score}
                  onChange={(v) => { if (typeof v === 'number') updateScore('auto_buy_score', v) }}
                  style={{ width: 88 }}
                  aria-label="自动抢单分数"
                  status={scoreOrderError ? 'error' : undefined}
                  aria-invalid={scoreOrderError}
                  aria-describedby={scoreOrderError ? 'eval-score-error' : undefined}
                />
              </div>
              <span style={{ fontSize: 12, color: token.colorTextTertiary }}>≥ 此分数：全自动拍下</span>
              <Slider
                min={0}
                max={100}
                value={evalConfig.auto_buy_score}
                onChange={(v) => updateScore('auto_buy_score', v)}
                marks={{ 0: '0', 60: '60', 80: '80', 100: '100' }}
                ariaLabelForHandle="自动抢单分数滑块"
                ariaValueTextFormatterForHandle={(v) => `${v} 分`}
                tooltip={{ formatter: (v) => `${v ?? 0} 分` }}
                style={{ marginTop: 8 }}
              />
            </div>
          </Card>

          <Card
            title={
              <Space>
                <RobotOutlined />
                AI 评估与自动采集
              </Space>
            }
            style={{ marginBottom: 16 }}
          >
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8 }}>
                <Space wrap>
                  <strong>AI 自动评估</strong>
                  <Tag color={evalConfig.ai_auto_eval ? 'green' : 'default'}>
                    {evalConfig.ai_auto_eval ? '已开启' : '已关闭'}
                  </Tag>
                </Space>
                <Switch
                  checked={evalConfig.ai_auto_eval}
                  onChange={(v) => update({ eval: { ...evalConfig, ai_auto_eval: v } })}
                  aria-label="AI 自动评估开关"
                />
              </div>
              <div style={{ fontSize: 11, color: token.colorTextTertiary }}>
                开启后对通过规则评估的商品自动调用 AI 视觉评估（消耗 OpenAI token），结果会实质性影响总分和风险等级
              </div>
            </div>

            <Divider />

            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8 }}>
                <Space wrap>
                  <strong>AI 深度分析</strong>
                  <Tag color={evalConfig.ai_auto_deep_analyze ? 'green' : 'default'}>
                    {evalConfig.ai_auto_deep_analyze ? '已开启' : '已关闭'}
                  </Tag>
                </Space>
                <Switch
                  checked={evalConfig.ai_auto_deep_analyze}
                  onChange={(v) => update({ eval: { ...evalConfig, ai_auto_deep_analyze: v } })}
                  aria-label="AI 深度分析开关"
                />
              </div>
              <div style={{ fontSize: 11, color: token.colorTextTertiary }}>
                对通过 AI 评估的商品自动做深度分析（消耗更多 token），覆盖成色、真伪、性价比等维度
              </div>
            </div>

            <Divider />

            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8 }}>
                <Space wrap>
                  <CloudDownloadOutlined />
                  <strong>自动官方采集</strong>
                  <Tag color={evalConfig.auto_collect_official ? 'green' : 'default'}>
                    {evalConfig.auto_collect_official ? '已开启' : '已关闭'}
                  </Tag>
                </Space>
                <Switch
                  checked={evalConfig.auto_collect_official}
                  onChange={(v) => update({ eval: { ...evalConfig, auto_collect_official: v } })}
                  aria-label="自动官方采集开关"
                />
              </div>
              <div style={{ fontSize: 11, color: token.colorTextTertiary, marginBottom: 8 }}>
                对通过评估的商品自动调用官方采集，提取评价 / 留言等爬虫拿不到的数据做深度验证
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13 }}>每轮最多采集</span>
                <InputNumber
                  size="small"
                  min={1}
                  max={20}
                  precision={0}
                  value={evalConfig.auto_collect_max_per_run}
                  onChange={(v) => {
                    if (typeof v === 'number') update({ eval: { ...evalConfig, auto_collect_max_per_run: v } })
                  }}
                  style={{ width: 80 }}
                  aria-label="每轮最多采集数量"
                />
                <span style={{ fontSize: 13 }}>条（避免拖慢 + 反爬）</span>
              </div>
            </div>

            {renderCollectStats}

            <Divider style={{ margin: '12px 0' }} />

            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8 }}>
                <Space wrap>
                  <ThunderboltOutlined />
                  <strong>AI 多轮优化建议</strong>
                  <Tag color={evalConfig.ai_multi_run_suggestion ? 'green' : 'default'}>
                    {evalConfig.ai_multi_run_suggestion ? '已开启' : '已关闭'}
                  </Tag>
                </Space>
                <Switch
                  checked={evalConfig.ai_multi_run_suggestion}
                  onChange={(v) => update({ eval: { ...evalConfig, ai_multi_run_suggestion: v } })}
                  aria-label="AI 多轮优化建议开关"
                />
              </div>
              <div style={{ fontSize: 11, color: token.colorTextTertiary, marginBottom: 8 }}>
                累积多轮运行数据后调用 AI 生成趋势洞察与优化建议，与单轮结论互补
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13 }}>每隔</span>
                <InputNumber
                  size="small"
                  min={2}
                  max={20}
                  precision={0}
                  value={evalConfig.ai_suggestion_interval}
                  onChange={(v) => {
                    if (typeof v === 'number') update({ eval: { ...evalConfig, ai_suggestion_interval: v } })
                  }}
                  style={{ width: 80 }}
                  aria-label="AI 优化建议间隔"
                />
                <span style={{ fontSize: 13 }}>轮生成一次（太小浪费 token，太大反馈滞后）</span>
              </div>
            </div>
          </Card>

          <Card
            title={
              <Space>
                <DollarCircleOutlined />
                任务价格区间梯度评分
              </Space>
            }
            style={{ marginBottom: 16 }}
          >
            <Alert
              type="info"
              showIcon={false}
              style={{ marginBottom: 12, fontSize: 12 }}
              message="下表以 600–800 元任务价为例；实际评分按每个任务配置的 min_price / max_price 计算。未配置任务区间时保持旧评分逻辑。"
            />
            <Table
              size="small"
              pagination={false}
              rowKey="range"
              scroll={{ x: 360 }}
              dataSource={priceRangeGradientRules}
              columns={[
                {
                  title: '任务价示例（元）',
                  dataIndex: 'range',
                  key: 'range',
                  width: 120,
                  render: (v: string) => <Tag color="blue">{v}</Tag>,
                },
                {
                  title: '评分含义',
                  dataIndex: 'label',
                  key: 'label',
                },
                {
                  title: '价格分调整',
                  dataIndex: 'score',
                  key: 'score',
                  width: 110,
                  render: (v: string, row: { color: string }) => <Tag color={row.color}>{v}</Tag>,
                },
              ]}
            />
            <div style={{ fontSize: 12, color: token.colorTextTertiary, marginTop: 8 }}>
              区间中段优先，靠近上下边缘轻扣分；超出区间会按偏离程度加重扣分，避免 1 元引流、配件价或明显偏高商品获得虚高评分。
            </div>
          </Card>

          <Card
            title="评估分布热力图（价格 × 评分）"
            style={{ marginBottom: 16 }}
            extra={
              <TipButton
                tip="重新获取近 7 天评估分布"
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => { void loadDist(true) }}
                loading={distLoading}
                data-testid="eval-dist-refresh"
                aria-label="刷新评估分布"
              >
                刷新
              </TipButton>
            }
          >
            <div aria-busy={distLoading} data-testid="eval-heatmap-region">
              {distLoading && !distData ? (
                <div
                  data-testid="eval-heatmap-loading"
                  style={{ minHeight: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                >
                  <Spin tip="正在加载评估分布…" size="large">
                    <div style={{ width: 240, height: 80 }} />
                  </Spin>
                </div>
              ) : (
                <DataBlock
                  failed={isFailed('dist')}
                  source={SOURCE_LABELS.dist}
                  count={distData?.total ?? 0}
                  emptyText="近 7 天暂无评估分布数据"
                  onRetry={loadDist}
                  errorDescription={distError ?? undefined}
                >
                  <Spin spinning={distLoading}>
                    {heatmapOption ? (
                      <>
                        <div data-testid="eval-heatmap">
                          <figure data-testid="eval-heatmap-role" aria-label={heatmapAriaLabel} style={{ margin: 0 }}>
                            <ReactECharts option={heatmapOption} style={{ height: 280 }} />
                          </figure>
                        </div>
                        <div
                          data-testid="eval-heatmap-caption"
                          style={{ fontSize: 11, color: token.colorTextSecondary }}
                        >
                          颜色越深表示该价格 × 评分区间的商品数越多（近 7 天，共 {heatmap.pricedTotal} 件有价格样本）。
                          悬停单元格查看自动抢单 / 通过 / 驳回明细。
                        </div>
                      </>
                    ) : (
                      <Empty
                        description="有评估记录，但缺少商品价格，暂无法生成价格 × 评分热力图"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                      />
                    )}
                  </Spin>
                </DataBlock>
              )}
            </div>
          </Card>

          <Card title="职业关键词（标签编辑器 + 回车添加）">
            <TagEditor
              value={evalConfig.professional_keywords}
              onChange={(v) => update({ eval: { ...evalConfig, professional_keywords: v } })}
              placeholder="输入职业关键词后回车，如：批发、代理"
              color="orange"
              ariaLabel="职业关键词输入"
            />
            <div style={{ fontSize: 12, color: token.colorTextTertiary, marginTop: 8 }}>
              商品描述中包含这些关键词时，职业度评分会被扣减。标签可直接点击关闭移除。
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
        onConfirm={handleConfirmSave}
      />
    </div>
  )
}

function CollectStatsPanel({
  stats,
  loading,
  error,
  lastRefreshStr,
  nextRefreshStr,
  onRetry,
}: {
  readonly stats: CollectStats | null
  readonly loading: boolean
  readonly error: string | null
  readonly lastRefreshStr: string
  readonly nextRefreshStr: string | null
  readonly onRetry: () => void
}) {
  const { token } = theme.useToken()
  let rateColor: string = token.colorError
  if (stats) {
    if (stats.success_rate >= 0.8) {
      rateColor = token.colorSuccess
    } else if (stats.success_rate >= 0.5) {
      rateColor = token.colorWarning
    }
  }
  return (
    <div
      data-testid="eval-collect-stats"
      aria-live="polite"
      aria-busy={loading}
      style={{
        marginTop: 12,
        padding: '8px 12px',
        background: 'var(--xh-bg-spotlight)',
        borderRadius: 6,
        fontSize: 12,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8, flexWrap: 'wrap' }}>
        <span style={{ color: token.colorTextSecondary, fontWeight: 500 }}>
          最近 24 小时采集统计
        </span>
        <span style={{ fontSize: 11, color: token.colorTextQuaternary }}>
          {loading ? '刷新中...' : `更新于 ${lastRefreshStr}`}
          {nextRefreshStr && ` · 下次 ${nextRefreshStr}`}
        </span>
      </div>

      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          action={
            <TipButton
              tip="重新获取采集统计"
              size="small"
              onClick={onRetry}
              loading={loading}
              data-testid="eval-stats-retry"
            >
              重试
            </TipButton>
          }
          data-testid="eval-collect-stats-error"
        />
      )}
      {!error && stats && (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 8 }}>
            <span>共 <strong style={{ color: token.colorText }}>{stats.total}</strong> 次</span>
            <span>成功 <strong style={{ color: token.colorSuccess }}>{stats.success}</strong> 次</span>
            <span>
              失败 <strong style={{ color: stats.failed > 0 ? token.colorError : token.colorText }}>{stats.failed}</strong> 次
            </span>
            <span>
              成功率 <Tag color={rateColor}>{(stats.success_rate * 100).toFixed(1)}%</Tag>
            </span>
          </div>
          {stats.is_paused && (
            <div style={{ marginBottom: 8 }}>
              <Tag color="red">已暂停（连续失败达阈值 {stats.fail_pause_threshold} 次）</Tag>
              <span style={{ fontSize: 11, color: token.colorTextTertiary, marginLeft: 8 }}>
                本轮剩余商品跳过采集，下轮自动重试
              </span>
            </div>
          )}
          {stats.failed > 0 && stats.top_failures.length > 0 && (
            <div>
              <div style={{ color: token.colorTextTertiary, marginBottom: 4 }}>主要失败原因：</div>
              <ul style={{ margin: 0, paddingLeft: 20, color: token.colorTextSecondary }}>
                {stats.top_failures.map((f) => (
                  <li key={`${f.reason}-${f.count}`} style={{ fontSize: 11 }}>
                    <span style={{ color: token.colorText }}>{f.reason}</span>
                    <span style={{ color: token.colorTextQuaternary, marginLeft: 8 }}>×{f.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {!error && !stats && (
        <span style={{ color: token.colorTextTertiary }}>
          {loading ? '加载中...' : '近 24 小时暂无采集记录'}
        </span>
      )}
    </div>
  )
}

function WeightSlider({
  label,
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  help,
  formatter,
  revertPath,
  originalValue,
  onRevert,
  invalid = false,
  errorId,
}: {
  readonly label: string
  readonly value: number
  readonly onChange: (v: number) => void
  readonly min?: number
  readonly max?: number
  readonly step?: number
  readonly help?: string
  readonly formatter?: (v: number) => string
  readonly revertPath?: string
  readonly originalValue?: unknown
  readonly onRevert?: (path: string) => void
  readonly invalid?: boolean
  readonly errorId?: string
}) {
  const helpId = useId()
  const showRevert = Boolean(revertPath) && typeof originalValue === 'number' && value !== originalValue
  // 百分比字段（max≤1）在输入框里按 0–100 展示，配置仍存 0–1。
  // 不使用 InputNumber 的 formatter/parser：两者叠加时，“90”会先被 max=1 判越界再回退为 0。
  const isPercent = max <= 1
  const inputValue = isPercent ? Math.round(value * 100) : value
  const describedBy = [help ? helpId : null, invalid && errorId ? errorId : null]
    .filter((v): v is string => typeof v === 'string')
    .join(' ') || undefined
  const precision = isPercent || step >= 1 ? 0 : 2

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>{label}</span>
        <Space size={4}>
          {showRevert && (
            <TipButton
              tip={`恢复${label}原始值`}
              aria-label={`恢复${label}原始值`}
              size="small"
              type="link"
              onClick={() => { if (revertPath) onRevert?.(revertPath) }}
              style={{ padding: 0, fontSize: 12 }}
            >
              ⏪
            </TipButton>
          )}
          <InputNumber
            size="small"
            min={isPercent ? 0 : min}
            max={isPercent ? 100 : max}
            step={isPercent ? 1 : step}
            precision={precision}
            value={inputValue}
            // 清空输入框时保留当前值，不把 null 静默折算成 0（会瞬间改写配置）
            onChange={(v) => {
              if (typeof v !== 'number') return
              onChange(isPercent ? v / 100 : v)
            }}
            suffix={isPercent ? '%' : undefined}
            style={{ width: isPercent ? 92 : 82 }}
            aria-label={label}
            aria-invalid={invalid || undefined}
            aria-describedby={describedBy}
            status={invalid ? 'error' : undefined}
          />
        </Space>
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={onChange}
        tooltip={{ formatter: formatter ? (v) => formatter(v ?? 0) : undefined }}
        ariaLabelForHandle={label}
        ariaValueTextFormatterForHandle={(v) => (formatter ? formatter(v) : String(v))}
      />
      {help && (
        <div id={helpId} style={{ fontSize: 11, color: 'var(--xh-text-tertiary)' }}>
          {help}
        </div>
      )}
    </div>
  )
}
