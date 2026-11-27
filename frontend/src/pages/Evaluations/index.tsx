import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Card, Table, Space, Spin, Input, Select, Slider, Row, Col,
  Empty, DatePicker, Alert, Progress, Checkbox, Tag, Statistic, InputNumber,
  Tooltip, Popconfirm, Drawer,
} from 'antd'
import {
  ReloadOutlined, AimOutlined, SearchOutlined, UndoOutlined,
  RetweetOutlined, SettingOutlined, LeftOutlined, RightOutlined,
  RobotOutlined, CloudDownloadOutlined, QuestionCircleOutlined, LoginOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { taskApi, evalApi, type EvalItem } from '../../api'
import { parseEvalDeepLink, evalDeepLinkMatchesFilters } from '../../utils/alertDeepLink'
import { usePersistentState } from '../../hooks/usePersistentState'
import { useDebouncedAction } from '../../hooks/useDebouncedAction'
import { useColumnConfig } from '../../hooks/useColumnConfig'
import { COLUMN_DEFINITIONS } from './columnDefinitions'
import EvalHeatmap from './components/EvalHeatmap'
import ResultBarChart from './components/ResultBarChart'
import PriceHistogram from './components/PriceHistogram'
import TrendSparkline from './components/TrendSparkline'
import ColumnSettingsModal from './components/ColumnSettingsModal'
import CollapsibleRail from './components/CollapsibleRail'
import { ExportButton } from '../../components/ExportButton'
import { AIEvalModal } from './components/AIEvalModal'
import { DeepAnalyzeModal } from './components/DeepAnalyzeModal'
import { CollectResultModal } from './components/CollectResultModal'
import { TipButton } from '@/components/TipButton'
import { AI_GATE_OFF_TIP, useAIEnabled } from '@/hooks/useAIEnabled'
import FetchFailedHint from '../../components/FetchFailedHint'
import { useFailedSources } from '../../hooks/useFailedSources'

import { useEvalFilters, type ResultCategory } from './hooks/useEvalFilters'
import { useEvalList } from './hooks/useEvalList'
import { useEvalDist } from './hooks/useEvalDist'
import { useEvalAI } from './hooks/useEvalAI'
import { useEvalCollect } from './hooks/useEvalCollect'
import { useEvalBatch } from './hooks/useEvalBatch'
import { useEvalTrend } from './hooks/useEvalTrend'
import { useEvalFeedback } from './hooks/useEvalFeedback'
import { useEvalColumns } from './hooks/useEvalColumns'

import { translateDimension, translateRejectReason } from './dimensionLabels'
import type { DistResponse, SellerTrendData } from './utils'
import './evaluations.css'

const { RangePicker } = DatePicker

// 窄屏列宽收敛：表格从 19 列（2436px）压到关键列，标题/价格/评分/状态/操作常驻。
// 不按 panelCollapsed 改列，面板展开时表格改为独占整行，避免再被挤到 587px。
const WIDE_WIDTHS: Record<string, number> = {
  title: 220, price: 90, brand: 80, score: 80, risk: 70, is_sold: 76, action: 80,
}
const COMPACT_WIDTHS: Record<string, number> = {
  title: 190, price: 84, brand: 80, score: 72, risk: 64, is_sold: 70, action: 72,
}
const NARROW_WIDTHS: Record<string, number> = {
  title: 130, price: 84, brand: 80, score: 64, risk: 60, is_sold: 62, action: 68,
}

function ExpandedDetail({ r, trendCache, trendLoading, trendError, loadSellerTrend }: {
  readonly r: EvalItem
  readonly trendCache: Record<string, SellerTrendData>
  readonly trendLoading: string
  readonly trendError: Record<string, string>
  readonly loadSellerTrend: (itemId: string) => Promise<void>
}) {
  const d = useMemo(() => {
    const p = r.payload ?? {}
    const dimScores = p.dimension_scores as Record<string, unknown> | undefined
    const ruleDims: Array<[string, number]> = []
    if (dimScores) {
      for (const [k, v] of Object.entries(dimScores)) {
        if (k === 'ai_condition_eval') continue
        if (typeof v === 'number') ruleDims.push([k, v])
      }
    }
    return {
      description: p.item_description as string | undefined,
      imageUrls: p.image_urls as string[] | undefined,
      reviews: p.reviews as string[] | undefined,
      sellerCreditScore: p.seller_credit_score as number | undefined,
      sellerOnSaleCount: p.seller_on_sale_count as number | undefined,
      sellerSoldCount: p.seller_sold_count as number | undefined,
      sellerRegisterDays: p.seller_register_days as number | undefined,
      dataSource: p.data_source as string | undefined,
      aiEval: dimScores?.ai_condition_eval as Record<string, unknown> | undefined,
      ruleDims,
      rejectReasons: p.reject_reasons as string[] | undefined,
    }
  }, [r.payload])

  const hasDetail = d.description || d.imageUrls?.length || d.reviews?.length ||
    d.sellerCreditScore != null || d.sellerOnSaleCount != null ||
    d.sellerSoldCount != null || d.sellerRegisterDays != null ||
    d.aiEval || d.ruleDims.length > 0 || d.rejectReasons?.length || d.dataSource

  return (
    <div>
      {hasDetail && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          {(d.description || d.imageUrls?.length) && (
            <Col span={24}>
              <Card size="small" title="商品详情" style={{ marginBottom: 8 }}>
                {d.description && (
                  <div style={{ marginBottom: 8, color: 'var(--xh-text-secondary)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
                    {d.description}
                  </div>
                )}
                {d.imageUrls && d.imageUrls.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {d.imageUrls.slice(0, 6).map((url, i) => (
                      // S1077：img 必须有 alt 属性，装饰性图片用空字符串
                      <img key={`${url}-${i}`} src={url} width={80} height={80} alt={`商品图片${i + 1}`} style={{ objectFit: 'cover', borderRadius: 6 }} />
                    ))}
                  </div>
                )}
              </Card>
            </Col>
          )}
          {(d.sellerCreditScore != null || d.sellerOnSaleCount != null || d.sellerSoldCount != null || d.sellerRegisterDays != null) && (
            <Col xs={24} md={12}>
              <Card size="small" title="卖家信息" style={{ marginBottom: 8 }}>
                <Row gutter={[8, 8]}>
                  {d.sellerCreditScore != null && <Col span={12}>芝麻信用：{d.sellerCreditScore}</Col>}
                  {d.sellerRegisterDays != null && <Col span={12}>注册天数：{d.sellerRegisterDays} 天</Col>}
                  {d.sellerOnSaleCount != null && <Col span={12}>在售数：{d.sellerOnSaleCount}</Col>}
                  {d.sellerSoldCount != null && <Col span={12}>已售数：{d.sellerSoldCount}</Col>}
                </Row>
              </Card>
            </Col>
          )}
          {(d.ruleDims.length > 0 || d.rejectReasons?.length) && (
            <Col xs={24} md={12}>
              <Card size="small" title="评估维度" style={{ marginBottom: 8 }}>
                {d.ruleDims.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                    {d.ruleDims.map(([k, v]) => (
                      <Tag key={k} color="blue">{translateDimension(k)}: {v}</Tag>
                    ))}
                  </div>
                )}
                {d.rejectReasons && d.rejectReasons.length > 0 && (
                  <div style={{ fontSize: 12 }}>
                    <span style={{ color: 'var(--xh-text-tertiary)' }}>拒绝原因: </span>
                    {d.rejectReasons.map((reason, i) => (
                      <Tag key={`${reason}-${i}`} color="orange" style={{ fontSize: 11, marginBottom: 2 }}>{translateRejectReason(reason)}</Tag>
                    ))}
                  </div>
                )}
              </Card>
            </Col>
          )}
          {d.reviews && d.reviews.length > 0 && (
            <Col span={24}>
              <Card size="small" title={`评价/留言 (${d.reviews.length})`} style={{ marginBottom: 8 }}>
                {d.reviews.slice(0, 5).map((review, i) => (
                  <div key={`${review}-${i}`} style={{ padding: '4px 0', borderBottom: i < 4 ? '1px solid var(--xh-border-secondary)' : 'none', fontSize: 13 }}>
                    {review}
                  </div>
                ))}
                {d.reviews.length > 5 && (
                  <div style={{ color: 'var(--xh-text-tertiary)', fontSize: 12, marginTop: 4 }}>
                    还有 {d.reviews.length - 5} 条评价
                  </div>
                )}
              </Card>
            </Col>
          )}
          {d.dataSource && (
            <Col span={24}><Tag color="blue">数据来源: {d.dataSource}</Tag></Col>
          )}
        </Row>
      )}
      <TrendSparkline
        trend={trendCache[r.item_id]}
        loading={trendLoading === r.item_id}
        error={trendError[r.item_id]}
        onLoad={() => loadSellerTrend(r.item_id)}
      />
    </div>
  )
}

