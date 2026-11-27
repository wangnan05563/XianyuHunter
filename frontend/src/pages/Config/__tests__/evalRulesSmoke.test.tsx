import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { App as AntApp } from 'antd'

const mocks = vi.hoisted(() => ({
  distribution: vi.fn(),
  stats: vi.fn(),
}))

vi.mock('../../../components/charts/EChart', () => ({ default: () => null }))
vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return {
    ...actual,
    evalApi: {
      ...actual.evalApi,
      distribution: mocks.distribution,
      autoCollectStats: mocks.stats,
    },
  }
})

import EvalRules from '../EvalRules'
import { useConfigStore } from '../../../stores/configStore'
import type { AppConfig } from '../../../api/types'

const CONFIG = {
  eval: {
    weights: { professional: 30, credit: 30, dispute: 20, price: 20 },
    thresholds: {
      on_sale_count: 10, post_count_30d: 5, top_category_ratio: 0.6,
      credit_score_min: 600, bad_review_max: 3, register_days_min: 180,
    },
    professional_keywords: ['验机', '原装'],
    pass_score: 60,
    auto_buy_score: 80,
    ai_auto_eval: true,
    ai_auto_deep_analyze: false,
    auto_collect_official: true,
    auto_collect_max_per_run: 20,
    auto_collect_dedup_window_minutes: 60,
    auto_collect_fail_pause_threshold: 3,
    ai_multi_run_suggestion: true,
    ai_suggestion_interval: 30,
  },
} as unknown as AppConfig

const DIST = {
  total: 6,
  price_range: [100, 1000],
  buckets: [[
    { count: 3, pass: 2, auto: 1, fail: 0 },
    { count: 3, pass: 1, auto: 0, fail: 2 },
  ]],
}

beforeEach(() => {
  mocks.distribution.mockReset().mockResolvedValue(DIST)
  mocks.stats.mockReset().mockResolvedValue({
    total: 12, success: 10, failed: 2, success_rate: 83.3,
    is_paused: false, fail_pause_threshold: 3,
    top_failures: [{ reason: '超时', count: 2 }],
  })
  useConfigStore.setState({
    config: CONFIG,
    original: structuredClone(CONFIG),
    loading: false,
    error: null,
  })
})

describe('评估规则页首屏', () => {
  it('渲染权重、阈值、分布图与采集统计', async () => {
    render(<AntApp><EvalRules /></AntApp>)

    expect(screen.getByText('评估规则')).toBeInTheDocument()
    expect(screen.getByTestId('eval-rules-layout')).toBeInTheDocument()
    await waitFor(() => expect(mocks.distribution).toHaveBeenCalled())
    await waitFor(() => expect(mocks.stats).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/最近 24 小时/)).toBeInTheDocument())
  })
})
