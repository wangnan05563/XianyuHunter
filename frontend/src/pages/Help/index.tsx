import { useState, useMemo } from 'react'
import { TipButton } from '@/components/TipButton'
import { Layout, Typography, Anchor, Space, Tag, Alert, Card, Divider, theme, Input } from 'antd'
import {
  ApiOutlined,
  RocketOutlined,
  DashboardOutlined,
  UnorderedListOutlined,
  ShoppingOutlined,
  ThunderboltOutlined,
  AuditOutlined,
  FieldTimeOutlined,
  FileTextOutlined,
  SettingOutlined,
  ToolOutlined,
  SearchOutlined,
  BulbOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  DollarOutlined,
  BellOutlined,
  RobotOutlined,
  MessageOutlined,
  DatabaseOutlined,
  CloudDownloadOutlined,
  ExperimentOutlined,
  AppstoreOutlined,
  BugOutlined,
  InfoCircleOutlined,
  TeamOutlined,
  GlobalOutlined,
} from '@ant-design/icons'
import type React from 'react'
import { useTheme } from '../../contexts/ThemeContext'
import { API_BASE } from '../../utils/apiBase'
import { TASK_MODE, TASK_MODE_VALUES, TASK_MODE_LABELS, TASK_MODE_HINTS } from '../../constants/taskStatus'
import type { TaskModeValue } from '../../constants/taskStatus'

const { Sider, Content } = Layout
const { Title, Paragraph, Text } = Typography

// ============ 文档内容数据结构 ============
// 将内容抽象为数据，便于维护和扩展；渲染逻辑与内容分离
interface DocBlock {
  type: 'feature' | 'steps' | 'scenario' | 'config' | 'note'
  title: string
  content: React.ReactNode
}

interface DocSection {
  id: string
  title: string
  icon: React.ReactNode
  intro: string
  blocks: DocBlock[]
}

// 通用块构造器：减少重复 JSX 模板
const feature = (title: string, content: React.ReactNode): DocBlock => ({ type: 'feature', title, content })
const steps = (title: string, items: string[]): DocBlock => ({
  type: 'steps',
  title,
  content: (
    <ol style={{ paddingLeft: 20, margin: 0 }}>
      {/* S6479：用步骤文本作 key 而非数组下标，避免重排导致状态错乱 */}
      {items.map((s) => (
        <li key={s} style={{ marginBottom: 6 }}>{s}</li>
      ))}
    </ol>
  ),
})
const scenario = (title: string, content: React.ReactNode): DocBlock => ({ type: 'scenario', title, content })
const config = (title: string, rows: [string, string, string][]): DocBlock => ({
  type: 'config',
  title,
  content: (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--xh-bg-spotlight)' }}>
            <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: '1px solid var(--xh-border)', width: '30%' }}>参数</th>
            <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: '1px solid var(--xh-border)', width: '20%' }}>示例</th>
            <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: '1px solid var(--xh-border)' }}>说明</th>
          </tr>
        </thead>
        <tbody>
          {/* S6479：用参数名作 key 而非下标 */}
          {rows.map(([k, v, d]) => (
            <tr key={k}>
              <td style={{ padding: '8px 12px', borderBottom: '1px solid var(--xh-border)' }}><Text code>{k}</Text></td>
              <td style={{ padding: '8px 12px', borderBottom: '1px solid var(--xh-border)', color: 'var(--xh-text-secondary)' }}>{v}</td>
              <td style={{ padding: '8px 12px', borderBottom: '1px solid var(--xh-border)', color: 'var(--xh-text-secondary)' }}>{d}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ),
})
const note = (title: string, content: React.ReactNode, type: 'info' | 'warning' = 'info'): DocBlock => ({
  type: 'note',
  title,
  content: <Alert type={type} showIcon message={content} style={{ marginTop: 0 }} />,
})

// 执行模式说明单源化（T02648）：名称与「要我做什么」读 constants/taskStatus，
// 帮助文档原先写「确认后执行 / 自动执行」，向导与任务列表同一档叫「需确认 / 全自动」，
// 三处各写一遍必然漂移，故这里只保留配色这类纯展示信息。
const MODE_TAG_COLORS: Record<TaskModeValue, string> = {
  [TASK_MODE.NOTIFY]: 'blue',
  [TASK_MODE.SEMI_AUTO]: 'purple',
  [TASK_MODE.CONFIRM]: 'orange',
  [TASK_MODE.AUTO]: 'green',
}

const ExecutionModeNote = () => (
  <>
    执行模式说明：
    {TASK_MODE_VALUES.map((mode) => (
      <span key={mode} style={{ display: 'block', marginTop: 4 }}>
        <Tag color={MODE_TAG_COLORS[mode]}>{mode}</Tag>
        {TASK_MODE_LABELS[mode]}——{TASK_MODE_HINTS[mode]}
      </span>
    ))}
    <span style={{ display: 'block', marginTop: 4 }}>高频任务（&lt;60 秒）有封号风险。</span>
  </>
)