function AnalysisPanel({
  panelCollapsed, onExpand, dist, distRange, distLoading, onDistRangeChange,
  passScore, autoBuyScore, thresholdTarget, onThresholdTargetChange, fetchSuggestion, suggestion,
  thresholdValue, onThresholdValueChange, thresholdPassCount, thresholdPassRate, pageItemsCount, totalCount,
}: {
  readonly panelCollapsed: boolean
  readonly onExpand: () => void
  readonly dist: DistResponse | null
  readonly distRange: number
  readonly distLoading: boolean
  readonly onDistRangeChange: (v: number) => void
  readonly passScore: number
  readonly autoBuyScore: number
  readonly thresholdTarget: number
  readonly onThresholdTargetChange: (v: number) => void
  readonly fetchSuggestion: () => void
  readonly suggestion: { suggested_threshold: number; current_pass_rate: number } | null
  readonly thresholdValue: number
  readonly onThresholdValueChange: (v: number) => void
  readonly thresholdPassCount: number
  readonly thresholdPassRate: number
  /** 当前页条数：通过数/通过率只按这一页样本计算 */
  readonly pageItemsCount: number
  /** 当前筛选下的全量条数（分页接口 total），仅用于说明口径 */
  readonly totalCount: number
}) {
  if (panelCollapsed === true) {
    return <CollapsibleRail onExpand={onExpand} />
  }
  return (
    <>
      <EvalHeatmap dist={dist} distRange={distRange} distLoading={distLoading} onRangeChange={onDistRangeChange} />
      <ResultBarChart dist={dist} passScore={passScore} autoBuyScore={autoBuyScore} />
      <PriceHistogram dist={dist} />
      <Card title={<><AimOutlined style={{ marginRight: 6 }} />阈值建议</>} style={{ marginTop: 16 }}>
        {/* T02667：原先「目标通过率 + 建议阈值」在两张卡里各算一遍——一张按本页样本、
            一张走后端全量，两个结果可能不同。现在只保留后端全量口径这一处；
            本页样本只用于下面「当前阈值」的通过率计算，不再给出第二套建议阈值。 */}
        <div style={{ marginBottom: 8 }}>目标通过率：{thresholdTarget}%</div>
        <Slider value={thresholdTarget} onChange={onThresholdTargetChange} min={10} max={90} step={5} />
        <TipButton type="primary" icon={<AimOutlined />} onClick={fetchSuggestion} style={{ marginTop: 8 }} block tip="根据目标通过率计算建议评分阈值（后端按全部评估计算）">
          计算建议阈值
        </TipButton>
        {suggestion && (
          <div style={{ marginTop: 16, padding: 12, background: 'rgba(82, 196, 26, 0.08)', borderRadius: 4 }}>
            <div>建议阈值：<b style={{ color: '#52c41a' }}>{suggestion.suggested_threshold}</b></div>
            <div style={{ fontSize: 12, color: 'var(--xh-text-tertiary)' }}>当前通过率：{(suggestion.current_pass_rate * 100).toFixed(1)}%</div>
            <div style={{ fontSize: 12, color: 'var(--xh-text-tertiary)' }}>样本数：{dist?.total ?? '—'}（全部评估）</div>
          </div>
        )}
        <div style={{ marginTop: 20, paddingTop: 12, borderTop: '1px solid #f0f0f0' }}>
          <div style={{ marginBottom: 8 }}>阈值通过率计算器</div>
          <div style={{ marginBottom: 8 }}>
            当前阈值：<b style={{ color: '#1890ff' }}>{thresholdValue}</b> 分
          </div>
          <Slider value={thresholdValue} onChange={onThresholdValueChange} min={0} max={100} step={1}
            marks={{ 0: '0', 60: '60', 80: '80', 100: '100' }} />
          <Row gutter={16} style={{ marginTop: 12 }}>
            <Col span={8}>
              <Statistic title="本页通过率" value={thresholdPassRate.toFixed(1)} suffix="%"
                valueStyle={{ color: thresholdPassRate >= 60 ? '#52c41a' : '#ff4d4f' }} />
            </Col>
            <Col span={8}>
              <Statistic title="本页通过数" value={thresholdPassCount} valueStyle={{ color: '#1890ff' }} />
            </Col>
            <Col span={8}>
              <Statistic title="本页总数" value={pageItemsCount} />
            </Col>
          </Row>
          {/* 三项同为本页口径：若只把「总数」换成全量 total，通过数/通过率仍是本页，
              三个数会互相除不通。这里统一标「本页」，并单独说明筛选后全量有多少条。 */}
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--xh-text-tertiary)' }}>
            {totalCount > pageItemsCount
              ? `当前筛选共 ${totalCount} 条，以上只统计本页 ${pageItemsCount} 条，翻页后数值会变`
              : `以上按当前 ${pageItemsCount} 条评估统计`}
          </div>
        </div>
      </Card>
    </>
  )
}

