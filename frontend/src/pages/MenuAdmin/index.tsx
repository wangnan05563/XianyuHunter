import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TipButton } from '@/components/TipButton'
import {
  Layout,
  Typography,
  Space,
  Table,
  Switch,
  InputNumber,
  Card,
  Spin,
  App as AntdApp,
  Popconfirm,
  Tag,
  Tooltip,
  Alert,
  Button,
  Modal,
  theme,
} from 'antd'
import {
  ArrowLeftOutlined,
  SettingOutlined,
  SaveOutlined,
  UndoOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { menuApi } from '../../api'
import type { MenuItem, MenuItemUpdate } from '../../api/menu'
import { MENU_GROUP_LABELS } from '../../api/menu'
import { extractApiError } from '../../utils/apiError'
import { registerNavigationGuard } from '../../utils/navigationGuard'
import './menu-admin.css'

const { Header, Content } = Layout
const { Title, Text } = Typography

// 表格行数据：扩展 MenuItem 含 key 用于 Table rowKey
// 项目规范：用 type 而非 interface 定义数据结构；扩展类型用交叉类型 & 而非 extends
type MenuRow = MenuItem & {
  // 行内编辑缓存：visible/sort_order 在表格内编辑，保存时统一提交
  editVisible: boolean
  editSortOrder: number
  // 是否被用户修改（用于高亮提示未保存）
  dirty: boolean
}

// 分组中文标签：从 menu.ts 共享常量导入，避免与 useMenuConfig 重复定义导致漂移

// 类别标签颜色：与 menu_registry.yaml 的 5 个分组对齐
const CATEGORY_TAG_COLOR: Record<string, string> = {
  overview: 'blue',
  data_view: 'cyan',
  config: 'purple',
  maintenance: 'orange',
  other: 'default',
}

export default function MenuAdmin() {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  // 通过 App 上下文取 message/modal，避免静态方法拿不到 ConfigProvider 主题与上下文
  const { message, modal } = AntdApp.useApp()

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 仅窄屏启用横向滚动，避免 1024 等宽度被无谓地撑出滚动条
  const [isNarrowTable, setIsNarrowTable] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches,
  )
  const tableScrollRef = useRef<HTMLDivElement>(null)
  // 编辑中数据
  const [rows, setRows] = useState<MenuRow[]>([])

  // 拉取菜单配置
  const loadMenus = useCallback(async (): Promise<void> => {
    setLoading(true)
    try {
      const data = await menuApi.get()
      setRows(
        data.map((m) => ({
          ...m,
          editVisible: m.visible,
          editSortOrder: m.sort_order,
          dirty: false,
        })),
      )
      setError(null)
    } catch (err) {
      // 持久错误态：刷新失败时保留错误说明，用户可在页面上重试，而不是只闪一次 toast
      setError(extractApiError(err, '加载菜单配置失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadMenus()
  }, [loadMenus])

  // 与 CSS 的 900px 断点保持一致：仅在窄屏让 AntD body 横向滚动
  useEffect(() => {
    const mql = window.matchMedia('(max-width: 900px)')
    const onChange = (e: MediaQueryListEvent): void => setIsNarrowTable(e.matches)
    setIsNarrowTable(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  // 窄屏 AntD sticky 会把表头/表体拆成两张 table，读屏会丢失列头关联。
  // 对可见表头隐藏 aria，并给表体补一个视觉隐藏的 thead，保留单表列头语义。
  useEffect(() => {
    const wrap = tableScrollRef.current
    if (!wrap || !isNarrowTable || loading || rows.length === 0) return
    const sync = (): void => {
      const header = wrap.querySelector<HTMLElement>('.ant-table-header')
      const bodyTable = wrap.querySelector<HTMLTableElement>('.ant-table-body > table')
      if (!header || !bodyTable) return
      header.setAttribute('aria-hidden', 'true')
      if (bodyTable.querySelector('thead.xh-menu-a11y-head')) return
      const titles = [...header.querySelectorAll<HTMLTableCellElement>('th')]
        .map((th) => (th.textContent || '').trim())
        .filter(Boolean)
      if (titles.length === 0) return
      const thead = document.createElement('thead')
      thead.className = 'xh-menu-a11y-head'
      const tr = document.createElement('tr')
      for (const title of titles) {
        const th = document.createElement('th')
        th.scope = 'col'
        th.textContent = title
        tr.appendChild(th)
      }
      thead.appendChild(tr)
      const tbody = bodyTable.querySelector('tbody')
      if (tbody) bodyTable.insertBefore(thead, tbody)
    }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(wrap, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [isNarrowTable, loading, rows.length])

  // 切换可见性
  const handleVisibleChange = useCallback((key: string, checked: boolean) => {
    setRows((prev) =>
      prev.map((r) =>
        r.key === key
          ? { ...r, editVisible: checked, dirty: checked !== r.visible || r.editSortOrder !== r.sort_order }
          : r,
      ),
    )
  }, [])

  // 调整排序值
  const handleSortOrderChange = useCallback((key: string, value: number | null) => {
    const v = typeof value === 'number' ? value : 0
    setRows((prev) =>
      prev.map((r) =>
        r.key === key
          ? { ...r, editSortOrder: v, dirty: r.editVisible !== r.visible || v !== r.sort_order }
          : r,
      ),
    )
  }, [])

  // 是否有未保存修改
  const dirtyCount = useMemo(() => rows.filter((r) => r.dirty).length, [rows])
  const hasDirty = dirtyCount > 0

  // 可见菜单的排序值重复：后端虽会用 key 稳定排序兜底，但用户无法从界面
  // 预知同值时谁先谁后；保存前直接拦下，避免制造新的歧义数据。
  const duplicateSortValues = useMemo(() => {
    const counts = new Map<number, number>()
    for (const r of rows) {
      if (!r.editVisible) continue
      counts.set(r.editSortOrder, (counts.get(r.editSortOrder) ?? 0) + 1)
    }
    return new Set(
      [...counts.entries()].filter(([, count]) => count > 1).map(([value]) => value),
    )
  }, [rows])
  const hasDuplicateSort = duplicateSortValues.size > 0

  // SPA 内部跳转守卫：菜单页的页面内容由 SheetWorkspace 按 activeSheet 渲染，
  // 侧栏点击会先激活目标 sheet 再 navigate，页面级 useBlocker 会随原页面卸载而失效。
  // 因此改为在导航动作执行前询问本页（见 utils/navigationGuard），确认后才切换。
  // 浏览器刷新/关闭仍由 beforeunload 兜底；页内刷新由 confirmDiscard 处理。
  const [leaveModalOpen, setLeaveModalOpen] = useState(false)
  const leaveResolveRef = useRef<((ok: boolean) => void) | null>(null)

  const confirmLeave = useCallback((): Promise<boolean> => {
    if (!hasDirty) return Promise.resolve(true)
    // 已有确认框时不再叠加：第二次点击按取消处理，避免 promise 悬挂
    if (leaveResolveRef.current) return Promise.resolve(false)
    setLeaveModalOpen(true)
    return new Promise<boolean>((resolve) => {
      leaveResolveRef.current = resolve
    })
  }, [hasDirty])

  const settleLeave = useCallback((ok: boolean) => {
    setLeaveModalOpen(false)
    const resolve = leaveResolveRef.current
    leaveResolveRef.current = null
    resolve?.(ok)
  }, [])

  useEffect(() => registerNavigationGuard(confirmLeave), [confirmLeave])

  // 卸载时若确认框仍开着，按取消收尾，避免外部调用方等不到结果
  useEffect(() => () => {
    const resolve = leaveResolveRef.current
    leaveResolveRef.current = null
    resolve?.(false)
  }, [])

  // 离开页面前提示：浏览器刷新/关闭兜底；SPA 内部跳转由 navigationGuard 拦截，页内刷新由 confirmDiscard 处理
  useEffect(() => {
    if (!hasDirty) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasDirty])

  // 丢弃修改确认：刷新与返回共用
  const confirmDiscard = useCallback(
    (action: () => void) => {
      modal.confirm({
        title: '放弃未保存的修改？',
        content: `当前有 ${dirtyCount} 项菜单配置修改尚未保存，继续操作将丢弃这些修改。`,
        okText: '放弃修改',
        cancelText: '继续编辑',
        okButtonProps: { danger: true },
        onOk: action,
      })
    },
    [dirtyCount, modal],
  )

  const handleBack = useCallback(() => {
    // 返回是浏览器历史导航，不经过侧栏的 openSheet 前守卫；这里主动复用同一确认框
    void confirmLeave().then((ok) => {
      if (ok) navigate(-1)
    })
  }, [confirmLeave, navigate])

  const handleReload = useCallback(() => {
    if (hasDirty) confirmDiscard(() => void loadMenus())
    else void loadMenus()
  }, [confirmDiscard, hasDirty, loadMenus])

  // 撤销全部未保存修改
  const handleUndoAll = useCallback(() => {
    setRows((prev) =>
      prev.map((r) => ({
        ...r,
        editVisible: r.visible,
        editSortOrder: r.sort_order,
        dirty: false,
      })),
    )
  }, [])

  // 保存配置
  const handleSave = useCallback(async (): Promise<void> => {
    if (hasDuplicateSort) {
      message.error('可见菜单的排序值不能重复，请先调整')
      return
    }
    // 仅提交被修改的项
    const updates: MenuItemUpdate[] = rows
      .filter((r) => r.dirty)
      .map((r) => ({
        key: r.key,
        visible: r.editVisible,
        sort_order: r.editSortOrder,
      }))

    if (updates.length === 0) {
      message.info('没有需要保存的修改')
      return
    }

    setSaving(true)
    const hide = message.loading('正在保存菜单配置…', 0)
    try {
      await menuApi.update(updates)
      // 保存成功后重新拉取，确保前端状态与后端一致
      await loadMenus()
      hide()
      message.success(`已保存 ${updates.length} 项修改`)
    } catch (err) {
      hide()
      message.error(extractApiError(err, '保存失败，请重试'))
    } finally {
      setSaving(false)
    }
  }, [hasDuplicateSort, loadMenus, message, rows])

  // 重置为默认配置
  const handleReset = useCallback(async (): Promise<void> => {
    setResetting(true)
    const hide = message.loading('正在重置为默认配置…', 0)
    try {
      await menuApi.reset()
      await loadMenus()
      hide()
      message.success('已重置为默认配置')
    } catch (err) {
      hide()
      message.error(extractApiError(err, '重置失败，请重试'))
    } finally {
      setResetting(false)
    }
  }, [loadMenus, message])

  // 横向滚动交给 AntD 的 table body，外层 region 保留焦点与键盘控制；
  // 这样表头可以独立于横向滚动容器做 sticky，首列固定仍由 CSS 负责。
  const handleTableScrollKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    // 输入框内方向键用于移动光标/调整数值，不能连带滚动表格
    if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')) return
    const body = e.currentTarget.querySelector<HTMLElement>('.ant-table-body')
    if (!body || body.scrollWidth <= body.clientWidth) return
    e.preventDefault()
    const step = Math.max(40, Math.min(120, Math.round(body.clientWidth * 0.1)))
    const maxScroll = body.scrollWidth - body.clientWidth
    body.scrollLeft = Math.max(0, Math.min(maxScroll, body.scrollLeft + (e.key === 'ArrowRight' ? step : -step)))
  }, [])

  // 表格列定义
  const columns = useMemo(
    () => [
      {
        title: '菜单项',
        dataIndex: 'label',
        key: 'label',
        render: (label: string, record: MenuRow) => (
          <Space>
            <Text strong>{label}</Text>
            {record.dirty && <Tag color="orange">未保存</Tag>}
          </Space>
        ),
      },
      {
        title: '标识',
        dataIndex: 'key',
        key: 'key',
        render: (key: string) => <Text type="secondary" code>{key}</Text>,
      },
      {
        title: '路径',
        dataIndex: 'path',
        key: 'path',
        render: (path: string) => <Text type="secondary" style={{ fontSize: 12 }}>{path}</Text>,
      },
      {
        title: '类别',
        dataIndex: 'category',
        key: 'category',
        render: (category: string) => (
          <Tag color={CATEGORY_TAG_COLOR[category] || 'default'}>
            {MENU_GROUP_LABELS[category] || category}
          </Tag>
        ),
      },
      {
        title: '可见',
        dataIndex: 'editVisible',
        key: 'visible',
        width: 80,
        render: (visible: boolean, record: MenuRow) => {
          const switchLabel = record.locked
            ? `${record.label} 是否可见（不可隐藏）`
            : `${record.label} 是否可见`
          return (
            <Tooltip
              title={record.locked ? '菜单管理是唯一的菜单恢复入口，不可隐藏' : undefined}
            >
              <Switch
                checked={visible}
                onChange={(checked) => handleVisibleChange(record.key, checked)}
                disabled={saving || resetting || record.locked}
                aria-label={switchLabel}
              />
            </Tooltip>
          )
        },
      },
      {
        title: '排序',
        dataIndex: 'editSortOrder',
        key: 'sort_order',
        width: 120,
        render: (order: number, record: MenuRow) => {
          const duplicate = duplicateSortValues.has(order)
          return (
            <InputNumber
              value={order}
              min={0}
              max={9999}
              step={10}
              onChange={(v) => handleSortOrderChange(record.key, v)}
              disabled={saving || resetting}
              aria-label={`${record.label} 排序值`}
              aria-describedby={duplicate ? 'menu-sort-duplicate-hint' : undefined}
              status={duplicate ? 'error' : undefined}
              style={{ width: '100%' }}
            />
          )
        },
      },
    ],
    [duplicateSortValues, handleSortOrderChange, handleVisibleChange, resetting, saving],
  )

  // 按 sort_order 排序展示
  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => a.sort_order - b.sort_order),
    [rows],
  )

  return (
    <Layout className="page-container xh-menu-admin-page" data-testid="menu-admin-page" style={{ height: '100%' }}>
      <Header
        className="xh-menu-admin-header"
        style={{
          background: token.colorBgContainer,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 1px 4px rgba(0, 0, 0, 0.04)',
          flex: '0 0 auto',
        }}
      >
        <Space className="xh-menu-admin-header-left">
          <TipButton
            tip="返回上一页"
            aria-label="返回上一页"
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={handleBack}
          />
          <SettingOutlined style={{ fontSize: 18, color: token.colorPrimary }} />
          <Title level={1} className="xh-menu-admin-title">菜单管理</Title>
        </Space>
        <Space className="xh-menu-admin-header-actions">
          <TipButton
            tip="重新加载菜单配置"
            aria-label="刷新"
            icon={<ReloadOutlined />}
            onClick={handleReload}
            disabled={loading || saving || resetting}
          >
            刷新
          </TipButton>
          <Popconfirm
            title="重置为默认配置"
            description={hasDirty
              ? '将清除所有自定义菜单配置，并丢弃当前未保存的修改，是否继续？'
              : '将清除所有自定义菜单配置，是否继续？'}
            onConfirm={() => void handleReset()}
            disabled={loading || saving || resetting}
          >
            <TipButton
              tip="重置为默认菜单配置"
              aria-label="重置为默认"
              icon={<UndoOutlined />}
              disabled={loading || saving || resetting}
            >
              重置为默认
            </TipButton>
          </Popconfirm>
          <TipButton
            tip="保存菜单配置修改"
            aria-label={hasDirty ? `保存菜单配置修改，${dirtyCount} 项未保存` : '保存菜单配置修改'}
            type="primary"
            icon={<SaveOutlined />}
            onClick={() => void handleSave()}
            disabled={!hasDirty || loading || saving || resetting || hasDuplicateSort}
            loading={saving}
          >
            保存{hasDirty ? ` (${dirtyCount})` : ''}
          </TipButton>
        </Space>
      </Header>
      <Content className="xh-menu-admin-content" style={{ background: token.colorBgLayout }}>
        <Card>
          <div className="xh-menu-admin-toolbar">
            <Text type="secondary" className="xh-menu-admin-hint">
              调整菜单项的可见性与排序值（保存后立即生效，影响所有页面的侧边栏展示）。
            </Text>
            {!loading && !error && <Text strong className="xh-menu-admin-count">共 {rows.length} 项</Text>}
          </div>

          {hasDuplicateSort && (
            <Alert
              id="menu-sort-duplicate-hint"
              className="xh-menu-sort-duplicate"
              type="error"
              showIcon
              message="可见菜单的排序值重复"
              description={`排序值 ${[...duplicateSortValues].join('、')} 被多个可见菜单使用；请改成唯一值后再保存。`}
            />
          )}

          {hasDirty && (
            <Alert
              className="xh-menu-dirty-banner"
              type="warning"
              showIcon
              message={`有 ${dirtyCount} 项修改尚未保存`}
              description="保存后立即生效；刷新、返回或切换页面时会提示放弃修改。"
              action={
                <Button size="small" onClick={handleUndoAll}>
                  撤销全部
                </Button>
              }
            />
          )}

          {error && (
            <Alert
              className="xh-menu-admin-error"
              type="error"
              showIcon
              message="菜单配置加载失败"
              description={error}
              action={
                <Button size="small" onClick={() => void loadMenus()}>
                  重新加载
                </Button>
              }
            />
          )}

          {loading ? (
            <div className="xh-menu-admin-loading" role="status" aria-live="polite">
              <Spin size="large" />
              <div style={{ marginTop: 12, color: token.colorTextSecondary }}>加载菜单配置…</div>
            </div>
          ) : (
            (rows.length > 0 || !error) && (
              <div
                ref={tableScrollRef}
                className="xh-menu-admin-table-scroll"
                role="region"
                aria-label="菜单配置表"
                tabIndex={0}
                onKeyDown={handleTableScrollKeyDown}
              >
                <Table<MenuRow>
                  rowKey="key"
                  columns={columns}
                  dataSource={sortedRows}
                  pagination={false}
                  size="middle"
                  bordered
                  sticky={isNarrowTable}
                  scroll={isNarrowTable ? { x: 820 } : undefined}
                  locale={{ emptyText: <div className="xh-menu-admin-empty">暂无菜单配置</div> }}
                />
              </div>
            )
          )}
        </Card>
      </Content>
      <Modal
        open={leaveModalOpen}
        title="放弃未保存的修改？"
        okText="放弃修改"
        cancelText="继续编辑"
        okButtonProps={{ danger: true }}
        maskClosable={false}
        onOk={() => settleLeave(true)}
        onCancel={() => settleLeave(false)}
      >
        当前有 {dirtyCount} 项菜单配置修改尚未保存，离开后将丢弃这些修改。
      </Modal>
    </Layout>
  )
}