// ============ 完整文档内容 ============
// 章节顺序与会话侧栏一致：quickstart → overview → data_view → config → maintenance → other
// 文档是给用户看的「当前版本行为」，数字与开关名必须能在代码/接口里找到对应实现，
// 不写规划中功能，也不保留已被删除的页面（如已并入客服配置的独立向量库页）。
const DOC_SECTIONS: DocSection[] = [
  {
    id: 'quickstart',
    title: '快速开始',
    icon: <RocketOutlined />,
    intro: '从安装到首次跑通任务的完整流程，新用户必读。',
    blocks: [
      feature('核心功能', '闲鱼猎人是闲鱼自动捡漏与抢单工具，支持关键词搜索、价格过滤、卖家评估、自动下单和多渠道通知，并提供 Web 控制台、移动端自适应页面和 AI 智能客服。'),
      steps('操作步骤', [
        String.raw`创建虚拟环境并安装依赖：python -m venv .venv → .venv\Scripts\pip install -r requirements.txt`,
        String.raw`安装浏览器：.venv\Scripts\playwright install chromium`,
        '复制配置：copy .env.example .env，填入推送 Key（Server酱 / PushPlus / Bark / 钉钉 / 企业微信 / Telegram 等）',
        String.raw`首次登录：.venv\Scripts\python -m xianyu_hunter login，弹出浏览器扫码登录闲鱼`,
        String.raw`启动服务：双击 scripts\启动服务.bat（默认 8001 端口，并自动启用调度器）`,
        '访问控制台：浏览器打开 http://127.0.0.1:8001/xianyu/（移动端自动跳转 /m）',
        '创建任务：在「任务管理」页面新建监控任务，设置关键词和价格区间',
        '保存任务：默认执行模式为「仅通知」，任务创建后状态为 RUNNING，由调度器按间隔执行',
      ]),
      scenario('使用场景', '刚部署完系统，需要从零开始配置并跑通第一个监控任务。'),
      note('注意事项', <>首次登录需人工扫码，登录态持久化到仓库根目录的 <Text code>./browser-data</Text>。若触发风控滑块会自动暂停，需重新登录。控制台默认开启 Token 鉴权；若 <Text code>.env</Text> 中未设置 <Text code>WEB_TOKEN</Text>，系统会自动生成并写入 .env，无需手动必填。启动脚本默认带调度器，只想查看数据时可用 <Text code>--no-with-scheduler</Text> 关闭。</>, 'warning'),
    ],
  },
  {
    id: 'dashboard',
    title: '仪表盘',
    icon: <DashboardOutlined />,
    intro: '系统总览入口，集中展示 KPI、趋势、告警、事件流和价格分布。',
    blocks: [
      feature('核心功能', '展示今日抢单数、评估数、任务数等核心 KPI；业务 KPI 带星级与环比；趋势大图支持切换指标与 7/30/90 天范围；评估漏斗展示命中率与误报率；异常雷达汇总失败订单、超时订单、低分评估；事件流与系统状态实时刷新；价格直方图支持按任务筛选并触发 AI 解读；快捷入口前 6 项常显，其余收入「更多」下拉。'),
      steps('操作步骤', [
        '进入控制台首页即为仪表盘',
        '查看顶部统计卡与业务 KPI，了解整体运行情况',
        '点击「趋势大图」切换指标与 7/30/90 天范围',
        '查看评估漏斗与命中率/误报率',
        '展开异常雷达查看失败订单、超时订单、低分评估详情',
        '观察事件流与系统状态，发现异常及时处理',
        '在价格直方图选择任务并点击 AI 分析获取解读',
        '通过快捷入口或「更多」下拉快速跳转到常用页面',
      ]),
      scenario('使用场景', '日常运维时快速了解系统运行状态，发现异常及时处理。'),
      note('注意事项', 'KPI 指标每 5 分钟刷新，概览卡片、事件流与迷你图每 60 秒刷新；调度器状态指示灯绿色表示运行中，红色表示已停止，调度器未运行时页面会显式告警。'),
    ],
  },
  {
    id: 'tasks',
    title: '任务管理',
    icon: <UnorderedListOutlined />,
    intro: '创建、编辑、启停监控任务，管理任务依赖关系。',
    blocks: [
      feature('核心功能', '任务是监控的基本单元，包含关键词、价格区间、发布天数、排除词、筛选标签、执行模式、调度间隔或 Cron、任务级评估阈值与价格/搜索/反检测覆盖。支持 start/pause/resume/stop 控制与任务间依赖（DAG）；任务详情页提供运行历史、评估记录、依赖关系、闲鱼内容关联四个 Tab。任务列表还支持 AI 自然语言建任务（需 AI 总开关开启）、模板市场与另存为模板、全部启动，以及启动/恢复前的登录态预检。'),
      steps('操作步骤', [
        '点击「新建任务」进入任务向导',
        '填写关键词（如 iPhone 15 Pro）',
        '设置价格区间、发布天数与排除词',
        '选择执行模式（默认「仅通知」；只有 auto 会在评估通过后自动拍下）',
        '设置调度间隔（建议 ≥ 60 秒），或改用 Cron 表达式',
        '可选：配置闲鱼筛选标签、任务级评估阈值与自定义 Prompt',
        '保存后任务默认为 RUNNING 状态',
        '在任务详情页查看运行历史、评估记录、依赖关系与闲鱼内容关联',
      ]),
      config('参数配置', [
        ['keyword', 'iPhone 15', '搜索关键词，必填'],
        ['min_price', '1500', '价格下限（元），留空不限'],
        ['max_price', '2500', '价格上限（元），留空不限'],
        ['max_publish_days', '7', '仅采集最近 N 天内发布的商品'],
        ['mode', 'notify', '执行模式：notify/confirm/auto/semi_auto，新建任务默认 notify'],
        ['interval_seconds', '120', '调度间隔（秒），建议 60-300'],
        ['cron', '0 */2 * * *', 'Cron 表达式，启用后覆盖 interval'],
        ['search_filters', '["personal_idle","verified"]', '闲鱼筛选标签 JSON 数组'],
        ['exclude_words', '["抽奖","拼单"]', '排除词列表'],
        ['eval_threshold', '70', '任务级评估阈值（留空沿用全局）'],
        ['notify_bargain_only', 'false', '仅通知捡漏商品（价格低于捡漏基准）'],
      ]),
      scenario('使用场景', <>监控特定商品捡漏：如设置 <Text code>keyword=iPhone 15</Text>、价格 1500-2500、模式 auto，系统会自动搜索、评估并抢单。</>),
      note('注意事项', <ExecutionModeNote />, 'warning'),
    ],
  },
  {
    id: 'items',
    title: '商品中心',
    icon: <ShoppingOutlined />,
    intro: '按任务聚合浏览已采集商品，支持实时搜索与导出。',
    blocks: [
      feature('核心功能', '按任务聚合展示商品，支持卡片/表格视图切换与列配置；标题搜索与搜索历史、地区/品牌/在售状态筛选；实时搜索（结果仅存在于当前页面会话、不入库）；数据库模式分页与单条删除；自动轮询（实时与数据库两种模式各自独立间隔）；监听搜索完成事件自动刷新；打开原帖、采集更新商品、删除关联、按当前任务导出 CSV。'),
      steps('操作步骤', [
        '从侧栏进入「商品中心」，在顶部选择目标任务',
        '点击「实时搜索」直接调用闲鱼搜索，或点击「刷新」更新当前任务数据',
        '使用标题搜索、地区/品牌/在售状态筛选缩小范围',
        '切换卡片/表格视图，点击「列配置」调整列顺序与显隐',
        '按需开启自动轮询并设置间隔（两种模式分别保存）',
        '点击商品打开原帖，或执行「采集更新商品」、删除关联',
        '点击导出按钮按当前任务导出商品 CSV',
      ]),
      scenario('使用场景', '回顾历史抓取记录，分析某类商品的价格分布和卖家特征；或用实时搜索快速查看当前闲鱼结果。'),
      note('注意事项', <>实时模式的结果只存在于本页会话、未入库，因此不分页、单条删除不可用；地区/品牌候选在实时模式取自本次搜索结果，在数据库模式按当前筛选收缩。商品中心不提供抢单入口，手动抢单请到「评估明细」，或从半自动通知回链进入「确认抢单」页。</>, 'warning'),
    ],
  },
  {
    id: 'evaluations',
    title: '评估明细',
    icon: <AuditOutlined />,
    intro: '4 维卖家评估模型、AI 鉴伪与手动抢单入口。',
    blocks: [
      feature('核心功能', '从职业度、信用、纠纷、价格异动 4 个维度评估卖家，综合评分决定是否进入抢单流程。支持单条 AI 成色评估、AI 深度鉴伪（盗图/损坏/一致性/模板）、手动抢单（二次确认）、按当前规则重新评估、批量评估未评估商品、批量 AI 评估、批量官方采集、阈值建议、分布统计、列配置与 CSV 导出。'),
      steps('操作步骤', [
        '进入「评估明细」页面',
        '修改筛选条件后约 300ms 自动查询；商品 ID 需回车或失焦触发查询',
        '查看评估列表与 4 维细分评分、拒绝原因',
        '对单条记录点击 AI 评估或深度鉴伪',
        '勾选多条后可批量 AI 评估或批量官方采集',
        '点击「重新评估」按当前评估规则重算（不调用 AI、不发通知）',
        '评分达标的商品可点击手动抢单，二次确认后提交真实订单',
        '点击导出按钮按当前任务导出评估 CSV',
      ]),
      config('评分维度', [
        ['职业度', '0-100', '卖家专业程度，越高越可靠'],
        ['信用', '0-100', '信用评级，反映历史交易信誉'],
        ['纠纷', '0-100', '纠纷率，越低越好'],
        ['价格异动', '0-100', '价格波动异常度，越低越稳定'],
      ]),
      scenario('使用场景', '分析卖家质量，调整评估阈值以过滤低质量卖家；对可疑卖家触发 AI 鉴伪；对达标商品手动下单。'),
      note('注意事项', '评估阈值在「评估规则」配置页调整；评分低于 pass_score 不会自动抢单。AI 功能受「AI 服务」总开关约束；卖家信息缺失时页面会提示仅按价格评估，可先重新登录再点「重新评估」。'),
    ],
  },
  {
    id: 'orders',
    title: '抢单记录',
    icon: <ThunderboltOutlined />,
    intro: '查看所有抢单记录、状态流转与人工接管。',
    blocks: [
      feature('核心功能', '记录每次抢单尝试，包含状态（待支付/已成功/已失败/已取消/人工接管/演练）、金额、商品信息、失败原因、截图等；顶部状态卡为后端全量统计，不随分页与筛选变化；支持按状态与类型（真实/演练）筛选、人工接管、取消待支付订单、CSV 导出；支持 URL 深链定位单条订单并自动翻页查找。'),
      steps('操作步骤', [
        '进入「抢单记录」页面',
        '查看顶部状态统计卡',
        '按状态或类型（真实/演练）筛选',
        '点击订单查看详情、失败原因与截图',
        '对待支付订单执行人工接管或取消',
        '从告警深链进入时，页面会按 ref 自动定位并说明是否被筛选挡住',
      ]),
      scenario('使用场景', '排查抢单失败原因，统计成功率，分析失败模式。'),
      note('注意事项', <>抢单窗口通常 &lt; 30 秒，对网络延迟敏感。价格容差校验失败会记录为 <Tag color="red">price_mismatch</Tag>。「演练」不是订单状态，而是 <Text code>type=dry_run</Text> 的演练记录，独立于状态筛选。</>, 'warning'),
    ],
  },
  {
    id: 'timeline',
    title: '事件时间线',
    icon: <FieldTimeOutlined />,
    intro: '订单与系统事件流的可视化展示，支持按类型过滤。',
    blocks: [
      feature('核心功能', '按时间倒序展示订单与系统事件，数据源可切换「订单+事件 / 仅订单 / 仅事件」；支持按任务筛选与事件类型标签过滤，并可「同步订阅」从通知配置同步过滤规则。后端 EventType 枚举覆盖 39 种事件（任务生命周期、商品/评估、购买、价格、通知、隧道、风控、认证、系统维护、智能客服），另有 db_admin 审计等运行态事件；标签颜色表示严重度（critical 红 / important 橙 / 其余蓝）。'),
      steps('操作步骤', [
        '进入「事件时间线」页面',
        '选择数据源（订单+事件 / 仅订单 / 仅事件）',
        '按任务筛选或点击事件类型标签过滤',
        '点击「同步订阅」把通知配置中的订阅规则同步为过滤条件',
        '点击事件查看详情，必要时刷新重新加载',
      ]),
      scenario('使用场景', '回溯问题发生时间线，定位异常事件的上下文。'),
      note('注意事项', '事件存储在 SQLite，长期运行后可在「系统清理」中按保留天数清理；事件类型标签与通知订阅项同源，新增类型会同步出现在过滤区。'),
    ],
  },
  {
    id: 'logs',
    title: '实时日志',
    icon: <FileTextOutlined />,
    intro: '实时查看与检索系统日志，支持 SSE 流式推送。',
    blocks: [
      feature('核心功能', '通过 SSE 实时推送日志，同时写入 data/logs/ 下的按日滚动文件；支持按级别（ERROR/WARNING/INFO）过滤、关键词搜索、任务 ID 与时间范围筛选；可导出当前筛选结果为 CSV（最多 1000 条）；展示级别分布统计与高频 stage；可暂停/停止自动滚动；SSE 断连自动重连。'),
      steps('操作步骤', [
        '进入「实时日志」页面',
        '选择日志级别，或输入关键词/任务 ID/时间范围后点击搜索',
        '实时观察日志输出，必要时暂停或停止滚动',
        '点击导出按钮按当前筛选条件导出 CSV',
      ]),
      scenario('使用场景', '实时监控任务执行过程，排查运行时问题。'),
      note('注意事项', '日志同时写入 data/logs/ 文件；界面导出的是当前搜索命中的记录（上限 1000 条），完整原始文件可直接在日志目录查看。'),
    ],
  },
  {
    id: 'error-logs',
    title: '错误日志',
    icon: <BugOutlined />,
    intro: '后台异常捕获、状态跟踪与 AI 诊断上下文。',
    blocks: [
      feature('核心功能', '专门捕获未处理异常与系统错误事件，支持按状态（新建/已解决/已忽略）、模块、级别、时间筛选；详情包含概览、堆栈信息、请求参数、服务器环境与 AI 上下文；AI 上下文支持 JSON/Markdown 两种视图并一键复制；支持批量更新处理状态。'),
      steps('操作步骤', [
        '进入「错误日志」页面',
        '按状态/模块/级别/时间范围筛选',
        '点击错误查看完整堆栈与请求参数',
        '切换 JSON/Markdown 查看并复制 AI 上下文',
        '把上下文粘贴到「智能客服」获取分析建议',
        '处理完成后把状态改为已解决或已忽略',
      ]),
      scenario('使用场景', '系统异常时快速定位错误，导出上下文供 AI 客服分析。'),
      note('注意事项', '错误日志通过全局异常处理器自动采集，无需手工埋点；批量更新状态只影响本地跟踪状态，不会修改错误本身。', 'warning'),
    ],
  },
  {
    id: 'notifications',
    title: '通知中心',
    icon: <BellOutlined />,
    intro: '系统通知列表、分类筛选与已读管理。',
    blocks: [
      feature('核心功能', '聚合所有系统通知，共 6 类：订单、登录、盯盘、任务、配置、系统；级别为信息/警告/错误。支持全部/未读/已读筛选、批量标记已读、单条与批量删除；三个计数均为后端全量口径，不随筛选变化；筛选与页码写入 URL，刷新或分享后保持状态；顶栏铃铛按告警优先口径展示。'),
      steps('操作步骤', [
        '点击顶栏铃铛图标或侧边栏「通知中心」',
        '使用顶部分段控件切换 全部/未读/已读',
        '按分类标签识别通知类型（订单/登录/盯盘/任务/配置/系统）',
        '点击通知查看详情或跳转关联页面',
        '使用「全部已读」或批量选择后标记已读/删除',
      ]),
      scenario('使用场景', '集中查看系统通知（订单成功、风控告警、盯盘降价、配置变更等），避免遗漏重要事件。'),
      note('注意事项', '通知通过 NotificationEngine 统一调度，与推送渠道（钉钉/企微等）解耦；重复通知按「用户 + 去重键」去重。', 'warning'),
    ],
  },
  {
    id: 'price-dashboard',
    title: '价格行情',
    icon: <DollarOutlined />,
    intro: '价格数据分析与多维度对比看板。',
    blocks: [
      feature('核心功能', '价格行情由 4 张卡片组成：品类横向对比图（可按均价/中位数/最低价/最高价/P10/P25/P75/P90/样本数排序，支持全部/近 7 天/近 30 天/近 90 天）、已成交价格区间、捡漏评估（输入当前价格给出极好捡漏/价格划算/价格适中/价格偏高/无法评估/超出监控范围）、品类统计明细表。'),
      steps('操作步骤', [
        '进入「价格行情」页面',
        '在品类对比图切换时间范围与排序指标',
        '在已成交价格区间选择任务与时间范围',
        '在捡漏评估输入当前价格并点击评估',
        '在品类统计表查看各品类的中位价、最低价、最高价与样本数',
      ]),
      scenario('使用场景', '分析某类商品的历史成交价区间，制定合理的捡漏价格策略。'),
      note('注意事项', '捡漏评估基于近期已售商品，新类目数据不足时仅供参考；「超出监控范围」表示当前价格超出了任务配置的最低/最高价。', 'warning'),
    ],
  },
  {
    id: 'config-price',
    title: '价格策略',
    icon: <SettingOutlined />,
    intro: '配置价格过滤规则与低价捡漏策略。',
    blocks: [
      feature('核心功能', '通过 4 组独立开关过滤商品：硬性价格上限（max_price）、硬性价格下限（min_price，防 1 元引流）、低于市场参考价比例（market_ratio）、同类低价 TopN（top_n）。另提供捡漏价格分位数配置（默认 P10），取近期已售价格最低 10% 的边界值作为捡漏基准。保存前会展示 Diff 预览。'),
      config('参数配置', [
        ['enabled_max / max_price', 'true / 10000', '开启后价格高于上限的商品被过滤'],
        ['enabled_min / min_price', 'true / 100', '开启后价格低于下限的商品被过滤（防引流）'],
        ['enabled_market_ratio / market_ratio', 'false / 0.8', '开启后低于市场参考价该比例的商品被过滤'],
        ['enabled_top_n / top_n', 'false / 5', '开启后仅保留同类低价前 N 件'],
        ['bargain_price.percentile', '0.10', '捡漏分位数（0~0.5），调高判定更宽松、调低更严格'],
      ]),
      note('注意事项', '以上为各组的默认开关与默认值，实际以当前配置为准；任务级价格区间优先于全局配置。保存前请确认 Diff 预览中的变更范围。', 'warning'),
    ],
  },
  {
    id: 'config-eval',
    title: '评估规则',
    icon: <SettingOutlined />,
    intro: '调整 4 维评估权重、职业卖家阈值与抢单分数线。',
    blocks: [
      feature('核心功能', '配置 4 个评估维度的权重（职业度/信用/纠纷/价格异动），权重总和必须为 100（可一键归一化）；配置职业卖家判定阈值、一票否决项、注册天数下限；设置通过分数（pass_score）与自动抢单分数（auto_buy_score）；可开启 AI 自动评估、AI 自动深度分析与自动官方采集（含去重窗口与失败退避）。保存前会展示 Diff 预览。'),
      config('参数配置', [
        ['professional', '30', '职业度权重（%），与其余三项总和需为 100'],
        ['credit', '30', '信用权重（%）'],
        ['dispute', '25', '纠纷权重（%）'],
        ['price', '15', '价格异动权重（%）'],
        ['pass_score', '60', '通过分数，达到后通知用户'],
        ['auto_buy_score', '80', '自动抢单分数，须 ≥ pass_score'],
        ['credit_score_min', '60', '信用分下限，低于一票否决'],
        ['bad_review_max', '3', '差评数上限，超过一票否决'],
        ['register_days_min', '30', '注册天数下限，不满则加风险分'],
      ]),
      note('注意事项', '权重总和必须为 100%，pass_score 不能大于 auto_buy_score，否则保存报错；修改阈值只影响后续评估，已评估记录需在「评估明细」点「重新评估」才会更新。', 'warning'),
    ],
  },
  {
    id: 'config-buyer',
    title: '抢单策略',
    icon: <SettingOutlined />,
    intro: '配置落单点击、超时、价格容差与单日上限。',
    blocks: [
      feature('核心功能', '配置落单流程的点击重试、提交订单超时、拍下价格容差、落单间隔、人工接管窗口与单日抢单上限（UTC 自然日、所有账号合计，0 表示不限）。'),
      config('参数配置', [
        ['click_retry_times', '2', '点击「立即购买」按钮的重试次数'],
        ['click_retry_interval', '1.0', '两次点击之间的退避间隔（秒）'],
        ['confirm_button_timeout', '10.0', '等待「提交订单」按钮出现的超时（秒）'],
        ['price_tolerance', '0.05', '拍下价格相对预期价格的允许偏差（5%）'],
        ['min_interval_between_orders', '0.0', '两次落单之间的最小间隔（秒）'],
        ['takeover_timeout_min', '30', '人工接管窗口（分钟）'],
        ['max_orders_per_day', '0', '单日抢单上限（UTC 自然日、所有账号合计，0=不限）'],
      ]),
      note('注意事项', '价格容差防止抢单时价格突变导致错价；接管窗口用于人工介入，超时将自动放弃。单日抢单上限为所有账号合计，不是按账号分别计算。', 'warning'),
    ],
  },
  {
    id: 'config-search',
    title: '搜索参数',
    icon: <SearchOutlined />,
    intro: '配置搜索分页、排序、区域、筛选标签与频率容错。',
    blocks: [
      feature('核心功能', '配置搜索结果每页条数、排序方式、超时时间、区域过滤与闲鱼筛选标签；配置搜索间隔与失败重试次数；设置新建任务的默认调度间隔与任务列表自动搜索默认开关；配置 WAF 启用状态与登录状态检查间隔。保存前会展示 Diff 预览。'),
      config('参数配置', [
        ['page_size', '20', '每页条数，可选 20/40/60/100'],
        ['sort_type', 'default', '排序：default/newest/price_asc/price_desc/want_count'],
        ['timeout', '30', '单次搜索请求的最大等待（秒）'],
        ['regions', '（空）', '区域过滤，逗号分隔，空表示全国'],
        ['filter_tags', '[]', '闲鱼筛选标签列表（如 personal_idle / 包邮）'],
        ['搜索间隔', '3', '两次搜索之间的最小间隔（秒）'],
        ['失败重试次数', '3', '连续失败达到此次数触发熔断暂停'],
        ['新建任务默认间隔', '60', '任务列表中新建任务的默认调度周期（秒）'],
        ['WAF 登录检查间隔', '30', '检查闲鱼登录状态的间隔（分钟）'],
      ]),
      note('注意事项', '搜索间隔过小、每页条数过大都会增加风控风险；任务级搜索/反检测覆盖优先于全局配置。WAF 相关配置与反爬登录管理配合使用。', 'warning'),
    ],
  },
  {
    id: 'config-notifier',
    title: '通知渠道',
    icon: <SettingOutlined />,
    intro: '配置多渠道通知、免打扰时段与事件订阅。',
    blocks: [
      feature('核心功能', '支持 Server酱、PushPlus、Bark、钉钉、企业微信、Telegram、ntfy、通用 Webhook 共 8 个渠道，可并发推送；支持启停渠道、填写凭证、拖拽排序渠道优先级、配置免打扰时段、按事件类型订阅、发送测试消息。渠道卡片标注免费/有限免费/收费。'),
      steps('操作步骤', [
        '进入「通知渠道」配置页',
        '点击渠道卡片启用/禁用',
        '填入对应渠道的 Token/Key/Webhook',
        '拖拽调整渠道优先级',
        '配置免打扰时段（如 23:00-07:00）',
        '按事件类型勾选订阅项',
        '点击测试发送验证配置',
      ]),
      scenario('使用场景', '匹配到目标商品时立即收到通知，多渠道并发确保不漏报。'),
      note('注意事项', '推送 Key 加密存储在系统 keyring（Windows DPAPI），不写入配置文件；免打扰时段内 critical 事件（如抢单成功、风控触发）仍会送达。', 'warning'),
    ],
  },
  {
    id: 'config-ai',
    title: 'AI 服务',
    icon: <SettingOutlined />,
    intro: '配置 AI 总开关、模型、预算与 Embedding。',
    blocks: [
      feature('核心功能', '提供 AI 功能总开关；支持 OpenAI 兼容 API 与 9 个 LLM 预设（OpenAI / DeepSeek / 智谱 GLM / Moonshot / 通义千问 / 文心一言 / 豆包 / Agnes AI / Ollama 本地），用于自然语言建任务、商品评估、深度鉴伪；Embedding 支持 5 种后端（Python 本地 sentence-transformers bge-small-zh-v1.5 dim=512 / OpenAI / Jina / Ollama / 复用 LLM）；可设置日/月预算、查看用量统计，并分别测试 LLM 与 Embedding 连接。'),
      config('参数配置', [
        ['api_base', 'https://api.openai.com/v1', 'API 基础地址（选择预设后自动填充）'],
        ['api_key', 'sk-...', 'API Key（加密存储）'],
        ['model', 'gpt-4o-mini', 'LLM 模型名称'],
        ['vision_model', 'gpt-4o', '多模态/图片分析模型'],
        ['budget_daily', '1.0', '每日预算上限（美元）'],
        ['budget_monthly', '20.0', '每月预算上限（美元）'],
        ['embedding_backend', 'local', 'Embedding 后端：local / openai / jina / ollama / inherit'],
      ]),
      steps('操作步骤', [
        '打开「AI 功能总开关」',
        '选择 LLM 预设并填写 API Key',
        '确认模型与多模态模型名称',
        '按需配置 Embedding 后端',
        '设置日/月预算',
        '分别点击 LLM 与 Embedding 的测试连接',
        '在用量统计中查看各端点消耗',
      ]),
      scenario('使用场景', '用自然语言描述需求（如“监控 iPhone 15 2500 以内”），AI 自动创建任务；或对可疑商品触发 AI 多模态鉴伪。'),
      note('注意事项', '关闭总开关后，AI 建任务、AI 成色评估、深度分析、智能客服/RAG/Agent、远程 Embedding 与价格解读全部停用（本地 Embedding 不受影响）；三个推理端点会退回规则解析，硬失败只保留「测试连接」。超出预算上限会自动停止 AI 调用。', 'warning'),
    ],
  },
  {
    id: 'config-chatbot',
    title: '客服配置',
    icon: <MessageOutlined />,
    intro: '智能客服配置：RAG / Agent / 知识库 / FAQ / 转人工 / 向量库。',
    blocks: [
      feature('核心功能', '配置智能客服的 9 个 Tab：基础配置（欢迎语）、RAG 检索、LLM 模型、AGENT 工具、知识库管理、FAQ 管理、转人工、向量库维护、审计日志。支持知识库定时自动更新、FAQ 快捷回复、点踩转人工与 PII 脱敏；向量库维护（原独立页面）已并入此处的「向量库维护」Tab。'),
      config('参数配置', [
        ['rag.top_k', '5', 'RAG 检索返回的片段数量（1-20）'],
        ['rag.similarity_threshold', '0.65', 'RAG 相似度阈值，低于此值的片段丢弃'],
        ['agent.enable_tools', 'true', '是否启用 Agent 工具调用'],
        ['kb.auto_update_enabled', 'true', '知识库是否定时自动更新'],
        ['kb.update_interval_hours', '6', '知识库自动更新间隔（小时）'],
        ['escalation.feedback_threshold', '2', '窗口内点踩达到此次数触发转人工'],
      ]),
      steps('操作步骤', [
        '进入「客服配置」页面',
        '在「基础配置」设置欢迎语',
        '在「RAG 检索」调整 top_k 与相似度阈值',
        '在「AGENT 工具」启用/停用工具调用',
        '在「知识库管理」设置自动更新与重建',
        '在「FAQ 管理」维护问答库（分类含「快捷」的项会出现在聊天快捷回复）',
        '在「转人工」设置点踩阈值、窗口与联系方式',
        '在「向量库维护」查看集合、创建快照、清理文档、查看重建进度',
        '在「审计日志」追踪配置变更',
      ]),
      scenario('使用场景', '调整 AI 客服回答的准确性边界，让系统自动调用工具完成任务（如查任务状态、读错误日志）。'),
      note('注意事项', '知识库修改后会触发后台重建，期间检索可能短暂回退到 BM25；转人工默认在窗口内连续点踩达到阈值时触发；向量库快照文件较大，清理前请确认磁盘空间。', 'warning'),
    ],
  },
  {
    id: 'config-version',
    title: '配置版本',
    icon: <SettingOutlined />,
    intro: '配置备份、回滚、导入导出与脱敏分享。',
    blocks: [
      feature('核心功能', '每次配置保存自动创建备份（保留最近 10 份）；提供版本时间线、备份详情查看、一键回滚到上一版本、从任意备份恢复；支持导出当前配置为 JSON、导入 JSON 前先预览差异再确认、生成脱敏配置用于分享。'),
      steps('操作步骤', [
        '进入「配置版本」页面',
        '查看系统版本、备份总数与最近备份时间',
        '在版本时间线查看历史备份，点击「查看详情」确认内容',
        '点击「一键回滚」回到上一版本，或对指定备份点「恢复」',
        '使用「导出配置」保存 JSON，或用「导入配置」预览差异后导入',
        '使用「分享配置」生成脱敏版本',
      ]),
      scenario('使用场景', '配置修改后系统异常，快速回滚到上一个正常版本；或把脱敏配置分享给他人复现问题。'),
      note('注意事项', '配置自动备份到 config/backups/，最多保留 10 份；回滚操作本身也会先备份当前配置，因此可以再次回滚。', 'warning'),
    ],
  },
  {
    id: 'maintenance-cleanup',
    title: '系统清理',
    icon: <ToolOutlined />,
    intro: '清理缓存、历史数据与日志，释放磁盘空间。',
    blocks: [
      feature('核心功能', '提供三个清理模块：缓存清理（浏览器数据、__pycache__ 等，支持后台统计进度与仅预览）、数据库清理（全部/旧事件记录/旧未关联商品/已售商品关联/VACUUM 压缩）、日志清理（按天数清理/大文件清理（>10MB）/全部清理）。危险操作均需二次确认，支持先预览后执行。'),
      steps('操作步骤', [
        '进入「系统清理」页面，查看存储状态概览',
        '在缓存清理选择范围，先开启「仅预览」确认将处理的内容',
        '在数据库清理选择范围并设置保留天数',
        '在日志清理选择按天数或大文件清理',
        '确认影响范围后执行清理',
      ]),
      scenario('使用场景', '长期运行后磁盘占用过高，定期清理历史数据。'),
      note('注意事项', '清理操作不可逆，执行前建议先备份；VACUUM 是长耗时且阻塞写入的操作，期间数据库写入会受阻；大数据目录的统计在后台分批进行，统计完成前不能执行真实删除。', 'warning'),
    ],
  },
  {
    id: 'maintenance-db',
    title: '数据库维护',
    icon: <DatabaseOutlined />,
    intro: '在线管理数据库表数据。',
    blocks: [
      feature('核心功能', '提供 11 张业务表的在线 CRUD：tasks / items / sellers / evaluations / orders / events / task_links / task_deps / notifications / accounts / proxies。支持搜索、分页、单条与批量删除（删除前展示级联影响并要求确认码）、导入（仅新增，主键冲突跳过，不覆盖现有数据）、导出 CSV/JSON（整表导出）。'),
      steps('操作步骤', [
        '进入「数据库维护」页面',
        '在侧边栏选择业务表',
        '使用搜索与分页查看数据，或打开行详情编辑',
        '删除前查看级联影响预览，输入确认码后执行',
        '可使用导入向表内新增记录，或导出整表为 CSV/JSON',
      ]),
      scenario('使用场景', '直接查看或修改数据库记录，排查数据问题。'),
      note('注意事项', '直接操作数据库有风险，建议先导出备份；不支持覆盖导入，主键/唯一键已存在的行会被跳过；删除不可撤销。', 'warning'),
    ],
  },
  {
    id: 'maintenance-batch',
    title: '批量采集',
    icon: <CloudDownloadOutlined />,
    intro: '定时批量采集在售商品最新数据。',
    blocks: [
      feature('核心功能', '按设定间隔自动采集所有在售商品的最新详情，检测已售状态与字段变更；间隔范围 1-1440 分钟（默认 60）；提供「当前执行」与「执行历史」两个 Tab；支持立即触发、暂停/继续/停止；停止后未处理商品会持久化，下次触发从断点续传；连续失败达到阈值自动熔断；执行历史可按状态筛选并可清空。'),
      steps('操作步骤', [
        '进入「批量采集」页面',
        '调整采集间隔（1-1440 分钟）',
        '点击「立即触发」手动启动，或等待定时调度',
        '执行中可暂停/继续/停止',
        '在「执行历史」查看每批次状态与统计，必要时清空历史',
      ]),
      scenario('使用场景', '长期运营时保持商品库新鲜度，及时下线已售商品。'),
      note('注意事项', '停止当前批次不会丢失未处理商品，下次触发会从断点继续；连续失败达到阈值会熔断当前批次并发送告警。', 'warning'),
    ],
  },
  {
    id: 'anticrawl',
    title: '反爬登录管理',
    icon: <ExperimentOutlined />,
    intro: '管理闲鱼登录态、Cookie 分层与反爬状态。',
    blocks: [
      feature('核心功能', '管理闲鱼登录态与反爬状态：登录策略评估（推荐策略、可用策略、健康动作）、会话状态（健康检查与操作）、指纹信息、频率统计、Cookie 层状态（身份层/会话层/追踪层）。支持扫码登录、手动 Cookie 注入、从系统浏览器导入、CDP 连接系统浏览器、复用 browser-data Cookie；WAF 状态分为正常/警告/已熔断。'),
      steps('操作步骤', [
        '进入「反爬登录管理」页面',
        '查看登录策略评估与会话健康状态',
        '登录态失效时点击「扫码登录」，或使用 Cookie 注入/浏览器导入',
        '查看指纹信息与频率统计，判断是否触发风控',
        '在 Cookie 层状态中查看各层有效性，必要时更新某一层 Cookie',
        'WAF 熔断后先降低搜索频率，再重新登录',
      ]),
      scenario('使用场景', '登录态失效后重新登录；风控触发后调整搜索频率；排查 Cookie 分层状态。'),
      note('注意事项', <>主动失效某一 Cookie 层会被标记为人工失效，后台自动同步会跳过该层、不会被自愈恢复；需重新登录或在「更新 Cookie」中重新提交该层。频率/QPS 与 WAF 登录检查间隔在「搜索参数」页配置；触发闲鱼 WAF 会自动暂停任务，连续失败请检查 <Text code>infra/selectors.py</Text> 是否需要更新。</>, 'warning'),
    ],
  },
  {
    id: 'tunnel',
    title: '内网穿透',
    icon: <GlobalOutlined />,
    intro: '将本地 Web 服务暴露到公网/局域网，支持远程访问。',
    blocks: [
      feature('核心功能', '为本地运行的 Web 控制台（默认端口 8001）建立内网穿透隧道，便于远程或移动端访问。支持 3 种 provider：Cloudflare（quick 临时域名 / named 固定域名，named 提供 cloudflared 登录、建隧道、配 DNS 向导）、cpolar、Tailscale。可配置 local_port、auto_start 与 tunnel_mode。'),
      config('参数配置', [
        ['provider', 'cloudflare', '穿透服务提供方：cloudflare / cpolar / tailscale'],
        ['local_port', '0', '隧道转发到的本地端口，0 表示从 server.port 继承'],
        ['auto_start', 'false', '后端启动时是否自动启动隧道'],
        ['tunnel_mode', 'quick', 'Cloudflare 模式：quick 临时域名 / named 固定域名'],
        ['hostname', '（空）', 'named 模式下的固定域名（需已在 Cloudflare DNS 托管）'],
      ]),
      steps('操作步骤', [
        '进入「内网穿透」页面（系统维护分组）',
        '选择 provider 并填入相应凭证（如 cpolar 的 authtoken）',
        'Cloudflare 可选 quick / named 模式，named 模式按向导完成登录与 DNS 配置',
        '保存后点击启动，复制生成的访问地址',
        '移动端或远程浏览器打开该地址即可访问控制台',
      ]),
      scenario('使用场景', '服务器部署在家庭/内网环境时，通过穿透隧道从外部网络或手机访问 Web 控制台。'),
      note('注意事项', 'Cloudflare named 模式需已托管域名与 Cloudflare 账号；自动下载的 CLI 二进制较大，首次启动可能较慢。', 'warning'),
    ],
  },
  {
    id: 'accounts',
    title: '多账号管理',
    icon: <TeamOutlined />,
    intro: '顶栏账号切换器：多闲鱼账号列表、切换与退出。',
    blocks: [
      feature('核心功能', '通过顶栏账号切换器管理多个闲鱼账号：账号列表（含头像、昵称、最后活跃、状态）、一键切换（失效旧 Cookie 缓存并重置全局轮换层状态）、退出当前账号（撤销 session）、查询会话事件日志（登录/切换/退出）。'),
      steps('操作步骤', [
        '在「反爬登录管理」页面添加并登录多个闲鱼账号',
        '点击顶栏账号切换器查看已登录账号列表',
        '点击目标账号切换（自动失效旧 Cookie 缓存）',
        '对当前账号点击「退出」撤销 session',
        '查看「会话事件」追踪登录/切换/退出变更',
      ]),
      scenario('使用场景', '多账号轮换分摊风控风险；不同账号监控不同类目商品。'),
      note('注意事项', '非 active 账号（Cookie 已过期）不可切换，需重新登录；default 用户可退出但不可删除；切换账号会失效所有用户级缓存并刷新页面。', 'warning'),
    ],
  },
  {
    id: 'chatbot',
    title: '智能客服',
    icon: <RobotOutlined />,
    intro: 'RAG + Agent 双引擎的智能助手。',
    blocks: [
      feature('核心功能', '基于 RAG 检索 + Agent 工具调用的智能客服：可查询任务状态、读取配置值、搜索错误日志、解答 FAQ。支持会话搜索与收藏、多会话历史、快捷回复（取自 FAQ 分类含「快捷」的项）、消息点赞/点踩、转人工、2 分钟内撤回与重新生成；AI 回复会展示引用来源与工具调用结果。'),
      steps('操作步骤', [
        '点击顶栏机器人图标或侧边栏「智能客服」',
        '在欢迎页选择快捷指令或自由提问',
        '查看 AI 回复及引用来源/工具调用结果',
        '对回答进行点赞/点踩反馈',
        '复杂问题点击「转人工」',
        '在左侧会话列表搜索、收藏或切换历史会话',
        '需要修正上一条消息时，在 2 分钟内撤回后重新生成',
      ]),
      scenario('使用场景', '新用户咨询功能用法；排查报错时贴堆栈让 AI 分析；查询任务/订单状态。'),
      note('注意事项', 'AI 回答仅供参考，重要操作前请人工确认；智能客服受「AI 服务」总开关约束，关闭后不可用；转人工阈值在「客服配置」中调整。', 'warning'),
    ],
  },
  {
    id: 'export',
    title: '数据导出',
    icon: <CloudDownloadOutlined />,
    intro: '商品/评估/订单/事件四类数据集 CSV 导出。',
    blocks: [
      feature('核心功能', '统一导出入口：支持商品、评估、订单、事件四类数据集导出为 CSV（UTF-8 BOM，Excel/Numbers 友好）；支持按任务 ID、时间范围、订单状态、事件等级筛选；界面会展示每类数据集的字段说明。'),
      steps('操作步骤', [
        '进入「数据导出」页面',
        '选择数据集类型（商品/评估/订单/事件）',
        '选择任务与时间范围，按需填写状态或等级过滤',
        '点击导出生成 CSV',
      ]),
      scenario('使用场景', '运营周报、月度数据分析；离线备份关键数据。'),
      note('注意事项', '大量数据导出可能耗时，建议分批或按时间分片；导出的是当前筛选条件命中的记录。', 'warning'),
    ],
  },
  {
    id: 'about',
    title: '关于 / 版本信息',
    icon: <InfoCircleOutlined />,
    intro: '系统元信息、资源入口与开源许可清单。',
    blocks: [
      feature('核心功能', '展示当前版本号、发布日期、Git SHA；一键检查 GitHub 最新版本；提供 7 项可见资源入口（开源软件声明、帮助文档、新手引导、API 文档、联系我们、官方社区、报告问题，其中帮助文档、新手引导为内部页面）；用户协议/隐私条款当前为占位地址，发布版自动隐藏；浏览 41 项前后端依赖的开源许可清单（前端 21 + 后端 20），支持按包名/许可证搜索。'),
      steps('操作步骤', [
        '点击侧边栏「其他」→「关于」，或在顶栏点击 ℹ️ 图标',
        '查看版本号、发布日期、Git SHA 等元信息',
        '点击「检查更新」按钮查询 GitHub 最新发布',
        '点击资源入口跳转外部资源或打开开源声明 Modal',
        '在 Modal 搜索框按包名/许可证过滤依赖',
      ]),
      note('注意事项', '自动检查更新：进入页面 5 秒后首次检查，之后每 60 分钟检查一次；后端 5 分钟缓存避免触发 GitHub 限流。', 'warning'),
    ],
  },
  {
    id: 'menu-admin',
    title: '菜单管理',
    icon: <AppstoreOutlined />,
    intro: '用户级菜单可见性/排序配置。',
    blocks: [
      feature('核心功能', '允许用户自定义侧边栏菜单：在表格中调整排序值、开关可见性，保存后生效；配置按用户隔离保存在 SQLite 的 user_menu_configs 表，重置后恢复默认。菜单管理项自身不可隐藏，它是菜单被隐藏后唯一的恢复入口。'),
      steps('操作步骤', [
        '进入「菜单管理」页面',
        '在表格中修改各菜单项的排序值',
        '用「可见」开关显示/隐藏菜单项',
        '点击「保存」写入偏好',
        '可点击「重置为默认」恢复默认配置',
      ]),
      scenario('使用场景', '专注特定工作流时隐藏无关菜单；不同角色（运营/管理员）展示不同菜单组合。'),
      note('注意事项', '菜单配置与登录用户绑定，不影响其他用户；菜单项仅控制侧边栏显示，不影响 API 访问权限；隐藏的菜单仍可通过命令面板或菜单管理恢复。', 'warning'),
    ],
  },
]

