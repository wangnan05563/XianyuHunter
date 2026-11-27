// 事件时间线页评审回归：页壳、筛选可访问性、事件 chips 键盘操作、持久失败态与空态区分。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { apiMocks } = vi.hoisted(() => ({
  apiMocks: {
    taskList: vi.fn(),
    timelineList: vi.fn(),
    configGet: vi.fn(),
  },
}))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  const a = actual as Record<string, Record<string, unknown>>
  return {
    ...actual,
    taskApi: { ...a.taskApi, list: apiMocks.taskList },
    timelineApi: { ...a.timelineApi, list: apiMocks.timelineList },
    configApi: { ...a.configApi, get: apiMocks.configGet },
  }
})

import TimelinePage from '../index'

const ERR = new Error('500')
const ITEMS = [
  {
    _kind: 'order', _ts: '2026-11-25T14:31:00+08:00', id: 'o1', title: '索尼 A7M4 单机身',
    amount: 12800, status: 'succeeded', task_id: 't1',
  },
  {
    _kind: 'event', _ts: '2026-11-25T13:20:00+08:00', id: 'e1', type: 'eval.passed',
    level: 'info', task_id: 't1', payload: { message: '评估通过：评分 92', score: 92, risk_level: 'low', is_passed: true },
  },
  {
    _kind: 'event', _ts: '2026-11-25T12:20:00+08:00', id: 'e2', type: 'buy.succeeded',
    level: 'info', task_id: 't1', payload: { message: '抢单成功：¥12800.00' },
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.taskList.mockResolvedValue({ items: [] })
  apiMocks.configGet.mockResolvedValue({})
  apiMocks.timelineList.mockResolvedValue({ items: ITEMS })
})

function renderPage() {
  return render(
    <MemoryRouter>
      <TimelinePage />
    </MemoryRouter>,
  )
}

describe('事件时间线页评审回归', () => {
  it('页面具备唯一 H1、根 testid 与副标题', async () => {
    const { container } = renderPage()
    await waitFor(() => expect(apiMocks.timelineList).toHaveBeenCalled())

    expect(screen.getByTestId('timeline-page')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: '事件时间线' })).toBeInTheDocument()
    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(container.querySelector('.timeline-page-subtitle')?.textContent).toContain('订单与系统事件')
  })

  it('四张统计卡与 4 个可访问筛选项/操作按钮', async () => {
    const { container } = renderPage()
    await waitFor(() => expect(apiMocks.timelineList).toHaveBeenCalled())

    expect(container.querySelectorAll('.timeline-stats-row .ant-card')).toHaveLength(4)
    expect(container.querySelectorAll('.timeline-filter-item')).toHaveLength(2)
    expect(screen.getAllByLabelText('按事件类型筛选').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('按任务筛选').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.timeline-event-types .ant-tag').length).toBeGreaterThanOrEqual(20)
  })

  it('事件 chips 具按钮语义并支持 Enter 键切换过滤', async () => {
    const { container } = renderPage()
    await waitFor(() => expect(apiMocks.timelineList).toHaveBeenCalled())

    const chips = [...container.querySelectorAll<HTMLElement>('.timeline-type-chip')]
    expect(chips.length).toBeGreaterThanOrEqual(20)
    chips.forEach((chip) => {
      expect(chip).toHaveAttribute('role', 'button')
      expect(chip).toHaveAttribute('tabindex', '0')
      expect(chip).toHaveAttribute('aria-pressed')
    })

    fireEvent.keyDown(chips[0], { key: 'Enter' })
    await waitFor(() => {
      expect(chips[1]).toHaveAttribute('aria-pressed', 'false')
    })
    // 事件过滤不能把订单项过滤掉
    expect(screen.getByText('索尼 A7M4 单机身')).toBeInTheDocument()
  })

  it('时间线请求失败时显示持久错误条与加载失败空态，不用 toast/正常空态冒充', async () => {
    apiMocks.timelineList.mockRejectedValue(ERR)
    const { container } = renderPage()

    await waitFor(() => expect(screen.getByText('加载事件时间线失败')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重新加载事件时间线' })).toBeInTheDocument()
    expect(screen.getByText('加载失败，暂无可显示的事件时间线')).toBeInTheDocument()
    expect(screen.queryByText('暂无匹配的时间线数据')).not.toBeInTheDocument()
    expect(screen.queryByText('加载时间线失败')).not.toBeInTheDocument()
    expect(container.querySelectorAll('.timeline-row')).toHaveLength(0)
  })

  it('点击重新加载后错误条消失并恢复列表', async () => {
    let shouldFail = true
    apiMocks.timelineList.mockImplementation(() => (
      shouldFail ? Promise.reject(ERR) : Promise.resolve({ items: ITEMS })
    ))
    const { container } = renderPage()

    await waitFor(() => expect(screen.getByText('加载事件时间线失败')).toBeInTheDocument())
    shouldFail = false
    fireEvent.click(screen.getByRole('button', { name: '重新加载事件时间线' }))

    await waitFor(() => expect(screen.queryByText('加载事件时间线失败')).not.toBeInTheDocument())
    expect(container.querySelectorAll('.timeline-row')).toHaveLength(3)
    expect(screen.getByText('索尼 A7M4 单机身')).toBeInTheDocument()
  })

  it('成功取到 0 条时保留正常空态，不显示错误条', async () => {
    apiMocks.timelineList.mockResolvedValue({ items: [] })
    const { container } = renderPage()

    await waitFor(() => expect(screen.getByText('暂无匹配的时间线数据')).toBeInTheDocument())
    expect(container.querySelector('.timeline-load-error')).toBeNull()
    expect(container.querySelector('.timeline-toolbar')).not.toBeNull()
  })
})
