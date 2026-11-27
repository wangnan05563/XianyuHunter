import type { EChartsCoreOption } from 'echarts/core'

/** 四维评估权重 */
export interface EvalWeights {
  professional: number
  credit: number
  dispute: number
  price: number
}

/** 评估分布二维桶 */
export interface EvalDistributionBucket {
  count: number
  pass: number
  auto: number
  fail: number
}

/** GET /api/evaluations/distribution 的页面消费结构 */
export interface EvalDistributionData {
  buckets: EvalDistributionBucket[][]
  price_range: [number, number]
  total: number
}

/** 热力图绘图数据与轴标签 */
export interface HeatmapCompute {
  data: Array<[number, number, number]>
  max: number
  xLabels: string[]
  yLabels: string[]
  pricedTotal: number
  hasPriceData: boolean
}

/** 与主题无关的 ECharts 颜色契约，由页面从 antd token 注入 */
export interface EvalChartPalette {
  axis: string
  axisLabel: string
  splitAreaA: string
  splitAreaB: string
  tooltipBg: string
  tooltipBorder: string
  tooltipText: string
  cellLabel: string
  cellLabelBorder: string
  primary: string
  visual: string[]
}

export const EMPTY_HEATMAP: HeatmapCompute = {
  data: [],
  max: 1,
  xLabels: [],
  yLabels: [],
  pricedTotal: 0,
  hasPriceData: false,
}

const WEIGHT_KEYS = ['professional', 'credit', 'dispute', 'price'] as const

export function sumWeights(weights: EvalWeights): number {
  return weights.professional + weights.credit + weights.dispute + weights.price
}

/**
 * 精确归一化权重总和为 100。
 * 先按比例向下取整，再把剩余的 1~3 个点按小数余量从大到小补齐；
 * 避免原实现逐项 Math.round 后出现 99/101，却仍提示“已归一化为 100”。
 */
export function normalizeWeights(weights: EvalWeights): EvalWeights | null {
  const total = sumWeights(weights)
  if (!Number.isFinite(total) || total <= 0) return null

  const raw = WEIGHT_KEYS.map((key) => {
    const weighted = Math.max(0, weights[key]) * 100 / total
    return { key, floor: Math.floor(weighted), fraction: weighted - Math.floor(weighted) }
  })
  let remainder = 100 - raw.reduce((sum, item) => sum + item.floor, 0)

  raw.sort((a, b) => b.fraction - a.fraction)
  const next = { ...weights }
  for (const item of raw) {
    const extra = remainder > 0 ? 1 : 0
    next[item.key] = item.floor + extra
    remainder -= extra
  }
  return next
}

/** 保存前的统一校验；页面用它同时控制提示、保存按钮与错误态 */
export function getEvalValidationError(
  weightsTotal: number,
  passScore: number,
  autoBuyScore: number,
): string | null {
  if (weightsTotal !== 100) return `权重总和必须为 100（当前为 ${weightsTotal}）`
  if (passScore > autoBuyScore) return '通过分数不能大于自动抢单分数'
  return null
}

export function isEvalDistributionData(value: unknown): value is EvalDistributionData {
  if (!value || typeof value !== 'object') return false
  const data = value as Partial<EvalDistributionData>
  if (!Array.isArray(data.buckets) || typeof data.total !== 'number') return false
  if (!Array.isArray(data.price_range) || data.price_range.length !== 2) return false
  if (!data.price_range.every((v) => typeof v === 'number' && Number.isFinite(v))) return false
  return data.buckets.every((row) => Array.isArray(row) && row.every((bucket) => (
    bucket
    && typeof bucket.count === 'number'
    && typeof bucket.pass === 'number'
    && typeof bucket.auto === 'number'
    && typeof bucket.fail === 'number'
  )))
}

/** 价格桶使用数量级分桶（与后端 log10 分桶一致），边界不能被线性等分冒充 */
export function formatPriceBoundary(value: number): string {
  const rounded = value >= 100
    ? Math.round(value)
    : Math.round(value * 10) / 10
  return `¥${rounded.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}`
}

/**
 * 将后端的二维桶转换为 ECharts heatmap 序列。
 * 价格轴严格按 log10 边界计算；旧实现用 price_range 线性等分，
 * 与后端对数桶错位，会把 ¥8~22 标成 ¥8~246。
 */
export function computeHeatmapData(distData: EvalDistributionData | null): HeatmapCompute {
  if (!distData || !isEvalDistributionData(distData) || distData.buckets.length === 0) {
    return { ...EMPTY_HEATMAP }
  }

  const sBins = distData.buckets.length
  const pBins = Math.max(...distData.buckets.map((row) => row.length))
  if (pBins <= 0) return { ...EMPTY_HEATMAP }

  const priceMin = Math.max(1, distData.price_range[0] || 1)
  const priceMax = distData.price_range[1] > priceMin
    ? distData.price_range[1]
    : priceMin * 1.1
  const logMin = Math.log10(priceMin)
  const logMax = Math.log10(priceMax)
  const logStep = (logMax - logMin) / pBins

  const result: HeatmapCompute = {
    ...EMPTY_HEATMAP,
    xLabels: [],
    yLabels: [],
    hasPriceData: true,
  }

  for (let i = 0; i < pBins; i++) {
    const lo = Math.pow(10, logMin + i * logStep)
    const hi = i === pBins - 1 ? priceMax : Math.pow(10, logMin + (i + 1) * logStep)
    result.xLabels.push(`${formatPriceBoundary(lo)}~${formatPriceBoundary(hi)}`)
  }

  const scoreStep = 100 / sBins
  for (let i = 0; i < sBins; i++) {
    const hi = Math.round(100 - i * scoreStep)
    const lo = Math.round(100 - (i + 1) * scoreStep)
    result.yLabels.push(`${lo}-${hi}`)
  }

  distData.buckets.forEach((row, si) => {
    row.forEach((bucket, pi) => {
      result.data.push([pi, si, bucket.count])
      result.pricedTotal += bucket.count
      if (bucket.count > result.max) result.max = bucket.count
    })
  })
  return result
}