// S3776 修复：把评估结果分类卡片提取为子组件
// 5 个 Card 共享 onClick + 条件 style 模式，用配置数组驱动消除 5 处内联 onClick + 5 处三元样式
function ResultCategoryCards({
  dist, passScore, autoBuyScore, resultCategory, onSelect,
}: {
  readonly dist: DistResponse
  readonly passScore: number
  readonly autoBuyScore: number
  readonly resultCategory: ResultCategory
  readonly onSelect: (category: ResultCategory) => void
}) {
  // 配置数组：5 个分类卡片共享同一渲染模板，只差 title/value/color
  // 为什么用数组：原 5 段几乎相同 JSX，每段含 onClick + 条件 style，配置化后子组件复杂度 ~3，主函数减 ~7
  // dist.total 只统计「有分数」的记录（分布图分母），insufficient_count 是 score=null 的那批；
  // 若直接展示 dist.total，「评估总数 39」会和下面 5 张卡之和 40、分页「共 40 条」对不上
  const cards: ReadonlyArray<{
    readonly key: ResultCategory
    readonly title: string
    readonly value: number
    readonly valueStyle?: { color: string }
    readonly activeColor: string
    readonly activeBg: string
  }> = [
    { key: null, title: '评估总数', value: dist.total + dist.insufficient_count, activeColor: '#1890ff', activeBg: 'rgba(24,144,255,0.06)' },
    { key: 'auto', title: `可抢(≥${autoBuyScore})`, value: dist.marginals.result.auto, valueStyle: { color: '#52c41a' }, activeColor: '#52c41a', activeBg: 'rgba(82,196,26,0.06)' },
    { key: 'pass', title: `通过(${passScore}-${autoBuyScore - 1})`, value: dist.marginals.result.pass, valueStyle: { color: '#1890ff' }, activeColor: '#1890ff', activeBg: 'rgba(24,144,255,0.06)' },
    { key: 'fail', title: `驳回(<${passScore})`, value: dist.marginals.result.fail, valueStyle: { color: '#ff4d4f' }, activeColor: '#ff4d4f', activeBg: 'rgba(255,77,79,0.06)' },
    { key: 'insufficient', title: '数据不足', value: dist.insufficient_count, valueStyle: { color: 'var(--xh-text-tertiary)' }, activeColor: '#faad14', activeBg: 'rgba(250,173,20,0.06)' },
  ]
  return (
    <Row gutter={[12, 12]} style={{ marginBottom: 16 }} className="evals-stats-row">
      {cards.map((c) => (
        <Col xs={12} md={8} xl={4} key={String(c.key)}>
          <Card
            size="small" hoverable
            onClick={() => onSelect(c.key)}
            role="button"
            tabIndex={0}
            aria-pressed={resultCategory === c.key}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(c.key)
              }
            }}
            style={resultCategory === c.key ? { borderColor: c.activeColor, background: c.activeBg } : {}}
          >
            <Statistic title={c.title} value={c.value} valueStyle={c.valueStyle} />
          </Card>
        </Col>
      ))}
      <Col xs={12} md={8} xl={4}>
        {/* 价格区间只展示统计，不参与筛选：去掉 hoverable 的点击暗示，
            六张卡里只有这一张点了没反应 */}
        <Card size="small" className="evals-price-range-card">
          <Statistic
            title="价格区间"
            value={dist.price_range[0] > 0 ? `¥${dist.price_range[0]}~${dist.price_range[1]}` : '—'}
            valueStyle={{ fontSize: 22 }}
          />
        </Card>
      </Col>
    </Row>
  )
}