// ============ 块渲染器 ============
function renderBlock(block: DocBlock): React.ReactNode {
  const blockStyle = { marginBottom: 20 }
  const titleStyle: React.CSSProperties = {
    fontSize: 15,
    fontWeight: 600,
    marginBottom: 8,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  }
  switch (block.type) {
    case 'feature':
      return (
        <div style={blockStyle}>
          <div style={titleStyle}><BulbOutlined style={{ color: '#1677ff' }} />{block.title}</div>
          <Paragraph style={{ color: 'var(--xh-text-secondary)', marginBottom: 0 }}>{block.content}</Paragraph>
        </div>
      )
    case 'steps':
      return (
        <div style={blockStyle}>
          <div style={titleStyle}><CheckCircleOutlined style={{ color: '#52c41a' }} />{block.title}</div>
          {block.content}
        </div>
      )
    case 'scenario':
      return (
        <div style={blockStyle}>
          <div style={titleStyle}><RocketOutlined style={{ color: '#722ed1' }} />{block.title}</div>
          <Paragraph style={{ color: 'var(--xh-text-secondary)', marginBottom: 0 }}>{block.content}</Paragraph>
        </div>
      )
    case 'config':
      return (
        <div style={blockStyle}>
          <div style={titleStyle}><SettingOutlined style={{ color: '#faad14' }} />{block.title}</div>
          {block.content}
        </div>
      )
    case 'note':
      return (
        <div style={blockStyle}>
          <div style={titleStyle}><WarningOutlined style={{ color: '#faad14' }} />{block.title}</div>
          {block.content}
        </div>
      )
  }
}

