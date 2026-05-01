import type { CallSummary } from "../hooks/useSummary";

interface Props {
  summary: CallSummary | null;
}

export function SummaryPanel({ summary }: Props) {
  if (!summary) return null;

  const s = summary.summary;

  return (
    <div className="summary-panel">
      <h3 className="panel-title">Call Summary</h3>
      <p className="summary-notes">{s.notes}</p>
      <div className="summary-stats">
        <div className="stat">
          <span className="stat-value">{s.total_appointments}</span>
          <span className="stat-label">Total</span>
        </div>
        <div className="stat">
          <span className="stat-value">{s.booked?.length || 0}</span>
          <span className="stat-label">Booked</span>
        </div>
        <div className="stat">
          <span className="stat-value">{s.cancelled?.length || 0}</span>
          <span className="stat-label">Cancelled</span>
        </div>
      </div>
      {s.booked && s.booked.length > 0 && (
        <div className="summary-booked">
          <h4>Booked</h4>
          {s.booked.map((a) => (
            <div key={a.id} className="summary-appt">
              <span>{a.departments?.name || "Unknown"}</span>
              <span>{new Date(a.slot_start_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
