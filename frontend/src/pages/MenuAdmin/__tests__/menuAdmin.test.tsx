// frontend/src/pages/MenuAdmin/__tests__/menuAdmin.test.tsx
// 菜单管理页回归：未保存状态、保存载荷、加载失败重试。
// 这里锁住本轮真实修复的根因：可见性切换必须按“新值 vs 服务端值”计算 dirty。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { App as AntdApp } from 'antd'

const { menuApiMock } = vi.hoisted(() => ({
  menuApiMock: {
    get: vi.fn(),
    update: vi.fn(),
    reset: vi.fn(),
  },
}))

vi.mock('../../../api', () => ({ menuApi: menuApiMock }))

import MenuAdmin from '../index'

type FixtureMenu = {
  key: string
  label: string
  icon: string
  path: string
  sort_order: number
  visible: boolean
  locked?: boolean
  category: string
}

const BASE_MENUS: FixtureMenu[] = [
  { key: 'dashboard', label: '仪表盘', icon: 'DashboardOutlined', path: '/', sort_order: 1, visible: true, category: 'overview' },
  { key: 'tasks', label: '任务管理', icon: 'UnorderedListOutlined', path: '/tasks', sort_order: 10, visible: true, category: 'data_view' },
  { key: 'menu_admin', label: '菜单管理', icon: 'SettingOutlined', path: '/menu-admin', sort_order: 999, visible: true, locked: true, category: 'other' },
]

let serverMenus: FixtureMenu[] = []

function resetServerMenus() {
  serverMenus = BASE_MENUS.map((m) => ({ ...m }))
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AntdApp>
        <MenuAdmin />
      </AntdApp>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  Object.values(menuApiMock).forEach((m) => m.mockReset())
  resetServerMenus()
  menuApiMock.get.mockImplementation(async () => serverMenus.map((m) => ({ ...m })))
  menuApiMock.update.mockImplementation(async (items: Array<{ key: string; visible?: boolean; sort_order?: number }>) => {
    serverMenus = serverMenus.map((m) => {
      const patch = items.find((item) => item.key === m.key)
      return patch ? { ...m, ...patch } : m
    })
    return { ok: true }
  })
  menuApiMock.reset.mockResolvedValue({ ok: true })
})

describe('菜单管理页回归', () => {
  it('可见性切换立即进入未保存态，并只提交被修改项', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByRole('heading', { name: '菜单管理' })).toBeInTheDocument())

    const switchEl = screen.getByRole('switch', { name: '任务管理 是否可见' })
    expect(switchEl).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(switchEl)

    await waitFor(() => expect(switchEl).toHaveAttribute('aria-checked', 'false'))
    const row = screen.getByText('任务管理').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('未保存')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存/ })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(menuApiMock.update).toHaveBeenCalledWith([{ key: 'tasks', visible: false, sort_order: 10 }]))
    await waitFor(() => expect(screen.getByText('已保存 1 项修改')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  it('排序值修改同样产生 dirty，保存载荷包含新排序', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByRole('heading', { name: '菜单管理' })).toBeInTheDocument())

    const input = screen.getByRole('spinbutton', { name: '任务管理 排序值' })
    fireEvent.change(input, { target: { value: '5' } })
    fireEvent.blur(input)

    const row = screen.getByText('任务管理').closest('tr')
    expect(within(row as HTMLElement).getByText('未保存')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(menuApiMock.update).toHaveBeenCalledWith([{ key: 'tasks', visible: true, sort_order: 5 }]))
  })

  it('加载失败展示持久错误态，点击重新加载后恢复表格', async () => {
    menuApiMock.get.mockRejectedValueOnce(new Error('mock load failed'))
    renderPage()

    await waitFor(() => expect(screen.getByText('菜单配置加载失败')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
    await waitFor(() => expect(screen.getByText('任务管理')).toBeInTheDocument())
    expect(screen.queryByText('菜单配置加载失败')).not.toBeInTheDocument()
  })
})
