import { useEffect, useState, useCallback, useMemo } from 'react'
import { formatDateTime as fmtTime } from '../../utils/timeFormat'
import {
  Card, Space, Input, Select, List, Tag, Empty, Spin, message,
  Tabs, Descriptions, Typography, Row, Col, Statistic, Popconfirm, Checkbox, Alert, Button,
} from 'antd'
import {
  ReloadOutlined, SearchOutlined, DeleteOutlined,
  CopyOutlined, CheckOutlined, StopOutlined, LeftOutlined, RightOutlined,
} from '@ant-design/icons'
import { TipButton } from '@/components/TipButton'
import { errorLogApi, type ErrorLog } from '../../api'
import { extractApiError } from '../../utils/apiError'
import { usePersistentState } from '../../hooks/usePersistentState'
import { useSearch } from '../../hooks/useSearch'
import { useSearchHistory } from '../../hooks/useSearchHistory'
import './errorLogs.css'

const { Text } = Typography

const PAGE_SIZE = 20

// 状态颜色映射
const STATUS_COLOR: Record<string, string> = {
  new: 'red',
  resolved: 'green',
  ignored: 'default',
}
const STATUS_LABEL: Record<string, string> = {
  new: '新建',
  resolved: '已解决',
  ignored: '已忽略',
}

// 详情面板代码块共享样式：使用 CSS 变量以跟随暗色主题
// 为什么用 var(--xh-*)：硬编码 #f5f5f5 在暗色下与白色字体同色无法辨识
const PRE_STYLE: React.CSSProperties = {
  background: 'var(--xh-bg-code)',
  color: 'var(--xh-text-primary)',
  padding: 12,
  borderRadius: 4,
  fontSize: 12,
  overflow: 'auto',
  maxHeight: 400,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
  border: '1px solid var(--xh-border)',
}

// 清理已失效的勾选 id：列表刷新后剔除非本次返回的 id（S2004 拆出避免函数嵌套过深）
function pruneSelection(prev: Set<number>, validIds: Set<number>): Set<number> {
  let changed = false
  const next = new Set<number>()
  prev.forEach((id) => {
    if (validIds.has(id)) next.add(id)
    else changed = true
  })
  return changed ? next : prev
}

