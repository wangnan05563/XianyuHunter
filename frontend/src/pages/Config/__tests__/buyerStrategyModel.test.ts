import { describe, expect, it } from 'vitest'
import type { AppConfig } from '../../../api'
import {
  ACCOUNT_ROTATOR_FIELDS,
  BUYER_RUNTIME_FIELDS,
  getAutoBuyThresholdWarning,
  getBuyerStrategyConfigIssue,
  getBuyerStrategyReadyConfig,
  getBuyerStrategyValidationError,
  getDailyOrderGuardText,
  toFiniteNumberOrNull,
} from '../buyerStrategyModel'

function makeConfig(overrides: {
  weights?: Partial<AppConfig['eval']['weights']>
  pass?: number
  auto?: number
  buyer?: Partial<AppConfig['buyer']>
  rotator?: Partial<AppConfig['account_rotator']>
} = {}): AppConfig {
  return {
    eval: {
      weights: {
        professional: 30,
        credit: 30,
        dispute: 25,
        price: 15,
        ...overrides.weights,
      },
      thresholds: { on_sale_count: 30, post_count_30d: 15, top_category_ratio: 0.8, credit_score_min: 60, bad_review_max: 3, register_days_min: 30 },
      professional_keywords: ['批发'],
      pass_score: overrides.pass ?? 75,
      auto_buy_score: overrides.auto ?? 80,
      ai_auto_eval: false,
      ai_auto_deep_analyze: false,
      auto_collect_official: true,
      auto_collect_max_per_run: 3,
      auto_collect_dedup_window_minutes: 30,
      auto_collect_fail_pause_threshold: 3,
      ai_multi_run_suggestion: true,
      ai_suggestion_interval: 5,
    },
    buyer: {
      click_retry_times: 2,
      click_retry_interval: 1,
      confirm_button_timeout: 10,
      price_tolerance: 0.05,
      min_interval_between_orders: 0,
      takeover_timeout_min: 30,
      max_orders_per_day: 0,
      ...overrides.buyer,
    },
    account_rotator: {
      cooldown_sec: 1800,
      fail_threshold: 5,
      ...overrides.rotator,
    },
  } as unknown as AppConfig
}

describe('buyerStrategyModel', () => {
  it('完整配置通过段守卫', () => {
    expect(getBuyerStrategyConfigIssue(makeConfig())).toBeNull()
    expect(getBuyerStrategyReadyConfig(makeConfig())).not.toBeNull()
  })

  it('缺少 eval / buyer / account_rotator 任一顶层段都拒绝渲染', () => {
    const noEval = makeConfig() as unknown as Record<string, unknown>
    delete noEval.eval
    expect(getBuyerStrategyConfigIssue(noEval as unknown as AppConfig)).toContain('eval')
    expect(getBuyerStrategyReadyConfig(noEval as unknown as AppConfig)).toBeNull()

    const noBuyer = makeConfig() as unknown as Record<string, unknown>
    delete noBuyer.buyer
    expect(getBuyerStrategyConfigIssue(noBuyer as unknown as AppConfig)).toContain('buyer')

    const noRotator = makeConfig() as unknown as Record<string, unknown>
    delete noRotator.account_rotator
    expect(getBuyerStrategyConfigIssue(noRotator as unknown as AppConfig)).toContain('account_rotator')
  })

  it('缺少 weights 或字段非数值时拒绝渲染', () => {
    const noWeights = makeConfig() as unknown as { eval: Record<string, unknown> }
    delete noWeights.eval.weights
    expect(getBuyerStrategyConfigIssue(noWeights as unknown as AppConfig)).toContain('eval.weights')

    const badBuyer = makeConfig({ buyer: { confirm_button_timeout: Number.NaN } })
    expect(getBuyerStrategyConfigIssue(badBuyer)).toContain('buyer.confirm_button_timeout')
  })

  it('权重总和非 100 时阻止保存', () => {
    const config = makeConfig({ weights: { professional: 29 } })
    expect(getBuyerStrategyValidationError(config)).toContain('权重总和必须为 100（当前为 99）')
  })

  it('通过分数大于自动抢单分数时阻止保存', () => {
    const config = makeConfig({ pass: 85, auto: 80 })
    expect(getBuyerStrategyValidationError(config)).toContain('通过分数不能大于自动抢单分数')
  })

  it('合法分数顺序允许保存', () => {
    expect(getBuyerStrategyValidationError(makeConfig({ pass: 75, auto: 80 }))).toBeNull()
  })

  it('未加载配置不产生默认值可保存路径', () => {
    expect(getBuyerStrategyConfigIssue(null)).toBe('配置尚未加载')
    expect(getBuyerStrategyValidationError(null)).toBe('配置尚未加载')
  })

  it('auto_buy_score 低于 80 只提示风险', () => {
    expect(getAutoBuyThresholdWarning(70)).toContain('低于建议阈值 80')
    expect(getAutoBuyThresholdWarning(75)).toContain('当前为 75 分')
    expect(getAutoBuyThresholdWarning(80)).toBeNull()
  })

  it('单日上限 0 明确显示不限制', () => {
    expect(getDailyOrderGuardText(0)).toContain('未设置（不限制）')
    expect(getDailyOrderGuardText(5)).toContain('当前 5 次/日')
    expect(getDailyOrderGuardText(Number.NaN)).toContain('未设置（不限制）')
  })

  it('运行时字段元数据覆盖 7 项且边界正确', () => {
    expect(BUYER_RUNTIME_FIELDS).toHaveLength(7)
    expect(new Set(BUYER_RUNTIME_FIELDS.map((f) => f.key)).size).toBe(7)
    expect(BUYER_RUNTIME_FIELDS.find((f) => f.key === 'click_retry_times')?.max).toBe(10)
    expect(BUYER_RUNTIME_FIELDS.find((f) => f.key === 'price_tolerance')?.max).toBe(1)
    expect(BUYER_RUNTIME_FIELDS.find((f) => f.key === 'max_orders_per_day')?.max).toBe(10000)
  })

  it('账号轮换字段元数据覆盖 2 项', () => {
    expect(ACCOUNT_ROTATOR_FIELDS).toHaveLength(2)
    expect(ACCOUNT_ROTATOR_FIELDS.map((f) => f.key)).toEqual(['cooldown_sec', 'fail_threshold'])
  })

  it('InputNumber 清空不静默折成 0', () => {
    expect(toFiniteNumberOrNull(null)).toBeNull()
    expect(toFiniteNumberOrNull(undefined)).toBeNull()
    expect(toFiniteNumberOrNull(Number.NaN)).toBeNull()
    expect(toFiniteNumberOrNull(0)).toBe(0)
    expect(toFiniteNumberOrNull(3.5)).toBe(3.5)
  })
})
