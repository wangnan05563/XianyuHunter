import { useState, useEffect, useCallback, useRef, type CSSProperties } from 'react'
import {
  Tabs, Switch, InputNumber, Input, Slider, Radio, Table, Modal,
  Card, Tag, Space, Typography, List, App as AntdApp, Spin, theme,
} from 'antd'
import { PlusOutlined, DeleteOutlined, RollbackOutlined, HistoryOutlined, SaveOutlined, UndoOutlined } from '@ant-design/icons'
import { chatbotApi } from './api'
import { KBStatusCard } from './components/KBStatusCard'
import { VectorAdminPanel } from './components/VectorAdminPanel'
import { DiffPreviewModal } from '@/components/DiffPreviewModal'
import { useChatbotConfigStore } from '@/stores/chatbotConfigStore'
import type { DiffChange } from '@/stores/configStore'
import { TipButton } from '@/components/TipButton'
import FetchFailedHint from '@/components/FetchFailedHint'
import DataBlock from '@/components/DataBlock'
import { useFailedSources } from '@/hooks/useFailedSources'
import { formatDateTime } from '@/utils/timeFormat'
import { extractApiError } from '@/utils/apiError'
import { confirmDanger, COUNT_UNKNOWN } from '@/components/DangerSummary'
import type { KBStatus, KBVersion, FAQ, AuditLog } from './types'
import './ChatbotConfig.css'

// 本页各路数据源（T02700 假回执清零）：原先统一 catch 成 null/[]，
// 后端故障会被说成「暂无重建记录 / 暂无 FAQ / 加载中...」。
type ConfigSource = 'config' | 'kbStatus' | 'kbVersions' | 'faqs' | 'audit' | 'rebuild'
const SOURCE_LABELS: Record<ConfigSource, string> = {
  config: '客服配置',
  kbStatus: '知识库状态',
  kbVersions: '版本列表',
  faqs: 'FAQ 列表',
  audit: '审计日志',
  rebuild: '重建记录',
}

// 从审计日志中过滤出重建/回滚记录，按时间倒序取前 5 条
// 后端 audit log 写入时 target 字段格式为 "{vid}|status:{status}|chunks:{n}" 或 "from:{vid}→to:{vid}|..."
function pickRebuildLogs(logs: AuditLog[]): AuditLog[] {
  return logs
    .filter(l => l.action === 'kb_rebuild' || l.action === 'kb_rollback')
    .slice(0, 5)
}

