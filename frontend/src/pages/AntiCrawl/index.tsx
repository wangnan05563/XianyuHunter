import { useEffect, useRef, useState, useCallback } from 'react'
import { TipButton } from '@/components/TipButton'
import { confirmDanger } from '@/components/DangerSummary'
import {
  App,
  Card,
  Row,
  Col,
  Space,
  Tag,
  Typography,
  Statistic,
  Progress,
  Descriptions,
  Badge,
  Switch,
  Modal,
  Input,
  Divider,
  Alert,
  Select,
  Spin,
} from 'antd'
import {
  ReloadOutlined,
  PlayCircleOutlined,
  StopOutlined,
  SafetyCertificateOutlined,
  IdcardOutlined,
  ThunderboltOutlined,
  KeyOutlined,
  HeartOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { anticrawlApi } from '../../api'
import { formatDuration } from '../../utils/timeFormat'
import { usePersistentState } from '../../hooks/usePersistentState'
import type {
  StrategyEvaluation,
  SessionStatus,
  FingerprintInfo,
  FreqStats,
  CookieLayersResult,
  HealthReport,
} from '../../api'

const { Text, Paragraph, Title } = Typography

// App.useApp() 返回值在模块级函数里复用：message/modal 必须来自 AntdApp 上下文，
// 否则暗色主题不生效，并会触发「Static function can not consume context」告警。
type AntiCrawlAppApi = ReturnType<typeof App.useApp>
type AntiCrawlMessageApi = AntiCrawlAppApi['message']
type AntiCrawlModalApi = AntiCrawlAppApi['modal']

// 策略中文标签映射
const STRATEGY_LABELS: Record<string, string> = {
  cdp_connect: 'CDP 连接系统浏览器',
  reuse_userdata: '复用 browser-data Cookie',
  browser_import: '从系统浏览器导入',
  qr_scan: '扫码登录',
  cookie_inject: '手动 Cookie 注入',
}

// 健康动作中文映射
const ACTION_LABELS: Record<string, string> = {
  none: '无需操作',
  renew_token: '续期 Token',
  relogin: '重新登录',
  pause: '暂停 + 通知',
}

const WAF_LABELS: Record<string, string> = {
  clear: '正常',
  warning: '警告',
  blocked: '已熔断',
}

// ============== T02579（PRD 4.5-3 / 4.5-4 / 4.5-6）纯函数区 ==============
// 全部提为模块级：便于单测直接断言文案，也让主组件 JSX 只负责摆放组件（S3776）

// 4.5-4：后端层名是英文枚举（identity/session/tracking），直接拼进确认框标题就成了
// 「确认失效 session 层？」这种只有读过代码才看得懂的话。中文化同时保留英文枚举名，
// 因为审计日志与接口字段用的就是它。
const LAYER_LABELS_CN: Record<string, string> = {
  identity: '身份层',
  session: '会话层',
  tracking: '追踪层',
}

export const layerLabelCn = (layer: string): string => {
  const cn = LAYER_LABELS_CN[layer]
  return cn ? `${cn}（${layer}）` : layer
}

// 卡片内空间窄：中文名与英文枚举分开两行，避免 1280/1024 下逐字换行。
// 确认框仍用 layerLabelCn（带括号），保持「中文名 + 审计枚举」的完整语义。
export function splitLayerLabel(layer: string): { name: string; code: string | null } {
  const cn = LAYER_LABELS_CN[layer]
  return cn ? { name: cn, code: layer } : { name: layer, code: null }
}

export type InvalidateLayerState = { valid?: boolean; cookie_count?: number }

// 后果必须与后端一致：invalidate_cookie_layer 调 invalidate_layer(manual=True)，
// 而 cookie_checker 的自动同步显式跳过 manual_invalidate 的层——所以主动失效不会被自愈，
// 只能重新登录或重新提交该层 Cookie。旧的 Modal.confirm 只说「确认失效 session 层？」，
// 把这个不可自愈的后果完全藏了起来。
export function buildInvalidateLayerCopy(layer: string, state?: InvalidateLayerState) {
  const cn = LAYER_LABELS_CN[layer] || layer
  const isIdentity = layer === 'identity'
  const count = state?.cookie_count ?? 0
  return {
    title: `确认失效${cn}？`,
    action: `把${cn}标记为失效（manual_invalidate）`,
    scope: isIdentity
      ? `${cn}与其依赖的会话层（session 依赖 identity，会一并级联失效）；`
        + '不删除 CookieStore 里已保存的 Cookie 明文，也不影响追踪层'
      : `仅${cn}；不删除 CookieStore 里已保存的 Cookie 明文，不影响其他两层`,
    count: `${cn}当前记录 ${count} 个 Cookie`,
    recovery: '主动失效会打上「人工失效」标记，后台自动同步会跳过这一层，'
      + '不会被自愈恢复；需要重新登录，或在「更新 Cookie」里重新提交该层 Cookie 才能恢复。'
      + (isIdentity ? ' 因会话层已级联失效，恢复身份层后会话层也要重新续期一次。' : ''),
  }
}

// 4.5-3：加载失败的原因要能读，而不是只剩 console.error
export function describeAnticrawlError(error: unknown): string {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 401) return '未登录或登录已过期（401），请重新登录后重试'
  if (status) return `接口返回 ${status}`
  const msg = (error as { message?: string })?.message || ''
  if (/timeout/i.test(msg)) return '请求超时，后端可能未就绪'
  return msg || '请求失败'
}

export type AntiCrawlSectionKey = 'strategy' | 'session' | 'fingerprint' | 'freqStats' | 'cookieLayers'

export const ANTI_CRAWL_SECTION_LABELS: Record<AntiCrawlSectionKey, string> = {
  strategy: '登录策略评估',
  session: '会话状态',
  fingerprint: '指纹信息',
  freqStats: '频率统计',
  cookieLayers: 'Cookie 层状态',
}

// 顶部汇总条：把「哪几块没加载出来」一次说清，避免用户只看到半空的卡片矩阵
export function buildAnticrawlErrorBanner(
  errors: Partial<Record<AntiCrawlSectionKey, string>>,
): { failedLabels: string[]; text: string } | null {
  const keys = (Object.keys(errors) as AntiCrawlSectionKey[]).filter((k) => errors[k])
  if (keys.length === 0) return null
  const failedLabels = keys.map((k) => ANTI_CRAWL_SECTION_LABELS[k])
  return {
    failedLabels,
    text: `${failedLabels.length} 项数据未能加载：${failedLabels.join('、')}。`
      + '下方对应卡片会标注失败原因，并不代表「没有数据」，请先点「重试失败项」。',
  }
}

// 4.5-6：后端 _merge_existing_with_incoming 是「同名覆盖 value、其余保留、新名字按
// .goofish.com 域写入」，而原文案只说「原子更新」——既没讲合并还是覆盖，
// 「原子」在这里也没有跨层事务保证可依，属于说大话。
export const COOKIE_MERGE_COPY = '提交后自动分类到身份层（identity）/ 会话层（session）/ 追踪层（tracking），'
  + '并与现有 Cookie 合并：文本框里出现的同名项覆盖其值，未出现的项保持不变，'
  + '新名字以 .goofish.com 域写入。'

