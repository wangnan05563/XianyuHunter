/**
 * 错误日志页 UI 修复回归：
 * 1. 后端 120 条但只返回 20 条/页时，必须展示总数与分页，不能静默丢 100 条；
 * 2. 有筛选但 0 条，要与「暂无错误日志」区分，并提供清除筛选；
 * 3. 列表加载失败要有页内重试，不能伪装成空数据；
 * 4. 清理失败要有兜底提示，不能留下未处理的 Promise。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { listMock, getMock, aiMock, cleanupMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  getMock: vi.fn(),
  aiMock: vi.fn(),
  cleanupMock: vi.fn(),
}))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  const errorLogApi = (actual as { errorLogApi: Record<string, unknown> }).errorLogApi
  return {
    ...actual,
    errorLogApi: {
      ...errorLogApi,
      list: listMock,
      get: getMock,
      getAiContext: aiMock,
      cleanup: cleanupMock,
    },
  }
})

import ErrorLogs from '../ErrorLogs'

const item = (id: number) => ({
  id,
  timestamp: `2026-11-20T14:${String(id % 60).padStart(2, '0')}:00`,
  error_type: 'PlaywrightTimeoutError',
  error_message: '等待浏览器响应超时，可能是网络抖动或登录态失效。',
  stack_trace: 'Traceback...',
  request_method: 'POST',
  request_path: '/api/tasks/42/run',
  request_params: { task_id: 42 },
  request_headers: { 'x-request-id': `req-${id}` },
  client_ip: '10.0.1.11',
  user_agent: 'XianyuHunter',
  server_env: { app: 'xianyu-hunter' },
  ai_context_json: '{}',
  ai_context_md: '# context',
  status: 'new',
  created_at: '2026-11-20T14:00:00',
})

const pageItems = (offset = 0) => Array.from({ length: 20 }, (_, i) => item(offset + i + 1))
const okPage = (offset = 0) => ({
  items: pageItems(offset),
  count: 120,
  status_counts: { new: 65, resolved: 35, ignored: 20 },
})

const apiError = (detail: string) => ({ response: { status: 500, data: { detail } } })

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/logs/errors']}>
      <ErrorLogs />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  listMock.mockReset()
  getMock.mockReset()
  aiMock.mockReset()
  cleanupMock.mockReset()
  getMock.mockResolvedValue(item(1))
  aiMock.mockResolvedValue('{}')
  cleanupMock.mockResolvedValue({ deleted: 0, status: 'ok' })
})

describe('错误日志页 UI 修复', () => {
  it('显示后端总条数与分页，翻页请求 offset=20', async () => {
    listMock.mockResolvedValue(okPage())
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('共 120 条')).toBeTruthy()
    expect(screen.getByText('1/6')).toBeTruthy()
    expect(screen.getAllByTestId('error-log-row')).toHaveLength(20)

    await user.click(screen.getByLabelText('下一页'))

    await waitFor(() => {
      expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ limit: 20, offset: 20 }))
    })
    await waitFor(() => expect(screen.getByText('2/6')).toBeTruthy())
  })

  it('筛选无结果时显示「未找到匹配」并可清除筛选，不冒充暂无数据', async () => {
    listMock.mockResolvedValue({ items: [], count: 0, status_counts: { new: 65, resolved: 35, ignored: 20 } })
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('暂无错误日志')).toBeTruthy()

    await user.type(screen.getByPlaceholderText('错误类型'), 'NoSuchError')

    expect(await screen.findByText('未找到匹配的错误日志')).toBeTruthy()
    expect(screen.queryByText('暂无错误日志')).toBeNull()

    await user.click(screen.getByText('清除筛选'))

    await waitFor(() => {
      expect((screen.getByPlaceholderText('错误类型') as HTMLInputElement).value).toBe('')
    })
    expect(await screen.findByText('暂无错误日志')).toBeTruthy()
  })

  it('列表加载失败显示页内失败区块并可重试，不用空数据冒充', async () => {
    listMock.mockRejectedValueOnce(apiError('数据库暂时不可用')).mockResolvedValue(okPage())
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('错误日志加载失败')).toBeTruthy()
    expect(screen.getByText(/数据库暂时不可用/)).toBeTruthy()
    expect(screen.queryByText('暂无错误日志')).toBeNull()

    await user.click(screen.getByText('重试'))

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('共 120 条')).toBeTruthy()
    expect(screen.queryByText('错误日志加载失败')).toBeNull()
  })

  it('批量清理失败时给出兜底提示', async () => {
    listMock.mockResolvedValue(okPage())
    cleanupMock.mockRejectedValue(apiError('清理服务暂不可用'))
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('共 120 条')
    await user.click(screen.getByText('批量清理'))
    await user.click(await screen.findByRole('button', { name: /OK|确\s*定/ }))

    await waitFor(() => expect(cleanupMock).toHaveBeenCalledWith(30))
    expect(await screen.findByText(/清理失败/)).toBeTruthy()
  })
})
