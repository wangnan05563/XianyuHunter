import { describe, expect, it } from 'vitest'
import {
  buildHeatmapOption,
  computeHeatmapData,
  formatPriceBoundary,
  getEvalValidationError,
  isEvalDistributionData,
  normalizeWeights,
  sumWeights,
  type EvalChartPalette,
  type EvalDistributionData,
} from '../evalRulesModel'

const palette: EvalChartPalette = {
  axis: '#d9d9d9',
  axisLabel: '#595959',
  splitAreaA: '#fafafa',
  splitAreaB: '#ffffff',
  tooltipBg: '#ffffff',
  tooltipBorder: '#d9d9d9',
  tooltipText: '#262626',
  cellLabel: '#262626',
  cellLabelBorder: '#ffffff',
  primary: '#1677ff',
  visual: ['#f5f5f5', '#bae7ff', '#69c0ff', '#1890ff'],
}

function distribution(): EvalDistributionData {
  const buckets = Array.from({ length: 5 }, (_, si) => (
    Array.from({ length: 5 }, (_, pi) => ({
      count: si === 2 && pi === 1 ? 7 : 1,
      pass: si === 1 ? 1 : 0,
      auto: si === 0 ? 1 : 0,
      fail: si >= 2 ? 1 : 0,
    }))
  ))
  return { buckets, price_range: [8, 1200], total: 32 }
}

describe('评估规则页数据模型', () => {
  it('归一化后总和精确为 100，并保留零权重项', () => {
    const normalized = normalizeWeights({ professional: 1, credit: 1, dispute: 1, price: 0 })
    expect(normalized).not.toBeNull()
    expect(sumWeights(normalized!)).toBe(100)
    expect(normalized!.price).toBe(0)
  })

  it('归一化为零权重时明确返回 null，不伪造成功', () => {
    expect(normalizeWeights({ professional: 0, credit: 0, dispute: 0, price: 0 })).toBeNull()
  })

  it('保存校验优先报告权重总和，再报告分数顺序', () => {
    expect(getEvalValidationError(99, 75, 80)).toContain('权重总和')
    expect(getEvalValidationError(100, 85, 80)).toContain('通过分数')
    expect(getEvalValidationError(100, 75, 80)).toBeNull()
  })

  it('拒绝缺少价格区间或桶字段的分布响应', () => {
    expect(isEvalDistributionData(distribution())).toBe(true)
    expect(isEvalDistributionData({ buckets: [], total: 0 })).toBe(false)
    expect(isEvalDistributionData({ ...distribution(), buckets: [[{ count: '3' }]] })).toBe(false)
  })

  it('价格轴按后端 log10 桶标注，不使用线性等分', () => {
    const heatmap = computeHeatmapData(distribution())
    expect(heatmap.xLabels).toHaveLength(5)
    expect(heatmap.xLabels[0]).toBe('¥8~¥21.8')
    expect(heatmap.xLabels[1]).toBe('¥21.8~¥59.4')
    expect(heatmap.xLabels.join('|')).not.toContain('¥246')
    expect(heatmap.pricedTotal).toBe(31)
    expect(heatmap.max).toBe(7)
  })

  it('格式化价格边界保持可读且不丢数量级', () => {
    expect(formatPriceBoundary(8)).toBe('¥8')
    expect(formatPriceBoundary(59.36)).toBe('¥59.4')
    expect(formatPriceBoundary(1200)).toBe('¥1,200')
  })

  it('heatmap tooltip 从 params.data 读取坐标，不把 dataIndex 当数组', () => {
    const heatmap = computeHeatmapData(distribution())
    const option = buildHeatmapOption(distribution(), heatmap, palette)
    expect(option).not.toBeNull()
    const tooltip = (option?.tooltip as { formatter: (p: unknown) => string })
    const text = tooltip.formatter({ data: [1, 2, 7], dataIndex: 13 })
    expect(text).toContain('商品数')
    expect(text).toContain('自动抢单')
    expect(text).not.toContain('undefined')
  })

  it('heatmap label 兼容 ECharts 的数组型 value', () => {
    const heatmap = computeHeatmapData(distribution())
    const option = buildHeatmapOption(distribution(), heatmap, palette)
    const series = option?.series as Array<{ label: { formatter: (p: unknown) => string } }>
    expect(series[0].label.formatter({ value: [1, 2, 7] })).toBe('7')
    expect(series[0].label.formatter({ value: [1, 2, 0] })).toBe('')
  })
})
