from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from livekit.agents import llm
from db import get_supabase, normalize_phone

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────

_CURRENT_SESSION_ID: ContextVar[str] = ContextVar("current_session_id", default="")
_LAST_SESSION_ID = ""


def set_current_session_id(session_id: str):
    """Bind tool event logging to the active LiveKit call session."""
    global _LAST_SESSION_ID
    _LAST_SESSION_ID = session_id
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_session_id(token) -> None:
    """Restore the previous session binding when the call worker exits."""
    _CURRENT_SESSION_ID.reset(token)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_tool_event(session_id: str, tool_name: str, status: str,
                    input_summary: dict | None = None,
                    result_summary: dict | None = None,
                    appointment_id: str | None = None,
                    latency_ms: int | None = None):
    # Prefer the context var (set by agent worker) over the LLM-provided param.
    resolved_session_id = _CURRENT_SESSION_ID.get() or _LAST_SESSION_ID or session_id
    if not resolved_session_id:
        return

    try:
        sb = get_supabase()
        sb.table("tool_events").insert({
            "session_id": resolved_session_id,
            "tool_name": tool_name,
            "status": status,
            "input_summary": json.dumps(input_summary or {}),
            "result_summary": json.dumps(result_summary or {}),
            "appointment_id": appointment_id,
            "latency_ms": latency_ms,
        }).execute()
    except Exception as e:
        log.warning(
            "TOOL_EVENT insert failed tool=%s status=%s session=%s: %s",
            tool_name,
            status,
            resolved_session_id,
            e,
            exc_info=log.isEnabledFor(logging.DEBUG),
        )


def _ok(user_message: str, **extra) -> dict:
    return {"success": True, "error_code": None, "user_message": user_message, **extra}


def _err(error_code: str, user_message: str) -> dict:
    return {"success": False, "error_code": error_code, "user_message": user_message}


def _needs_display_name(name: object) -> bool:
    """True when we should ask for a name on file (new row or empty name)."""
    if name is None:
        return True
    if not isinstance(name, str):
        return True
    return len(name.strip()) < 2


# ── tools ────────────────────────────────────────────────────────