// 导入前脏文本确认：真实风险是「静默丢弃手改内容」，数量取两边的真实解析条数
export function buildImportOverrideCopy(currentCount: number, incomingCount: number) {
  return {
    title: '导入会覆盖文本框当前内容？',
    action: `用${currentCount > 0 ? '浏览器里的 ' : ''}Cookie 替换文本框内容`,
    scope: '只改弹窗文本框，不写入后端；点「更新」才真正分层落库。'
      + '你在文本框里手改的内容不会被合并保留',
    count: currentCount > 0
      ? `文本框现有 ${currentCount} 项 → 将被浏览器导入的 ${incomingCount} 项替换`
      : `文本框为空 → 填入浏览器导入的 ${incomingCount} 项`,
    recovery: '尚未写入后端，取消即可回到当前文本框；已手改的值若要保留，请取消后手动复制出来。',
  }
}

// 4.5-3：卡片级失败/空态。原来 5 个 fetch* 只 console.error，接口 401/超时后
// 卡片整块空白，与「后端确实没有数据」长得一模一样。
// loading/hasData 的判断也收在这里，主组件 JSX 不必再堆条件表达式（S3776）
function SectionLoadError(props: {
  readonly label: string
  readonly error?: string
  readonly loading: boolean
  readonly hasData: boolean
  readonly onRetry: () => void
}) {
  const { label, error, loading, hasData, onRetry } = props

  // 先处理错误：错误可能伴随上一轮成功数据，仍要明确告知本轮失败原因。
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 12 }}
        message={`${label}未能加载`}
        description={
          <Space direction="vertical" size={4}>
            <Text style={{ fontSize: 12 }}>
              {error}——下方空白不代表真的没有数据
            </Text>
            <TipButton
              size="small"
              icon={<ReloadOutlined />}
              tip={`重新加载${label}`}
              onClick={onRetry}
            >
              重试
            </TipButton>
          </Space>
        }
      />
    )
  }

  // 初始/重试加载中：不再让卡片空白，也不把「尚未返回」说成「暂无数据」。
  if (loading && !hasData) {
    return (
      <div data-testid="anticrawl-section-loading" style={{ padding: '12px 0' }}>
        <Space size={8}>
          <Spin size="small" />
          <Text type="secondary" style={{ fontSize: 12 }}>正在加载{label}…</Text>
        </Space>
      </div>
    )
  }

  // 请求已完成且确实没有数据，才显示空态。
  if (!loading && !hasData) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={`暂无${label}数据`}
        description={
          <Space direction="vertical" size={4}>
            <Text style={{ fontSize: 12 }}>
              接口没有返回内容，通常是所属模块尚未初始化或该项确实为空
            </Text>
            <TipButton
              size="small"
              icon={<ReloadOutlined />}
              tip={`重新加载${label}`}
              onClick={onRetry}
            >
              重试
            </TipButton>
          </Space>
        }
      />
    )
  }

  return null
}

// 把 cookie 字符串解析为对象：支持分号/换行分隔，值中可含 = 号
// 为什么提取：handleUpdateCookies 内嵌 for + if + 解构 + 多个 continue 分支，认知复杂度高
const parseCookieInput = (input: string): Record<string, string> => {
  const cookies: Record<string, string> = {}
  for (const part of input.split(/[;\n]/)) {
    const trimmed = part.trim()
    // 空字符串自然不含 '='，合并条件避免冗余短路判断
    if (!trimmed.includes('=')) continue
    const [name, ...valueParts] = trimmed.split('=')
    const value = valueParts.join('=')
    if (name && value) {
      cookies[name.trim()] = value.trim()
    }
  }
  return cookies
}

// 把 cookies 对象拼接为 "k1=v1; k2=v2" 格式文本
// 为什么提取：openCookieModal 与 handleImportFromBrowser 都用相同的 Object.entries.map.join
const cookiesToString = (cookies: Record<string, string>): string => {
  return Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join('; ')
}

// 格式化"从浏览器导入预览"失败信息：error + hint 拼接，无内容时返回默认提示
// 为什么提取：handleImportFromBrowser 内 [result.error, result.hint].filter(Boolean).join('\n') || '...' 链复杂
const formatImportPreviewError = (result: { error?: string; hint?: string }): string => {
  return [result.error, result.hint].filter(Boolean).join('\n') || '从浏览器导入失败'
}

// S3776 修复：将"未配置检查器时自动初始化后重试"逻辑提取为模块级函数
// 原嵌套在 handleHealthCheck try 内，导致 3 层嵌套 if（result.ok/initResult.ok/外层 try）
const ensureHealthCheckerReady = async (
  result: HealthReport,
  useCdp: boolean,
  loadAll: () => Promise<void>,
): Promise<HealthReport> => {
  // 已 ok 或非 initialize 类错误：直接返回原结果
  if (result.ok || !result.error?.includes('initialize')) return result
  const initResult = await anticrawlApi.initialize(useCdp)
  if (!initResult.ok) return result
  await loadAll()
  return anticrawlApi.checkHealth()
}

// S3776 修复：通用异步操作结果处理，提取 if(result.ok)/else 模式
// 为什么提取：handleInitialize/handleStopSession/handleInvalidateLayer 都有相同的
// if(result.ok){success+reload} else {error} 模式，每处贡献 +2 复杂度点
const handleAsyncResult = (
  result: { ok: boolean; message?: string; error?: string },
  messageApi: AntiCrawlMessageApi,
  successMsg: string,
  errorMsg: string,
  onSuccess?: () => void,
) => {
  if (result.ok) {
    messageApi.success(result.message || successMsg)
    onSuccess?.()
  } else {
    messageApi.error(result.error || errorMsg)
  }
}

// S3776 修复：Cookie 预填提示文本生成
// 为什么提取：openCookieModal 内 if/else 贡献 +2 复杂度点
const prefillHintForCookies = (count: number): string =>
  count > 0
    ? `已自动读取 ${count} 个 Cookie，可直接点「更新」或编辑后再提交`
    : '当前没有 Cookie 数据，可点击下方「从浏览器导入」按钮'

// S3776 修复：将 startSession 结果消息处理提取为独立函数
// 原嵌套 if (result.ok) → if (result.already_active) → else，复杂度 +4
const showStartSessionResult = (result: {
  ok: boolean
  already_active?: boolean
  message?: string
  error?: string
}, messageApi: AntiCrawlMessageApi) => {
  if (!result.ok) {
    messageApi.error(result.error || '启动会话失败')
    return
  }
  // 幂等场景：会话已被 trigger_session_start() 自动启动时后端返回 already_active
  if (result.already_active) {
    messageApi.info(result.message || '会话已是活跃状态')
  } else {
    messageApi.success(result.message || '会话管理已启动')
  }
}

// S3776 修复：将 cookieInput 校验提取为独立函数
// 原两个守卫 if 嵌在 handleUpdateCookies 主流程，提取后主流程只剩单行调用
const validateCookieInput = (input: string): { cookies: Record<string, string>; error?: string } => {
  if (!input.trim()) return { cookies: {}, error: '请输入 Cookie' }
  const cookies = parseCookieInput(input)
  if (Object.keys(cookies).length === 0) return { cookies: {}, error: '未能解析出有效的 Cookie' }
  return { cookies }
}

