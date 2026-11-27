import { type CSSProperties, useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Card,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Typography,
  theme,
} from 'antd'
import { SaveOutlined, UndoOutlined } from '@ant-design/icons'
import { TipButton } from '@/components/TipButton'
import { aiApi, type AIConfig as AIConfigData, type AIUsage } from '../../../api'
import { extractApiError } from '../../../utils/apiError'
import { confirmDanger } from '../../../components/DangerSummary'
import { buildDisableAICopy } from './dangerCopy'
import { PRESETS, EMBEDDING_PRESETS } from './constants'
import {
  canTestEmbedding,
  canTestLLM,
  diffAIConfig,
  getAIConfigValidationError,
  isBudgetDirty,
  normalizeAIConfig,
} from './aiConfigModel'
import ModelConfigForm, { type TestResult } from './components/ModelConfigForm'
import EmbeddingConfigForm, { type EmbeddingTestResult } from './components/EmbeddingConfigForm'
import UsageStats from './components/UsageStats'
import BudgetSettings from './components/BudgetSettings'
import FetchFailedHint from '../../../components/FetchFailedHint'
import { useFailedSources } from '../../../hooks/useFailedSources'
import { invalidateAIEnabled } from '../../../hooks/useAIEnabled'
import './AIConfig.css'

const { Text } = Typography

type AIConfigSource = 'config' | 'usage'
const SOURCE_LABELS: Record<AIConfigSource, string> = {
  config: 'AI 配置',
  usage: 'AI 用量',
}

const EMPTY_CONFIG = normalizeAIConfig(null)