@llm.function_tool()
async def identify_user(
    phone: str,
    session_id: str = "",
) -> dict:
    """Look up or create the patient row for this phone number.

    **Phone** is the only input — never pass a name here. After this call, if
    ``needs_display_name`` is true, ask how they want their name on file and call
    ``update_patient_profile``.

    Args:
        phone: Indian mobile number (10 digits, or with +91 prefix).
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    _log_tool_event(session_id, "identify_user", "started", {"phone": phone})

    try:
        normalized = normalize_phone(phone)
    except ValueError as e:
        log.info("identify_user: invalid phone from STT/model phone=%r err=%s", phone, e)
        _log_tool_event(session_id, "identify_user", "failed", {"phone": phone},
                        {"error": str(e)})
        return _err("INVALID_PHONE", str(e))

    sb = get_supabase()
    digits = "".join(c for c in normalized if c.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else digits

    try:
        user = sb.table("users").select("*").eq("phone", normalized).execute()
    except Exception as e:
        log.exception(
            "identify_user: Supabase users.select failed phone_tail=%s (check SUPABASE_* in .env)",
            tail,
        )
        _log_tool_event(
            session_id,
            "identify_user",
            "failed",
            {"phone": phone},
            {"error": type(e).__name__, "detail": str(e)[:500]},
        )
        return _err(
            "BACKEND_ERROR",
            "We could not reach the patient records service. Please try again in a moment.",
        )

    if user.data:
        user_record = user.data[0]
        need_name = _needs_display_name(user_record.get("name"))
        _log_tool_event(session_id, "identify_user", "succeeded",
                        result_summary={
                            "user_id": user_record["id"],
                            "phone": normalized,
                            "name": user_record.get("name"),
                            "is_new_user": False,
                            "needs_display_name": need_name,
                        },
                        latency_ms=int((time.monotonic() - t0) * 1000))
        log.info("identify_user: found user id=%s tail=%s", user_record["id"], tail)
        return _ok(
            f"Number verified — I have your file on record (ends with {tail}).",
            user_id=user_record["id"],
            phone=normalized,
            name=user_record.get("name"),
            is_new_user=False,
            needs_display_name=need_name,
        )

    try:
        new_user = sb.table("users").insert({
            "phone": normalized,
            "name": None,
            "patient_number": f"P{uuid.uuid4().hex[:8].upper()}",
        }).execute().data[0]
    except Exception as e:
        log.exception("identify_user: Supabase users.insert failed phone_tail=%s", tail)
        _log_tool_event(
            session_id,
            "identify_user",
            "failed",
            {"phone": phone},
            {"error": type(e).__name__, "detail": str(e)[:500]},
        )
        return _err(
            "BACKEND_ERROR",
            "We could not create your record right now. Please try again shortly.",
        )

    _log_tool_event(session_id, "identify_user", "succeeded",
                    result_summary={
                        "user_id": new_user["id"],
                        "phone": normalized,
                        "name": new_user.get("name"),
                        "is_new_user": True,
                        "needs_display_name": True,
                    },
                    latency_ms=int((time.monotonic() - t0) * 1000))
    log.info("identify_user: created user id=%s tail=%s", new_user["id"], tail)
    return _ok(
        f"I've opened a new file for this number (ends with {tail}).",
        user_id=new_user["id"],
        phone=normalized,
        name=new_user.get("name"),
        is_new_user=True,
        needs_display_name=True,
    )


@llm.function_tool()
async def update_patient_profile(
    user_id: str,
    full_name: str,
    session_id: str = "",
) -> dict:
    """Save the name the caller wants on their chart after identify_user.

    Only for display and staff handoff — **never** use this for identity; phone + user_id stay authoritative.
    If speech-to-text garbles the name, offer to spell or shorten it.

    Args:
        user_id: UUID from identify_user.
        full_name: Name as the patient wants it shown (one string).
        session_id: Current conversation session ID.
    """
    name = " ".join((full_name or "").split()).strip()
    if len(name) < 2:
        return _err("INVALID_NAME", "I didn't catch a full name — could you say it again?")
    if len(name) > 120:
        return _err("INVALID_NAME", "That name is too long — could you give a shorter version?")

    t0 = time.monotonic()
    _log_tool_event(
        session_id,
        "update_patient_profile",
        "started",
        {"user_id": user_id, "full_name": name},
    )
    try:
        sb = get_supabase()
        sb.table("users").update({"name": name}).eq("id", user_id).execute()
    except Exception as e:
        log.exception("update_patient_profile: Supabase update failed user_id=%s", user_id)
        _log_tool_event(
            session_id,
            "update_patient_profile",
            "failed",
            {"user_id": user_id},
            {"error": type(e).__name__, "detail": str(e)[:500]},
        )
        return _err(
            "BACKEND_ERROR",
            "We could not update your name just now. We can still continue with your appointment.",
        )

    _log_tool_event(
        session_id,
        "update_patient_profile",
        "succeeded",
        {"user_id": user_id},
        result_summary={"name": name},
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
    log.info("update_patient_profile: user_id=%s name=%r", user_id, name)
    return _ok(f"I've saved {name} on your file.", user_id=user_id, name=name)


@llm.function_tool()
async def list_departments(session_id: str = "") -> dict:
    """List all active departments available for appointment booking.

    Args:
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    sb = get_supabase()
    depts = sb.table("departments").select("id, name").eq("is_active", True).execute()
    result = _ok("Available departments",
               departments=[{"id": d["id"], "name": d["name"]} for d in depts.data])
    _log_tool_event(session_id, "list_departments", "succeeded",
                    result_summary={"count": len(depts.data)},
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


@llm.function_tool()
async def fetch_slots(
    department_id: int,
    session_id: str = "",
) -> dict:
    """Fetch available appointment slots for a department.

    Args:
        department_id: The department ID to fetch slots for.
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    _log_tool_event(session_id, "fetch_slots", "started", {"department_id": department_id})

    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    # Get slots not already booked
    slots = sb.table("appointment_slots").select(
        "id, slot_start_at, slot_end_at"
    ).eq("department_id", department_id).gte("slot_start_at", now).eq(
        "is_available", True
    ).order("slot_start_at").limit(20).execute()

    if not slots.data:
        _log_tool_event(session_id, "fetch_slots", "succeeded",
                        result_summary={"count": 0})
        return _ok("No slots available for this department in the next 7 days.",
                    slots=[], count=0)

    _log_tool_event(session_id, "fetch_slots", "succeeded",
                    result_summary={"count": len(slots.data)},
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return _ok(f"Found {len(slots.data)} available slots.",
               slots=slots.data, count=len(slots.data))


@llm.function_tool()
async def book_appointment(
    user_id: str,
    department_id: int,
    slot_start_at: str,
    slot_end_at: str,
    reason: str | None = None,
    idempotency_key: str | None = None,
    session_id: str = "",
) -> dict:
    """Book an appointment for a user. Requires prior confirmation.

    Args:
        user_id: The user's UUID.
        department_id: The department to book with.
        slot_start_at: ISO 8601 UTC start time.
        slot_end_at: ISO 8601 UTC end time.
        reason: Optional reason for visit.
        idempotency_key: Deduplication key for retries.
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    _log_tool_event(session_id, "book_appointment", "started", {
        "user_id": user_id, "department_id": department_id,
        "slot_start_at": slot_start_at,
    })

    sb = get_supabase()

    # Check idempotency
    if idempotency_key:
        existing = sb.table("appointments").select("id, status").eq(
            "idempotency_key", idempotency_key
        ).execute()
        if existing.data:
            return _ok("Appointment already booked.",
                       appointment_id=existing.data[0]["id"])

    try:
        appt = sb.table("appointments").insert({
            "user_id": user_id,
            "department_id": department_id,
            "slot_start_at": slot_start_at,
            "slot_end_at": slot_end_at,
            "status": "booked",
            "reason": reason,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }).execute().data[0]

        # Audit event
        sb.table("appointment_events").insert({
            "appointment_id": appt["id"],
            "event_type": "created",
            "new_values": json.dumps({"status": "booked", "slot_start_at": slot_start_at}),
            "actor": "agent",
        }).execute()

        _log_tool_event(session_id, "book_appointment", "succeeded",
                        appointment_id=appt["id"],
                        result_summary={
                            "appointment_id": appt["id"],
                            "status": "booked",
                            "slot_start_at": slot_start_at,
                            "slot_end_at": slot_end_at,
                            "reason": reason,
                        },
                        latency_ms=int((time.monotonic() - t0) * 1000))

        return _ok("Appointment booked successfully.",
                   appointment_id=appt["id"],
                   slot_start_at=slot_start_at, slot_end_at=slot_end_at)

    except Exception as e:
        error_msg = str(e)
        if "idx_appointments_no_double_book" in error_msg or "idx_appointments_no_patient_overlap" in error_msg:
            _log_tool_event(session_id, "book_appointment", "failed",
                            result_summary={"error": "double_booking"})
            return _err("SLOT_UNAVAILABLE",
                        "That slot is no longer available. Please choose another time.")
        _log_tool_event(session_id, "book_appointment", "failed",
                        result_summary={"error": error_msg})
        return _err("BOOKING_FAILED", "Unable to book appointment. Please try again.")


@llm.function_tool()
async def retrieve_appointments(
    user_id: str,
    session_id: str = "",
) -> dict:
    """Retrieve upcoming booked appointments for a user.

    Args:
        user_id: The user's UUID.
        session_id: Current conversation session ID.
    """
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    appts = sb.table("appointments").select(
        "id, department_id, slot_start_at, slot_end_at, status, reason, departments(name)"
    ).eq("user_id", user_id).eq("status", "booked").gte(
        "slot_start_at", now
    ).order("slot_start_at").execute()

    return _ok(f"Found {len(appts.data)} upcoming appointments.",
               appointments=appts.data)


@llm.function_tool()
async def cancel_appointment(
    appointment_id: str,
    cancellation_reason: str | None = None,
    session_id: str = "",
) -> dict:
    """Cancel a booked appointment. Requires prior confirmation.

    Args:
        appointment_id: The appointment UUID to cancel.
        cancellation_reason: Optional reason for cancellation.
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    _log_tool_event(session_id, "cancel_appointment", "started",
                    {
                        "appointment_id": appointment_id,
                        "cancellation_reason": cancellation_reason,
                    })

    sb = get_supabase()

    appt = sb.table("appointments").select("id, status, slot_start_at").eq(
        "id", appointment_id
    ).execute()

    if not appt.data:
        return _err("NOT_FOUND", "Appointment not found.")

    if appt.data[0]["status"] != "booked":
        return _err("INVALID_STATUS", "This appointment cannot be cancelled.")

    old = appt.data[0]
    updated = sb.table("appointments").update({
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "cancellation_reason": cancellation_reason,
    }).eq("id", appointment_id).execute().data[0]

    sb.table("appointment_events").insert({
        "appointment_id": appointment_id,
        "event_type": "cancelled",
        "old_values": json.dumps({"status": old["status"]}),
        "new_values": json.dumps({"status": "cancelled"}),
        "actor": "agent",
    }).execute()

    _log_tool_event(session_id, "cancel_appointment", "succeeded",
                    appointment_id=appointment_id,
                    result_summary={
                        "appointment_id": appointment_id,
                        "slot_start_at": old["slot_start_at"],
                        "status": "cancelled",
                    },
                    latency_ms=int((time.monotonic() - t0) * 1000))

    return _ok("Appointment cancelled successfully.",
               appointment_id=appointment_id)


@llm.function_tool()
async def modify_appointment(
    appointment_id: str,
    new_slot_start_at: str,
    new_slot_end_at: str,
    new_department_id: int | None = None,
    session_id: str = "",
) -> dict:
    """Modify an existing appointment to a new slot. Requires prior confirmation.

    Args:
        appointment_id: The appointment UUID to modify.
        new_slot_start_at: New ISO 8601 UTC start time.
        new_slot_end_at: New ISO 8601 UTC end time.
        new_department_id: Optional new department ID.
        session_id: Current conversation session ID.
    """
    t0 = time.monotonic()
    _log_tool_event(session_id, "modify_appointment", "started",
                    {
                        "appointment_id": appointment_id,
                        "new_slot_start_at": new_slot_start_at,
                        "new_slot_end_at": new_slot_end_at,
                        "new_department_id": new_department_id,
                    })

    sb = get_supabase()

    appt = sb.table("appointments").select("*").eq("id", appointment_id).execute()
    if not appt.data:
        return _err("NOT_FOUND", "Appointment not found.")
    if appt.data[0]["status"] != "booked":
        return _err("INVALID_STATUS", "Only booked appointments can be modified.")

    old = appt.data[0]
    update_data = {
        "slot_start_at": new_slot_start_at,
        "slot_end_at": new_slot_end_at,
        "lock_version": old["lock_version"] + 1,
    }
    if new_department_id:
        update_data["department_id"] = new_department_id

    try:
        updated = sb.table("appointments").update(update_data).eq(
            "id", appointment_id
        ).execute().data[0]

        sb.table("appointment_events").insert({
            "appointment_id": appointment_id,
            "event_type": "modified",
            "old_values": json.dumps({
                "slot_start_at": old["slot_start_at"],
                "department_id": old["department_id"],
            }),
            "new_values": json.dumps({
                "slot_start_at": new_slot_start_at,
                "department_id": new_department_id or old["department_id"],
            }),
            "actor": "agent",
        }).execute()

        _log_tool_event(session_id, "modify_appointment", "succeeded",
                        appointment_id=appointment_id,
                        result_summary={
                            "new_slot_start_at": new_slot_start_at,
                            "new_slot_end_at": new_slot_end_at,
                            "department_id": new_department_id or old["department_id"],
                        },
                        latency_ms=int((time.monotonic() - t0) * 1000))

        return _ok("Appointment rescheduled successfully.",
                   appointment_id=appointment_id,
                   new_slot_start_at=new_slot_start_at)

    except Exception as e:
        if "idx_appointments" in str(e):
            return _err("SLOT_UNAVAILABLE",
                        "The new slot is not available. Please choose another time.")
        return _err("MODIFY_FAILED", "Unable to reschedule. Please try again.")


@llm.function_tool()
async def record_confirmation(
    appointment_id: str,
    action: str,
    details: str,
    session_id: str = "",
) -> dict:
    """Record the user's explicit confirmation before a state-changing action.

    Args:
        appointment_id: The appointment being confirmed.
        action: The action being confirmed (book, cancel, modify).
        details: Human-readable summary of what was confirmed.
        session_id: Current conversation session ID.
    """
    _log_tool_event(session_id, "record_confirmation", "succeeded", {
        "appointment_id": appointment_id, "action": action,
        "details": details,
    }, {"confirmed": True})
    return _ok("Confirmation recorded.", action=action, confirmed=True)


@llm.function_tool()
async def end_conversation(
    user_id: str,
    session_id: str = "",
    summary_notes: str | None = None,
) -> dict:
    """End the conversation and generate a structured summary.

    Args:
        session_id: Current conversation session ID.
        user_id: The user's UUID.
        summary_notes: Optional LLM-generated notes about the conversation.
    """
    t0 = time.monotonic()
    # Always prefer the context var set by the agent worker over the LLM-provided value
    # (LLMs hallucinate session IDs like "session_12345" which fail UUID validation).
    resolved_session_id = _CURRENT_SESSION_ID.get() or _LAST_SESSION_ID or session_id
    sb = get_supabase()

    # Get appointments for this session
    appts = sb.table("appointments").select(
        "id, status, slot_start_at, departments(name)"
    ).eq("user_id", user_id).execute()

    summary = {
        "notes": summary_notes or "Call ended.",
        "total_appointments": len(appts.data),
        "booked": [a for a in appts.data if a["status"] == "booked"],
        "cancelled": [a for a in appts.data if a["status"] == "cancelled"],
        "timestamp": _ts(),
    }

    sb.table("call_summaries").upsert({
        "session_id": resolved_session_id,
        "user_id": user_id,
        "summary": json.dumps(summary),
        "appointment_ids": [a["id"] for a in appts.data],
    }, on_conflict="session_id").execute()

    sb.table("conversation_sessions").update({
        "status": "completed",
        "session_state": "completed",
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", resolved_session_id).execute()

    _log_tool_event(resolved_session_id, "end_conversation", "succeeded",
                    result_summary={"summary_generated": True},
                    latency_ms=int((time.monotonic() - t0) * 1000))

    return _ok("Conversation ended. Thank you for calling!", summary=summary)


# Collect all tools for the agent
ALL_TOOLS = [
    identify_user,
    update_patient_profile,
    list_departments,
    fetch_slots,
    book_appointment,
    retrieve_appointments,
    cancel_appointment,
    modify_appointment,
    record_confirmation,
    end_conversation,
]