function heatmapPointValue(value: unknown): number | null {
  const list = Array.isArray(value) ? value : null
  if (!list || list.length < 3) return null
  const count = Number(list[2])
  return Number.isFinite(count) ? count : null
}

/**
 * 构造热力图 option。
 * 关键修复：tooltip 的 dataIndex 是数字索引，旧代码把 `[pi, si] = p.dataIndex`
 * 当数组解构会抛 TypeError；这里统一从 `params.data` 读取坐标。
 */
export function buildHeatmapOption(
  distData: EvalDistributionData | null,
  heatmap: HeatmapCompute,
  palette: EvalChartPalette,
): EChartsCoreOption | null {
  if (!distData?.buckets?.length || !heatmap.hasPriceData) return null

  return {
    animationDuration: 250,
    tooltip: {
      position: 'top',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      formatter: (params: unknown) => {
        const item = (Array.isArray(params) ? params[0] : params) as { data?: unknown } | undefined
        const coords = Array.isArray(item?.data) ? item.data : []
        const pi = Number(coords[0])
        const si = Number(coords[1])
        const bucket = distData.buckets[si]?.[pi]
        if (!bucket || bucket.count === 0) return '该区间暂无商品'
        return `${heatmap.xLabels[pi]} × 评分 ${heatmap.yLabels[si]}<br/>商品数：<b>${bucket.count}</b><br/>自动抢单：${bucket.auto} / 通过：${bucket.pass} / 驳回：${bucket.fail}`
      },
    },
    grid: { left: '3%', right: '4%', bottom: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: heatmap.xLabels,
      name: '价格',
      nameTextStyle: { color: palette.axisLabel, fontSize: 10 },
      splitArea: { show: true, areaStyle: { color: [palette.splitAreaA, palette.splitAreaB] } },
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.axisLabel, fontSize: 9, rotate: 30 },
    },
    yAxis: {
      type: 'category',
      data: heatmap.yLabels,
      name: '评分',
      nameTextStyle: { color: palette.axisLabel, fontSize: 10 },
      splitArea: { show: true, areaStyle: { color: [palette.splitAreaA, palette.splitAreaB] } },
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.axisLabel, fontSize: 9 },
    },
    visualMap: {
      min: 0,
      max: heatmap.max,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: '0%',
      inRange: { color: palette.visual },
      textStyle: { color: palette.axisLabel, fontSize: 10 },
      formatter: (value: number) => `${value}件`,
    },
    series: [
      {
        type: 'heatmap',
        data: heatmap.data,
        itemStyle: {
          borderColor: palette.tooltipBorder,
          borderWidth: 1,
          borderRadius: 2,
        },
        emphasis: {
          itemStyle: {
            borderColor: palette.primary,
            borderWidth: 2,
            shadowBlur: 8,
            shadowColor: palette.primary,
          },
        },
        label: {
          show: true,
          color: palette.cellLabel,
          textBorderColor: palette.cellLabelBorder,
          textBorderWidth: 1,
          fontSize: 9,
          formatter: (params: unknown) => {
            const item = (Array.isArray(params) ? params[0] : params) as { value?: unknown } | undefined
            const count = heatmapPointValue(item?.value)
            return count && count > 0 ? String(count) : ''
          },
        },
      },
    ],
  }
}

/** 雷达图 option：颜色全部由 token 注入，暗色主题不再沿用浅色硬编码 */
export function buildRadarOption(weights: EvalWeights, palette: EvalChartPalette): EChartsCoreOption {
  return {
    animationDuration: 250,
    tooltip: {
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      formatter: () => [
        `职业度：${weights.professional}`,
        `信誉：${weights.credit}`,
        `纠纷：${weights.dispute}`,
        `价格：${weights.price}`,
        `总和：${sumWeights(weights)}`,
      ].join('<br/>'),
    },
    radar: {
      indicator: [
        { name: '职业度', max: 100 },
        { name: '信誉', max: 100 },
        { name: '纠纷', max: 100 },
        { name: '价格', max: 100 },
      ],
      shape: 'polygon',
      splitNumber: 5,
      axisName: { color: palette.axisLabel, fontSize: 11 },
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.axis } },
      splitArea: { areaStyle: { color: [palette.splitAreaA, palette.splitAreaB] } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: [weights.professional, weights.credit, weights.dispute, weights.price],
            name: '当前权重',
            areaStyle: { color: palette.primary, opacity: 0.28 },
            lineStyle: { color: palette.primary, width: 2 },
            itemStyle: { color: palette.primary },
            symbolSize: 5,
          },
        ],
      },
    ],
  }
}
