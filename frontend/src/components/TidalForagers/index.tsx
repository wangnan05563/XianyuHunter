import { useEffect, useState, type ComponentType } from 'react'

/**
 * Tidal Foragers 生成艺术背景的按需加载入口。
 *
 * p5 体积较大，静态引入会把它钉死在登录页首屏 chunk 内，
 * 因此真正的 sketch 放在 TidalForagersCanvas.tsx，由这里动态 import 拆成独立 chunk。
 *
 * 开关语义：系统「减少动效」（prefers-reduced-motion: reduce）视为动画关闭，
 * 此时既不挂载 canvas，也不会发起 p5 chunk 的网络请求。
 */
export default function TidalForagers() {
  const [Canvas, setCanvas] = useState<ComponentType | null>(null)

  useEffect(() => {
    const prefersReducedMotion =
      globalThis.window === undefined ||
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
    if (prefersReducedMotion) return

    let mounted = true
    import('./TidalForagersCanvas')
      .then((mod) => {
        if (mounted) setCanvas(() => mod.default)
      })
      // 背景动画属于装饰层，加载失败不应阻塞登录流程
      .catch(() => undefined)

    return () => {
      mounted = false
    }
  }, [])

  if (!Canvas) return null
  return <Canvas />
}