// S3776 修复：批量选择 Alert 提取为子组件
// 原 JSX 含 5 处三元（batchCollecting ? collectProgress : batchProgress）+ 1 处 && 条件渲染
// 提取后用单个 progress 变量替代 3 处三元，主函数减少 1 处 && 条件渲染
function BatchSelectionAlert({
  selectedCount, batchAIEvaluating, batchCollecting,
  batchProgress, batchCollectProgress,
  onBatchAIEval, onBatchCollectOfficial, onClearSelection,
}: {
  readonly selectedCount: number
  readonly batchAIEvaluating: boolean
  readonly batchCollecting: boolean
  readonly batchProgress: { done: number; total: number }
  readonly batchCollectProgress: { done: number; total: number }
  readonly onBatchAIEval: () => void
  readonly onBatchCollectOfficial: () => void
  readonly onClearSelection: () => void
}) {
  // 必须在 selectedCount 早退之前调用（规则 1：Hook 不得有条件调用）
  const aiOff = useAIEnabled().enabled === false
  if (selectedCount === 0) return null
  // 进度数据选择：批量采集进行中显示采集进度，否则显示 AI 评估进度
  const progress = batchCollecting ? batchCollectProgress : batchProgress
  const showProgress = batchAIEvaluating || batchCollecting
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 12 }}
      message={
        <Space>
          <span>已选择 <b>{selectedCount}</b> 项</span>
          <TipButton type="primary" size="small" icon={<RobotOutlined />} loading={batchAIEvaluating} onClick={onBatchAIEval} tip={aiOff ? AI_GATE_OFF_TIP : '对选中商品批量执行 AI 成色评估'}>
            批量 AI 评估 ({selectedCount} 项)
          </TipButton>
          <TipButton size="small" icon={<CloudDownloadOutlined />} loading={batchCollecting} onClick={onBatchCollectOfficial} tip="对选中商品批量访问官方页面采集">
            批量官方采集 ({selectedCount} 项)
          </TipButton>
          <TipButton size="small" onClick={onClearSelection} tip="取消已选中的商品">取消选择</TipButton>
        </Space>
      }
      description={showProgress && (
        <Progress
          percent={Math.round((progress.done / progress.total) * 100)}
          size="small"
          format={() => `${progress.done}/${progress.total}`}
        />
      )}
    />
  )
}

// 筛选标签与控件必须作为一个整体换行。原先直接塞进 Space 的 span+控件会被
// 拆成两个独立 flex item，1024/768 下出现「任务ID：」独占一行、输入框掉到下一行。
function FilterItem({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="evals-filter-item">
      <span className="evals-filter-label">{label}：</span>
      {children}
    </div>
  )
}

// 本页独立请求的数据源（T02899）：任务列表喂筛选下拉，
// 原先把 reject 吞成空块后，「没有任务」与「后端挂了」是同一个画面。
type EvaluationsSource = 'tasks'

const SOURCE_LABELS: Record<EvaluationsSource, string> = {
  tasks: '任务列表',
}

// S3776：深链提示的可见性判定提到模块级，主组件不再内联复合布尔表达式
function isDeepLinkVisible(itemId: string | undefined, scoreMax: number | undefined, applied: boolean): boolean {
  return Boolean(itemId || scoreMax != null) && applied
}

// S3358/S4624/S7735：文案用 if 分支拼装，避免嵌套三元与嵌套模板字符串
function buildDeepLinkMessage(itemId: string | undefined, scoreMax: number | undefined, matches: boolean): string {
  const idText = itemId ?? '—'
  if (matches) {
    if (scoreMax == null) return `已按告警定位到商品 ${idText}`
    return `已按告警定位到商品 ${idText}（评分 ≤ ${scoreMax}）`
  }
  if (scoreMax == null) {
    return `链接里的告警定位条件（商品 ${idText}）已被手动筛选覆盖，下方列表按当前筛选条件展示`
  }
  return `链接里的告警定位条件（商品 ${idText} / 评分 ≤ ${scoreMax}）已被手动筛选覆盖，下方列表按当前筛选条件展示`
}

// S3358：空态文案改用 if/else，避免嵌套三元
function resolveEvalEmptyDescription(loadError: string | null, hasActiveFilter: boolean): string {
  if (loadError) return '评估数据加载失败，请使用上方“重新加载”'
  if (hasActiveFilter) return '没有符合筛选条件的评估记录'
  return '暂无评估数据'
}

