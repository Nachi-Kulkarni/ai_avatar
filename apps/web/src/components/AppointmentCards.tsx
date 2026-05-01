import type { Appointment } from "../hooks/useAppointments";

interface Props {
  appointments: Appointment[];
}

function formatTime(utcStr: string): string {
  return new Date(utcStr).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

export function AppointmentCards({ appointments }: Props) {
  if (appointments.length === 0) return null;

  return (
    <div>
      <h3 className="panel-title">Appointments</h3>
      <div className="appointment-list">
        {appointments.map((appt) => (
          <div key={appt.id} className={`appointment-card ${appt.status === "cancelled" ? "appt-cancelled" : ""}`}>
            <div className="appt-dept">
              {appt.departments?.name || `Dept #${appt.department_id}`}
            </div>
            <div className="appt-time">{formatTime(appt.slot_start_at)}</div>
            <span className={`appt-status appt-badge-${appt.status}`}>
              {appt.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
