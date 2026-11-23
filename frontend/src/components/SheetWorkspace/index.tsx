import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { SheetTabs } from './SheetTabs'
import { SheetContent } from './SheetContent'
import { SheetPreferences } from './SheetPreferences'
import { useSheetSync } from '../../hooks/useSheetSync'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useSheetStore } from '../../stores/sheetStore'
import { useConfigStore } from '../../stores/configStore'
import { useChatbotConfigStore } from '../../stores/chatbotConfigStore'
import { requestNavigation } from '../../utils/navigationGuard'
import {
  collectChangedPaths,
  filterChangedPaths,
  scopeForPath,
} from '../../stores/configDirty'
import { confirmDanger } from '../DangerSummary'
import './sheet.css'

/**
 * SheetWorkspace 容器组件
 *
 * 职责：组装标签栏、内容区、偏好设置面板，注入 navigate 到 store，
 * 同步移动端模式，驱动 URL↔栈 双向同步。
 *
 * 为什么在此处注入 navigate 而非 store 内部直接 import：
 * store 是纯逻辑层，不应感知路由库；navigate 来自 Router 上下文，
 * 必须在组件树内获取，由容器注入实现关注点分离。
 */
export function SheetWorkspace() {
  const navigate = useNavigate()
  const location = useLocation()
  const isMobile = useIsMobile()
  const sheets = useSheetStore((s) => s.sheets)
  const activeId = useSheetStore((s) => s.activeId)
  const preferences = useSheetStore((s) => s.preferences)
  const activateSheet = useSheetStore((s) => s.activateSheet)
  const closeSheet = useSheetStore((s) => s.closeSheet)
  const minimizeSheet = useSheetStore((s) => s.minimizeSheet)
  const setMobileMode = useSheetStore((s) => s.setMobileMode)
  const setNavigator = useSheetStore((s) => s.setNavigator)

  const config = useConfigStore((s) => s.config)
  const original = useConfigStore((s) => s.original)
  // T05072：客服配置独立 store，关闭确认的脏判断要把它的草稿算进去
  const chatbotDirtyCount = useChatbotConfigStore((s) =>
    s.config && s.original ? s.dirtyPaths().length : 0,
  )

  const [prefOpen, setPrefOpen] = useState(false)

  // 注入 navigate 到 store（栈→URL 同步通道）
  // 为什么用 useEffect 而非渲染时调用：setNavigator 触发 set()，在渲染中调用会无限循环
  useEffect(() => {
    setNavigator(navigate)
  }, [navigate, setNavigator])

  // 同步移动端模式到 store：影响 openSheet 的单 sheet 替换逻辑
  useEffect(() => {
    setMobileMode(isMobile)
  }, [isMobile, setMobileMode])

  // URL→栈 同步：监听 location.pathname 变化调用 openSheet
  useSheetSync()

  const activeSheet = sheets.find((s) => s.id === activeId)

  // 关闭按钮行为：根据偏好选择最小化（保留状态）或真正关闭。
  // 脏判断只认该 sheet 自己那段配置：全局快照级脏会把别页的改动算到这一页头上（T02572 项 2）。
  const handleClose = (id: string) => {
    const target = sheets.find((s) => s.id === id)
    const scope = target ? scopeForPath(target.path) : null
    const scopeDirty = scope && config && original
      ? filterChangedPaths(collectChangedPaths(config, original), scope)
      : []
    // T05072：/config/chatbot 的草稿在独立 store 里，不在 CONFIG_SCOPE_BY_ROUTE 内
    const chatbotDirty = target?.path === '/config/chatbot' ? chatbotDirtyCount : 0
    const dirty = [...scopeDirty, ...Array.from({ length: chatbotDirty }, () => 'chatbot')]
    const apply = () => {
      if (preferences.minimizeInsteadOfClose) {
        minimizeSheet(id)
      } else {
        closeSheet(id)
      }
    }
    // 关闭的是当前页：先过页面注册的未保存守卫（如菜单管理页）。
    // 用 #close 伪路径强制触发确认，因为目标 path 与当前 URL 相同。
    const runGuarded = () => {
      if (!target || id !== activeId) {
        apply()
        return
      }
      void requestNavigation(`${target.path}#close`, location.pathname, apply)
    }
    if (!target || dirty.length === 0) {
      runGuarded()
      return
    }
    const keepDraft = preferences.minimizeInsteadOfClose
    void confirmDanger({
      title: `「${target.title}」有未保存的改动`,
      action: keepDraft ? '把该页最小化' : '放弃该页未保存的配置改动并关闭标签页',
      scope: keepDraft
        ? '只收起这一页；草稿仍留在配置里，重新打开该页可继续编辑'
        : '只影响这一页的改动；其他标签页的草稿与已保存的配置不受影响',
      count: `${dirty.length} 项`,
      recovery: keepDraft
        ? '改动未保存到服务端，浏览器刷新或退出后丢失'
        : '关闭后草稿不可恢复，需重新编辑并保存',
    }).then((ok) => {
      if (ok) runGuarded()
    })
  }

  // 激活/最小化当前页会卸载页面组件，必须先过页面守卫，避免菜单页草稿被静默丢弃。
  const handleActivate = (id: string) => {
    const target = sheets.find((s) => s.id === id)
    if (!target) {
      activateSheet(id)
      return
    }
    void requestNavigation(target.path, location.pathname, () => activateSheet(id))
  }

  const handleMinimize = (id: string) => {
    if (id !== activeId) {
      minimizeSheet(id)
      return
    }
    const target = sheets.find((s) => s.id === id)
    if (!target) {
      minimizeSheet(id)
      return
    }
    void requestNavigation(`${target.path}#minimize`, location.pathname, () => minimizeSheet(id))
  }

  return (
    <div className={`sheet-workspace${preferences.enableAnimation ? '' : ' no-animation'}`}>
      {/* 移动端隐藏标签栏：单 sheet 全屏模式 */}
      {!isMobile && (
        <SheetTabs
          sheets={sheets}
          activeId={activeId}
          preferences={preferences}
          onActivate={handleActivate}
          onClose={handleClose}
          onMinimize={handleMinimize}
          onOpenPreferences={() => setPrefOpen(true)}
        />
      )}
      <div className="sheet-content-area">
        <SheetContent sheet={activeSheet} />
      </div>
      <SheetPreferences open={prefOpen} onClose={() => setPrefOpen(false)} />
    </div>
  )
}

export default SheetWorkspace