export default function Evaluations() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  // 告警深链（T02620）：抽屉的「低分评估」项跳过来带 ?item_id=<id>&score_max=<0-100>，
  // 本页必须真的读取，否则链接只是「跳过来自己找」——与 Orders.tsx 读 ?ref= 同一族。
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const deepLink = useMemo(() => parseEvalDeepLink(searchParams), [searchParams])
  const filters = useEvalFilters()
  const list = useEvalList({
    itemId: filters.itemId,
    taskId: filters.taskId,
    scoreRange: filters.scoreRange,
    dateRange: filters.dateRange,
    brandFilter: filters.brandFilter,
    soldFilter: filters.soldFilter,
    resultCategory: filters.resultCategory,
    priceRange: filters.priceRange,
    includeOutOfRange: filters.includeOutOfRange,
  })
  const dist = useEvalDist(list.items, {
    taskId: filters.taskId,
    priceRange: filters.priceRange,
    includeOutOfRange: filters.includeOutOfRange,
  })
  const ai = useEvalAI()
  const collect = useEvalCollect(list.load)
  const batch = useEvalBatch(list.items, filters.taskId, list.load, dist.loadDist)
  const trend = useEvalTrend()
  const feedback = useEvalFeedback(list.setItems)

  // 深链落地即套用筛选：只做一次（用户之后手动改筛选不再被 URL 覆盖）。
  // 必须「先写筛选 state，再在下一轮渲染后 reload」两步走：useEvalList 通过
  // filtersRef 读快照，同一轮里直接 load() 会拿到旧筛选条件（测试实测过一次）。
  // reloadToken 就是「渲染已追平」的信号，清除定位与深链套用共用这条通道。
  const deepLinkApplied = useRef(false)
  const [reloadToken, setReloadToken] = useState(0)
  useEffect(() => {
    if (deepLinkApplied.current) return
    if (!deepLink.itemId && deepLink.scoreMax == null) return
    deepLinkApplied.current = true
    if (deepLink.itemId) filters.setItemId(deepLink.itemId)
    if (deepLink.scoreMax != null) filters.setScoreRange([0, deepLink.scoreMax])
    setReloadToken((t) => t + 1)
    // 依赖刻意留空：本效果只在挂载时消费一次 URL，重复执行会覆盖用户手动筛选
  }, [])
  useEffect(() => {
    if (reloadToken === 0) return
    list.setPage(1)
    list.load()
  }, [reloadToken])

  // 清除深链：去掉 URL 里的 item_id/score_max，并把筛选复位后重新加载
  const clearDeepLink = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('item_id')
    next.delete('score_max')
    setSearchParams(next, { replace: true })
    filters.setItemId('')
    filters.setScoreRange([0, 100])
    deepLinkApplied.current = true
    setReloadToken((t) => t + 1)
  }

  // 深链参数是否仍然是当前筛选的真实描述（T02831），判定在 utils/alertDeepLink 里，
  // 好让「只带 score_max」「商品ID 被改掉」这些分支能被单元测试逐个钉住。
  // 首帧筛选还没套用（套用要等下一轮渲染），故 deepLinkApplied 之前不出提示条，
  // 免得把「马上要套用」闪成「已被覆盖」。
  const deepLinkVisible = isDeepLinkVisible(deepLink.itemId, deepLink.scoreMax, deepLinkApplied.current)
  const deepLinkMatchesFilters = evalDeepLinkMatchesFilters(deepLink, {
    itemId: filters.itemId,
    scoreRange: filters.scoreRange,
  })

  const columns = useEvalColumns({
    autoBuyScore: dist.autoBuyScore,
    passScore: dist.passScore,
    collecting: collect.collecting,
    feedbackSubmitting: feedback.feedbackSubmitting,
    manualTaking: batch.manualTaking,
    onAIEval: ai.onAIEval,
    onDeepAnalyze: ai.onDeepAnalyze,
    onCollectOfficial: collect.onCollectOfficial,
    onFeedback: feedback.onFeedback,
    onManualTakeover: batch.onManualTakeover,
    onTitleClick: batch.onTitleClick,
  })

  const [columnConfigOpen, setColumnConfigOpen] = useState(false)
  const {
    order: columnOrder,
    hidden: hiddenColumns,
    toggleHidden: toggleColumnHidden,
    moveColumn: moveColumnOrder,
    reset: resetColumnConfig,
    applyConfig: applyColumnConfig,
  } = useColumnConfig('xh.evals.columns', COLUMN_DEFINITIONS)

  // 1280/1440 笔记本上「分析面板展开」会把 19 列表格压到 ~587px（实测 1280），
  // 表格要横向滚 2436px 才能看到标题/评分；窄屏默认收起，宽屏仍保持原来的展开态。
  // 用 matchMedia 而不是 globalThis.innerWidth：jsdom 测试环境没有真实视口，不会误触发。
  const defaultPanelCollapsed = typeof globalThis !== 'undefined'
    && typeof globalThis.matchMedia === 'function'
    && globalThis.matchMedia('(max-width: 1599px)').matches
  const [panelCollapsed, setPanelCollapsed] = usePersistentState<boolean>(
    'xh.evals.panelCollapsed', defaultPanelCollapsed,
    { validator: (v): v is boolean => typeof v === 'boolean' },
  )

  const { isFailed, track } = useFailedSources<EvaluationsSource>()

  // track 内部 mark 是稳定引用，这里不依赖 filters（它的对象每次渲染都换，进了依赖会自触发无限请求）
  const loadTasks = useCallback(() => {
    void track('tasks', taskApi.list({ limit: 200 })).then((res) => {
      if (res) filters.setTasks(res.items || [])
    })
  }, [track]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadTasks()
  }, [loadTasks])

  const onSearch = () => {
    if (list.page === 1) {
      list.load()
    } else {
      list.setPage(1)
    }
  }

  // T02669：筛选时机统一——非文本控件改完 300ms 后自动查，文本框仍走回车/失焦，
  // 「查询」按钮保留为立即执行（取消尚未到点的自动查询，马上查一次）。
  const autoSearch = useDebouncedAction(onSearch)

  // 商品ID 是文本框：只在「确实改过」时查询，否则单纯点进去又点出来也会发一次请求
  const itemIdAtLastQuery = useRef(filters.itemId)
  const queryItemId = () => {
    if (filters.itemId === itemIdAtLastQuery.current) return
    itemIdAtLastQuery.current = filters.itemId
    autoSearch.flush()
  }

  const onReset = () => {
    autoSearch.cancel()
    filters.reset()
    // 不能在这里直接 list.load()：filters 是同一轮里的旧快照，load 会带着刚清掉的
    // 条件再查一次（实测点「清空筛选条件」后仍 0 条）。统一走 page=1 + reloadToken，
    // 等下一轮渲染 filtersRef 更新后再发请求；page>1 时也能保证请求的是第 1 页。
    list.setPage(1)
    setReloadToken((t) => t + 1)
  }

  const [viewportWidth, setViewportWidth] = useState<number>(() => (
    typeof globalThis === 'undefined' ? 1280 : globalThis.innerWidth
  ))
  useEffect(() => {
    const onResize = () => setViewportWidth(globalThis.innerWidth)
    globalThis.addEventListener('resize', onResize)
    return () => globalThis.removeEventListener('resize', onResize)
  }, [])
  const isNarrow = viewportWidth <= 900
  const isCompact = viewportWidth <= 1200
  const isNarrowAnalysis = viewportWidth <= 1599

  const adaptedColumns = useMemo(() => {
    let widths = WIDE_WIDTHS
    if (isNarrow) widths = NARROW_WIDTHS
    else if (isCompact) widths = COMPACT_WIDTHS
    return columns.map((col) => {
      const width = widths[String(col.key ?? '')]
      return width ? { ...col, width } : col
    })
  }, [columns, isNarrow, isCompact])

  const visibleColumns = applyColumnConfig(adaptedColumns)

  // 品牌候选优先用后端全量 DISTINCT；请求失败静默回落当页提取（与商品页同口径，T02662）
  const [brandFacets, setBrandFacets] = useState<string[]>([])
  useEffect(() => {
    let cancelled = false
    evalApi.brandFacets(filters.taskId || undefined)
      .then((brands) => { if (!cancelled) setBrandFacets(brands) })
      .catch(() => { if (!cancelled) setBrandFacets([]) })
    return () => { cancelled = true }
  }, [filters.taskId])

  const pageBrands = [...new Set(
    list.items.map((i) => i.payload?.brand).filter(Boolean)
  )].map(String)
  const brandOptions = (brandFacets.length ? brandFacets : pageBrands)
    .sort((a, b) => a.localeCompare(b))

  // 评审 2.4-2：重新评估原先静默一键触发全库重算，这里把「会重算什么范围」显式化。
  // 范围口径与后端一致（routes/evaluations_recompute.py:361）：按 user_id + 可选 task_id
  // 取 eval.* 事件重算，不受当前时间窗/价格筛选影响，故描述里不引用这两个筛选值。
  const selectedTaskName = filters.tasks.find((t) => t.id === filters.taskId)?.name
  const selectedTask = filters.tasks.find((t) => t.id === filters.taskId)
  // 选中任务后价格区间会自动带出任务的 min/max（useEvalFilters），这不是用户主动筛选。
  // 空态判断要把它排除，否则任务本身没有评估数据时会被误判成「筛选没命中」。
  const priceFilterActive = (filters.priceRange[0] != null
    && filters.priceRange[0] !== (selectedTask?.min_price ?? null))
    || (filters.priceRange[1] != null
      && filters.priceRange[1] !== (selectedTask?.max_price ?? null))
  const recomputeScope = filters.taskId
    ? `任务「${selectedTaskName || filters.taskId}」下的历史评估记录`
    : '当前账号全部历史评估记录（未选择任务）'

  // 空态要区分「筛选没命中」和「任务本来没有评估数据」：
  // 旧实现两者都写「暂无评估数据」，用户搜了个不存在的商品 ID 会以为数据丢了。
  // taskId 是页面上下文，不算筛选条件（否则任何任务空态都会被判成筛选无结果）。
  const hasActiveFilter = Boolean(
    filters.itemId
    || filters.scoreRange[0] > 0 || filters.scoreRange[1] < 100
    || filters.dateRange
    || filters.brandFilter
    || filters.soldFilter !== 'all'
    || priceFilterActive
    || filters.includeOutOfRange
    || filters.resultCategory,
  )

  const renderExpandedRow = (r: EvalItem) => (
    <ExpandedDetail
      r={r}
      trendCache={trend.trendCache}
      trendLoading={trend.trendLoading}
      trendError={trend.trendError}
      loadSellerTrend={trend.loadSellerTrend}
    />
  )

  const analysisPanelNode = (collapsed: boolean) => (
    <AnalysisPanel
      panelCollapsed={collapsed}
      onExpand={() => setPanelCollapsed(false)}
      dist={dist.dist}
      distRange={dist.distRange}
      distLoading={dist.distLoading}
      onDistRangeChange={dist.setDistRange}
      passScore={dist.passScore}
      autoBuyScore={dist.autoBuyScore}
      thresholdTarget={dist.thresholdTarget}
      onThresholdTargetChange={dist.setThresholdTarget}
      fetchSuggestion={dist.fetchSuggestion}
      suggestion={dist.suggestion}
      thresholdValue={dist.thresholdValue}
      onThresholdValueChange={dist.setThresholdValue}
      thresholdPassCount={dist.thresholdPassCount}
      thresholdPassRate={dist.thresholdPassRate}
      pageItemsCount={list.items.length}
      totalCount={list.total}
    />
  )

  return (
    <div className="page-container evals-page" data-testid="evaluations-page">
      <header className="evals-page-header">
        <div className="evals-page-heading">
          <h1>评估明细</h1>
          <div className="evals-page-subtitle">复核商品评分、风险与结果分布，支持按任务、价格和时间筛选</div>
        </div>
        <TipButton
          className="evals-analysis-toggle"
          icon={panelCollapsed ? <RightOutlined /> : <LeftOutlined />}
          aria-label={panelCollapsed ? '展开分析面板' : '收起分析面板'}
          onClick={() => setPanelCollapsed(!panelCollapsed)}
          tip={panelCollapsed ? '展开分析面板' : '收起分析面板'}
        >
          {panelCollapsed ? '展开分析' : '收起分析'}
        </TipButton>
      </header>
      {/* 深链落地说明（T02620）：让用户知道当前视图是「告警带来的筛选」而非全量列表，
          并提供一键回到正常筛选——与 Orders.tsx 的 ref 定位提示同构。
          T02831：这条提示描述的是「URL 参数 = 当前筛选」，用户手动改过筛选之后参数就不再是
          当前视图的真实描述，所以按是否仍一致分两种文案；不自动清参数——清掉会把用户
          点进来的那条线索直接抹掉，降级文案至少还说明差在哪。 */}
      {deepLinkVisible && (
        <Alert
          type={deepLinkMatchesFilters ? 'info' : 'warning'}
          showIcon
          style={{ marginBottom: 12 }}
          message={buildDeepLinkMessage(deepLink.itemId, deepLink.scoreMax, deepLinkMatchesFilters)}
          action={<TipButton size="small" onClick={clearDeepLink} tip="清除告警深链参数，回到正常筛选视图">清除定位</TipButton>}
        />
      )}
      <Card style={{ marginBottom: 16 }} styles={{ body: { padding: 16 } }} className="evals-filter-card">
        {/* 17 个筛选项在 1280 下原本占 4 行 / 235px；收紧行内与行间距，尽量压到 3 行，
            给下方 19 列表格多留可视高度（不改控件尺寸，避免小屏触控目标过小） */}
        <Space wrap size={[8, 8]}>
          <FilterItem label="商品ID">
            <Input
              placeholder="模糊匹配" allowClear style={{ width: 160 }}
              aria-label="按商品 ID 筛选"
              value={filters.itemId} onChange={(e) => filters.setItemId(e.target.value)}
              onPressEnter={queryItemId}
              onBlur={queryItemId}
            />
          </FilterItem>
          <FilterItem label="任务ID">
            <Select
              placeholder="选择任务" allowClear showSearch
              aria-label="按任务筛选"
              style={{ width: 260 }}
              value={filters.taskId || undefined}
              onChange={(v) => { filters.setTaskId(v || ''); autoSearch.schedule() }}
              onClear={() => filters.setTaskId('')}
              filterOption={(input, option) =>
                (option?.label as string ?? '').toLowerCase().includes(input.toLowerCase())
              }
              options={filters.tasks.map((t) => ({
                label: `${t.name}（${t.keyword}）`,
                value: t.id,
              }))}
              notFoundContent="暂无任务"
            />
          </FilterItem>
          <FilterItem label="评分范围">
            <Slider
              range style={{ width: 200 }}
              ariaLabelForHandle={['最低评分', '最高评分']}
              value={filters.scoreRange}
              onChange={(v) => { filters.setScoreRange(v as [number, number]); autoSearch.schedule() }}
            />
          </FilterItem>
          <FilterItem label="时间范围">
            <RangePicker
              aria-label="按时间范围筛选"
              value={filters.dateRange as [dayjs.Dayjs, dayjs.Dayjs] | null}
              onChange={(v) => { filters.setDateRange(v ? [v[0]?.toISOString() ?? '', v[1]?.toISOString() ?? ''] : null); autoSearch.schedule() }}
            />
          </FilterItem>
          <FilterItem label="品牌">
            <Select
              placeholder="选择品牌"
              allowClear
              showSearch
              aria-label="按品牌筛选"
              style={{ width: 160 }}
              value={filters.brandFilter}
              onChange={(v) => { filters.setBrandFilter(v || ''); autoSearch.schedule() }}
              onClear={() => filters.setBrandFilter('')}
              filterOption={(input, option) =>
                (option?.label as string ?? '').toLowerCase().includes(input.toLowerCase())
              }
              options={brandOptions.map((b) => ({ label: b, value: b }))}
              notFoundContent="暂无品牌"
            />
          </FilterItem>
          <FilterItem label="状态">
            <Select
              placeholder="状态筛选"
              aria-label="按商品状态筛选"
              style={{ width: 100 }}
              allowClear
              value={filters.soldFilter}
              onChange={(v) => { filters.setSoldFilter(v || 'all'); autoSearch.schedule() }}
              options={[
                { label: '全部', value: 'all' },
                { label: '在售', value: 'onsale' },
                { label: '已售', value: 'sold' },
              ]}
            />
          </FilterItem>
          <div className="evals-filter-item evals-price-filter">
            <Tooltip
              title={
                <div style={{ lineHeight: 1.6 }}>
                  <div><b>查询规则：</b></div>
                  <div>1. 价格区间：商品价格需在 [最低, 最高] 范围内</div>
                  <div>2. 低于市场参考价：选中任务后，按任务 market_ratio 配置过滤掉价格高于「同任务商品中位数 × market_ratio」的商品（样本数 ≥ 3 才生效）</div>
                  <div style={{ marginTop: 4, color: '#aaa' }}>提示：勾选「显示超范围」可跳过市场参考价过滤，用于审计历史商品</div>
                  <div style={{ marginTop: 4, color: '#aaa' }}>改完条件约 300ms 后自动查询；商品ID 要回车或移出输入框才生效，「查询」按钮随时可立即执行</div>
                </div>
              }
            >
            <span className="evals-filter-label" style={{ cursor: 'help', borderBottom: '1px dashed currentColor' }}>
                价格范围：
                <QuestionCircleOutlined style={{ marginLeft: 2, fontSize: 12, color: '#999' }} />
              </span>
            </Tooltip>
            <InputNumber
              placeholder="最低"
              aria-label="最低价格"
              min={0}
              style={{ width: 90 }}
              value={filters.priceRange[0]}
              onChange={(v) => { filters.setPriceRange([v ?? null, filters.priceRange[1]]); autoSearch.schedule() }}
            />
            <span>-</span>
            <InputNumber
              placeholder="最高"
              aria-label="最高价格"
              min={0}
              style={{ width: 90 }}
              value={filters.priceRange[1]}
              onChange={(v) => { filters.setPriceRange([filters.priceRange[0], v ?? null]); autoSearch.schedule() }}
            />
          </div>
          <div className="evals-filter-item">
            <Tooltip title="开启后显示超出任务价格范围的历史商品（审计用）">
              <Checkbox
                checked={filters.includeOutOfRange}
                onChange={(e) => { filters.setIncludeOutOfRange(e.target.checked); autoSearch.schedule() }}
              >
                显示超范围
              </Checkbox>
            </Tooltip>
          </div>
          <TipButton type="primary" icon={<SearchOutlined />} onClick={autoSearch.flush} tip="立即按当前筛选条件查询（改完条件约 300ms 后也会自动查一次）">查询</TipButton>
          <TipButton icon={<UndoOutlined />} onClick={onReset} tip="重置全部筛选条件">重置</TipButton>
          <TipButton icon={<ReloadOutlined />} onClick={list.load} loading={list.loading} tip="重新加载评估列表数据">刷新</TipButton>
          <Popconfirm
            title="确认重新评估？"
            description={(
              <div style={{ maxWidth: 340 }}>
                <div>重算范围：{recomputeScope}</div>
                <div>
                  将用当前评估规则重新打分并覆盖这些记录的评估结果，覆盖后无法恢复原分值。
                </div>
                <div>
                  重算不受上方时间窗与价格筛选限制；不调用 AI、不产生接口费用，也不发送通知。
                </div>
                <div style={{ color: 'var(--xh-text-secondary)' }}>
                  实际处理条数将在完成后提示。
                </div>
              </div>
            )}
            okText="确认重算"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            placement="bottomRight"
            onConfirm={batch.onRecompute}
          >
            <TipButton
              icon={<RetweetOutlined />}
              loading={batch.recomputing}
              tip="用当前规则重新计算评估分值，点击后需二次确认"
            >
              重新评估
            </TipButton>
          </Popconfirm>
          <TipButton icon={<RetweetOutlined />} onClick={batch.onBatchEvaluateUnevaluated} loading={batch.batchEvaluating} tip="对尚未评估的商品批量评估">批量评估未评估商品</TipButton>
          <TipButton icon={<SettingOutlined />} onClick={() => setColumnConfigOpen(true)} tip="打开列显示与排序配置">列配置</TipButton>
          <ExportButton dataset="evaluations" params={{ task_id: filters.taskId || undefined }} />
        </Space>
      </Card>

      {isFailed('tasks') && (
        <FetchFailedHint
          source={SOURCE_LABELS.tasks}
          onRetry={loadTasks}
          description="任务下拉的选项取自该接口。失败时列表仍是上次取到的数据，但按任务筛选暂时用不了。"
        />
      )}
      {dist.distFailed && (
        <FetchFailedHint
          source={dist.distSourceLabel}
          onRetry={dist.loadDist}
          description="下方分布统计卡与阈值面板整块取不到数据——这是「没取到」，不是「近 7 天没有评估记录」。"
        />
      )}

      {dist.dist && dist.dist.insufficient_count > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`有 ${dist.dist.insufficient_count} 条评估记录因卖家信息缺失仅基于价格评估`}
          description={(
            <div>
              <div>建议先在 Dashboard 页面重新登录闲鱼，登录成功后再回来点上方「重新评估」获取完整结果。</div>
              <div style={{ color: 'var(--xh-text-secondary)' }}>
                重新评估只按当前规则重算分值：不调用 AI、不产生接口费用，也不发送通知。
              </div>
            </div>
          )}
          action={(
            <TipButton
              icon={<LoginOutlined />}
              tip="前往登录页刷新闲鱼登录态，登录后回到评估明细"
              onClick={() => navigate(`/login?redirect=${encodeURIComponent('/evaluations')}`)}
            >
              前往登录
            </TipButton>
          )}
        />
      )}

      {dist.dist && (
        <ResultCategoryCards
          dist={dist.dist}
          passScore={dist.passScore}
          autoBuyScore={dist.autoBuyScore}
          resultCategory={filters.resultCategory}
          onSelect={(cat) => { filters.toggleResultCategory(cat); list.setPage(1); setTimeout(list.load, 0) }}
        />
      )}

      <Row gutter={[16, 16]} className="evals-main-row">
        <Col xs={24} xxl={panelCollapsed ? 23 : 16} className="evals-table-col">
          <Card style={{ transition: 'all 0.2s ease' }} className="evals-list-card">
            {list.loadError && (
              <Alert
                type="error"
                showIcon
                className="evals-load-error"
                style={{ marginBottom: 12 }}
                message="评估列表加载失败"
                description={list.loadError}
                action={(
                  <TipButton
                    icon={<ReloadOutlined />}
                    loading={list.loading}
                    onClick={list.load}
                    aria-label="重新加载评估列表"
                    tip="重新请求评估列表"
                  >
                    重新加载
                  </TipButton>
                )}
              />
            )}
            <BatchSelectionAlert
              selectedCount={batch.selectedRowKeys.length}
              batchAIEvaluating={batch.batchAIEvaluating}
              batchCollecting={batch.batchCollecting}
              batchProgress={batch.batchProgress}
              batchCollectProgress={batch.batchCollectProgress}
              onBatchAIEval={batch.onBatchAIEval}
              onBatchCollectOfficial={batch.onBatchCollectOfficial}
              onClearSelection={() => batch.setSelectedRowKeys([])}
            />
            <Spin spinning={list.loading}>
              {list.items.length === 0 && (
                <Empty description={resolveEvalEmptyDescription(list.loadError, hasActiveFilter)}>
                  {!list.loadError && hasActiveFilter && (
                    <TipButton tip="清空商品ID、评分/时间/品牌/状态/价格筛选与结果分类，回到全部评估" onClick={onReset}>
                      清空筛选条件
                    </TipButton>
                  )}
                </Empty>
              )}
              {list.items.length > 0 && (
                <Table
                  columns={visibleColumns}
                  dataSource={list.items}
                  rowKey={(r) => `${r.item_id}-${r.created_at}`}
                  size="middle"
                  tableLayout="fixed"
                  rowSelection={{
                    selectedRowKeys: batch.selectedRowKeys,
                    onChange: (keys) => batch.setSelectedRowKeys(keys),
                  }}
                  scroll={{ x: '100%' }}
                  // 与列表同口径：排序只在当前页 dataSource 内生效，分页在后端
                  showSorterTooltip={{ title: '仅对当前页排序；跨页顺序仍由后端分页决定' }}
                  expandable={{
                    expandedRowRender: renderExpandedRow,
                    rowExpandable: () => true,
                  }}
                  pagination={{
                    current: list.page,
                    pageSize: list.pageSize,
                    total: list.total,
                    showSizeChanger: !isNarrow,
                    showQuickJumper: !isNarrow,
                    pageSizeOptions: ['10', '20', '50', '100'],
                    showLessItems: true,
                    onChange: (p, ps) => { list.setPage(p); list.setPageSize(ps) },
                    showTotal: (t) => filters.resultCategory ? `过滤后 ${t} 条` : `共 ${t} 条`,
                  }}
                />
              )}
            </Spin>
          </Card>
        </Col>

        {!isNarrowAnalysis && (
          <Col
            xxl={panelCollapsed ? 1 : 8}
            className={`evals-analysis-col ${panelCollapsed ? 'is-collapsed' : 'is-expanded'}`}
          >
            {analysisPanelNode(panelCollapsed)}
          </Col>
        )}
      </Row>

      {/* <1600px 用 Drawer 承载分析：表格保持整行宽度，展开时图表覆盖在表格上方，
          关闭后立即回到列表；不再把表格压到 587px，也不用把 1500px 高的分析块塞进表格上方。 */}
      {isNarrowAnalysis && (
        <Drawer
          title="评估分析"
          placement="right"
          width={isNarrow ? '100%' : 520}
          open={!panelCollapsed}
          onClose={() => setPanelCollapsed(true)}
        >
          {analysisPanelNode(false)}
        </Drawer>
      )}

      <AIEvalModal
        open={ai.aiModalOpen}
        loading={ai.aiLoading}
        result={ai.aiResult}
        itemId={ai.aiItemId}
        onCancel={() => ai.setAiModalOpen(false)}
      />

      <DeepAnalyzeModal
        open={ai.deepModalOpen}
        loading={ai.deepLoading}
        result={ai.deepResult}
        itemId={ai.deepItemId}
        onCancel={() => ai.setDeepModalOpen(false)}
      />

      <CollectResultModal
        open={collect.collectModalOpen}
        result={collect.collectResult}
        onCancel={() => collect.setCollectModalOpen(false)}
      />

      <ColumnSettingsModal
        open={columnConfigOpen}
        onClose={() => setColumnConfigOpen(false)}
        definitions={COLUMN_DEFINITIONS}
        order={columnOrder}
        hidden={hiddenColumns}
        onToggleHidden={toggleColumnHidden}
        onMove={moveColumnOrder}
        onReset={resetColumnConfig}
      />
    </div>
  )
}
