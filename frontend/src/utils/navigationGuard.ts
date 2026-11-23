/**
 * SPA 内部导航守卫（单槽位）
 *
 * 为什么不在页面里直接用 useBlocker：
 * 本项目的页面内容由 SheetWorkspace 按 activeSheet.path 渲染，侧栏点击会先
 * openSheet（激活目标 sheet）再 navigate。路由守卫拦住 navigate 时目标页面
 * 已经挂载、原页面已卸载，useBlocker 随之消失，确认框无法可靠工作。
 *
 * 因此把守卫前移到「导航动作执行前」：页面注册一个异步确认函数，侧栏点击
 * 先询问，确认后才 openSheet/navigate。取消时不改 sheet 栈，也不会残留目标标签页。
 *
 * 单槽位是刻意的：同一时刻只允许一个页面持有未保存守卫（当前仅菜单管理页）。
 * 后注册的页面覆盖前一个；返回的清理函数只在仍持有槽位时清空，避免旧页面卸载
 * 时误清掉新页面的守卫。
 */

export type NavigationGuard = (nextPath: string) => Promise<boolean> | boolean

let activeGuard: NavigationGuard | null = null

export function registerNavigationGuard(guard: NavigationGuard): () => void {
  activeGuard = guard
  return () => {
    if (activeGuard === guard) activeGuard = null
  }
}

/**
 * 请求一次 SPA 导航：
 * - 没有守卫、目标与当前路径相同：直接执行
 * - 有守卫：等待页面确认；确认后才执行 action，取消则完全不执行（不改 sheet 栈）
 */
export async function requestNavigation(
  nextPath: string,
  currentPath: string,
  action: () => void,
): Promise<void> {
  if (nextPath === currentPath) {
    action()
    return
  }
  if (!activeGuard) {
    action()
    return
  }
  try {
    const allowed = await activeGuard(nextPath)
    if (allowed) action()
  } catch {
    // 守卫自身异常时不放行，避免未保存数据被静默丢弃
  }
}
