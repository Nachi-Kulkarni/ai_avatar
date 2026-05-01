import type { Appointment } from "../hooks/useAppointments";
import type { ToolEvent } from "../hooks/useToolEvents";

interface Props {
  events: ToolEvent[];
  appointments: Appointment[];
}

export interface ExtractedSnapshot {
  name: string;
  phoneNumber: string;
  date: string;
  time: string;
  intent: string;
}

const INTENT_LABELS: Record<string, string> = {
  book: "Book appointment",
  book_appointment: "Book appointment",
  modify: "Reschedule appointment",
  modify_appointment: "Reschedule appointment",
  cancel: "Cancel appointment",
  cancel_appointment: "Cancel appointment",
  retrieve_appointments: "View appointments",
  list_departments: "List departments",
  fetch_slots: "Check availability",
  identify_user: "Verify caller",
  update_patient_profile: "Save name on file",
  record_confirmation: "Confirm action",
};

/** Browse tools: labels can update while exploring slots, but never overwrite book/cancel/modify. */
function applyWeakToolIntent(
  event: ToolEvent,
  applyIntent: (label: string, weak: boolean) => void,
) {
  if (event.status !== "succeeded") return;
  if (event.tool_name === "fetch_slots") applyIntent(INTENT_LABELS.fetch_slots, true);
  if (event.tool_name === "list_departments") applyIntent(INTENT_LABELS.list_departments, true);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function formatDateParts(value: string): { date: string; time: string } {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return { date: "Pending", time: "Pending" };
  }

  return {
    date: parsed.toLocaleDateString("en-IN", {
      timeZone: "Asia/Kolkata",
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    }),
    time: parsed.toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    }),
  };
}

export function deriveSnapshot(events: ToolEvent[], appointments: Appointment[]): ExtractedSnapshot {
  let verifiedName = "";
  let phoneNumber = "";
  let slotStartAt = "";
  let intent = "";
  let strongIntent = false;

  const applyIntent = (label: string, weak: boolean) => {
    if (!label) return;
    if (weak) {
      if (strongIntent) return;
      intent = label;
      return;
    }
    strongIntent = true;
    intent = label;
  };

  for (const event of events) {
    if (event.tool_name === "identify_user" && event.status === "succeeded") {
      const n = asText(event.result_summary.name);
      if (n) verifiedName = n;
      phoneNumber =
        asText(event.result_summary.phone) || asText(event.input_summary.phone) || phoneNumber;
      applyIntent(INTENT_LABELS.identify_user, true);
    }

    if (event.tool_name === "update_patient_profile" && event.status === "succeeded") {
      const n = asText(event.result_summary.name);
      if (n) verifiedName = n;
      applyIntent(INTENT_LABELS.update_patient_profile, true);
    }

    if (event.tool_name === "record_confirmation" && event.status === "succeeded") {
      const label = INTENT_LABELS[asText(event.input_summary.action)];
      if (label) applyIntent(label, false);
    }

    if (event.tool_name === "book_appointment") {
      const start =
        event.status === "succeeded"
          ? asText(event.result_summary.slot_start_at) || asText(event.input_summary.slot_start_at)
          : asText(event.input_summary.slot_start_at);
      if (start) slotStartAt = start;
      if (event.status === "succeeded") applyIntent("Book appointment", false);
      else if (event.status === "started") applyIntent("Book appointment", false);
    }

    if (event.tool_name === "modify_appointment") {
      const start =
        event.status === "succeeded"
          ? asText(event.result_summary.new_slot_start_at) ||
            asText(event.input_summary.new_slot_start_at)
          : asText(event.input_summary.new_slot_start_at);
      if (start) slotStartAt = start;
      if (event.status === "succeeded" || event.status === "started") {
        applyIntent("Reschedule appointment", false);
      }
    }

    if (event.tool_name === "cancel_appointment" && event.status === "succeeded") {
      const st = asText(event.result_summary.slot_start_at);
      if (st) slotStartAt = st;
      applyIntent("Cancel appointment", false);
    }

    applyWeakToolIntent(event, applyIntent);
  }

  if (!slotStartAt && appointments.length > 0) {
    const booked = appointments.filter((a) => a.status === "booked");
    const pool = booked.length > 0 ? booked : appointments;
    const latestAppointment = pool
      .slice()
      .sort((left, right) => new Date(right.slot_start_at).getTime() - new Date(left.slot_start_at).getTime())[0];
    slotStartAt = latestAppointment?.slot_start_at || "";
  }

  const dateParts = slotStartAt ? formatDateParts(slotStartAt) : { date: "Pending", time: "Pending" };

  const phoneOk = Boolean(phoneNumber);
  const nameDisplay = verifiedName || (phoneOk ? "Not on file" : "Pending");

  return {
    name: nameDisplay,
    phoneNumber: phoneNumber || "Pending",
    date: dateParts.date,
    time: dateParts.time,
    intent: intent || "Pending",
  };
}

interface PanelProps extends Props {
  /** Live = in-call; review = post-call handoff */
  variant?: "live" | "review";
}

export function ConversationCapturePanel({ events, appointments, variant = "live" }: PanelProps) {
  const snapshot = deriveSnapshot(events, appointments);

  return (
    <section className="capture-panel">
      <div className="capture-heading">
        <h3 className="panel-title">Extracted from conversation</h3>
        <span className="capture-badge">{variant === "live" ? "Live" : "Handoff"}</span>
      </div>

      <div className="capture-grid">
        <div className="capture-field">
          <span>Name</span>
          <strong>{snapshot.name}</strong>
        </div>
        <div className="capture-field">
          <span>Phone number</span>
          <strong>{snapshot.phoneNumber}</strong>
        </div>
        <div className="capture-field">
          <span>Date</span>
          <strong>{snapshot.date}</strong>
        </div>
        <div className="capture-field">
          <span>Time</span>
          <strong>{snapshot.time}</strong>
        </div>
        <div className="capture-field capture-field-wide">
          <span>Intent</span>
          <strong>{snapshot.intent}</strong>
        </div>
      </div>
    </section>
  );
}