export default function ErrorLogs() {
  // 搜索历史：错误类型关键词持久化到 localStorage，供快速复用
  const { history, add, clear } = useSearchHistory({ namespace: 'error_logs' })
  const [loading, setLoading] = useState(false)
  const [errorLogs, setErrorLogs] = useState<ErrorLog[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<ErrorLog | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [listError, setListError] = useState<string | null>(null)
  const [cleanupLoading, setCleanupLoading] = useState(false)
  // 批量勾选用：使用 Set 存储 id，便于 O(1) 检查与切换
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [batchLoading, setBatchLoading] = useState(false)

  // 筛选条件（持久化：刷新后保持上次筛选状态）
  const [filterStatus, setFilterStatus] = usePersistentState<string | undefined>(
    'xh.errorLogs.filterStatus', undefined,
    { validator: (v): v is string | undefined => v === undefined || typeof v === 'string' },
  )
  const [filterType, setFilterType] = usePersistentState<string>(
    'xh.errorLogs.filterType', '',
    { validator: (v): v is string => typeof v === 'string' },
  )
  const [aiFormat, setAiFormat] = useState<'json' | 'markdown'>('json')
  const [aiContent, setAiContent] = useState('')
  const [aiLoading, setAiLoading] = useState(false)

  const loadList = useCallback(() => {
    setLoading(true)
    setListError(null)
    errorLogApi
      .list({
        status: filterStatus,
        error_type: filterType || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      })
      .then((res) => {
        setErrorLogs(res.items)
        setTotal(res.count ?? res.items.length)
        setStatusCounts(res.status_counts || {})
        // 列表刷新后清理失效勾选，避免对已不存在记录执行批量操作
        // S2004：原嵌套 setSelectedIds((prev) => {...}) 拆为模块级 pruneSelection
        const validIds = new Set(res.items.map((it) => it.id))
        setSelectedIds((prev) => pruneSelection(prev, validIds))
        // 搜索成功且关键词非空时记录历史，供后续快速复用
        if (filterType?.trim()) {
          add(filterType.trim())
        }
      })
      .catch((err: unknown) => {
        // 失败时保留上一次成功列表，同时给出可重试的区块；不要用「暂无错误日志」冒充空数据
        setListError(extractApiError(err, '加载错误日志失败'))
      })
      .finally(() => setLoading(false))
  }, [filterStatus, filterType, page, add])

  const loadDetail = useCallback((id: number) => {
    setDetailLoading(true)
    errorLogApi
      .get(id)
      .then((data) => setDetail(data))
      .catch((err: unknown) => message.error(extractApiError(err, '加载详情失败')))
      .finally(() => setDetailLoading(false))
  }, [])

  const loadAiContext = useCallback(
    (id: number, format: 'json' | 'markdown') => {
      setAiLoading(true)
      errorLogApi
        .getAiContext(id, format)
        .then((content) => setAiContent(content))
        .catch((err: unknown) => message.error(extractApiError(err, '加载 AI 上下文失败')))
        .finally(() => setAiLoading(false))
    },
    [],
  )

  // 搜索防抖：filterStatus/filterType 变化时 400ms 防抖触发，避免每次输入都请求
  // async 包装：loadList 同步返回 void，async 包装后返回 Promise<void> 满足 useSearch 接口
  const { doSearch } = useSearch({
    search: async () => { loadList() },
    // 只按筛选条件/页码触发：loadList 的闭包随渲染变化，放进 deps 会让防抖反复重排
    deps: [filterStatus, filterType, page],
  })

  // 选中错误日志时加载详情和 AI 上下文
  useEffect(() => {
    if (selectedId) {
      loadDetail(selectedId)
      loadAiContext(selectedId, aiFormat)
    }
  }, [selectedId])

  const handleStatusChange = (id: number, status: string) => {
    errorLogApi
      .updateStatus(id, status)
      .then(() => {
        message.success('状态已更新')
        loadList()
        if (detail?.id === id) {
          setDetail({ ...detail, status: status as ErrorLog['status'] })
        }
      })
      .catch(() => message.error('更新状态失败'))
  }

  const handleDelete = (id: number) => {
    errorLogApi
      .delete(id)
      .then(() => {
        message.success('已删除')
        if (selectedId === id) {
          setSelectedId(null)
          setDetail(null)
        }
        // 同步移除已删除项的勾选状态，避免下次操作命中失效 id
        setSelectedIds((prev) => {
          if (!prev.has(id)) return prev
          const next = new Set(prev)
          next.delete(id)
          return next
        })
        loadList()
      })
      .catch(() => message.error('删除失败'))
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 全选/取消全选当前列表：以当前 errorLogs 为范围，避免跨页累积选择造成误操作
  const allChecked = useMemo(
    () => errorLogs.length > 0 && errorLogs.every((it) => selectedIds.has(it.id)),
    [errorLogs, selectedIds],
  )
  const someChecked = useMemo(
    () => errorLogs.some((it) => selectedIds.has(it.id)) && !allChecked,
    [errorLogs, selectedIds],
  )

  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      if (allChecked) {
        // 取消当前列表的全选，保留可能存在的其他选择（实际场景中应一并清掉）
        const next = new Set(prev)
        errorLogs.forEach((it) => next.delete(it.id))
        return next
      }
      const next = new Set(prev)
      errorLogs.forEach((it) => next.add(it.id))
      return next
    })
  }

  const clearSelection = () => setSelectedIds(new Set())

  // action 类型必须与 errorLogApi.batch 及 ErrorLog.status 字段对齐，
  // 否则调用方传 'resolved'/'ignored' 与旧签名 'resolve'/'ignore' 冲突
  const handleBatchAction = (
    action: 'delete' | 'resolved' | 'ignored' | 'new',
    successMsg: string,
  ) => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setBatchLoading(true)
    errorLogApi
      .batch(ids, action)
      .then((r) => {
        message.success(`${successMsg}：${r.affected} 条`)
        // 批量删除可能命中当前选中详情，统一重置详情避免脏数据
        if (action === 'delete' && selectedId && ids.includes(selectedId)) {
          setSelectedId(null)
          setDetail(null)
        } else if (detail && ids.includes(detail.id) && action !== 'delete') {
          // 状态变更同步到详情面板，避免左右两侧显示不一致
          setDetail({ ...detail, status: action })
        }
        clearSelection()
        loadList()
      })
      .catch(() => message.error('批量操作失败'))
      .finally(() => setBatchLoading(false))
  }

  const handleCopyAi = () => {
    navigator.clipboard.writeText(aiContent)
      .then(() => message.success('已复制到剪贴板'))
      .catch(() => message.error('复制失败，请手动选择文本'))
  }

  const handleAiFormatChange = (fmt: 'json' | 'markdown') => {
    setAiFormat(fmt)
    if (selectedId) loadAiContext(selectedId, fmt)
  }

  const handleCleanup = () => {
    setCleanupLoading(true)
    errorLogApi
      .cleanup(30)
      .then((r) => {
        message.success(`已清理 ${r.deleted} 条`)
        loadList()
      })
      .catch((err: unknown) => message.error(`清理失败：${extractApiError(err, '请稍后重试')}`, 10))
      .finally(() => setCleanupLoading(false))
  }

  const hasFilter = Boolean(filterStatus) || Boolean(filterType.trim())
  const resetFilters = () => {
    setFilterStatus(undefined)
    setFilterType('')
    setPage(1)
  }
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // 翻页后清空跨页勾选，避免「已选 N 项」里混入上一页看不见的记录
  useEffect(() => {
    setSelectedIds(new Set())
  }, [page])

  // 总数缩小时回落到最后一页，避免停在已经不存在的页码
  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  // Tab 内容构建
  const tabItems = detail
    ? [
        {
          key: 'overview',
          label: '概览',
          children: (
            <Descriptions
              column={1}
              bordered
              size="small"
              labelStyle={{ width: 100, whiteSpace: 'nowrap' }}
            >
              <Descriptions.Item label="错误类型">
                <Tag color="red">{detail.error_type}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="错误消息">{detail.error_message}</Descriptions.Item>
              <Descriptions.Item label="发生时间">{fmtTime(detail.timestamp)}</Descriptions.Item>
              <Descriptions.Item label="请求方法">{detail.request_method || 'N/A'}</Descriptions.Item>
              <Descriptions.Item label="请求路径">{detail.request_path || 'N/A'}</Descriptions.Item>
              <Descriptions.Item label="客户端 IP">{detail.client_ip || 'N/A'}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Select
                  size="small"
                  value={detail.status}
                  style={{ width: 120 }}
                  onChange={(v) => handleStatusChange(detail.id, v)}
                  options={[
                    { label: '新建', value: 'new' },
                    { label: '已解决', value: 'resolved' },
                    { label: '已忽略', value: 'ignored' },
                  ]}
                />
              </Descriptions.Item>
            </Descriptions>
          ),
        },
        {
          key: 'stack',
          label: '堆栈信息',
          children: (
            <pre style={PRE_STYLE}>
              {detail.stack_trace || '(无堆栈信息)'}
            </pre>
          ),
        },
        {
          key: 'request',
          label: '请求参数',
          children: (
            <pre style={PRE_STYLE}>
              {JSON.stringify(
                {
                  method: detail.request_method,
                  path: detail.request_path,
                  params: detail.request_params,
                  headers: detail.request_headers,
                  client_ip: detail.client_ip,
                  user_agent: detail.user_agent,
                },
                null,
                2,
              )}
            </pre>
          ),
        },
        {
          key: 'env',
          label: '服务器环境',
          children: (
            <pre style={PRE_STYLE}>
              {JSON.stringify(detail.server_env, null, 2)}
            </pre>
          ),
        },
        {
          key: 'ai',
          label: 'AI 上下文',
          children: (
            <div>
              <Space style={{ marginBottom: 8 }}>
                <Select
                  size="small"
                  value={aiFormat}
                  style={{ width: 120 }}
                  onChange={handleAiFormatChange}
                  options={[
                    { label: 'JSON', value: 'json' },
                    { label: 'Markdown', value: 'markdown' },
                  ]}
                />
                <TipButton tip="复制 AI 上下文到剪贴板" size="small" icon={<CopyOutlined />} onClick={handleCopyAi}>
                  复制
                </TipButton>
              </Space>
              <Spin spinning={aiLoading}>
                <pre style={PRE_STYLE}>
                  {aiContent || '(无 AI 上下文)'}
                </pre>
              </Spin>
            </div>
          ),
        },
      ]
    : []

  return (
    <div className="page-container xh-error-logs-page" aria-label="错误日志">
      <h1 className="xh-visually-hidden">错误日志</h1>
      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="新建"
              value={statusCounts.new || 0}
              valueStyle={{ color: '#ff4d4f' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="已解决"
              value={statusCounts.resolved || 0}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已忽略" value={statusCounts.ignored || 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card className="xh-error-log-actions">
            <Space direction="vertical" style={{ width: '100%' }}>
              <TipButton tip="重新加载错误日志列表" icon={<ReloadOutlined />} onClick={loadList} loading={loading} block>
                刷新
              </TipButton>
              <Popconfirm
                title="清理已解决/已忽略的错误日志"
                description="清理 30 天前的记录"
                onConfirm={handleCleanup}
              >
                <TipButton
                  tip="清理 30 天前已解决/已忽略的日志"
                  icon={<DeleteOutlined />}
                  loading={cleanupLoading}
                  block
                >
                  批量清理
                </TipButton>
              </Popconfirm>
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        {/* 左侧：错误列表 */}
        <Col span={10}>
          <Card
            title="错误列表"
            data-testid="error-log-list-card"
            extra={(
              <Space size={4} data-testid="error-log-pager">
                <Text type="secondary" style={{ fontSize: 12 }} data-testid="error-log-total">
                  共 {total} 条
                </Text>
                <TipButton
                  tip="上一页"
                  aria-label="上一页"
                  type="text"
                  size="small"
                  icon={<LeftOutlined />}
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                />
                <Text style={{ fontSize: 12 }}>{page}/{totalPages}</Text>
                <TipButton
                  tip="下一页"
                  aria-label="下一页"
                  type="text"
                  size="small"
                  icon={<RightOutlined />}
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                />
              </Space>
            )}
          >
            <Space style={{ marginBottom: 16 }} wrap>
              <Select
                allowClear
                placeholder="状态"
                style={{ width: 100 }}
                value={filterStatus}
                onChange={(v) => {
                  setFilterStatus(v)
                  setPage(1)
                }}
                options={[
                  { label: '新建', value: 'new' },
                  { label: '已解决', value: 'resolved' },
                  { label: '已忽略', value: 'ignored' },
                ]}
              />
              <Input
                placeholder="错误类型"
                style={{ width: 120 }}
                value={filterType}
                onChange={(e) => {
                  setFilterType(e.target.value)
                  setPage(1)
                }}
                onPressEnter={() => { setPage(1); doSearch() }}
                allowClear
              />
              <TipButton tip="按状态与错误类型搜索日志" type="primary" icon={<SearchOutlined />} onClick={() => { setPage(1); doSearch() }}>
                搜索
              </TipButton>
            </Space>
            {/* 搜索历史小药丸：点击复用历史关键词，避免重复输入 */}
            {history.length > 0 && (
              <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
                {history.map((kw) => (
                  <Tag
                    key={kw}
                    onClick={() => { setFilterType(kw); setPage(1) }}
                    style={{ cursor: 'pointer', margin: 0, fontSize: 11 }}
                  >
                    {kw}
                  </Tag>
                ))}
                <TipButton tip="清空搜索历史关键词" type="link" size="small" onClick={clear} style={{ padding: 0, fontSize: 11 }}>
                  清空
                </TipButton>
              </div>
            )}
            {/* 批量操作工具栏：仅在存在勾选时显示，避免空闲状态干扰筛选视线 */}
            {selectedIds.size > 0 && (
              <div
                style={{
                  marginBottom: 12,
                  padding: '8px 12px',
                  background: 'var(--xh-bg-info)',
                  border: '1px solid var(--xh-border-info)',
                  borderRadius: 4,
                }}
              >
                <Space wrap>
                  <Checkbox
                    checked={allChecked}
                    indeterminate={someChecked}
                    onChange={toggleSelectAll}
                  >
                    全选本页
                  </Checkbox>
                  <Text strong>已选 {selectedIds.size} 项</Text>
                  <TipButton
                    tip="将选中项标记为已解决"
                    size="small"
                    icon={<CheckOutlined />}
                    loading={batchLoading}
                    onClick={() => handleBatchAction('resolved', '已标记为已解决')}
                  >
                    标记已解决
                  </TipButton>
                  <TipButton
                    tip="将选中项标记为已忽略"
                    size="small"
                    icon={<StopOutlined />}
                    loading={batchLoading}
                    onClick={() => handleBatchAction('ignored', '已标记为已忽略')}
                  >
                    标记已忽略
                  </TipButton>
                  <Popconfirm
                    title="确认批量删除选中项？"
                    onConfirm={() => handleBatchAction('delete', '已删除')}
                  >
                    <TipButton
                      tip="批量删除选中的错误日志"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      loading={batchLoading}
                    >
                      批量删除
                    </TipButton>
                  </Popconfirm>
                  <TipButton tip="清除当前勾选" size="small" type="link" onClick={clearSelection}>
                    取消选择
                  </TipButton>
                </Space>
              </div>
            )}
            <Spin spinning={loading}>
              {listError && (
                <Alert
                  type="error"
                  showIcon
                  data-testid="error-log-load-failure"
                  message="错误日志加载失败"
                  description={(
                    <>
                      <div>{listError}</div>
                      {errorLogs.length > 0 && <div>下方仍是上一次成功加载的内容，可重试刷新。</div>}
                    </>
                  )}
                  action={<Button size="small" autoInsertSpace={false} onClick={loadList}>重试</Button>}
                  style={{ marginBottom: 12 }}
                />
              )}
              {errorLogs.length === 0 && !listError && (
                  <Empty description={hasFilter ? '未找到匹配的错误日志' : '暂无错误日志'} data-testid="error-log-empty">
                    {hasFilter && (
                      <Button size="small" onClick={resetFilters}>清除筛选</Button>
                    )}
                  </Empty>
              )}
              {errorLogs.length > 0 && (
                <List
                  size="small"
                  bordered
                  className="xh-error-log-list"
                  data-testid="error-log-list"
                  style={{ maxHeight: 500, overflow: 'auto' }}
                  dataSource={errorLogs}
                  renderItem={(item) => (
                    <List.Item
                      className="xh-error-log-row"
                      data-testid="error-log-row"
                      onClick={() => setSelectedId(item.id)}
                      style={{
                        cursor: 'pointer',
                        background: selectedId === item.id ? 'var(--xh-bg-info)' : undefined,
                        borderLeft: selectedId === item.id ? '3px solid var(--xh-border-info)' : undefined,
                      }}
                      actions={[
                        <Popconfirm
                          key="delete"
                          title="确认删除？"
                          onConfirm={(e) => {
                            e?.stopPropagation()
                            handleDelete(item.id)
                          }}
                        >
                          <TipButton
                            tip="删除该条错误日志"
                            type="text"
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Popconfirm>,
                      ]}
                    >
                      <div style={{ width: '100%', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        {/* Checkbox 阻止冒泡，避免点击勾选时误触发详情加载 */}
                        <Checkbox
                          checked={selectedIds.has(item.id)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggleSelect(item.id)}
                          style={{ marginTop: 2 }}
                        />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Space size={4} wrap>
                            <Tag color={STATUS_COLOR[item.status]} style={{ margin: 0 }}>
                              {STATUS_LABEL[item.status] || item.status}
                            </Tag>
                            <Tag color="red" className="xh-error-log-type" title={item.error_type}>
                              {item.error_type}
                            </Tag>
                          </Space>
                          {item.request_path && (
                            <div className="xh-error-log-path" title={`${item.request_method} ${item.request_path}`}>
                              <Text style={{ fontSize: 12, color: 'var(--xh-text-tertiary)' }}>
                                {item.request_method} {item.request_path}
                              </Text>
                            </div>
                          )}
                          <div className="xh-error-log-message" style={{ marginTop: 4, fontSize: 13 }}>
                            {item.error_message.length > 80
                              ? `${item.error_message.slice(0, 80)}...`
                              : item.error_message}
                          </div>
                          <div className="xh-error-log-time" style={{ fontSize: 11, color: 'var(--xh-text-tertiary)', marginTop: 2 }}>
                            {fmtTime(item.timestamp)}
                          </div>
                        </div>
                      </div>
                    </List.Item>
                  )}
                />
              )}
            </Spin>
          </Card>
        </Col>

        {/* 右侧：详情面板 */}
        <Col span={14}>
          <Card title="错误详情" data-testid="error-log-detail-card" styles={{ body: { minHeight: 520 } }}>
            <Spin spinning={detailLoading}>
              {detail ? (
                <Tabs items={tabItems} />
              ) : (
                <Empty description="选择左侧错误日志查看详情" />
              )}
            </Spin>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
