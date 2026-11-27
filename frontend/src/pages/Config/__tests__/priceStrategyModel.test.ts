import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PRICE_STRATEGY,
  estimatePriceHits,
  formatBinRange,
  getPriceStrategyError,
  isHistogramPayload,
  normalizePriceStrategy,
} from '../priceStrategyModel'

describe('价格策略页数据模型', () => {
  it('接受后端对象 bins 契约，拒绝旧版 counts/字符串 bins 契约', () => {
    const valid = {
      bins: [{ min: 0, max: 100, count: 3 }],
      summary: { count: 3, min: 0, max: 90, mean: 40, median: 40, p25: 20, p75: 80 },
      scope: { mode: 'all', task_id: null, label: '全部任务' },
      mode: 'auto',
    }
    expect(isHistogramPayload(valid)).toBe(true)
    expect(isHistogramPayload({ bins: ['0', '100'], counts: [3, 4] })).toBe(false)
    expect(isHistogramPayload({ bins: [{ min: '0', max: 100, count: 3 }], summary: {} })).toBe(false)
  })

  it('完全落在硬性上下限内的桶精确通过，完全在外的桶精确过滤', () => {
    const estimate = estimatePriceHits(
      [
        { min: 0, max: 200, count: 5 },
        { min: 200, max: 800, count: 10 },
        { min: 800, max: 1000, count: 3 },
      ],
      { ...DEFAULT_PRICE_STRATEGY, enabled_min: true, min_price: 200, enabled_max: true, max_price: 800 },
    )
    expect(estimate).toMatchObject({ total: 18, pass: 10, reject: 8, boundary: 0, estimated: false })
  })

  it('跨阈值桶按重叠比例估算，并标记 estimated/boundary', () => {
    const estimate = estimatePriceHits(
      [{ min: 100, max: 300, count: 10 }],
      { ...DEFAULT_PRICE_STRATEGY, enabled_min: true, min_price: 200, enabled_max: false, max_price: 10000 },
    )
    expect(estimate.pass).toBe(5)
    expect(estimate.reject).toBe(5)
    expect(estimate.boundary).toBe(10)
    expect(estimate.estimated).toBe(true)
    expect(estimate.bins[0].status).toBe('boundary')
  })

  it('关闭上下限后全部商品视为通过', () => {
    const estimate = estimatePriceHits(
      [{ min: 0, max: 100, count: 7 }],
      { ...DEFAULT_PRICE_STRATEGY, enabled_min: false, enabled_max: false },
    )
    expect(estimate).toMatchObject({ total: 7, pass: 7, reject: 0, estimated: false })
  })

  it('开放式末桶在阈值切入桶内时标记边界，不冒充精确值', () => {
    const cut = estimatePriceHits(
      [{ min: 20000, max: null, count: 10 }],
      { ...DEFAULT_PRICE_STRATEGY, enabled_min: false, enabled_max: true, max_price: 30000 },
    )
    expect(cut).toMatchObject({ total: 10, boundary: 10, estimated: true })
    expect(cut.bins[0].status).toBe('boundary')

    const allAbove = estimatePriceHits(
      [{ min: 20000, max: null, count: 10 }],
      { ...DEFAULT_PRICE_STRATEGY, enabled_min: false, enabled_max: true, max_price: 800 },
    )
    expect(allAbove).toMatchObject({ total: 10, pass: 0, reject: 10, estimated: false })
  })

  it('拒绝下限高于上限、越界值和非法 TopN', () => {
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, min_price: 800, max_price: 800 })).toContain('硬性下限')
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, max_price: 50001 })).toContain('硬性上限')
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, min_price: 5001 })).toContain('硬性下限')
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, top_n: 0 })).toContain('TopN')
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, market_ratio: 1.2 })).toContain('市场参考价')
    expect(getPriceStrategyError({ ...DEFAULT_PRICE_STRATEGY, min_price: 200, max_price: 800 })).toBeNull()
  })

  it('配置缺失时合并安全默认值，保留显式 false', () => {
    expect(normalizePriceStrategy(undefined)).toEqual(DEFAULT_PRICE_STRATEGY)
    expect(normalizePriceStrategy({ enabled_market_ratio: false, top_n: 10 })).toMatchObject({
      enabled_market_ratio: false,
      top_n: 10,
      max_price: DEFAULT_PRICE_STRATEGY.max_price,
    })
  })

  it('格式化价格区间，开放式末桶不伪造上界', () => {
    expect(formatBinRange({ min: 200, max: 800 })).toBe('¥200–¥800')
    expect(formatBinRange({ min: 20000, max: null })).toContain('以上')
  })
})
