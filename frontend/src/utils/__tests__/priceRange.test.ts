import { describe, expect, it } from 'vitest'
import { formatPriceRange } from '../priceRange'

// 2026-11-20 UI 走查回归：列表 `¥不限 ~ ¥不限`、详情 `不限 - 不限` 两处口径不一致，
// 且详情用 `if (minPrice)` 判空把 0 元当成「不限」。这里把三条边界钉死。
describe('formatPriceRange', () => {
  it('两端都有价格时带 ¥ 且用统一分隔符', () => {
    expect(formatPriceRange(1000, 2000)).toBe('¥1000 ~ ¥2000')
  })

  it('null / undefined 显示「不限」而不是 ¥null', () => {
    expect(formatPriceRange(null, null)).toBe('不限 ~ 不限')
    expect(formatPriceRange(undefined, 500)).toBe('不限 ~ ¥500')
    expect(formatPriceRange(500, undefined)).toBe('¥500 ~ 不限')
  })

  it('0 是合法价格，不能被当成「不限」', () => {
    expect(formatPriceRange(0, 100)).toBe('¥0 ~ ¥100')
    expect(formatPriceRange(0, 0)).toBe('¥0 ~ ¥0')
  })
})
