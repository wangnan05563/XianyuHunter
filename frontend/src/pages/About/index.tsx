import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Typography } from 'antd'
import { BrandCard } from './BrandCard'
import { AboutMenuList } from './AboutMenuList'
import { OpenSourceLicenses } from './OpenSourceLicenses'
import { useUpdateChecker } from './useUpdateChecker'
import { aboutApi } from '../../api'
import { TEXTS } from './i18n'
import './about.css'

const { Text } = Typography

interface BuildInfo {
  version: string
  buildDate: string
  gitSha: string
  browserKernel?: string
}

const KERNEL_LABELS: Record<string, string> = {
  msedge: '系统 Edge',
  chrome: '系统 Chrome',
  bundled: '随包 Chromium',
  'download-on-first-use': '未安装（首次使用时下载）',
  unavailable: '不可用',
}

// API 失败时的兜底值，确保 UI 不白屏
const FALLBACK: BuildInfo = { version: '--', buildDate: '--', gitSha: 'unknown' }

type InfoState = 'loading' | 'ready' | 'error'

export default function About() {
  const [info, setInfo] = useState<BuildInfo>(FALLBACK)
  const [infoState, setInfoState] = useState<InfoState>('loading')
  const [licensesOpen, setLicensesOpen] = useState(false)
  const infoRequestRef = useRef(0)

  // 拉取元信息；失败时明确进入错误态，由用户主动重试，避免把「--」当真实数据展示
  const loadInfo = useCallback(() => {
    const requestId = ++infoRequestRef.current
    setInfoState('loading')
    aboutApi.get().then(
      (d) => {
        if (requestId !== infoRequestRef.current) return
        setInfo({
          version: d.version,
          buildDate: d.build_date,
          gitSha: d.git_sha,
          browserKernel: d.browser?.active,
        })
        setInfoState('ready')
      },
      () => {
        if (requestId !== infoRequestRef.current) return
        setInfo(FALLBACK)
        setInfoState('error')
      },
    )
  }, [])

  useEffect(() => {
    loadInfo()
    return () => {
      // 卸载或重新请求时让旧请求的 then 回调失效
      infoRequestRef.current += 1
    }
  }, [loadInfo])

  const { state, run, copy, download, update, install } = useUpdateChecker(info.version)

  // 嵌入 MainLayout 的 SheetWorkspace 内：不再渲染自有 Header/Layout/返回按钮，
  // 由 SheetWorkspace 的 sheet-content-area 提供滚动容器，标签栏提供关闭入口
  return (
    <div className="page-container xh-about-content" data-testid="about-page">
      <header className="xh-about-header">
        <Typography.Title level={1} className="xh-about-h1">
          {TEXTS.h1Title}
        </Typography.Title>
        <Typography.Paragraph type="secondary" className="xh-about-lead">
          {TEXTS.pageLead}
        </Typography.Paragraph>
      </header>

      {infoState === 'error' && (
        <Alert
          type="warning"
          showIcon
          className="xh-about-info-alert"
          message={TEXTS.infoLoadFailedTitle}
          description={TEXTS.infoLoadFailedDesc}
          action={
            <Button size="small" onClick={loadInfo}>
              {TEXTS.infoLoadRetry}
            </Button>
          }
        />
      )}

      <BrandCard
        version={info.version}
        buildDate={info.buildDate}
        gitSha={info.gitSha}
        loading={infoState === 'loading'}
        available={infoState === 'ready'}
        state={state}
        download={download}
        onCheck={run}
        onCopy={copy}
        onUpdate={() => update(state)}
        onInstall={() => install(state)}
      />
      <AboutMenuList onOpenLicenses={() => setLicensesOpen(true)} />

      <div className="xh-about-footer">
        {infoState === 'ready' && info.browserKernel && (
          <Text type="secondary" className="xh-about-kernel">
            浏览器内核：{KERNEL_LABELS[info.browserKernel] ?? info.browserKernel}
          </Text>
        )}
        <Text type="secondary" className="xh-about-copyright">
          © {new Date().getFullYear()} {TEXTS.footerCopyright}
        </Text>
        <Text type="secondary" className="xh-about-disclaimer">
          {TEXTS.riskDisclaimer}
        </Text>
      </div>

      <OpenSourceLicenses
        open={licensesOpen}
        onClose={() => setLicensesOpen(false)}
      />
    </div>
  )
}
