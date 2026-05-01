import type { ToolEvent } from "../hooks/useToolEvents";

interface Props {
  events: ToolEvent[];
}

const STATUS_LABELS: Record<string, string> = {
  started: "running",
  in_progress: "working",
  succeeded: "done",
  failed: "failed",
  needs_confirmation: "confirm",
};

const TOOL_LABELS: Record<string, string> = {
  identify_user: "Identifying patient",
  list_departments: "Loading departments",
  fetch_slots: "Fetching slots",
  book_appointment: "Booking appointment",
  retrieve_appointments: "Loading appointments",
  cancel_appointment: "Cancelling",
  modify_appointment: "Rescheduling",
  record_confirmation: "Confirming",
  end_conversation: "Ending call",
};

export function ToolActivityPanel({ events }: Props) {
  const recent = events.slice(-8).reverse();

  return (
    <div>
      <h3 className="panel-title">Activity</h3>
      {recent.length === 0 && (
        <p className="panel-empty">Waiting for activity...</p>
      )}
      <div className="tool-events-list">
        {recent.map((event) => (
          <div key={event.id} className={`tool-card tool-card-${event.status}`}>
            <div className="tool-card-header">
              <span className="tool-name">
                {TOOL_LABELS[event.tool_name] || event.tool_name}
              </span>
              <span className={`tool-status tool-status-${event.status}`}>
                {STATUS_LABELS[event.status]}
              </span>
            </div>
            {event.latency_ms != null && (
              <span className="tool-latency">{event.latency_ms}ms</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