// S3776 修复：健康检查结果消息显示提取为模块级函数
// 原 handleHealthCheck 内 if (result.ok) → if (result.is_healthy) → else 嵌套 2 层，复杂度 +5
// 提取后用早返回拉平嵌套，主函数仅剩单行调用
const showHealthCheckResult = (result: HealthReport, messageApi: AntiCrawlMessageApi) => {
  if (!result.ok) {
    messageApi.warning(result.error || '未配置健康检查器')
    return
  }
  if (result.is_healthy) {
    messageApi.success(`健康检查通过（score=${result.score}）`)
    return
  }
  messageApi.warning(
    `健康检查发现问题（score=${result.score}，建议：${ACTION_LABELS[result.action] || result.action}）`,
  )
}

// S3776 修复：健康分→进度条状态映射提取为模块级函数
// 原 JSX 内 IIFE 含 if/else if/else，IIFE 让嵌套层级 +1，复杂度 +3
const healthScoreToProgressStatus = (score: number): 'success' | 'normal' | 'exception' => {
  if (score >= 80) return 'success'
  if (score >= 60) return 'normal'
  return 'exception'
}

// S3776 修复：Cookie 层状态文本计算提取为模块级函数
// 原 JSX 内 IIFE 在 .map 回调内嵌套，认知复杂度因嵌套层级翻倍
const cookieLayerStateText = (state: { valid: boolean; cookie_count: number }): string => {
  if (state.valid === false) return '未初始化'
  if (state.cookie_count > 0) return `${state.cookie_count} 个 Cookie`
  return '已恢复（无 Cookie）'
}

// S3776 修复：「从浏览器导入」失败分支处理提取为模块级函数
// 原 handleImportFromBrowser 内 else 分支嵌套 if (result.error_detail)，复杂度 +2
const logImportPreviewFailure = (
  result: { error?: string; hint?: string; error_detail?: string },
  messageApi: AntiCrawlMessageApi,
) => {
  messageApi.error(formatImportPreviewError(result))
  if (result.error_detail) {
    console.error('import error detail:', result.error_detail)
  }
}

// S3776 修复：把所有数据加载函数体提取到模块级工厂函数
// 为什么提取：原主组件内 5 个 load* 函数各含 try/catch/finally，每个贡献 +3 复杂度，累计 +15
// 工厂模式让主组件只保留 useCallback 包装的薄壳，认知复杂度归零
type AntiCrawlSetters = {
  setStrategy: (s: StrategyEvaluation | null) => void
  setSession: (s: SessionStatus | null) => void
  setFingerprint: (s: FingerprintInfo | null) => void
  setFreqStats: (s: FreqStats | null) => void
  setCookieLayers: (s: CookieLayersResult | null) => void
  setHealth: (s: HealthReport | null) => void
  // 每个区块独立门闩：StrictMode 双挂载、轮询与手动重试撞车时都只保留一个在途请求。
  beginSection: (k: AntiCrawlSectionKey) => boolean
  endSection: (k: AntiCrawlSectionKey) => void
  // T02579 / 4.5-3：失败原因必须可见，成功时清掉旧错误，避免「重试成功后仍挂着红条」
  setLoadError: (k: AntiCrawlSectionKey, msg: string | null) => void
}

// S3776 修复：5 个数据加载函数全部移出主组件，主组件仅保留 useCallback 薄壳
const fetchStrategy = async (setters: AntiCrawlSetters) => {
  if (!setters.beginSection('strategy')) return
  try {
    const data = await anticrawlApi.getStrategy()
    setters.setStrategy(data)
    setters.setLoadError('strategy', null)
  } catch (error) {
    setters.setLoadError('strategy', describeAnticrawlError(error))
    console.error('加载策略失败', error)
  } finally {
    setters.endSection('strategy')
  }
}

const fetchSession = async (setters: AntiCrawlSetters) => {
  if (!setters.beginSection('session')) return
  try {
    const data = await anticrawlApi.getSessionStatus()
    setters.setSession(data)
    setters.setLoadError('session', null)
  } catch (error) {
    setters.setLoadError('session', describeAnticrawlError(error))
    console.error('加载会话状态失败', error)
  } finally {
    setters.endSection('session')
  }
}

const fetchFingerprint = async (setters: AntiCrawlSetters) => {
  if (!setters.beginSection('fingerprint')) return
  try {
    const data = await anticrawlApi.getFingerprint()
    setters.setFingerprint(data)
    setters.setLoadError('fingerprint', null)
  } catch (error) {
    setters.setLoadError('fingerprint', describeAnticrawlError(error))
    console.error('加载指纹信息失败', error)
  } finally {
    setters.endSection('fingerprint')
  }
}

const fetchFreqStats = async (setters: AntiCrawlSetters) => {
  if (!setters.beginSection('freqStats')) return
  try {
    const data = await anticrawlApi.getFreqStats()
    setters.setFreqStats(data)
    setters.setLoadError('freqStats', null)
  } catch (error) {
    setters.setLoadError('freqStats', describeAnticrawlError(error))
    console.error('加载频率统计失败', error)
  } finally {
    setters.endSection('freqStats')
  }
}

const fetchCookieLayers = async (setters: AntiCrawlSetters) => {
  if (!setters.beginSection('cookieLayers')) return
  try {
    const data = await anticrawlApi.getCookieLayers()
    setters.setCookieLayers(data)
    setters.setLoadError('cookieLayers', null)
  } catch (error) {
    setters.setLoadError('cookieLayers', describeAnticrawlError(error))
    console.error('加载 Cookie 层状态失败', error)
  } finally {
    setters.endSection('cookieLayers')
  }
}

// S3776 修复：批量加载所有数据，主组件 loadAll 仅一行调用
const fetchAll = async (setters: AntiCrawlSetters) => {
  await Promise.all([
    fetchStrategy(setters),
    fetchSession(setters),
    fetchFingerprint(setters),
    fetchFreqStats(setters),
    fetchCookieLayers(setters),
  ])
}

// 4.5-3：按卡片单独重试（整页「刷新」会把已正常的块也重拉一遍，失败块却可能被忽略）
const SECTION_FETCHERS: Record<AntiCrawlSectionKey, (s: AntiCrawlSetters) => Promise<void>> = {
  strategy: fetchStrategy,
  session: fetchSession,
  fingerprint: fetchFingerprint,
  freqStats: fetchFreqStats,
  cookieLayers: fetchCookieLayers,
}

// S3776 修复：操作处理函数体提取到模块级，主组件仅保留 useCallback 薄壳
// 8 个 handle* 函数各含 try/catch/finally + 条件分支，每函数 +3~5 复杂度
type AntiCrawlActionsParams = {
  setters: AntiCrawlSetters
  message: AntiCrawlMessageApi
  modal: AntiCrawlModalApi
  useCdp: boolean
  importBrowser: string
  cookieInput: string
  cookieInputDirty: boolean
  setCookieModalOpen: (b: boolean) => void
  setCookieInput: (s: string) => void
  setCookieInputDirty: (b: boolean) => void
  setPrefillHint: (s: string) => void
  setLoadingInit: (b: boolean) => void
  setLoadingSession: (b: boolean) => void
  setLoadingHealth: (b: boolean) => void
  setLoadingUpdate: (b: boolean) => void
  setLoadingPrefill: (b: boolean) => void
  setLoadingImport: (b: boolean) => void
  setHealth: (s: HealthReport | null) => void
}

