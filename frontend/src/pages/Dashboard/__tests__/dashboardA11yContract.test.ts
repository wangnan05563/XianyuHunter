// 仪表盘结构 / 可访问性契约：页面标识、H1、可键盘操作卡片、图表与筛选名称、移动端标题。
// 运行层由 Playwright 四档视口兜底；这里守住源码里容易被重构掉的契约点。
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (p: string) => readFileSync(resolve(__dirname, p), 'utf-8')
const INDEX = read('../index.tsx')
const STAT = read('../components/StatCardsSection.tsx')
const KPI = read('../components/KpiSection.tsx')
const FUNNEL = read('../components/EvalFunnelCard.tsx')
const HIST = read('../components/PriceHistogramCard.tsx')
const TREND = read('../components/TrendModal.tsx')
const CSS = read('../dashboard.css')
const MOBILE = read('../../../mobile/pages/Dashboard/index.tsx')

describe('仪表盘页面标识', () => {
  it('桌面页有唯一 H1 与页面 testid', () => {
    expect(INDEX).toContain('data-testid="dashboard-page"')
    expect(INDEX).toContain('<h1 className="dashboard-page-title">仪表盘</h1>')
    expect(INDEX).toContain('className="page-container xh-dashboard-page"')
  })

  it('移动页有页面级 H1', () => {
    expect(MOBILE).toContain('function MobileDashboardHeader')
    expect(MOBILE).toContain('<h1')
    expect(MOBILE).toContain('仪表盘')
    expect(MOBILE).toContain('MobileDashboardHeader')
  })
})

describe('仪表盘可访问性契约', () => {
  it('统计卡与快捷入口可用键盘操作', () => {
    expect(STAT).toContain('role="button"')
    expect(STAT).toContain('tabIndex={0}')
    expect(STAT).toContain("e.key === 'Enter' || e.key === ' '")
    expect(INDEX).toContain('role="button"')
    expect(INDEX).toContain('tabIndex={0}')
    expect(INDEX).toContain('aria-label={`打开${e.title}：${e.desc}`}')
    expect(CSS).toContain('.stat-card:focus-visible')
    expect(CSS).toContain('.quick-entry-card:focus-visible')
  })

  it('图表有名称或明确作为装饰隐藏', () => {
    // S6819：图表容器改用语义化 <figure> + aria-label，不再挂 role="img"
    expect(FUNNEL).toContain('<figure')
    expect(FUNNEL).toContain('aria-label={`评估漏斗图：')
    expect(HIST).toContain('<figure')
    expect(HIST).toContain('aria-label={`价格分布直方图')
    expect(HIST).toContain('aria-label="价格分布任务筛选"')
    expect(TREND).toContain('<figure')
    expect(TREND).toContain('aria-label={`${trendMetricLabel')
    expect(STAT).toContain('aria-hidden="true"')
  })

  it('KPI 在 768 宽度改为两列，避免文字被挤断', () => {
    expect(KPI).toContain('xs={12} md={12} lg={6}')
  })
})
