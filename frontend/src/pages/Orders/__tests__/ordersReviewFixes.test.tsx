/**
 * 抢单记录页评审修复回归（/orders）
 *
 * 覆盖本轮 UI 评审的四类缺陷：
 * 1) 页面没有 H1/页壳、统计卡四列硬挤；
 * 2) 筛选区无分组、状态 Select 无可访问名称、缺「已取消」状态；
 * 3) 列表加载失败只弹 toast 后仍显示「暂无抢单记录」，无法区分空数据与接口故障；
 * 4) 8 列固定 1200px 横向滚动，窄屏把状态/创建时间/操作挤出首屏。
 * 静态守卫锁住页壳与响应式契约，渲染用例验证失败/重试与按钮读屏名。
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MemoryRouter } from 'react-router-dom'
import type { OrderItem } from '../../../api/types'

class FakeEventSource {
  constructor(public url: string) {}
  addEventListener() {}
  close() {}
}

const { listMock, takeoverMock } = vi.hoisted(() => ({ listMock: vi.fn(), takeoverMock: vi.fn() }))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  const orderApi = (actual as { orderApi: Record<string, unknown> }).orderApi
  return { ...actual, orderApi: { ...orderApi, list: listMock, takeover: takeoverMock } }
})

import Orders from '../Orders'

const STATS = {
  pending: 1, paid: 0, succeeded: 0, failed: 0, cancelled: 0, takeover: 0, total: 1,
}

function pendingOrder(): OrderItem {
  return {
    id: 'o1', item_id: 'i_1', price: 99, status: 'pending_pay',
    created_at: '2026-10-21T00:00:00', confirmed_at: null,
  }
}

function okPage() {
  return { items: [pendingOrder()], count: 1, total: 1, stats: STATS, by_status: { pending_pay: 1 } }
}

function failedRequest() {
  return { response: { data: { detail: '订单列表接口故意失败（mock）' } } }
}

function messageText(): string {
  return Array.from(document.querySelectorAll('.ant-message-notice-content'))
    .map((n) => n.textContent)
    .join('|')
}

beforeAll(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  Element.prototype.scrollIntoView = () => {}
})
afterAll(() => { vi.unstubAllGlobals() })

beforeEach(() => {
  localStorage.clear()
  listMock.mockReset()
  takeoverMock.mockReset()
})

describe('页壳与可访问性', () => {
  it('根节点带 testid、唯一 H1 与说明副标题', async () => {
    listMock.mockResolvedValue(okPage())
    render(<MemoryRouter initialEntries={['/orders']}><Orders /></MemoryRouter>)
    const page = await screen.findByTestId('orders-page')
    expect(page.querySelectorAll('h1')).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: '抢单记录' })).toBeInTheDocument()
    expect(page.querySelector('.orders-page-subtitle')?.textContent).toContain('抢单结果')
  })

  it('状态筛选有读屏名，操作按钮不再只靠图标', async () => {
    listMock.mockResolvedValue(okPage())
    render(<MemoryRouter initialEntries={['/orders']}><Orders /></MemoryRouter>)
    expect(await screen.findByRole('combobox', { name: '按订单状态筛选' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '接管订单' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '删除订单' })).toBeInTheDocument()
  })
})

describe('加载失败：持久 Alert + 重试', () => {
  it('失败时不弹 toast，显示 Alert、重试按钮与区分后的空态', async () => {
    listMock.mockRejectedValue(failedRequest())
    render(<MemoryRouter initialEntries={['/orders']}><Orders /></MemoryRouter>)

    expect(await screen.findByText('加载抢单记录失败')).toBeInTheDocument()
    expect(screen.getByText('订单列表接口故意失败（mock）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载抢单记录' })).toBeInTheDocument()
    expect(screen.getByText(/抢单记录加载失败，请使用上方/)).toBeInTheDocument()
    await waitFor(() => expect(messageText()).toBe(''))
    expect(document.querySelector('.ant-table-tbody .ant-table-row')).toBeNull()
  })

  it('点击重试恢复列表并移除 Alert', async () => {
    listMock.mockRejectedValueOnce(failedRequest()).mockResolvedValue(okPage())
    render(<MemoryRouter initialEntries={['/orders']}><Orders /></MemoryRouter>)

    const retry = await screen.findByRole('button', { name: '重新加载抢单记录' })
    await userEvent.click(retry)

    expect(await screen.findByText('i_1')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('加载抢单记录失败')).toBeNull())
    expect(document.querySelector('.ant-table-tbody .ant-table-row')).not.toBeNull()
  })
})

describe('响应式契约（静态守卫）', () => {
  const tsx = readFileSync(resolve(__dirname, '../Orders.tsx'), 'utf-8')
  const css = readFileSync(resolve(__dirname, '../orders.css'), 'utf-8')

  it('三档列宽收口，窄屏不再固定 1200px 横向滚动', () => {
    expect(tsx).toContain('ORDER_COLUMN_WIDTHS')
    expect(tsx).toContain("scroll={{ x: '100%' }}")
    expect(tsx).not.toContain('scroll={{ x: 1200 }}')
    expect(tsx).toContain('tableLayout="fixed"')
  })

  it('宽档保留确认时间/接管截止，中窄档移除，状态与创建时间始终在列', () => {
    expect(tsx).toContain('layoutMode === \'wide\'')
    expect(tsx).toContain("title: '确认时间'")
    expect(tsx).toContain("title: '接管截止'")
    expect(tsx).toContain("title: '状态'")
    expect(tsx).toContain("title: '创建时间'")
    // 确认时间/接管截止的宽度只在宽档定义，中窄档不会渲染这两列
    expect(tsx).toContain('width: columnWidths.confirmed')
    expect(tsx).toContain('width: columnWidths.deadline')
  })

  it('工具栏分组、统计卡栅格与操作列安全区均落到样式/JSX', () => {
    expect(tsx).toContain('orders-stats-row')
    expect(tsx).toContain('orders-toolbar')
    expect(tsx).toContain('orders-filter-item')
    expect(tsx).toContain('<Col xs={12} md={12} xl={6}>')
    expect(css).toContain('padding-right: 56px')
    expect(css).toContain('padding-right: 72px')
    expect(css).toContain('.orders-action-label')
  })
})