export default function Help() {
  const { isDark } = useTheme()
  const { token: themeToken } = theme.useToken()
  const [collapsed, setCollapsed] = useState(false)
  const [search, setSearch] = useState('')

  // 搜索过滤：标题或简介包含关键词的章节保留
  const filteredSections = useMemo(() => {
    if (!search.trim()) return DOC_SECTIONS
    const kw = search.toLowerCase()
    return DOC_SECTIONS.filter(
      (s) => s.title.toLowerCase().includes(kw) || s.intro.toLowerCase().includes(kw),
    )
  }, [search])

  // 锚点目录项
  const anchorItems = filteredSections.map((s) => ({
    key: `#${s.id}`,
    href: `#${s.id}`,
    title: (
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {s.icon}
        <span style={{ fontSize: 13 }}>{s.title}</span>
      </span>
    ),
  }))

  // 嵌入 SheetWorkspace 内显示：不再渲染自有 Header/Layout 外壳，
  // 由 SheetWorkspace 的 sheet-content-area 提供滚动容器，标签栏提供关闭入口。
  // 保留 Sider 用作章节锚点导航，breakpoint="lg" 在窄屏自动折叠避免遮挡内容。
  return (
    <Layout style={{ minHeight: '100%', background: themeToken.colorBgLayout }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        collapsedWidth={0}
        breakpoint="lg"
        trigger={null}
        theme={isDark ? 'dark' : 'light'}
        style={{
          background: themeToken.colorBgContainer,
          position: 'sticky',
          top: 0,
          height: '100%',
          overflow: 'auto',
        }}
        width={240}
      >
        <div style={{ padding: '16px 16px 8px' }}>
          <Text strong style={{ fontSize: 14 }}>文档目录</Text>
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Input
            placeholder="搜索章节..."
            size="small"
            allowClear
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Anchor
          affix={false}
          items={anchorItems}
          offsetTop={16}
          style={{ padding: '0 16px' }}
        />
      </Sider>
      <Content style={{ background: themeToken.colorBgLayout, overflow: 'visible' }}>
        <div style={{ maxWidth: 900, margin: '0 auto', padding: '32px 24px 64px' }}>
          {/* 文档头部简介 */}
          <Card style={{ marginBottom: 24, background: 'linear-gradient(135deg, rgba(255,98,0,0.06), rgba(255,133,51,0.04))' }}>
            <Space align="start" size={16}>
              <RocketOutlined style={{ fontSize: 32, color: themeToken.colorPrimary }} />
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                  <Title level={3} style={{ margin: 0 }}>闲鱼猎人使用文档</Title>
                  <TipButton
                    tip="在新标签页打开 API 文档"
                    type="text"
                    icon={<ApiOutlined />}
                    onClick={() => globalThis.open(`${API_BASE}api/docs`, '_blank')}
                  >
                    API 文档
                  </TipButton>
                </div>
                <Paragraph style={{ color: 'var(--xh-text-secondary)', marginTop: 8, marginBottom: 0 }}>
                  系统涵盖监控、评估、抢单、通知全流程。本文档按侧栏菜单顺序介绍各功能模块的使用方法，
                  包括核心功能、操作步骤、使用场景、参数配置和注意事项。
                </Paragraph>
              </div>
            </Space>
          </Card>

          {/* 各章节内容 */}
          {filteredSections.map((section) => (
            <Card
              key={section.id}
              id={section.id}
              style={{ marginBottom: 24, scrollMarginTop: 16 }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <span style={{ fontSize: 22, color: themeToken.colorPrimary }}>{section.icon}</span>
                <Title level={4} style={{ margin: 0 }}>{section.title}</Title>
              </div>
              <Paragraph style={{ color: 'var(--xh-text-secondary)', marginBottom: 16 }}>
                {section.intro}
              </Paragraph>
              <Divider style={{ margin: '0 0 20px' }} />
              {/* S6479：用 block.title 作 key 而非下标 */}
              {section.blocks.map((block) => (
                <div key={block.title}>{renderBlock(block)}</div>
              ))}
            </Card>
          ))}

          {filteredSections.length === 0 && (
            <div style={{ textAlign: 'center', padding: 64, color: 'var(--xh-text-quaternary)' }}>
              未找到匹配的章节
            </div>
          )}

          {/* 底部 */}
          <div style={{ textAlign: 'center', color: 'var(--xh-text-quaternary)', fontSize: 13, marginTop: 32 }}>
            <Divider />
            <Text type="secondary">闲鱼猎人 · 帮助文档</Text>
          </div>
        </div>
      </Content>
    </Layout>
  )
}