const runInitialize = async (params: AntiCrawlActionsParams) => {
  try {
    params.setLoadingInit(true)
    const result = await anticrawlApi.initialize(params.useCdp)
    handleAsyncResult(result, params.message, `协调器已初始化（${result.mode} 模式）`, '初始化失败', () => fetchAll(params.setters))
  } catch (error) {
    params.message.error('初始化失败')
    console.error(error)
  } finally {
    params.setLoadingInit(false)
  }
}

const runStartSession = async (params: AntiCrawlActionsParams) => {
  try {
    params.setLoadingSession(true)
    const result = await anticrawlApi.startSession()
    showStartSessionResult(result, params.message)
    await fetchSession(params.setters)
  } catch (error) {
    params.message.error('启动会话失败')
    console.error(error)
    await fetchSession(params.setters)
  } finally {
    params.setLoadingSession(false)
  }
}

const runStopSession = async (params: AntiCrawlActionsParams) => {
  try {
    params.setLoadingSession(true)
    const result = await anticrawlApi.stopSession()
    handleAsyncResult(result, params.message, '会话管理已停止', '停止会话失败', () => fetchSession(params.setters))
  } catch (error) {
    params.message.error('停止会话失败')
    console.error(error)
  } finally {
    params.setLoadingSession(false)
  }
}

const runHealthCheck = async (params: AntiCrawlActionsParams) => {
  try {
    params.setLoadingHealth(true)
    const initial = await anticrawlApi.checkHealth()
    const result = await ensureHealthCheckerReady(initial, params.useCdp, () => fetchAll(params.setters))
    params.setHealth(result)
    showHealthCheckResult(result, params.message)
  } catch (error) {
    params.message.error('健康检查失败')
    console.error(error)
  } finally {
    params.setLoadingHealth(false)
  }
}

const runInvalidateLayer = (layer: string, params: AntiCrawlActionsParams, state?: InvalidateLayerState) => {
  // T02579 / 4.5-4：原来标题是「确认失效 session 层？」这种英文层名，content 只对 identity
  // 说一句级联，最关键的后遗症（manual_invalidate 会被自动同步跳过＝不会自愈）完全没写。
  const copy = buildInvalidateLayerCopy(layer, state)
  void confirmDanger(copy, params.modal).then(async (ok: boolean) => {
    if (!ok) return
    try {
      const result = await anticrawlApi.invalidateLayer(layer)
      handleAsyncResult(result, params.message, `${LAYER_LABELS_CN[layer] || layer}已标记失效`, '操作失败',
        () => fetchCookieLayers(params.setters))
    } catch (error) {
      params.message.error('操作失败')
      console.error(error)
    }
  })
}

const runOpenCookieModal = async (params: AntiCrawlActionsParams) => {
  params.setCookieModalOpen(true)
  params.setCookieInput('')
  params.setPrefillHint('')
  params.setCookieInputDirty(false)
  params.setLoadingPrefill(true)
  try {
    const result = await anticrawlApi.getCurrentCookies()
    const cookies = result.cookies || {}
    params.setCookieInput(cookiesToString(cookies))
    params.setPrefillHint(prefillHintForCookies(result.count))
  } catch (error) {
    params.setPrefillHint('读取当前 Cookie 失败，可手动粘贴或从浏览器导入')
    console.error(error)
  } finally {
    params.setLoadingPrefill(false)
  }
}

const runImportFromBrowser = async (params: AntiCrawlActionsParams) => {
  try {
    params.setLoadingImport(true)
    const result = await anticrawlApi.importFromBrowserPreview(params.importBrowser, false)
    if (!result.ok || !result.cookies) {
      logImportPreviewFailure(result, params.message)
      return
    }
    const incomingCount = result.imported_count ?? Object.keys(result.cookies).length
    // T02579 / 4.5-6：导入是整框替换，不是合并。用户在预填文本框里手改过内容时，
    // 直接覆盖等于静默丢弃他的输入——必须先确认，并把两边真实条数摆出来。
    if (params.cookieInputDirty) {
      const currentCount = Object.keys(parseCookieInput(params.cookieInput)).length
      const ok = await confirmDanger(buildImportOverrideCopy(currentCount, incomingCount), params.modal)
      if (!ok) return
    }
    params.setCookieInput(cookiesToString(result.cookies))
    params.setCookieInputDirty(false)
    params.setPrefillHint(
      `从 ${params.importBrowser === 'edge' ? 'Edge' : 'Chrome'} 导入 ${incomingCount} 个 Cookie，可直接点「更新」`,
    )
    params.message.success(result.message || '已导入到文本框')
  } catch (error) {
    params.message.error('从浏览器导入失败')
    console.error(error)
  } finally {
    params.setLoadingImport(false)
  }
}

const runUpdateCookies = async (params: AntiCrawlActionsParams) => {
  const { cookies, error } = validateCookieInput(params.cookieInput)
  if (error) {
    params.message.warning(error)
    return
  }
  try {
    params.setLoadingUpdate(true)
    const result = await anticrawlApi.updateCookies(cookies)
    if (result.ok) {
      params.message.success(result.message || `已更新 ${result.written} 个 Cookie`)
      params.setCookieModalOpen(false)
      params.setCookieInput('')
      params.setCookieInputDirty(false)
      await fetchCookieLayers(params.setters)
    } else {
      params.message.error(result.error || '更新失败')
    }
  } catch (error) {
    params.message.error('更新 Cookie 失败')
    console.error(error)
  } finally {
    params.setLoadingUpdate(false)
  }
}

