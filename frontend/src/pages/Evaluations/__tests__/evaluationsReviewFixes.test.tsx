// frontend/src/pages/Evaluations/__tests__/evaluationsReviewFixes.test.tsx
// 评估明细 UI 评审闭环的契约测试：页头/筛选可访问名/失败持久态与重试。
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { listMock, distMock, taskListMock, suggestMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  distMock: vi.fn(),
  taskListMock: vi.fn(),
  suggestMock: vi.fn(),
}))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  const evalApi = (actual as { evalApi: Record<string, unknown> }).evalApi
  const taskApi = (actual as { taskApi: Record<string, unknown> }).taskApi
  return {
    ...actual,
    evalApi: {
      ...evalApi,
      list: listMock,
      distribution: distMock,
      thresholdSuggestion: suggestMock,
      sellerTrend: vi.fn().mockResolvedValue({ points: [] }),
    },
    taskApi: { ...taskApi, list: taskListMock },
  }
})

vi.mock('../../../components/charts/EChart', () => ({ default: () => null }))

import Evaluations from '../index'

const DIST = {
  buckets: [],
  marginals: { price: [], score: [], result: { pass: 4, auto: 3, fail: 3 } },
  distribution: [],
  suggested_threshold: { score: 70, pass_rate: 0.5 },
  total: 10,
  insufficient_count: 0,
  price_range: [500, 20000] as [number, number],
  thresholds: { pass_score: 60, auto_buy_score: 80 },
}

const pageItems = [92, 58, 88].map((score, i) => ({
  id: i + 1, task_id: 't1', item_id: `i${i}`, created_at: '2026-10-01T00:00:00',
  payload: { score, item_title: `测试商品${i}`, item_price: 1000 + i },
}))

function mockListOk() {
  listMock.mockResolvedValue({ items: pageItems, count: pageItems.length, total: pageItems.length })
}

beforeAll(() => {
  Element.prototype.scrollIntoView = () => {}
})

beforeEach(() => {
  localStorage.clear()
  // usePersistentState 的存储格式；收起分析，避免 Drawer 遮住断言目标
  localStorage.setItem('xh.evals.panelCollapsed', JSON.stringify({ __v: 1, data: true }))
  listMock.mockReset()
  distMock.mockReset()
  taskListMock.mockReset()
  suggestMock.mockReset()
  distMock.mockResolvedValue(DIST)
  taskListMock.mockResolvedValue({ items: [], count: 0, total: 0 })
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/evaluations']}>
      <Evaluations />
    </MemoryRouter>,
  )
}

describe('评估明细页壳与筛选可访问名', () => {
  it('有唯一 H1 与稳定页面标识，分析开关有可访问名', async () => {
    mockListOk()
    renderPage()
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '评估明细' })).toBeInTheDocument())
    expect(screen.getByTestId('evaluations-page')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开分析面板' })).toBeInTheDocument()
  })

  it('关键筛选控件都能通过可访问名定位', async () => {
    mockListOk()
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('按商品 ID 筛选')).toBeInTheDocument())
    expect(screen.getByRole('combobox', { name: '按任务筛选' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '按品牌筛选' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '按商品状态筛选' })).toBeInTheDocument()
    expect(screen.getByLabelText('最低价格')).toBeInTheDocument()
    expect(screen.getByLabelText('最高价格')).toBeInTheDocument()
  })

  it('统计卡是可键盘操作的按钮，并暴露当前选中状态', async () => {
    mockListOk()
    renderPage()
    await waitFor(() => expect(screen.getByRole('button', { name: /评估总数/ })).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /可抢/ })).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('评估列表失败态', () => {
  it('失败时保留 Alert 与重试入口，空态文案区别于无数据', async () => {
    listMock.mockRejectedValue(new Error('mock boom'))
    renderPage()
    await waitFor(() => expect(screen.getByText('评估列表加载失败')).toBeInTheDocument())
    expect(screen.getByText('加载评估列表失败，请点击重试')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新加载评估列表/ })).toBeInTheDocument()
    expect(screen.getByText(/评估数据加载失败，请使用上方/)).toBeInTheDocument()
    expect(screen.queryByText('暂无评估数据')).not.toBeInTheDocument()
  })

  it('点重试后清掉错误并恢复列表', async () => {
    listMock
      .mockRejectedValueOnce(new Error('mock boom'))
      .mockResolvedValueOnce({ items: pageItems, count: pageItems.length, total: pageItems.length })
    renderPage()
    await waitFor(() => expect(screen.getByText('评估列表加载失败')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /重新加载评估列表/ }))
    await waitFor(() => expect(screen.getByText('测试商品0')).toBeInTheDocument())
    expect(screen.queryByText('评估列表加载失败')).not.toBeInTheDocument()
  })
})
