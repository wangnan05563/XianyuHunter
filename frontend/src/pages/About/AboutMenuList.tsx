import { Card, theme } from 'antd'
import { ExportOutlined, FileTextOutlined, RightOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { TEXTS } from './i18n'
import { API_BASE } from '../../utils/apiBase'

interface MenuItem {
  readonly key: string
  readonly label: string
  readonly href: string
  readonly external: boolean
}

// 9 个条目：外链 + SPA 内链 + licenses（Modal 回调）；按需求 §5.1 排序
const MENU_ITEMS: MenuItem[] = [
  { key: 'terms', label: TEXTS.menu.terms, href: 'https://example.com/terms', external: true },
  { key: 'privacy', label: TEXTS.menu.privacy, href: 'https://example.com/privacy', external: true },
  // licenses 触发 Modal，不走链接
  { key: 'licenses', label: TEXTS.menu.licenses, href: '#licenses', external: false },
  // help 为 SPA 内链（保留 /help 路由作回流入口）
  { key: 'help', label: TEXTS.menu.help, href: '/help', external: false },
  // 引导页是 MainLayout 之外的整页路由，不进 sheetRegistry，用内链整页跳转
  { key: 'onboarding', label: TEXTS.menu.onboarding, href: '/onboarding', external: false },
  { key: 'api', label: TEXTS.menu.api, href: `${API_BASE}api/docs`, external: true },
  { key: 'contact', label: TEXTS.menu.contact, href: 'mailto:dev@example.com', external: true },
  { key: 'community', label: TEXTS.menu.community, href: 'https://github.com/wangnan05563/XianyuHunter', external: true },
  { key: 'report', label: TEXTS.menu.report, href: 'https://github.com/wangnan05563/XianyuHunter/issues/new', external: true },
]

// 发布版不得把占位域名交给用户：地址未就绪的条目不渲染，填入真实地址即自动出现
const PLACEHOLDER_HOST = 'example.com'
// 走 <Link> 的 SPA 内链 key（其余 external:false 项各有自己的交互方式）
const SPA_INTERNAL_KEYS = new Set(['help', 'onboarding'])
const VISIBLE_ITEMS = MENU_ITEMS.filter((item) => !item.href.includes(PLACEHOLDER_HOST))

interface AboutMenuListProps {
  readonly onOpenLicenses: () => void
}

// 内部入口用右箭头/文件图标；只有真正的新窗口外链才显示外链图标
function MenuIcon({ item }: { readonly item: MenuItem }) {
  if (item.external) return <ExportOutlined className="xh-about-menu-icon" aria-hidden />
  if (item.key === 'licenses') return <FileTextOutlined className="xh-about-menu-icon" aria-hidden />
  return <RightOutlined className="xh-about-menu-icon" aria-hidden />
}

export function AboutMenuList({ onOpenLicenses }: AboutMenuListProps) {
  const { token } = theme.useToken()

  const handleClick = (item: MenuItem, e: React.MouseEvent) => {
    if (item.key === 'licenses') {
      e.preventDefault()
      onOpenLicenses()
    }
  }

  const hoverHandlers = {
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.backgroundColor = token.colorBgTextHover
    },
    onMouseLeave: (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.backgroundColor = 'transparent'
    },
  }

  return (
    <Card
      style={{
        background: token.colorBgContainer,
        border: `1px solid ${token.colorBorderSecondary}`,
        borderRadius: 6,
        overflow: 'hidden',
      }}
      styles={{ body: { padding: 0 } }}
    >
      <nav aria-label={TEXTS.menuNavLabel}>
        <ul className="xh-about-menu-list">
          {VISIBLE_ITEMS.map((item, idx) => {
            // SPA 内链用 Link 组件避免整页刷新；licenses 走 Modal 回调
            const isInternal = !item.external && SPA_INTERNAL_KEYS.has(item.key)
            const linkStyle = {
              borderTop: idx > 0 ? `1px solid ${token.colorBorderSecondary}` : 'none',
            }
            const commonProps = {
              className: 'xh-about-menu-link',
              style: linkStyle,
              ...hoverHandlers,
            }
            const externalAria = item.external ? `${item.label}（新窗口打开）` : undefined
            if (isInternal) {
              return (
                <li key={item.key} className="xh-about-menu-item">
                  <Link to={item.href} {...commonProps}>
                    <span>{item.label}</span>
                    <MenuIcon item={item} />
                  </Link>
                </li>
              )
            }
            return (
              <li key={item.key} className="xh-about-menu-item">
                <a
                  href={item.href}
                  target={item.external ? '_blank' : undefined}
                  rel={item.external ? 'noopener noreferrer' : undefined}
                  aria-label={externalAria}
                  aria-haspopup={item.key === 'licenses' ? 'dialog' : undefined}
                  {...commonProps}
                  onClick={(e) => handleClick(item, e)}
                >
                  <span>{item.label}</span>
                  <MenuIcon item={item} />
                </a>
              </li>
            )
          })}
        </ul>
      </nav>
    </Card>
  )
}
