import { describe, expect, it } from 'vitest'
import type { AppConfig } from '../../../api'
import {
  SEARCH_MAX_PAGES_MAX,
  SEARCH_MAX_PAGES_MIN,
  getMaxDelayMs,
  getOperationDelaySeconds,
  getPageSizeOptions,
  getQpsFromDelay,
  getSearchConfigIssue,
  getSearchConfigReadyConfig,
  getSearchConfigValidationError,
  toFiniteNumberOrNull,
} from '../searchConfigModel'

function makeConfig(): AppConfig {
  return {
    search: {
      page_size: 50,
      sort_type: 'newest',
      timeout: 90,
      regions: '',
      filter_tags: ['包邮'],
      max_pages: 5,
    },
    antidetect: {
      qps: 20,
      min_delay_ms: 3000,
      max_delay_ms: 4500,
      fail_pause_threshold: 3,
      fail_window_sec: 3600,
    },
    task_scheduler: {
      default_interval_seconds: 60,
      auto_search_enabled: false,
      auto_search_concurrency: 1,
    },
    waf: { enabled: true, login_check_interval_min: 30 },
  } as unknown as AppConfig
}

describe('searchConfigModel', () => {
  it('四段完整时通过守卫并返回强类型配置', () => {
    expect(getSearchConfigIssue(makeConfig())).toBeNull()
    expect(getSearchConfigReadyConfig(makeConfig())).not.toBeNull()
  })

  it('缺少 search / antidetect / task_scheduler / waf 任一段都拒绝渲染', () => {
    const missing = [
      ['search', 'search'],
      ['antidetect', 'antidetect'],
      ['task_scheduler', 'task_scheduler'],
      ['waf', 'waf'],
    ] as const
    for (const [segment, label] of missing) {
      const config = makeConfig() as unknown as Record<string, unknown>
      delete config[segment]
      expect(getSearchConfigIssue(config as unknown as AppConfig)).toContain(label)
      expect(getSearchConfigReadyConfig(config as unknown as AppConfig)).toBeNull()
    }
  })

  it('search 缺 max_pages 时列入缺失清单', () => {
    const config = makeConfig() as unknown as { search: Record<string, unknown> }
    delete config.search.max_pages
    expect(getSearchConfigIssue(config as unknown as AppConfig)).toContain('search.max_pages')
    expect(getSearchConfigReadyConfig(config as unknown as AppConfig)).toBeNull()
  })

  it('字段类型错误时拒绝渲染', () => {
    const config = makeConfig() as unknown as { search: Record<string, unknown> }
    config.search.filter_tags = '包邮'
    expect(getSearchConfigIssue(config as unknown as AppConfig)).toContain('search.filter_tags')
  })

  it('翻页深度越界时阻止保存', () => {
    const config = makeConfig()
    config.search!.max_pages = SEARCH_MAX_PAGES_MAX + 1
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('翻页深度')
    config.search!.max_pages = SEARCH_MAX_PAGES_MIN - 1
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('翻页深度')
  })

  it('排序方式不在支持列表时阻止保存', () => {
    const config = makeConfig()
    config.search!.sort_type = 'unknown'
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('排序方式')
  })

  it('超时与失败阈值越界时阻止保存', () => {
    const config = makeConfig()
    config.search!.timeout = 121
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('搜索超时')
    config.search!.timeout = 90
    config.antidetect!.fail_pause_threshold = 11
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('失败暂停阈值')
  })

  it('最大延迟小于最小延迟时阻止保存', () => {
    const config = makeConfig()
    config.antidetect!.max_delay_ms = 2500
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('最大操作延迟')
  })

  it('任务周期与 WAF 间隔越界时阻止保存', () => {
    const config = makeConfig()
    config.task_scheduler!.default_interval_seconds = 10
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('默认采集周期')
    config.task_scheduler!.default_interval_seconds = 60
    config.waf!.login_check_interval_min = 1
    expect(getSearchConfigValidationError(getSearchConfigReadyConfig(config))).toContain('登录检查间隔')
  })

  it('每页选项包含后端默认 50，并动态保留历史非标准值', () => {
    expect(getPageSizeOptions(50).map((item) => item.value)).toContain(50)
    expect(getPageSizeOptions(80).map((item) => item.value)).toContain(80)
    expect(getPageSizeOptions(50).filter((item) => item.value === 50)).toHaveLength(1)
  })

  it('毫秒延迟按秒展示并限制在页面范围', () => {
    expect(getOperationDelaySeconds(3000)).toBe(3)
    expect(getOperationDelaySeconds(5000)).toBe(5)
    expect(getOperationDelaySeconds(100)).toBe(1)
    expect(getOperationDelaySeconds(40000)).toBe(30)
    expect(getOperationDelaySeconds(Number.NaN)).toBe(1)
  })

  it('请求频率与最大延迟换算正确', () => {
    expect(getQpsFromDelay(3)).toBe(20)
    expect(getQpsFromDelay(5)).toBe(12)
    expect(getQpsFromDelay(0)).toBe(60)
    expect(getMaxDelayMs(3)).toBe(4500)
    expect(getMaxDelayMs(5)).toBe(7500)
  })

  it('InputNumber 清空不静默折成 0', () => {
    expect(toFiniteNumberOrNull(null)).toBeNull()
    expect(toFiniteNumberOrNull(undefined)).toBeNull()
    expect(toFiniteNumberOrNull(Number.NaN)).toBeNull()
    expect(toFiniteNumberOrNull(0)).toBe(0)
  })
})
