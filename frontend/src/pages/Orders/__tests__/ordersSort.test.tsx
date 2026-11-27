/**
 * 抢单记录时间列服务端排序（T02677）
 *
 * 回归的缺陷：创建/确认时间列无排序入口，后端固定 created_at desc，
 * 接管场景下「找最新的待接管单」只能翻页人肉找；金额列只有当前页本地排序，
 * 翻页后顺序就乱。现在三列都走服务端排序，排序变更回到第 1 页，
 * 且纯翻页（非受控 sorter 仍上报排序）不得误重置页码。
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import type { OrderItem } from '../../../api/types'

class FakeEventSource {
  constructor(public url: string) {}
  addEventListener() {}
  close() {}
}

const { listMock } = vi.hoisted(() => ({ listMock: vi.fn() }))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  const orderApi = (actual as { orderApi: Record<string, unknown> }).orderApi
  return { ...actual, orderApi: { ...orderApi, list: listMock } }
})

import Orders from '../Orders'

const STATS = {
  pending: 0, paid: 0, succeeded: 0, failed: 0, cancelled: 0, takeover: 0, total: 0,
}

function makeOrders(n: number): OrderItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `o${i + 1}`, item_id: `i_${i + 1}`, price: 10 + i, status: 'succeeded',
    created_at: `2026-10-01T00:00:${String(i % 60).padStart(2, '0')}`,
    confirmed_at: null,
  }))
}

const originalMatchMedia = globalThis.matchMedia

// jsdom 默认所有媒体查询都返回 false，Grid.useBreakpoint 会把页面判为窄屏，
// 此时「确认时间」按设计不渲染（只在 ≥1200 的宽档出现）。本文件固定模拟 1280 宽屏。
function wideScreenMatchMedia(query: string) {
  const min = Number(query.match(/min-width:\s*(\d+)px/)?.[1] || 0)
  const max = Number(query.match(/max-width:\s*(\d+)px/)?.[1] || 0)
  return {
    matches: min <= 1280 && (!max || max >= 1280),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }
}

beforeAll(() => {
  globalThis.matchMedia = wideScreenMatchMedia as typeof globalThis.matchMedia
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterAll(() => {
  globalThis.matchMedia = originalMatchMedia
  vi.unstubAllGlobals()
})

beforeEach(() => {
  localStorage.clear()
  listMock.mockReset()
  listMock.mockResolvedValue({
    items: makeOrders(100), count: 100, total: 100,
    stats: STATS, by_status: {},
  })
})

function renderOrders() {
  render(
    <MemoryRouter initialEntries={['/orders']}>
      <Orders />
    </MemoryRouter>,
  )
}

async function sortHeader(title: string): Promise<HTMLElement> {
  // 「创建时间/确认时间」在页面其他区域（如列设置弹层）也出现，必须限定到表头 th。
  // 列表接口 resolve 后 React 还要再渲染一帧，慢机器上直接 getByText 会抢跑，统一用 waitFor。
  let th: HTMLElement | undefined
  await waitFor(() => {
    th = screen.queryAllByText(title).map((n) => n.closest('th')).find(Boolean) as HTMLElement | undefined
    if (!th) throw new Error(`未找到「${title}」表头`)
  })
  return th as HTMLElement
}

async function lastCall() {
  await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(1))
  return listMock.mock.calls[listMock.mock.calls.length - 1][0]
}

describe('时间列服务端排序（T02677）', () => {
  it('默认请求按 created_at desc（与后端原固定行为一致）', async () => {
    renderOrders()
    expect(await lastCall()).toMatchObject({ sort_by: 'created_at', sort_dir: 'desc' })
  })

  it('点创建时间表头切升序：请求 sort_dir=asc', async () => {
    renderOrders()
    await lastCall()
    await userEvent.click(await sortHeader('创建时间'))
    expect(await lastCall()).toMatchObject({ sort_by: 'created_at', sort_dir: 'asc' })
  })

  it('确认时间列同样走服务端排序', async () => {
    renderOrders()
    await lastCall()
    await userEvent.click(await sortHeader('确认时间'))
    expect(await lastCall()).toMatchObject({ sort_by: 'confirmed_at', sort_dir: 'asc' })
  })

  it('金额列不再是仅当前页的本地排序，也并入服务端', async () => {
    renderOrders()
    await lastCall()
    await userEvent.click(await sortHeader('金额'))
    expect(await lastCall()).toMatchObject({ sort_by: 'price', sort_dir: 'asc' })
  })

  it('排序变更后回到第 1 页', async () => {
    renderOrders()
    await lastCall()
    await userEvent.click(await sortHeader('创建时间'))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(listMock.mock.calls[1][0]).toMatchObject({ page_num: 1 })
  })

  it('排序生效后翻页：page 跟随且排序不丢，也不被误重置回第 1 页', async () => {
    renderOrders()
    await lastCall()
    await userEvent.click(await sortHeader('创建时间'))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    // 非受控 sorter 在纯翻页时仍会上报当前排序——只在排序真变了时才走排序分支
    await userEvent.click(screen.getByTitle('2'))
    expect(await lastCall()).toMatchObject({ page_num: 2, sort_by: 'created_at', sort_dir: 'asc' })
  })
})
