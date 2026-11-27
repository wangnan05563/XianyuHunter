import { Spin, Empty, Pagination } from 'antd'
import type { TimelineEntry } from '../../../api'
import { ORDER_STATUS } from '../../../constants/orderStatus'
import { LOG_LEVEL } from '../../../constants/logLevels'
import { formatShortDateTime } from '../../../utils/timeFormat'
import TimelineItem from './TimelineItem'

interface TimelineListProps {
  readonly items: TimelineEntry[]
  readonly page: number
  readonly pageSize: number
  readonly total: number
  readonly loading: boolean
  readonly loadError: boolean
  readonly expandedItems: Set<number>
  readonly taskMap: Map<string, string>
  readonly onPageChange: (p: number) => void
  readonly onToggleExpand: (idx: number) => void
}

export default function TimelineList({
  items, page, pageSize, total, loading, loadError, expandedItems, taskMap,
  onPageChange, onToggleExpand,
}: TimelineListProps) {
  return (
    <Spin spinning={loading}>
      <div className="timeline-list">
        {loadError && (
          <Empty
            className="timeline-load-error-empty"
            description="加载失败，暂无可显示的事件时间线"
          />
        )}
        {!loadError && total === 0 && (
          <Empty description="暂无匹配的时间线数据" />
        )}
        {!loadError && total > 0 && (
          <>
            {/* 紧凑靠左列表：时间戳内联，释放横向空间 */}
            <div className="timeline-rows">
              {items.map((item, idx) => {
                const globalIdx = (page - 1) * pageSize + idx
                const isOrder = item._kind === 'order'
                const level = (item.level || '').toLowerCase()
                const eventType = item.type || ''
                const status = item.status || ''

                // 确定圆点颜色：订单按状态，事件按 level/类型
                let dotColor = '#1890ff'
                if (isOrder) {
                  if (status === ORDER_STATUS.SUCCEEDED) dotColor = '#52c41a'
                  else if (status === ORDER_STATUS.FAILED) dotColor = '#ff4d4f'
                  else if (status === ORDER_STATUS.PENDING_PAY || status === ORDER_STATUS.TAKEOVER_PENDING) dotColor = '#faad14'
                } else if (level === LOG_LEVEL.ERROR || eventType === 'buy.failed') {
                  dotColor = '#ff4d4f'
                } else if (level === LOG_LEVEL.WARNING) {
                  dotColor = '#faad14'
                } else if (eventType.startsWith('eval.passed') || eventType === 'buy.succeeded') {
                  dotColor = '#52c41a'
                }

                const dotIcon = (() => {
                  // 订单类固定购物车图标；其余按日志级别选 emoji
                  if (isOrder) return '🛒'
                  if (level === LOG_LEVEL.ERROR) return '🔴'
                  if (level === LOG_LEVEL.WARNING) return '🟠'
                  return '📌'
                })()

                return (
                  <div
                    key={`${item.id || ''}-${idx}`}
                    className="timeline-row"
                  >
                    {/* 左侧：圆点 + 时间戳 */}
                    <div className="timeline-row-time">
                      {formatShortDateTime(item._ts, 'month-day-time')}
                    </div>

                    {/* 圆点 */}
                    <div className="timeline-row-marker">
                      <span className="timeline-row-icon">{dotIcon}</span>
                      <span
                        className="timeline-row-dot"
                        style={{ backgroundColor: dotColor }}
                      />
                    </div>

                    {/* 右侧：内容区（占满剩余空间） */}
                    <div className="timeline-row-content">
                      <TimelineItem
                        item={item}
                        index={globalIdx}
                        isExpanded={expandedItems.has(globalIdx)}
                        onToggleExpand={onToggleExpand}
                        taskMap={taskMap}
                      />
                    </div>
                  </div>
                )
              })}
            </div>

            {/* 分页 */}
            <div className="timeline-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={total}
                showTotal={(total) => `共 ${total} 条`}
                showSizeChanger={false}
                onChange={onPageChange}
                size="small"
              />
            </div>
          </>
        )}
      </div>
    </Spin>
  )
}
