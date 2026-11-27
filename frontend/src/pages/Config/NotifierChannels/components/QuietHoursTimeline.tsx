import { getQuietHoursSlots } from '../notifierModel'

interface QuietHoursTimelineProps {
  readonly start: string
  readonly end: string
}

export default function QuietHoursTimeline({ start, end }: QuietHoursTimelineProps) {
  const slots = getQuietHoursSlots(start, end)
  const quietCount = slots.filter((slot) => slot.quiet).length

  return (
    <div>
      <figure
        className="notifier-timeline-bar"
        aria-label={`免打扰时段时间轴：${start} 至 ${end}，静默 ${quietCount} 个半小时格`}
        style={{ margin: 0 }}
      >
        {slots.map((slot) => (
          <span
            key={slot.index}
            className={slot.quiet ? 'quiet' : 'active'}
            title={slot.title}
            data-testid={`quiet-slot-${slot.index}`}
            aria-hidden="true"
          />
        ))}
      </figure>
      <div className="notifier-timeline-axis" aria-hidden="true">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>24:00</span>
      </div>
      <div className="notifier-timeline-legend">
        <span><span className="quiet-dot" aria-hidden="true" />静默时段</span>
        <span><span className="active-dot" aria-hidden="true" />推送时段</span>
      </div>
    </div>
  )
}