function AIConfigPage() { // NOSONAR S3776：页面级容器组件，主体为组合式子组件与条件渲染；再拆分需跨组件传参与状态提升，回归风险大于收益
  const { token } = theme.useToken()
  const { message, modal } = AntdApp.useApp()
  const { failed, mark, isFailed } = useFailedSources<AIConfigSource>()

  // 配置/用量都用 null 表示「尚未成功加载」，避免把内置默认值渲染成真实数据。
  const [config, setConfig] = useState<AIConfigData | null>(null)
  const [configBaseline, setConfigBaseline] = useState<AIConfigData | null>(null)
  const [configLoading, setConfigLoading] = useState(true)
  const [configError, setConfigError] = useState('')

  const [usage, setUsage] = useState<AIUsage | null>(null)
  const [budgetBaseline, setBudgetBaseline] = useState<AIUsage['budget'] | null>(null)
  const [usageLoading, setUsageLoading] = useState(true)
  const [usageError, setUsageError] = useState('')

  // UI 状态
  const [showApiKey, setShowApiKey] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  // Embedding 测试状态独立于 LLM：两端点可能完全不同，需分别验证
  const [embTesting, setEmbTesting] = useState(false)
  const [embTestResult, setEmbTestResult] = useState<EmbeddingTestResult | null>(null)
  const [budgetSaving, setBudgetSaving] = useState(false)
  const [configSaving, setConfigSaving] = useState(false)
  const [toggleSaving, setToggleSaving] = useState(false)
  const bootstrapped = useRef(false)
  // 用量刷新会覆盖 usage.budget；用 ref 判断是否有未保存预算草稿，避免测试连接成功后的刷新吞掉用户输入。
  const usageRef = useRef<AIUsage | null>(null)
  const budgetBaselineRef = useRef<AIUsage['budget'] | null>(null)

  useEffect(() => {
    usageRef.current = usage
  }, [usage])

  useEffect(() => {
    budgetBaselineRef.current = budgetBaseline
  }, [budgetBaseline])

  const loadConfig = useCallback(async () => {
    setConfigLoading(true)
    mark('config', true)
    setConfigError('')
    try {
      const data = await aiApi.getConfig()
      const normalized = normalizeAIConfig(data)
      setConfig(normalized)
      setConfigBaseline(normalized)
      mark('config', true)
    } catch (error) {
      setConfig(null)
      setConfigBaseline(null)
      mark('config', false)
      setConfigError(extractApiError(error, '网络错误'))
    } finally {
      setConfigLoading(false)
    }
  }, [mark])

  const loadUsage = useCallback(async (preserveBudgetDraft = false) => {
    setUsageLoading(true)
    mark('usage', true)
    setUsageError('')
    try {
      const data = await aiApi.getUsage()
      const previous = usageRef.current
      const keepDraft = preserveBudgetDraft
        && Boolean(previous)
        && isBudgetDirty(budgetBaselineRef.current, previous?.budget ?? null)
      if (keepDraft && previous) {
        const next = { ...data, budget: previous.budget }
        usageRef.current = next
        setUsage(next)
      } else {
        usageRef.current = data
        budgetBaselineRef.current = data.budget
        setUsage(data)
        setBudgetBaseline(data.budget)
      }
      mark('usage', true)
    } catch (error) {
      usageRef.current = null
      budgetBaselineRef.current = null
      setUsage(null)
      setBudgetBaseline(null)
      mark('usage', false)
      setUsageError(extractApiError(error, '网络错误'))
    } finally {
      setUsageLoading(false)
    }
  }, [mark])

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    void loadConfig()
    void loadUsage()
  }, [loadConfig, loadUsage])

  const dirtyFields = diffAIConfig(configBaseline, config)
  const dirtyCount = dirtyFields.length
  const validationError = getAIConfigValidationError(config)
  const budgetDirty = isBudgetDirty(budgetBaseline, usage?.budget ?? null)
  const configReady = Boolean(config) && !isFailed('config') && !configLoading
  const usageReady = Boolean(usage) && !isFailed('usage') && !usageLoading

  const updateConfig = (patch: Partial<AIConfigData>) => {
    setConfig((prev) => (prev ? { ...prev, ...patch } : prev))
  }

  const handleReset = () => {
    if (!configBaseline) return
    setConfig({ ...configBaseline })
    setShowApiKey(false)
    setTestResult(null)
    setEmbTestResult(null)
    message.info('已放弃本页未保存的修改')
  }

  const handleSaveConfig = async () => {
    if (!config) {
      message.error('AI 配置尚未加载成功，为避免用空值覆盖现有配置，请先重试加载', 5)
      return
    }
    if (!config.ai_enabled) {
      message.warning('AI 功能已关闭，请先开启后再保存配置')
      return
    }
    if (validationError) {
      message.error(validationError, 5)
      return
    }
    setConfigSaving(true)
    try {
      const saved = await aiApi.putConfig(config)
      // 后端返回可能省略 embedding 字段；与当前草稿合并再规范化，避免无谓清空。
      const normalized = normalizeAIConfig({ ...config, ...saved })
      setConfig(normalized)
      setConfigBaseline(normalized)
      message.success('AI 服务配置已保存')
    } catch (error) {
      message.error(extractApiError(error, '保存失败'), 5)
    } finally {
      setConfigSaving(false)
    }
  }

  // 切换 AI 开关（关闭时需二次确认）
  const handleToggleAI = async (checked: boolean) => {
    if (!checked) {
      // 配置或用量没取到时，「0 个模型 / 今日 0 次」是假后果，改用「未能预先统计」。
      const countsUnknown = !config || !usage || !!failed.config || !!failed.usage
      const confirmed = await confirmDanger(
        buildDisableAICopy(
          config ?? EMPTY_CONFIG,
          usage?.today ?? { total_calls: 0, total_tokens: 0 },
          countsUnknown,
        ),
        modal,
      )
      if (!confirmed) return
    }

    setToggleSaving(true)
    try {
      await aiApi.putConfig({ ai_enabled: checked })
      setConfig((prev) => (prev ? { ...prev, ai_enabled: checked } : prev))
      setConfigBaseline((prev) => (prev ? { ...prev, ai_enabled: checked } : prev))
      // 让「智能建任务」「AI 成色评估」等入口的置灰跟着真实配置走
      invalidateAIEnabled(checked)
      message.success(checked ? 'AI 功能已开启' : 'AI 功能已关闭')
    } catch (error) {
      message.error(extractApiError(error, '操作失败'), 5)
    } finally {
      setToggleSaving(false)
    }
  }

  // 预设只改本页草稿，必须点「保存配置」才写入；后端在 preset_id 变化时会切到该预设的独立 Key。
  const applyPreset = (presetKey: keyof typeof PRESETS) => {
    const preset = PRESETS[presetKey]
    if (!preset) return
    updateConfig({
      base_url: preset.base_url,
      model: preset.model,
      vision_model: preset.vision_model,
      preset_id: presetKey,
    })
    message.info(`已填入 ${preset.label} 预设，点「保存配置」后生效`)
  }

  // 测试连接：直接用表单草稿 dry-run，不隐式保存。
  const handleTestConnection = async () => {
    if (!config || !canTestLLM(config)) return
    setTesting(true)
    setTestResult(null)
    try {
      const data = await aiApi.testConnection({
        base_url: config.base_url,
        api_key: config.api_key,
        model: config.model,
      })
      if (data.ok) {
        setTestResult({
          success: true,
          text: `连接成功 (${data.model || ''})。表单值未保存，生效请点击「保存配置」。`,
        })
        // 测试成功会记录一次 test_connection 用量；刷新时保留用户尚未保存的预算草稿
        void loadUsage(true)
      } else {
        setTestResult({ success: false, text: data.detail || '连接失败，请检查 Base URL、模型与 API Key' })
      }
    } catch (error) {
      setTestResult({ success: false, text: extractApiError(error, '测试连接失败') })
    } finally {
      setTesting(false)
    }
  }

  // Embedding 预设同样只改草稿，和 LLM 配置共用页头保存入口。
  const applyEmbeddingPreset = (presetKey: keyof typeof EMBEDDING_PRESETS) => {
    const preset = EMBEDDING_PRESETS[presetKey]
    if (!preset) return
    updateConfig({
      embedding_base_url: preset.embedding_base_url,
      embedding_model: preset.embedding_model,
      embedding_dimensions: preset.embedding_dimensions,
    })
    message.info(`已填入 ${preset.label} 预设，点「保存配置」后生效`)
  }

  const handleTestEmbeddingConnection = async () => {
    if (!config || !canTestEmbedding(config)) return
    setEmbTesting(true)
    setEmbTestResult(null)
    try {
      const data = await aiApi.testEmbedding({
        base_url: config.embedding_base_url,
        api_key: config.embedding_api_key,
        model: config.embedding_model,
        dimensions: config.embedding_dimensions,
      })
      if (data.ok) {
        setEmbTestResult({
          success: true,
          text: `连接成功 (${data.model || ''}, ${data.dimensions || 0} 维)。表单值未保存，生效请点击「保存配置」。`,
        })
      } else {
        setEmbTestResult({ success: false, text: data.detail || '连接失败，请检查 Embedding 端点与模型' })
      }
    } catch (error) {
      setEmbTestResult({ success: false, text: extractApiError(error, 'Embedding 测试失败') })
    } finally {
      setEmbTesting(false)
    }
  }

  const handleSaveBudget = async () => {
    if (!usage || !budgetDirty) return
    setBudgetSaving(true)
    try {
      await aiApi.putBudget({
        daily_token_limit: usage.budget.daily_token_limit,
        daily_cost_limit_usd: usage.budget.daily_cost_limit_usd,
        rate_limit_per_min: usage.budget.rate_limit_per_min,
      })
      // 立即把基线对齐到本次提交值，按钮不必等用量刷新就能回到禁用态。
      const savedBudget = { ...usage.budget }
      budgetBaselineRef.current = savedBudget
      setBudgetBaseline(savedBudget)
      message.success('预算设置已保存')
      void loadUsage()
    } catch (error) {
      message.error(extractApiError(error, '保存预算失败'), 5)
    } finally {
      setBudgetSaving(false)
    }
  }

  const canSave = Boolean(config && config.ai_enabled && dirtyCount > 0 && !validationError && !configSaving)
  const canReset = Boolean(config && dirtyCount > 0 && !configSaving)
  let dirtyLabel: string
  if (configLoading) {
    dirtyLabel = '正在加载配置…'
  } else if (isFailed('config') || !config) {
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
    <div className="page-container" data-testid="ai-config-page">
      <header style={headerStyle} data-testid="ai-config-header" className="ai-config-header">
        <div className="ai-config-header-title">
          <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.4 }}>AI 服务</h1>
          <div style={{ marginTop: 3, fontSize: 12, color: token.colorTextTertiary }}>
            配置 LLM 与 Embedding 服务，查看今日用量并设置调用预算
          </div>
        </div>
        <Space wrap align="center" className="ai-config-header-actions">
          <Tag
            color={!configLoading && !isFailed('config') && dirtyCount > 0 ? 'orange' : undefined}
            data-testid="ai-dirty-count"
            aria-live="polite"
          >
            {dirtyLabel}
          </Tag>
          <TipButton
            tip={dirtyCount > 0 ? '放弃本页未保存的修改并恢复服务端配置' : '当前没有可重置的修改'}
            icon={<UndoOutlined />}
            onClick={handleReset}
            disabled={!canReset}
            data-testid="ai-reset-config"
          >
            重置
          </TipButton>
          <TipButton
            tip={validationError ?? (dirtyCount === 0 ? '当前没有需要保存的修改' : '保存 AI 服务配置')}
            type="primary"
            icon={<SaveOutlined />}
            onClick={() => { void handleSaveConfig() }}
            loading={configSaving}
            disabled={Boolean(config) && !canSave}
            data-testid="ai-save-config"
          >
            保存配置
          </TipButton>
        </Space>
        {validationError && (
          <Alert
            type="error"
            showIcon
            message={validationError}
            style={{ width: '100%', marginTop: 4 }}
            data-testid="ai-config-validation"
          />
        )}
      </header>

      {configLoading && (
        <div className="ai-config-loading" data-testid="ai-config-loading">
          <Spin size="large" />
          <span>正在加载 AI 服务配置…</span>
        </div>
      )}

      {!configLoading && isFailed('config') && (
        <FetchFailedHint
          source={SOURCE_LABELS.config}
          description={`${configError}。为避免把内置默认值当成服务端配置并误保存，表单暂不展示；请重试后再操作。`}
          onRetry={() => { void loadConfig() }}
        />
      )}

      {!configLoading && isFailed('config') && (
        <Card style={{ marginBottom: 16, border: '2px solid var(--xh-border-secondary)' }}>
          <div className="ai-toggle-row">
            <div>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
                AI 功能总开关
                <Tag color="default">状态未知</Tag>
              </h3>
              <p style={{ marginTop: 4, color: 'var(--xh-text-tertiary)', margin: '4px 0 0' }}>
                配置未加载成功，无法确认服务端当前开关状态；为避免误操作，开关暂不可用。
              </p>
            </div>
            <Switch
              aria-label="AI 功能总开关"
              checked={false}
              disabled={!!failed.config}
              checkedChildren="ON"
              unCheckedChildren="OFF"
              style={{ minWidth: 80 }}
            />
          </div>
        </Card>
      )}

      {configReady && config && (
        <>
          <Card
            style={{
              marginBottom: 16,
              border: '2px solid var(--xh-border-secondary)',
              background: 'var(--xh-bg-spotlight)',
            }}
          >
            <div className="ai-toggle-row">
              <div>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
                  AI 功能总开关
                  <Tag color={config.ai_enabled ? 'success' : 'error'}>
                    {config.ai_enabled ? '已开启' : '已关闭'}
                  </Tag>
                </h3>
                <p style={{ marginTop: 4, color: 'var(--xh-text-tertiary)', margin: '4px 0 0' }}>
                  关闭后 AI 建任务、AI 成色评估/深度分析与「测试连接」会直接返回「AI 功能已关闭」，
                  不再消耗 token；后台任务的自动评估会跳过 AI 沿用规则结果，
                  AI 对话助手与 Embedding 向量服务不受本开关影响
                </p>
              </div>
              <Switch
                aria-label="AI 功能总开关"
                checked={config.ai_enabled}
                onChange={(checked) => { void handleToggleAI(checked) }}
                checkedChildren="ON"
                unCheckedChildren="OFF"
                loading={toggleSaving}
                disabled={configSaving}
                style={{ minWidth: 80 }}
              />
            </div>
          </Card>

          <div
            className="ai-config-editable"
            style={{
              opacity: config.ai_enabled ? 1 : 0.5,
              pointerEvents: config.ai_enabled ? 'auto' : 'none',
            }}
            aria-disabled={!config.ai_enabled}
          >
            {!config.ai_enabled && (
              <div className="ai-config-disabled-overlay">
                <Text strong style={{ fontSize: 18, color: 'var(--xh-text-tertiary)' }}>
                  AI 功能已关闭，以下配置不可用
                </Text>
              </div>
            )}

            <Tabs
              destroyOnHidden={false}
              items={[
                {
                  key: 'ai-service',
                  label: 'AI 服务配置',
                  children: (
                    <ModelConfigForm
                      config={config}
                      onConfigChange={updateConfig}
                      showApiKey={showApiKey}
                      onToggleShowApiKey={() => setShowApiKey(!showApiKey)}
                      onApplyPreset={applyPreset}
                      testing={testing}
                      testResult={testResult}
                      onTestConnection={() => { void handleTestConnection() }}
                    />
                  ),
                },
                {
                  key: 'embedding-service',
                  label: 'Embedding 向量服务配置',
                  children: (
                    <EmbeddingConfigForm
                      config={config}
                      onConfigChange={updateConfig}
                      showApiKey={showApiKey}
                      onToggleShowApiKey={() => setShowApiKey(!showApiKey)}
                      onApplyPreset={applyEmbeddingPreset}
                      testing={embTesting}
                      testResult={embTestResult}
                      onTestConnection={() => { void handleTestEmbeddingConnection() }}
                    />
                  ),
                },
              ]}
            />
          </div>

          {usageLoading && (
            <Card className="ai-usage-loading" data-testid="ai-usage-loading">
              <Spin size="small" />
              <span>正在加载用量数据…</span>
            </Card>
          )}

          {!usageLoading && isFailed('usage') && (
            <FetchFailedHint
              source={SOURCE_LABELS.usage}
              description={`${usageError}。为避免把内置默认值当成真实用量或按它保存预算，本区块暂不展示。`}
              onRetry={() => { void loadUsage() }}
            />
          )}

          {usageReady && usage && (
            <>
              <UsageStats usage={usage} />
              <BudgetSettings
                budget={usage.budget}
                onBudgetChange={(patch) => setUsage((prev) => (prev ? { ...prev, budget: { ...prev.budget, ...patch } } : prev))}
                saving={budgetSaving}
                dirty={budgetDirty}
                onSave={() => { void handleSaveBudget() }}
              />
            </>
          )}
        </>
      )}
    </div>
  )
}

/** 页面外壳提供 AntdApp 上下文，避免静态 message/modal 在暗色主题下告警。 */
export default function AIConfig() {
  return (
    <AntdApp component={false}>
      <AIConfigPage />
    </AntdApp>
  )
}