export default function ChatbotConfigPage() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  const { token } = theme.useToken()
  const { message, modal } = AntdApp.useApp()
  // T05072：配置草稿收进 useChatbotConfigStore（统一保存范式）。
  // 页级只保留四路只读/动作数据源（kbStatus/kbVersions/faqs/auditLogs）。
  const config = useChatbotConfigStore((s) => s.config)
  const configError = useChatbotConfigStore((s) => s.error)
  const update = useChatbotConfigStore((s) => s.update)
  const dirtyPaths = useChatbotConfigStore((s) => s.dirtyPaths)
  const resetDraft = useChatbotConfigStore((s) => s.reset)
  const previewSave = useChatbotConfigStore((s) => s.previewSave)
  const confirmSave = useChatbotConfigStore((s) => s.confirmSave)
  const loadConfig = useChatbotConfigStore((s) => s.load)
  const [kbStatus, setKbStatus] = useState<KBStatus | null>(null)
  const [kbVersions, setKbVersions] = useState<KBVersion[]>([])
  const [faqs, setFaqs] = useState<FAQ[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [loading, setLoading] = useState(false)
  const [faqModalOpen, setFaqModalOpen] = useState(false)
  const [editingFaq, setEditingFaq] = useState<FAQ | null>(null)
  // 统一保存范式：dry-run 预览弹窗状态（口径同 SearchConfig 等五个配置页）
  const [saving, setSaving] = useState(false)
  const [diffChanges, setDiffChanges] = useState<DiffChange[]>([])
  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const { failed, mark, settle } = useFailedSources<ConfigSource>()
  // StrictMode 会在挂载后立即卸载重挂，首屏五路请求会被重复打一遍；门闩保证只引导一次。
  const bootstrapped = useRef(false)

  const loadAll = useCallback(async (): Promise<void> => {
    setLoading(true)
    // 错误隔离：allSettled 让单个失败不影响其他数据源（否则 chatbot 模块未启用时
    // Promise.all 整体 reject，kbVersions 保持空，看起来"无重建记录"）。
    // T02700：隔离不等于静默 —— 每一路失败都要上账，各区块据此显示错误态而不是空态。
    // 配置路走统一保存 store（T05072）：草稿合并在 store 内完成，失败返回 null 供上账。
    const cfg = await loadConfig()
    const [statusRes, versionsRes, faqRes, auditRes] = await Promise.allSettled([
      chatbotApi.getKBStatus(),
      chatbotApi.listKBVersions(),
      chatbotApi.listFAQ(),
      chatbotApi.listAuditLogs(1, 50),
    ])
    if (cfg) {
      mark('config', true)
    } else {
      mark('config', false)
      message.error(useChatbotConfigStore.getState().error || '智能客服模块未启用或加载失败')
    }
    const status = settle(statusRes, 'kbStatus')
    if (status) setKbStatus(status)
    const versions = settle(versionsRes, 'kbVersions')
    if (versions) setKbVersions(versions)
    const faqList = settle(faqRes, 'faqs')
    if (faqList) setFaqs(faqList)
    const audit = settle(auditRes, 'audit')
    // 重建记录与审计日志同源（pickRebuildLogs 从 audit 里筛），失败要一起上账
    if (audit) {
      setAuditLogs(audit.items)
      mark('rebuild', true)
    } else {
      mark('rebuild', false)
    }
    setLoading(false)
  }, [settle, mark, loadConfig, message])

  // T02900：区块级重试。原先每个失败区块的「重试」都指向 loadAll()，一处失败要把五路一起重取：
  // setLoading(true) 让整页骨架屏闪一遍，活动页签之外的区块被顺手重置，
  // 而 getConfig() 成功时又会把正在编辑的表单覆盖回后端值。
  // 这里只重取失败的那一路：不碰 loading，除重试 config 之外不碰表单。
  const reload = useCallback(async (...sources: ConfigSource[]): Promise<void> => {
    await Promise.all(sources.map(async (source) => {
      switch (source) {
        case 'config': {
          // T05072：重取配置走统一保存 store（草稿合并防覆盖，与主配置 applyDrafts 同口径）
          await loadConfig()
          break
        }
        case 'kbStatus': {
          const r = await Promise.allSettled([chatbotApi.getKBStatus()])
          const status = settle(r[0], 'kbStatus')
          if (status) setKbStatus(status)
          break
        }
        case 'kbVersions': {
          const r = await Promise.allSettled([chatbotApi.listKBVersions()])
          const versions = settle(r[0], 'kbVersions')
          if (versions) setKbVersions(versions)
          break
        }
        case 'faqs': {
          const r = await Promise.allSettled([chatbotApi.listFAQ()])
          const faqList = settle(r[0], 'faqs')
          if (faqList) setFaqs(faqList)
          break
        }
        case 'audit':
        case 'rebuild': {
          // 重建记录与审计日志同源，重取一次两头都要消账（口径同 loadAll）
          const r = await Promise.allSettled([chatbotApi.listAuditLogs(1, 50)])
          const audit = settle(r[0], 'audit')
          if (audit) setAuditLogs(audit.items)
          mark('rebuild', !!audit)
          break
        }
      }
    }))
  }, [settle, mark, loadConfig])

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    void loadAll()
  }, [loadAll])

  // building 状态轮询：构建中每 2 秒拉取一次 status，构建完成时整体刷新
  // 不再使用单次 setTimeout，因为大型知识库构建可能 30s+，单次刷新无法感知中途进度
  useEffect(() => {
    if (!kbStatus?.building) return
    const timer = setInterval(async () => {
      try {
        const status = await chatbotApi.getKBStatus()
        setKbStatus(status)
        if (!status.building) {
          // 构建结束：停止轮询并整体刷新（拿到新版本列表和审计日志）
          reload('kbStatus', 'kbVersions', 'audit')
        }
      } catch {
        // 静默失败，下次轮询继续
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [kbStatus?.building, reload])

  // T05072：草稿更新——只写本地草稿，不再逐字段自动 PUT。
  // 保存统一走「预览 dry-run diff → 确认落库」通道（下方 handleSave/handleConfirmSave），
  // 与 SearchConfig 等五个配置页的统一保存范式一致；失败回滚由保存时的后端校验兜底。
  const updateConfig = (key: string, value: string | number | boolean) => {
    update(key, value)
  }

  // ============== 统一保存范式（T05072 / FR-E-4，D-7 裁决：纳入） ==============
  const handleSave = async () => {
    if (!config) return
    setSaving(true)
    try {
      const changes = await previewSave()
      if (changes.length === 0) {
        message.info('配置未变更')
        return
      }
      setDiffChanges(changes)
      setDiffModalOpen(true)
    } catch (e) {
      message.error(extractApiError(e, '预览失败，请重试'), 5)
    } finally {
      setSaving(false)
    }
  }

  const handleConfirmSave = async () => {
    try {
      setSaving(true)
      // T05209：保存后按热加载结果区分提示——热加载键已实时生效，重启生效键明示延迟
      const result = await confirmSave()
      setDiffModalOpen(false)
      if (result.restartRequired.length > 0) {
        message.success(`客服配置已保存；${result.restartRequired.length} 项需重启应用后才生效`, 6)
      } else {
        message.success('客服配置已保存，已实时生效')
      }
    } catch (e) {
      message.error(extractApiError(e, '保存失败，请重试'), 5)
    } finally {
      setSaving(false)
    }
  }

  const dirtyCount = config ? dirtyPaths().length : 0

  const handleReset = () => {
    resetDraft()
    message.info('已放弃未保存的改动')
  }

  const handleRebuild = async (force: boolean) => {
    await chatbotApi.rebuildKB(force)
    // 立即拉取一次 status 触发 building=true，让轮询 useEffect 接管后续进度
    try {
      const status = await chatbotApi.getKBStatus()
      setKbStatus(status)
    } catch {
      // 静默：轮询 useEffect 会在 2s 后自动重试
    }
  }

  const handleRollback = async (versionId: string) => {
    const target = kbVersions.find((v) => v.id === versionId)
    const shortId = versionId.slice(0, 8)
    const current = kbStatus?.current_version ? kbStatus.current_version.slice(0, 8) : '（未知）'
    const confirmed = await confirmDanger({
      title: `回滚到版本 ${shortId}？`,
      action: '将当前知识库切换到该历史版本',
      scope: `当前版本 ${current} 的全部片段会被版本 ${shortId} 替换；回滚期间对话功能短暂不可用`,
      count: target?.chunk_count ?? COUNT_UNKNOWN,
      recovery: '当前版本记录仍保留在版本列表，可再次回滚；版本快照文件不会被删除',
    }, modal)
    if (!confirmed) return
    try {
      await chatbotApi.rollbackKB(versionId)
      message.success('回滚成功')
      reload('kbStatus', 'kbVersions', 'audit')
    } catch (e) {
      message.error(extractApiError(e, '回滚失败'), 5)
    }
  }

  const handleSaveFaq = async (faq: FAQ) => {
    try {
      await chatbotApi.upsertFAQ(faq)
      message.success('已保存')
      setFaqModalOpen(false)
      const list = await chatbotApi.listFAQ()
      setFaqs(list)
    } catch (e) {
      message.error(extractApiError(e, '保存失败'), 5)
    }
  }

  const handleDeleteFaq = async (faq: FAQ) => {
    const confirmed = await confirmDanger({
      title: '删除这条 FAQ？',
      action: '从 FAQ 列表删除该条问答',
      scope: `仅删除「${faq.question}」这一条；其他 FAQ 不受影响`,
      count: 1,
      recovery: '删除后不可恢复；需要时可重新新增该条问答',
    }, modal)
    if (!confirmed) return
    try {
      await chatbotApi.deleteFAQ(faq.id!)
      message.success('已删除')
      const list = await chatbotApi.listFAQ()
      setFaqs(list)
    } catch (e) {
      message.error(extractApiError(e, '删除失败'), 5)
    }
  }

  const loadAuditLogs = async () => {
    try {
      const { items } = await chatbotApi.listAuditLogs(1, 50)
      setAuditLogs(items)
      mark('audit', true)
      mark('rebuild', true)
    } catch (e) {
      mark('audit', false)
      mark('rebuild', false)
      message.error(extractApiError(e, '加载审计日志失败'), 5)
    }
  }

  const rebuildLogs = pickRebuildLogs(auditLogs)
  const configReady = Boolean(config) && !loading
  const canSave = Boolean(config && dirtyCount > 0 && !saving)
  const canReset = Boolean(config && dirtyCount > 0 && !saving)
  let dirtyLabel: string
  if (loading) {
    dirtyLabel = '正在加载配置…'
  } else if (!config) {
    dirtyLabel = '配置未加载'
  } else if (dirtyCount > 0) {
    dirtyLabel = `配置未保存 ${dirtyCount} 项修改`
  } else {
    dirtyLabel = '配置无未保存修改'
  }
  const headerStyle: CSSProperties = {
    position: 'sticky',
    top: 0,
    zIndex: 20,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    flexWrap: 'wrap',
    padding: '14px 0 12px',
    marginBottom: 16,
    background: token.colorBgLayout,
    borderBottom: `1px solid ${token.colorBorderSecondary}`,
  }

  return (
    <div className="page-container" data-testid="chatbot-config-page">
      <header style={headerStyle} className="chatbot-config-header" data-testid="chatbot-config-header">
        <div className="chatbot-config-header-title">
          <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>客服配置</h1>
          <div style={{ marginTop: 3, fontSize: 12, color: token.colorTextTertiary }}>
            配置 RAG、Agent、知识库、FAQ 与转人工策略
          </div>
        </div>
        <Space wrap align="center" className="chatbot-config-header-actions">
          <Tag
            color={!loading && config && dirtyCount > 0 ? 'orange' : undefined}
            data-testid="chatbot-dirty-count"
            aria-live="polite"
          >
            {dirtyLabel}
          </Tag>
          <TipButton
            tip={dirtyCount > 0 ? '放弃全部未保存的配置改动并恢复服务端值' : '当前没有可重置的修改'}
            icon={<UndoOutlined />}
            onClick={handleReset}
            disabled={!canReset}
            data-testid="chatbot-reset-config"
          >
            重置
          </TipButton>
          <TipButton
            tip={dirtyCount === 0 ? '当前没有需要保存的修改' : '预览差异并保存客服配置'}
            type="primary"
            icon={<SaveOutlined />}
            onClick={() => { void handleSave() }}
            loading={saving}
            disabled={!canSave}
            data-testid="chatbot-save-config"
          >
            保存配置
          </TipButton>
        </Space>
      </header>

      {loading && (
        <div className="chatbot-config-loading" data-testid="chatbot-config-loading">
          <Spin size="large" />
          <span>正在加载客服配置…</span>
        </div>
      )}

      {!loading && !config && (
        <FetchFailedHint
          source={SOURCE_LABELS.config}
          description={`${configError || '接口请求失败'}。为避免把默认值当成服务端配置并误保存，表单暂不展示；请重试后再操作。`}
          onRetry={() => void reload('config')}
        />
      )}

      {configReady && config && (
        <Card className="chatbot-config-card">
          <Tabs
        // 旧入口 /maintenance/vector 会带 ?tab=vector-admin 跳过来（T02577 D-9）
        defaultActiveKey={new URLSearchParams(globalThis.location?.search ?? '').get('tab') ?? 'basic'}
        items={[
          {
            key: 'basic',
            label: '基础配置',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Typography.Text>启用智能客服</Typography.Text>
                  <Switch
                    aria-label="启用智能客服"
                    checked={config.enabled}
                    onChange={(v) => updateConfig('enabled', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>历史轮数（1-20）</Typography.Text>
                  <InputNumber
                    aria-label="历史轮数"
                    min={1} max={20}
                    value={config.max_history_turns}
                    onChange={(v) => v != null && updateConfig('max_history_turns', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>会话超时（分钟，5-1440）</Typography.Text>
                  <InputNumber
                    aria-label="会话超时分钟"
                    min={5} max={1440}
                    value={config.session_timeout_min}
                    onChange={(v) => v != null && updateConfig('session_timeout_min', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                {/* M1 欢迎语：与文档《客服配置-详细设计》§1.4 对齐。
                    原 Config.tsx 漏掉了这个字段的 UI，导致 DB 一旦被写入脏值
                    （如历史遗留的占位串）就再也无法从界面清掉，引导卡顶部会一直
                    显示乱码。这里补上 200 字符 TextArea + 500ms 防抖保存 +
                    恢复默认按钮（点击 → PUT 空串 → 后端 DELETE 该行）。 */}
                <div>
                  <Typography.Text>欢迎语（留空使用默认文案）</Typography.Text>
                  <Input.TextArea
                    aria-label="欢迎语"
                    value={config.welcome_message ?? ''}
                    onChange={(e) => updateConfig('welcome_message', e.target.value)}
                    placeholder="默认：Hi，我是智能客服小蜜，请问有什么可以帮您？"
                    maxLength={200}
                    showCount
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    style={{ marginTop: 8 }}
                  />
                  <TipButton
                    size="small"
                    type="link"
                    tip="清空欢迎语，恢复默认文案"
                    disabled={!config.welcome_message}
                    onClick={() => updateConfig('welcome_message', '')}
                    style={{ paddingLeft: 0, marginTop: 4 }}
                  >
                    恢复默认
                  </TipButton>
                </div>
              </Space>
            ),
          },
          {
            key: 'rag',
            label: 'RAG 检索',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Typography.Text>top_k（1-20）</Typography.Text>
                  <InputNumber
                    aria-label="RAG top_k"
                    min={1} max={20}
                    value={config.rag.top_k}
                    onChange={(v) => v != null && updateConfig('rag.top_k', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>相似度阈值（0-1，热更新）</Typography.Text>
                  <Slider
                    ariaLabelForHandle="RAG 相似度阈值"
                    min={0} max={1} step={0.05}
                    value={config.rag.similarity_threshold}
                    onChange={(v) => updateConfig('rag.similarity_threshold', v)}
                    style={{ width: 300, marginLeft: 16, display: 'inline-flex' }}
                  />
                </div>
                <div>
                  <Typography.Text>context 最大字符（500-32000）</Typography.Text>
                  <InputNumber
                    aria-label="RAG 上下文最大字符"
                    min={500} max={32000}
                    value={config.rag.max_context_chars}
                    onChange={(v) => v != null && updateConfig('rag.max_context_chars', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
              </Space>
            ),
          },
          {
            key: 'llm',
            label: 'LLM 模型',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Typography.Text>模型名（热更新）</Typography.Text>
                  <Input
                    aria-label="LLM 模型名"
                    value={config.llm.model}
                    onChange={(e) => updateConfig('llm.model', e.target.value)}
                    placeholder="如 gpt-4o-mini / qwen-plus / glm-4-flash"
                    style={{ marginLeft: 16, width: 300 }}
                  />
                </div>
                <div>
                  <Typography.Text>采样温度（0-2，热更新）</Typography.Text>
                  <Slider
                    ariaLabelForHandle="LLM 采样温度"
                    min={0} max={2} step={0.1}
                    value={config.llm.temperature}
                    onChange={(v) => updateConfig('llm.temperature', v)}
                    style={{ width: 300, marginLeft: 16, display: 'inline-flex' }}
                  />
                </div>
                <div>
                  <Typography.Text>单次回复最大 token（1-4096）</Typography.Text>
                  <InputNumber
                    aria-label="单次回复最大 token"
                    min={1} max={4096}
                    value={config.llm.max_tokens}
                    onChange={(v) => v != null && updateConfig('llm.max_tokens', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>HTTP 总超时（5-120 秒）</Typography.Text>
                  <InputNumber
                    aria-label="LLM HTTP 总超时秒"
                    min={5} max={120}
                    value={config.llm.http_timeout_sec}
                    onChange={(v) => v != null && updateConfig('llm.http_timeout_sec', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>首 token 超时（3-60 秒）</Typography.Text>
                  <InputNumber
                    aria-label="LLM 首 token 超时秒"
                    min={3} max={60}
                    value={config.llm.first_token_timeout_sec}
                    onChange={(v) => v != null && updateConfig('llm.first_token_timeout_sec', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>视觉模型（留空则用主模型处理图片）</Typography.Text>
                  <Input
                    aria-label="LLM 视觉模型"
                    value={config.llm.vision_model ?? ''}
                    onChange={(e) => updateConfig('llm.vision_model', e.target.value)}
                    placeholder="如 gpt-4o / qwen-vl-max（留空=不单独配置）"
                    style={{ marginLeft: 16, width: 300 }}
                  />
                </div>
              </Space>
            ),
          },
          {
            key: 'agent',
            label: 'AGENT 工具',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Typography.Text>启用工具</Typography.Text>
                  <Switch
                    aria-label="AGENT 启用工具"
                    checked={config.agent.enable_tools}
                    onChange={(v) => updateConfig('agent.enable_tools', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>最大轮数（1-10）</Typography.Text>
                  <InputNumber
                    aria-label="AGENT 最大轮数"
                    min={1} max={10}
                    value={config.agent.max_tool_rounds}
                    onChange={(v) => v != null && updateConfig('agent.max_tool_rounds', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>触发模式</Typography.Text>
                  <Radio.Group
                    aria-label="工具触发模式"
                    value={config.agent.tool_trigger_mode}
                    onChange={(e) => updateConfig('agent.tool_trigger_mode', e.target.value)}
                    style={{ marginLeft: 16 }}
                  >
                    <Radio value="function_calling">function_calling</Radio>
                    <Radio value="fallback">fallback</Radio>
                  </Radio.Group>
                </div>
              </Space>
            ),
          },
          {
            key: 'kb',
            label: '知识库管理',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <DataBlock
                  failed={!!failed.kbStatus}
                  source={SOURCE_LABELS.kbStatus}
                  count={kbStatus ? 1 : 0}
                  emptyText="未取到知识库状态"
                  onRetry={() => void reload('kbStatus')}
                >
                  {kbStatus && <KBStatusCard status={kbStatus} onRebuild={handleRebuild} />}
                </DataBlock>
                <Card
                  size="small"
                  title={
                    <Space>
                      <HistoryOutlined />
                      <span>最近重建记录</span>
                    </Space>
                  }
                >
                  <DataBlock
                    failed={!!failed.rebuild}
                    source={SOURCE_LABELS.rebuild}
                    count={rebuildLogs.length}
                    emptyText="暂无重建记录"
                    onRetry={() => void reload('rebuild')}
                  >
                    <List
                      size="small"
                      dataSource={rebuildLogs}
                      renderItem={(log) => (
                        <List.Item>
                          <List.Item.Meta
                            avatar={
                              <Tag color={log.action === 'kb_rebuild' ? 'blue' : 'purple'}>
                                {log.action === 'kb_rebuild' ? '重建' : '回滚'}
                              </Tag>
                            }
                            title={
                              <Typography.Text style={{ fontSize: 12 }}>
                                {log.target}
                              </Typography.Text>
                            }
                            description={
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                {formatDateTime(log.created_at)} · 来源：{log.source}
                              </Typography.Text>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  </DataBlock>
                </Card>
                <div>
                  <Typography.Text>定时自动更新</Typography.Text>
                  <Switch
                    aria-label="定时自动更新知识库"
                    checked={config.kb.auto_update_enabled}
                    onChange={(v) => updateConfig('kb.auto_update_enabled', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>更新间隔（小时，1-168）</Typography.Text>
                  <InputNumber
                    aria-label="知识库更新间隔小时"
                    min={1} max={168}
                    value={config.kb.update_interval_hours}
                    onChange={(v) => v != null && updateConfig('kb.update_interval_hours', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                {failed.kbVersions && (
                  <FetchFailedHint source={SOURCE_LABELS.kbVersions} onRetry={() => void reload('kbVersions')} />
                )}
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={kbVersions}
                  scroll={{ x: 760 }}
                  locale={{
                    // 上方已有错误条时，表格里不能再挂「暂无版本记录」，否则空态与错误态互相打脸
                    emptyText: failed.kbVersions
                      ? <span style={{ color: 'var(--xh-text-tertiary)' }}>未取到版本记录（见上方错误提示）</span>
                      : '暂无版本记录',
                  }}
                  pagination={{ pageSize: 5 }}
                  columns={[
                    { title: '版本', dataIndex: 'id', key: 'id', render: (v: string) => <Tag>{v.slice(0, 8)}</Tag> },
                    { title: '类型', dataIndex: 'build_type', key: 'build_type' },
                    { title: '片段数', dataIndex: 'chunk_count', key: 'chunk_count' },
                    { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => {
                      // 拆解嵌套三元为独立 if/else，便于阅读
                      let color = 'red'
                      if (s === 'success') color = 'green'
                      else if (s === 'partial') color = 'orange'
                      return <Tag color={color}>{s}</Tag>
                    } },
                    { title: '时间', dataIndex: 'created_at', key: 'created_at', render: (v: string) => formatDateTime(v) },
                    {
                      title: '操作', key: 'actions',
                      render: (_: unknown, record: KBVersion) => (
                        <TipButton
                          size="small"
                          tip="回滚知识库至该版本"
                          icon={<RollbackOutlined />}
                          disabled={record.is_current}
                          onClick={() => handleRollback(record.id)}
                        >
                          回滚
                        </TipButton>
                      ),
                    },
                  ]}
                />
              </Space>
            ),
          },
          {
            key: 'faq',
            label: 'FAQ 管理',
            children: (
              <>
                <TipButton
                  tip="新增一条 FAQ"
                  type="primary"
                  icon={<PlusOutlined />}
                  style={{ marginBottom: 16 }}
                  onClick={() => { setEditingFaq(null); setFaqModalOpen(true) }}
                >
                  新增 FAQ
                </TipButton>
                {failed.faqs && (
                  <FetchFailedHint source={SOURCE_LABELS.faqs} onRetry={() => void reload('faqs')} />
                )}
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={faqs}
                  scroll={{ x: 720 }}
                  locale={{
                    emptyText: failed.faqs
                      ? <span style={{ color: 'var(--xh-text-tertiary)' }}>未取到 FAQ（见上方错误提示）</span>
                      : '暂无 FAQ',
                  }}
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true },
                    { title: '答案', dataIndex: 'answer', key: 'answer', ellipsis: true },
                    { title: '分类', dataIndex: 'category', key: 'category' },
                    { title: '启用', dataIndex: 'enabled', key: 'enabled', render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
                    {
                      title: '操作', key: 'actions',
                      render: (_: unknown, record: FAQ) => (
                        <Space>
                          <TipButton size="small" tip="编辑该条 FAQ" onClick={() => { setEditingFaq(record); setFaqModalOpen(true) }}>编辑</TipButton>
                          <TipButton size="small" tip="删除该条 FAQ" aria-label="删除该条 FAQ" danger icon={<DeleteOutlined />} onClick={() => void handleDeleteFaq(record)} />
                        </Space>
                      ),
                    },
                  ]}
                />
                <FaqEditModal
                  open={faqModalOpen}
                  faq={editingFaq}
                  onSave={handleSaveFaq}
                  onCancel={() => setFaqModalOpen(false)}
                />
              </>
            ),
          },
          {
            key: 'escalation',
            label: '转人工',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Typography.Text>点踩阈值（1-10，热更新）</Typography.Text>
                  <InputNumber
                    aria-label="转人工点踩阈值"
                    min={1} max={10}
                    value={config.escalation.feedback_threshold}
                    onChange={(v) => v != null && updateConfig('escalation.feedback_threshold', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>统计窗口（分钟，5-1440，热更新）</Typography.Text>
                  <InputNumber
                    aria-label="转人工统计窗口分钟"
                    min={5} max={1440}
                    value={config.escalation.feedback_window_min}
                    onChange={(v) => v != null && updateConfig('escalation.feedback_window_min', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
                <div>
                  <Typography.Text>联系方式（热更新）</Typography.Text>
                  <Input
                    aria-label="转人工联系方式"
                    value={config.escalation.contact}
                    onChange={(e) => updateConfig('escalation.contact', e.target.value)}
                    placeholder="如：QQ群 123456"
                    style={{ marginLeft: 16, width: 300 }}
                  />
                </div>
                <div>
                  <Typography.Text>PII 脱敏</Typography.Text>
                  <Switch
                    aria-label="PII 脱敏"
                    checked={config.escalation.sanitize_pii}
                    onChange={(v) => updateConfig('escalation.sanitize_pii', v)}
                    style={{ marginLeft: 16 }}
                  />
                </div>
              </Space>
            ),
          },
          {
            key: 'vector-admin',
            label: '向量库维护',
            children: <VectorAdminPanel />,
          },
          {
            key: 'audit',
            label: '审计日志',
            children: (
              <>
                <TipButton tip="刷新审计日志列表" style={{ marginBottom: 16 }} onClick={loadAuditLogs}>刷新</TipButton>
                {failed.audit && (
                  <FetchFailedHint source={SOURCE_LABELS.audit} onRetry={() => void loadAuditLogs()} />
                )}
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={auditLogs}
                  scroll={{ x: 720 }}
                  locale={{
                    emptyText: failed.audit
                      ? <span style={{ color: 'var(--xh-text-tertiary)' }}>未取到审计日志（见上方错误提示）</span>
                      : '暂无审计日志',
                  }}
                  pagination={{ pageSize: 20 }}
                  columns={[
                    { title: '动作', dataIndex: 'action', key: 'action' },
                    { title: '目标', dataIndex: 'target', key: 'target', ellipsis: true },
                    { title: '来源', dataIndex: 'source', key: 'source' },
                    { title: '时间', dataIndex: 'created_at', key: 'created_at', render: (v: string) => formatDateTime(v) },
                  ]}
                />
              </>
            ),
          },
        ]}
      />
      {/* 统一保存：dry-run diff 预览弹窗（D-6 解耦复用；客服配置字段不在
          参数计算器规则表内，showValidation=false 避免无关建议） */}
      <DiffPreviewModal
        open={diffModalOpen}
        diffChanges={diffChanges}
        config={config}
        loading={saving}
        showValidation={false}
        restartRequiredKeys={config?.restart_required_keys}
        onCancel={() => setDiffModalOpen(false)}
        onConfirm={() => void handleConfirmSave()}
      />
        </Card>
      )}
    </div>
  )
}

// FAQ 编辑弹窗
function FaqEditModal({
  open, faq, onSave, onCancel,
}: {
  readonly open: boolean
  readonly faq: FAQ | null
  readonly onSave: (faq: FAQ) => void
  readonly onCancel: () => void
}) {
  const [form, setForm] = useState<FAQ>({
    question: '',
    answer: '',
    category: '通用',
    enabled: true,
  })

  useEffect(() => {
    if (faq) {
      setForm(faq)
    } else {
      setForm({ question: '', answer: '', category: '通用', enabled: true })
    }
  }, [faq, open])

  return (
    <Modal
      title={faq ? '编辑 FAQ' : '新增 FAQ'}
      open={open}
      onOk={() => {
        if (!form.question.trim() || !form.answer.trim()) return
        onSave(form)
      }}
      okButtonProps={{ disabled: !form.question.trim() || !form.answer.trim() }}
      onCancel={onCancel}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Input
          aria-label="FAQ 问题"
          placeholder="问题"
          value={form.question}
          onChange={(e) => setForm({ ...form, question: e.target.value })}
        />
        <Input.TextArea
          aria-label="FAQ 答案"
          placeholder="答案"
          rows={4}
          value={form.answer}
          onChange={(e) => setForm({ ...form, answer: e.target.value })}
        />
        <Input
          aria-label="FAQ 分类"
          placeholder="分类"
          value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })}
        />
        <div>
          <Typography.Text>启用</Typography.Text>
          <Switch
            aria-label="FAQ 启用"
            checked={form.enabled}
            onChange={(v) => setForm({ ...form, enabled: v })}
            style={{ marginLeft: 16 }}
          />
        </div>
      </Space>
    </Modal>
  )
}
