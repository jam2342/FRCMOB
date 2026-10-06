import type { EventSearchItem } from '../api';
import { CalendarIcon, ChevronLeftIcon, ChevronRightIcon } from '../components/ui/Icons';

type CalendarDay = { token: string; inMonth: boolean; dayNum: number };
type GridProps = {
  modalGridDays: CalendarDay[];
  modalDayEvents: Map<string, EventSearchItem[]>;
  expandedCalendarDays: Set<string>;
  toggleCalendarDayExpanded: (token: string) => void;
  selectedEventKey: string;
  eventKey: (event: EventSearchItem) => string;
  onSelect: (event: EventSearchItem, dayToken: string) => void;
  compact?: boolean;
};

export function EventCalendarGrid({
  modalGridDays,
  modalDayEvents,
  expandedCalendarDays,
  toggleCalendarDayExpanded,
  selectedEventKey,
  eventKey,
  onSelect,
  compact = false,
}: GridProps) {
  return (
    <div className={compact ? 'home-calendar-modal-grid home-calendar-drawer-grid-inner' : 'home-calendar-modal-grid'}>
      {modalGridDays.map((day) => {
        const events = modalDayEvents.get(day.token) || [];
        const isExpanded = expandedCalendarDays.has(day.token);
        const visible = isExpanded ? events : events.slice(0, compact ? 1 : 2);
        const overflow = events.length - visible.length;
        return (
          <div
            key={`modal-day-${day.token}`}
            className={`home-calendar-modal-day ${day.inMonth ? '' : 'off-month'} ${isExpanded ? 'expanded' : ''}`.trim()}
          >
            <div className="home-calendar-modal-day-num">{day.dayNum}</div>
            <div className="home-calendar-modal-day-events">
              {visible.map((event) => {
                const key = eventKey(event);
                return (
                  <button
                    key={`modal-chip-${day.token}-${event.event_key}`}
                    type="button"
                    className={`home-calendar-modal-chip ${selectedEventKey === key ? 'active' : ''}`.trim()}
                    onClick={() => onSelect(event, day.token)}
                    title={event.name}
                  >
                    {event.name}
                  </button>
                );
              })}
              {overflow > 0 ? (
                <button
                  type="button"
                  className="home-calendar-modal-more"
                  onClick={() => toggleCalendarDayExpanded(day.token)}
                  aria-expanded={false}
                >
                  +{overflow}
                  {compact ? '' : ' more'}
                </button>
              ) : null}
              {isExpanded && events.length > (compact ? 1 : 2) ? (
                <button
                  type="button"
                  className="home-calendar-modal-more"
                  onClick={() => toggleCalendarDayExpanded(day.token)}
                  aria-expanded={true}
                >
                  {compact ? 'Less' : 'Show less'}
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function EventCalendarModal({
  monthLabel,
  eventCount,
  onClose,
  onShiftMonth,
  ...gridProps
}: GridProps & { monthLabel: string; eventCount: number; onClose: () => void; onShiftMonth: (delta: number) => void }) {
  return (
    <div
      className="home-calendar-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Event calendar"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="home-calendar-modal">
        <header className="home-calendar-modal-head">
          <div className="home-calendar-modal-title">
            <CalendarIcon className="icon-inline" />
            <h2>{monthLabel}</h2>
            <small>
              {eventCount} event{eventCount === 1 ? '' : 's'}
            </small>
          </div>
          <div className="home-calendar-modal-nav">
            <button
              type="button"
              className="center-btn ghost home-calendar-nav-btn"
              onClick={() => onShiftMonth(-1)}
              aria-label="Previous month"
            >
              <ChevronLeftIcon className="icon-inline" />
            </button>
            <button
              type="button"
              className="center-btn ghost home-calendar-nav-btn"
              onClick={() => onShiftMonth(1)}
              aria-label="Next month"
            >
              <ChevronRightIcon className="icon-inline" />
            </button>
            <button
              type="button"
              className="home-calendar-modal-close"
              onClick={() => onClose()}
              aria-label="Close calendar"
            >
              ×
            </button>
          </div>
        </header>
        <div className="home-calendar-modal-weekdays">
          {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((label) => (
            <div key={`weekday-${label}`} className="home-calendar-modal-weekday">
              {label}
            </div>
          ))}
        </div>
        <EventCalendarGrid {...gridProps} />
      </div>
    </div>
  );
}
