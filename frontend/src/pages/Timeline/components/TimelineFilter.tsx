import { Select, Tag } from 'antd'
import { ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { EVENT_TYPE_OPTIONS } from '../../../constants/eventTypes'
import { TipButton } from '@/components/TipButton'
import type { Task } from '../../../api'

interface TimelineFilterProps {
  readonly types: string
  readonly taskId: string | undefined
  readonly tasks: Task[]
  readonly loading: boolean
  readonly eventTypeFilter: string[]
  readonly onTypesChange: (v: string) => void
  readonly onTaskIdChange: (v: string | undefined) => void
  readonly onReload: () => void
  readonly onSyncFromNotifier: () => void
  readonly onEventTypeFilterChange: (filter: string[]) => void
}

export default function TimelineFilter({
  types, taskId, tasks, loading, eventTypeFilter,
  onTypesChange, onTaskIdChange, onReload, onSyncFromNotifier, onEventTypeFilterChange,
}: TimelineFilterProps) {
  const toggleEventType = (value: string) => {
    if (eventTypeFilter.length === 0) {
      // 当前是「全部」模式 → 点击后只选这一个
      onEventTypeFilterChange([value])
    } else if (eventTypeFilter.includes(value)) {
      const next = eventTypeFilter.filter(v => v !== value)
      onEventTypeFilterChange(next.length === 0 ? [] : next)
    } else {
      onEventTypeFilterChange([...eventTypeFilter, value])
    }
  }

  return (
    <>
      <div className="timeline-toolbar">
        <div className="timeline-filter-item">
          <span className="timeline-filter-label">事件类型：</span>
          <Select
            className="timeline-event-type-select"
            aria-label="按事件类型筛选"
            value={types}
            onChange={(v) => onTypesChange(v)}
            options={[
              { label: '全部（订单+事件）', value: 'orders,events' },
              { label: '仅订单', value: 'orders' },
              { label: '仅事件', value: 'events' },
            ]}
          />
        </div>
        <div className="timeline-filter-item">
          <span className="timeline-filter-label">任务筛选：</span>
          <Select
            className="timeline-task-select"
            aria-label="按任务筛选"
            allowClear
            placeholder="全部任务"
            value={taskId}
            onChange={(v) => onTaskIdChange(v)}
            options={tasks.map((t) => ({ label: `${t.name}（${t.keyword}）`, value: t.id }))}
          />
        </div>
        <div className="timeline-toolbar-actions">
          <TipButton
            aria-label="重新加载时间线"
            tip="重新加载时间线数据"
            icon={<ReloadOutlined />}
            onClick={onReload}
            loading={loading}
          >
            刷新
          </TipButton>
        </div>
      </div>

      {/* 事件类型过滤（与通知订阅联动） */}
      <section className="timeline-event-types" aria-label="事件类型过滤">
        <div className="timeline-event-types-head">
          <span className="timeline-event-types-title">事件类型过滤</span>
          <TipButton
            aria-label="从通知配置同步订阅规则"
            tip="从通知配置同步订阅规则"
            size="small"
            icon={<SyncOutlined />}
            onClick={onSyncFromNotifier}
          >
            同步订阅
          </TipButton>
          {eventTypeFilter.length > 0 && (
            <TipButton
              aria-label="清除事件类型过滤"
              tip="清除事件类型过滤"
              size="small"
              type="link"
              onClick={() => onEventTypeFilterChange([])}
            >
              清除过滤
            </TipButton>
          )}
          <span className="timeline-event-types-count">
            {eventTypeFilter.length > 0
              ? `已选 ${eventTypeFilter.length} / ${EVENT_TYPE_OPTIONS.length} 种`
              : '显示全部事件类型'}
          </span>
        </div>
        <div className="timeline-type-chips">
          {EVENT_TYPE_OPTIONS.map(opt => {
            const isActive = eventTypeFilter.length === 0 || eventTypeFilter.includes(opt.value)
            const severityColor = (() => {
              // 严重度配色：critical=红 important=橙 其他=蓝
              if (opt.severity === 'critical') return 'red'
              if (opt.severity === 'important') return 'orange'
              return 'blue'
            })()
            return (
              <Tag
                key={opt.value}
                className="timeline-type-chip"
                color={isActive ? severityColor : 'default'}
                role="button"
                tabIndex={0}
                aria-pressed={isActive}
                aria-label={`${opt.label}：${isActive ? '已启用' : '未启用'}`}
                onClick={() => toggleEventType(opt.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    toggleEventType(opt.value)
                  }
                }}
              >
                {opt.label}
              </Tag>
            )
          })}
        </div>
      </section>
    </>
  )
}