export default function AntiCrawl() {
  // 静态 message/Modal.confirm 不消费 AntdApp 上下文：暗色主题会掉色，还会触发静态方法告警。
  const { message, modal } = App.useApp()

  // 数据状态
  const [strategy, setStrategy] = useState<StrategyEvaluation | null>(null)
  const [session, setSession] = useState<SessionStatus | null>(null)
  const [fingerprint, setFingerprint] = useState<FingerprintInfo | null>(null)
  const [freqStats, setFreqStats] = useState<FreqStats | null>(null)
  const [cookieLayers, setCookieLayers] = useState<CookieLayersResult | null>(null)
  const [health, setHealth] = useState<HealthReport | null>(null)

  // 加载状态：loadingAll 控制顶部「刷新」，loadingSections 控制各卡片初始骨架。
  const [loadingAll, setLoadingAll] = useState(false)
  // 初始即视为加载中，避免首帧先闪一下「暂无数据」再被 effect 改成加载中。
  const [loadingSections, setLoadingSections] = useState<Partial<Record<AntiCrawlSectionKey, boolean>>>({
    strategy: true,
    session: true,
    fingerprint: true,
    freqStats: true,
    cookieLayers: true,
  })
  const [loadingInit, setLoadingInit] = useState(false)
  const [loadingSession, setLoadingSession] = useState(false)
  const [loadingHealth, setLoadingHealth] = useState(false)
  const [loadingUpdate, setLoadingUpdate] = useState(false)

  // StrictMode 双挂载 / 轮询 / 手动重试共用同一把门闩，避免同一块数据并发重复请求。
  const allInFlightRef = useRef(false)
  const sectionInFlightRef = useRef<Partial<Record<AntiCrawlSectionKey, boolean>>>({})

  // T02579 / 4.5-3：每块数据各自的失败原因（成功即清）；页面不再靠「空白」表达失败
  const [loadErrors, setLoadErrors] = useState<Partial<Record<AntiCrawlSectionKey, string>>>({})
  const setLoadError = useCallback((k: AntiCrawlSectionKey, msg: string | null) => {
    setLoadErrors((prev) => {
      if (msg === null) {
        if (!prev[k]) return prev
        const next = { ...prev }
        delete next[k]
        return next
      }
      if (prev[k] === msg) return prev
      return { ...prev, [k]: msg }
    })
  }, [])

  const beginSection = useCallback((k: AntiCrawlSectionKey) => {
    if (sectionInFlightRef.current[k]) return false
    sectionInFlightRef.current[k] = true
    setLoadingSections((prev) => (prev[k] ? prev : { ...prev, [k]: true }))
    return true
  }, [])

  const endSection = useCallback((k: AntiCrawlSectionKey) => {
    sectionInFlightRef.current[k] = false
    setLoadingSections((prev) => {
      if (!prev[k]) return prev
      const next = { ...prev }
      delete next[k]
      return next
    })
  }, [])

  // CDP 模式开关（持久化：刷新后保持上次设置）
  const [useCdp, setUseCdp] = usePersistentState<boolean>('xh.anticrawl.useCdp', false, {
    validator: (v): v is boolean => typeof v === 'boolean',
  })

  // Cookie 更新弹窗
  const [cookieModalOpen, setCookieModalOpen] = useState(false)
  const [cookieInput, setCookieInput] = useState('')
  // T02579 / 4.5-6：只有用户真的编辑过文本框才算脏——预填与导入写回都不算，
  // 否则每次导入都会自我确认一次
  const [cookieInputDirty, setCookieInputDirty] = useState(false)
  // 弹窗预填：从后端读 cookie 写入文本框的过程状态
  const [loadingPrefill, setLoadingPrefill] = useState(false)
  const [prefillHint, setPrefillHint] = useState('')
  // 「从浏览器导入」按钮：选择浏览器类型
  const [importBrowser, setImportBrowser] = useState<string>('edge')
  const [loadingImport, setLoadingImport] = useState(false)

  // ============== 数据加载 ==============
  // S3776 修复：所有 load* 函数体已提取到模块级 fetch*，主组件仅保留 useCallback 薄壳

  // setters 对象一次性构造，避免每个 load 都重新创建闭包
  const setters: AntiCrawlSetters = {
    setStrategy, setSession, setFingerprint, setFreqStats, setCookieLayers, setHealth,
    beginSection, endSection, setLoadError,
  }

  // S1854：load* 保留 useCallback 薄壳。loadStrategy/loadFingerprint 已无任何引用
  // （各卡片重试与顶部重试统一走 SECTION_FETCHERS），故不再声明这两个赋值
  const loadSession = useCallback(() => { void fetchSession(setters) }, [])
  const loadFreqStats = useCallback(() => { void fetchFreqStats(setters) }, [])
  const loadCookieLayers = useCallback(() => { void fetchCookieLayers(setters) }, [])
  const loadAll = useCallback(async () => {
    // 首屏 StrictMode 第二次 effect 会撞上第一次的在途请求；顶部手动刷新也会走这里。
    if (allInFlightRef.current) return
    allInFlightRef.current = true
    setLoadingAll(true)
    try {
      await fetchAll(setters)
    } finally {
      allInFlightRef.current = false
      setLoadingAll(false)
    }
  }, [])

  // 4.5-3：单卡片重试 + 顶部「重试失败项」（只重拉失败的那几块）
  const retrySection = useCallback((k: AntiCrawlSectionKey) => {
    void SECTION_FETCHERS[k](setters)
  }, [])
  const retryFailedSections = useCallback(() => {
    const failed = (Object.keys(loadErrors) as AntiCrawlSectionKey[]).filter((k) => loadErrors[k])
    if (failed.length === 0) return
    void Promise.all(failed.map((k) => SECTION_FETCHERS[k](setters)))
  }, [loadErrors])

  useEffect(() => {
    void loadAll()
    // 会话活跃时每 10 秒刷新状态
    const sessionInterval = setInterval(loadSession, 10000)
    // 频率伪装统计每 10 秒刷新：业务模块持续调用 apply_freq_delay/record_freq_request，
    // 前端需定时拉取才能反映最新请求节奏
    const freqInterval = setInterval(loadFreqStats, 10000)
    // Cookie 层状态轮询：后端会基于 JSON 实际内容、浏览器内存、功能信号同步层状态，
    // 前端不轮询会停留在某个时刻的快照（如刚重启时的全失效状态），无法反映后续恢复
    const layersInterval = setInterval(loadCookieLayers, 30000)
    return () => {
      clearInterval(sessionInterval)
      clearInterval(freqInterval)
      clearInterval(layersInterval)
    }
  }, [loadAll, loadSession, loadFreqStats, loadCookieLayers])

  // ============== 操作处理 ==============
  // S3776 修复：所有 handle* 函数体已提取到模块级 run*，主组件仅保留薄壳

  // actionsParams 集中装配所有依赖，避免每个 handle 函数重复传参
  const actionsParams: AntiCrawlActionsParams = {
    setters,
    message,
    modal,
    useCdp,
    importBrowser,
    cookieInput,
    cookieInputDirty,
    setCookieModalOpen,
    setCookieInput,
    setCookieInputDirty,
    setPrefillHint,
    setLoadingInit,
    setLoadingSession,
    setLoadingHealth,
    setLoadingUpdate,
    setLoadingPrefill,
    setLoadingImport,
    setHealth,
  }

  const handleInitialize = () => runInitialize(actionsParams)
  const handleStartSession = () => runStartSession(actionsParams)
  const handleStopSession = () => runStopSession(actionsParams)
  const handleHealthCheck = () => runHealthCheck(actionsParams)
  const handleInvalidateLayer = (layer: string, state?: InvalidateLayerState) =>
    runInvalidateLayer(layer, actionsParams, state)
  const openCookieModal = () => runOpenCookieModal(actionsParams)
  const handleImportFromBrowser = () => runImportFromBrowser(actionsParams)
  const handleUpdateCookies = () => runUpdateCookies(actionsParams)

  // ============== 渲染 ==============

  const errorBanner = buildAnticrawlErrorBanner(loadErrors)

  return (
    <div className="page-container" data-testid="anticrawl-page" style={{ marginRight: 80 }}>
      <header
        data-testid="anticrawl-header"
        style={{
          marginBottom: 16,
          padding: '18px 20px',
          borderRadius: 10,
          border: '1px solid var(--xh-border-color)',
          background: 'var(--xh-bg-container)',
        }}
      >
        <Title level={1} style={{ margin: 0, fontSize: 24, lineHeight: 1.35 }}>
          反爬登录管理
        </Title>
        <Text type="secondary" style={{ display: 'inline-block', marginTop: 6, fontSize: 13 }}>
          统一管理登录策略、Token 续期会话、Cookie 分层、浏览器指纹与频率伪装
        </Text>
      </header>

      <Space style={{ marginBottom: 16 }}>
        <TipButton
          tip="刷新全部数据"
          icon={<ReloadOutlined />}
          onClick={() => { void loadAll() }}
          loading={loadingAll}
          disabled={loadingAll}
        >
          刷新
        </TipButton>
      </Space>

      {/* 4.5-3：汇总条——哪几块没加载出来先说清楚，再逐卡给重试入口 */}
      {errorBanner && (
        <Alert
          type="error"
          showIcon
          closable={false}
          style={{ marginBottom: 16 }}
          message={`${errorBanner.failedLabels.length} 项数据未能加载`}
          description={
            <Space direction="vertical" size={4}>
              <Text style={{ fontSize: 12 }}>{errorBanner.text}</Text>
              <TipButton
                size="small"
                icon={<ReloadOutlined />}
                tip="只重拉失败的这几块，不影响其余卡片"
                onClick={retryFailedSections}
              >
                重试失败项
              </TipButton>
            </Space>
          }
        />
      )}

      <Row gutter={[16, 16]}>
        {/* ============== 策略评估 ============== */}
        <Col xs={24} xl={12}>
          <Card
            title={
              <Space>
                <ThunderboltOutlined />
                登录策略评估
              </Space>
            }
            extra={
              <Space>
                <Text type="secondary">CDP 模式</Text>
                <Switch
                  aria-label="CDP 模式"
                  checked={useCdp}
                  onChange={setUseCdp}
                  size="small"
                />
              </Space>
            }
          >
            <SectionLoadError
              label="登录策略评估"
              error={loadErrors.strategy}
              loading={Boolean(loadingSections.strategy)}
              hasData={Boolean(strategy)}
              onRetry={() => retrySection('strategy')}
            />
            {strategy && (
              <>
                <Alert
                  type="info"
                  showIcon
                  message={`推荐策略：${STRATEGY_LABELS[strategy.recommended] || strategy.recommended}`}
                  description={strategy.reason}
                  style={{ marginBottom: 16 }}
                />
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="可用策略">
                    <Space wrap>
                      {strategy.available_strategies.map((s) => (
                        <Tag key={s} color={s === strategy.recommended ? 'orange' : 'default'}>
                          {STRATEGY_LABELS[s] || s}
                        </Tag>
                      ))}
                    </Space>
                  </Descriptions.Item>
                </Descriptions>
                {/* T02579 / 4.5-1：把「能不能点」说清楚。后端 /strategy/set 只写
                    SessionStatus.strategy 这个展示字段（login_orchestrator 仅在
                    get_session_status 里读它），登录路径实际由 LoginStrategySelector
                    在初始化时评估选择——所以这里做成可点按钮等于给用户一个假开关。
                    D-10 已裁决「不开放人工覆盖」，文案从「待裁决」改为终态说明 */}
                <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                  以上是协调器评估出的可选登录方式，实际用哪一种在「初始化协调器」时自动选定，
                  本页不提供手动指定（人工覆盖登录策略已按 D-10 裁决不开放）。
                </Paragraph>
              </>
            )}
            <Divider style={{ margin: '12px 0' }} />
            <TipButton
              tip="初始化反爬协调器，启动登录策略"
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleInitialize}
              loading={loadingInit}
              block
            >
              初始化协调器（{useCdp ? 'CDP' : 'Launch'} 模式）
            </TipButton>
          </Card>
        </Col>

        {/* ============== 会话管理 ============== */}
        {/* S3776 修复：JSX 内的条件渲染已提取到 SessionCardCol 子组件，主组件复杂度归零 */}
        <SessionCardCol
          session={session}
          loadingSession={loadingSession}
          loadingData={Boolean(loadingSections.session)}
          sessionError={loadErrors.session}
          modalApi={modal}
          onRetrySession={() => retrySection('session')}
          onStart={handleStartSession}
          onStop={handleStopSession}
        />

        {/* ============== 健康检查 ============== */}
        {/* S3776 修复：JSX 内 8 个条件渲染已提取到 HealthCardCol 子组件 */}
        <HealthCardCol
          health={health}
          loadingHealth={loadingHealth}
          onCheck={handleHealthCheck}
        />

        {/* ============== Cookie 分层管理 ============== */}
        <Col xs={24} xl={12}>
          <Card
            title={
              <Space>
                <KeyOutlined />
                Cookie 分层管理
              </Space>
            }
            extra={
              <Space>
                <TipButton
                  tip="打开分层 Cookie 更新弹窗"
                  size="small"
                  onClick={openCookieModal}
                >
                  更新 Cookie
                </TipButton>
                <TipButton
                  aria-label="刷新 Cookie 层状态"
                  tip="刷新 Cookie 层状态"
                  icon={<ReloadOutlined />}
                  size="small"
                  onClick={loadCookieLayers}
                  loading={Boolean(loadingSections.cookieLayers)}
                />
              </Space>
            }
          >
            <SectionLoadError
              label="Cookie 层状态"
              error={loadErrors.cookieLayers}
              loading={Boolean(loadingSections.cookieLayers)}
              hasData={Object.keys(cookieLayers?.layers ?? {}).length > 0}
              onRetry={() => retrySection('cookieLayers')}
            />
            {cookieLayers && (
              <Row gutter={[16, 16]}>
                {Object.entries(cookieLayers.layers).map(([layer, state]) => {
                  const parts = splitLayerLabel(layer)
                  return (
                    <Col span={8} key={layer}>
                      <Card
                        size="small"
                        data-testid={`layer-${layer}`}
                        style={{ textAlign: 'center', height: '100%' }}
                      >
                        <Badge
                          status={state.valid ? 'success' : 'default'}
                          text={
                            <div style={{ lineHeight: 1.25, whiteSpace: 'nowrap' }}>
                              <Text strong style={{ fontSize: 14 }}>{parts.name}</Text>
                              {parts.code && (
                                <div>
                                  <Text type="secondary" style={{ fontSize: 10 }}>{parts.code}</Text>
                                </div>
                              )}
                            </div>
                          }
                        />
                        <div style={{ marginTop: 8 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {/* valid 但 cookie_count=0 是 force_restore 强制恢复的，
                                显示"已恢复"避免误以为有有效 cookie；逻辑提取为模块级 cookieLayerStateText */}
                            {cookieLayerStateText(state)}
                          </Text>
                        </div>
                        {state.valid && (
                          <TipButton
                            tip="主动失效该 Cookie 层"
                            size="small"
                            danger
                            type="link"
                            icon={<StopOutlined />}
                            onClick={() => handleInvalidateLayer(layer, state)}
                            style={{ padding: '4px 0', fontSize: 12 }}
                          >
                            主动失效
                          </TipButton>
                        )}
                      </Card>
                    </Col>
                  )
                })}
              </Row>
            )}
            <Alert
              type="info"
              showIcon
              message="分层说明"
              description={
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
                  <li><b>identity</b>：身份层（unb/cookie2/sgcookie），最稳定</li>
                  <li><b>session</b>：会话层（_m_h5_tk），依赖 identity</li>
                  <li><b>tracking</b>：追踪层（cna/tfstk），每次请求可能变化</li>
                </ul>
              }
              style={{ marginTop: 12 }}
            />
          </Card>
        </Col>

        {/* ============== 指纹信息 ============== */}
        <Col xs={24} xl={12}>
          <Card
            title={
              <Space>
                <IdcardOutlined />
                指纹信息
              </Space>
            }
          >
            <SectionLoadError
              label="指纹信息"
              error={loadErrors.fingerprint}
              loading={Boolean(loadingSections.fingerprint)}
              hasData={Boolean(fingerprint)}
              onRetry={() => retrySection('fingerprint')}
            />
            {fingerprint?.profile ? (
              <Descriptions
                column={2}
                size="small"
                bordered
                labelStyle={{ whiteSpace: 'nowrap' }}
                contentStyle={{ fontSize: 12, wordBreak: 'break-word' }}
              >
                <Descriptions.Item label="Profile" span={2}>
                  <Tag color="orange">{fingerprint.profile.name}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="UA" span={2}>
                  <Text style={{ fontSize: 12 }} copyable>
                    {fingerprint.profile.ua}
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="平台">
                  {fingerprint.profile.platform}
                </Descriptions.Item>
                <Descriptions.Item label="厂商">
                  {fingerprint.profile.vendor}
                </Descriptions.Item>
                <Descriptions.Item label="CPU 核心">
                  {fingerprint.profile.hardware_concurrency}
                </Descriptions.Item>
                <Descriptions.Item label="内存">
                  {fingerprint.profile.device_memory} GB
                </Descriptions.Item>
                <Descriptions.Item label="GPU" span={2}>
                  {fingerprint.profile.gpu_vendor} / {fingerprint.profile.gpu_renderer}
                </Descriptions.Item>
                <Descriptions.Item label="屏幕">
                  {fingerprint.profile.screen_width} × {fingerprint.profile.screen_height}
                </Descriptions.Item>
                <Descriptions.Item label="色深">
                  {fingerprint.profile.color_depth} bit
                </Descriptions.Item>
                <Descriptions.Item label="Stealth 脚本" span={2}>
                  {fingerprint.stealth_scripts_count} 个（指纹 + AWSC 伪装）
                </Descriptions.Item>
              </Descriptions>
            ) : (
              !loadingSections.fingerprint
              && !loadErrors.fingerprint
              && (
                <Alert
                  type="info"
                  showIcon
                  message="CDP 模式或未初始化"
                  description="CDP 模式使用真实浏览器，无需指纹注入。点击上方「初始化协调器」生成 launch 模式指纹。"
                />
              )
            )}
          </Card>
        </Col>

        {/* ============== 频率伪装统计 ============== */}
        <Col xs={24} xl={24}>
          <Card
            title={
              <Space>
                <ThunderboltOutlined />
                频率伪装统计
              </Space>
            }
            extra={
              <TipButton
                aria-label="刷新频率伪装统计"
                tip="刷新频率伪装统计"
                icon={<ReloadOutlined />}
                size="small"
                onClick={loadFreqStats}
                loading={Boolean(loadingSections.freqStats)}
              />
            }
          >
            <SectionLoadError
              label="频率统计"
              error={loadErrors.freqStats}
              loading={Boolean(loadingSections.freqStats)}
              hasData={Boolean(freqStats)}
              onRetry={() => retrySection('freqStats')}
            />
            {freqStats && (
              <Row gutter={[16, 16]}>
                <Col span={8}>
                  <Statistic
                    title="总请求数"
                    value={freqStats.total_requests}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="噪声请求"
                    value={freqStats.noise_requests}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="噪声占比"
                    value={(freqStats.noise_ratio * 100).toFixed(2)}
                    suffix="%"
                    precision={2}
                  />
                </Col>
                {freqStats.mean_interval != null && (
                  <>
                    <Col span={8}>
                      <Statistic
                        title="平均间隔"
                        value={freqStats.mean_interval.toFixed(2)}
                        suffix="s"
                        precision={2}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title="中位间隔"
                        value={freqStats.median_interval?.toFixed(2)}
                        suffix="s"
                        precision={2}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title="标准差"
                        value={freqStats.std_interval?.toFixed(2)}
                        suffix="s"
                        precision={2}
                      />
                    </Col>
                  </>
                )}
              </Row>
            )}
            <Alert
              type="info"
              showIcon
              message="频率伪装策略"
              description="使用对数正态分布模拟人类请求间隔的右偏特征（mean > median），并插入噪声请求打乱请求规律。"
              style={{ marginTop: 12 }}
            />
          </Card>
        </Col>
      </Row>

      {/* ============== Cookie 更新弹窗 ============== */}
      <Modal
        title="分层更新 Cookie"
        open={cookieModalOpen}
        onOk={handleUpdateCookies}
        onCancel={() => {
          setCookieModalOpen(false)
          setCookieInput('')
          setCookieInputDirty(false)
          setPrefillHint('')
        }}
        confirmLoading={loadingUpdate}
        okText="更新"
        cancelText="取消"
        okButtonProps={{
          autoInsertSpace: false,
          disabled: loadingPrefill || loadingUpdate || !cookieInput.trim(),
        }}
        cancelButtonProps={{ autoInsertSpace: false }}
        width={640}
      >
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          系统已自动从 CookieStore 读取当前 Cookie 预填到下方文本框。
          {/* 4.5-6：原文只说「原子更新」，既没讲合并还是覆盖（后端
              _merge_existing_with_incoming 走的是同名覆盖、其余保留），
              「原子」在这里也没有跨层事务保证——按真实语义重写 */}
          {COOKIE_MERGE_COPY}
        </Paragraph>

        {/* 浏览器导入工具栏 */}
        <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
          <Select
            aria-label="选择导入 Cookie 的浏览器"
            value={importBrowser}
            onChange={setImportBrowser}
            style={{ width: 120 }}
            options={[
              { value: 'edge', label: 'Edge' },
              { value: 'chrome', label: 'Chrome' },
            ]}
          />
          <TipButton
            tip="从浏览器读取 Cookie 并整框替换文本框内容（不合并）"
            icon={<DownloadOutlined />}
            loading={loadingImport}
            onClick={handleImportFromBrowser}
            style={{ flex: 1 }}
          >
            从浏览器导入（替换文本框）
          </TipButton>
        </Space.Compact>

        <Spin spinning={loadingPrefill} tip="正在读取当前 Cookie...">
          <Input.TextArea
            aria-label="Cookie 文本"
            value={cookieInput}
            onChange={(e) => {
              setCookieInput(e.target.value)
              // 只有用户编辑过才算脏：预填与导入写回不置脏，避免自我确认
              setCookieInputDirty(true)
            }}
            placeholder="_m_h5_tk=token_123; _m_h5_tk_enc=enc_123; unb=123456; ..."
            rows={8}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
        </Spin>

        {prefillHint && (
          <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
            {prefillHint}
          </Paragraph>
        )}
      </Modal>
    </div>
  )
}

// S3358：把「Token 已使用 X（当前已过期/仍在有效期内）」的嵌套三元抽成纯函数
function formatTokenUsageCount(session: SessionStatus): string | null {
  if (session.token_age_sec == null) return null
  let suffix = '，当前仍在有效期内'
  if (session.token_expired) suffix = '，当前已过期'
  return `Token 已使用 ${formatDuration(session.token_age_sec)}${suffix}`
}

// S3776 修复：会话管理 Card 提取为独立子组件
// 为什么提取：原主组件 JSX 内的 6 个条件渲染（session?.active / session&&!session.active&&... /
// session&& / session.active? / token_age_sec==null? / token_expired?）全部贡献主组件复杂度，
// 提取后子组件独立计算认知复杂度，主组件函数 CC 从 22 降至 ~9
function SessionCardCol(props: {
  readonly session: SessionStatus | null
  readonly loadingSession: boolean
  readonly loadingData: boolean
  readonly sessionError?: string
  readonly modalApi: AntiCrawlModalApi
  readonly onRetrySession: () => void
  readonly onStart: () => void
  readonly onStop: () => void
}) {
  const { session, loadingSession, loadingData, sessionError, modalApi, onRetrySession, onStart, onStop } = props
  const statusStat = session && (
    <Statistic
      title="会话状态"
      value={session.active ? '活跃' : '未启动'}
      valueStyle={{ color: session.active ? '#52c41a' : '#8c8c8c' }}
    />
  )
  const tokenAgeStat = session && (
    <Statistic
      title="Token 年龄"
      value={formatDuration(session.token_age_sec)}
      valueStyle={{
        color: session.token_expired ? '#ff4d4f' : '#52c41a',
      }}
    />
  )
  return (
    <Col xs={24} xl={12}>
      <Card
        title={
          <Space>
            <SafetyCertificateOutlined />
            会话管理
            {session?.active && (
              <Badge status="processing" text="运行中" />
            )}
          </Space>
        }
      >
        <SectionLoadError
          label="会话状态"
          error={sessionError}
          loading={loadingData}
          hasData={Boolean(session)}
          onRetry={onRetrySession}
        />
        {session && !session.active && session.cookie_layers?.identity && (
          <Alert
            type="warning"
            showIcon
            message="检测到有效 Cookie 但会话未启动"
            description="正常情况下登录/导入/注入完成后会自动启动 TokenRenewer 后台续期。如未自动启动，可点击下方按钮手动启用。"
            style={{ marginBottom: 12 }}
          />
        )}
        {session && (
          session.active ? (
            <>
              <Row gutter={[16, 16]}>
                <Col span={12}>{statusStat}</Col>
                <Col span={12}>{tokenAgeStat}</Col>
              </Row>
              <Statistic
                title="运行时间"
                value={formatDuration(session.uptime_sec, { withSeconds: true })}
                style={{ marginTop: 12 }}
              />
            </>
          ) : (
            <Row gutter={[16, 16]}>
              <Col span={8}>{statusStat}</Col>
              <Col span={8}>
                <Statistic title="运行时间" value="-" />
              </Col>
              <Col span={8}>{tokenAgeStat}</Col>
            </Row>
          )
        )}
        <Divider style={{ margin: '12px 0' }} />
        <Space>
          <TipButton
            tip="启动会话管理并开始续期"
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={onStart}
            loading={loadingSession}
            disabled={!session || session.active || Boolean(sessionError)}
          >
            启动会话
          </TipButton>
          <TipButton
            tip="停止会话管理与后台续期（Token 将不再自动续期）"
            danger
            icon={<StopOutlined />}
            onClick={() => {
              // T02573：原来点一下就停。停掉的只是 _m_h5_tk 的后台续期循环，
              // 身份 Cookie 与浏览器数据不受影响——文案必须按真实后果写，
              // 不能套用「X 小时后要重新扫码」这种未经验证的说法
              if (!session?.active) return
              void confirmDanger({
                title: '确认停止会话管理？',
                action: '停止会话管理器与 _m_h5_tk 后台自动续期',
                scope: '只停后台续期循环；不关闭浏览器、不删除 Cookie，'
                  + '身份类 Cookie 与浏览器指纹数据保持不变',
                count: formatTokenUsageCount(session),
                recovery: '点「启动会话」即可恢复自动续期；若期间 Token 过期，'
                  + '接口会命中风控并需要重新走一次登录流程',
              }, modalApi).then((ok: boolean) => { if (ok) onStop() })
            }}
            loading={loadingSession}
            disabled={!session?.active || Boolean(sessionError)}
          >
            停止会话
          </TipButton>
        </Space>
      </Card>
    </Col>
  )
}

// S3776 修复：健康检查 Card 提取为独立子组件
// 为什么提取：原主组件 JSX 内 8 个条件渲染（health?.ok? / cookie_valid? / api_reachable? /
// page_accessible? / action!=='none' && / needs_attention? / health?.error || '...'）全部贡献
// 主组件复杂度，提取后子组件独立计算，主组件函数 CC 进一步下降
function HealthCardCol(props: {
  readonly health: HealthReport | null
  readonly loadingHealth: boolean
  readonly onCheck: () => void
}) {
  const { health, loadingHealth, onCheck } = props
  return (
    <Col xs={24} xl={24}>
      <Card
        title={
          <Space>
            <HeartOutlined />
            健康检查
          </Space>
        }
        extra={
          <TipButton
            tip="执行健康检查"
            icon={<ReloadOutlined />}
            onClick={onCheck}
            loading={loadingHealth}
            size="small"
          >
            检查
          </TipButton>
        }
      >
        {health?.ok ? (
          <>
            <Progress
              percent={health.score}
              status={healthScoreToProgressStatus(health.score)}
              format={(percent) => `${percent}分`}
              style={{ marginBottom: 16 }}
            />
            <Row gutter={[16, 8]}>
              <Col span={6}>
                <Statistic
                  title="Cookie"
                  value={health.cookie_valid ? '有效' : '无效'}
                  valueStyle={{ color: health.cookie_valid ? '#52c41a' : '#ff4d4f', fontSize: 14 }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="API"
                  value={health.api_reachable ? '可达' : '不可达'}
                  valueStyle={{ color: health.api_reachable ? '#52c41a' : '#ff4d4f', fontSize: 14 }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="页面"
                  value={health.page_accessible ? '可访问' : '不可访问'}
                  valueStyle={{ color: health.page_accessible ? '#52c41a' : '#ff4d4f', fontSize: 14 }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="WAF"
                  value={WAF_LABELS[health.waf_status] || health.waf_status}
                  valueStyle={{ fontSize: 14 }}
                />
              </Col>
            </Row>
            {health.action !== 'none' && (
              <Alert
                type={health.needs_attention ? 'error' : 'warning'}
                showIcon
                message={`建议操作：${ACTION_LABELS[health.action] || health.action}`}
                style={{ marginTop: 12 }}
              />
            )}
          </>
        ) : (
          <Text type="secondary">
            {health?.error || '点击「检查」按钮执行健康检查'}
          </Text>
        )}
      </Card>
    </Col>
  )
}
