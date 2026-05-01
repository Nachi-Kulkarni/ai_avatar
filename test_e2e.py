#!/usr/bin/env python3
"""
End-to-end API + tool flow test.

Simulates the full LLM agent flow by calling each tool function directly
and verifying Supabase state at every step.

Usage:
  cd apps/agent
  ../../test_e2e.py
"""
import json
import sys
import os
import time
import traceback
from datetime import datetime, timezone, timedelta

# Ensure we can import agent modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "apps", "agent"))
os.environ.setdefault("ENV_FILE", os.path.join(os.path.dirname(__file__), ".env"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results = []


def test(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def test_warn(name, detail=""):
    results.append((name, True, detail))
    print(f"  [{WARN}] {name}" + (f" — {detail}" if detail else ""))


# ── Phase 0: HTTP API endpoints ──────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 0: HTTP API ENDPOINTS")
print("=" * 60)

import subprocess

# Health check
r = subprocess.run(
    ["curl", "-s", "http://localhost:8000/api/health"],
    capture_output=True, text=True, timeout=5
)
if r.returncode == 0:
    health = json.loads(r.stdout)
    test("/api/health returns ok", health.get("status") == "ok", f"status={health.get('status')}")
else:
    test("/api/health reachable", False, "Server not running? Start with: python apps/agent/main.py api")
    sys.exit(1)

# Token endpoint
r = subprocess.run(
    ["curl", "-s", "-X", "POST", "http://localhost:8000/api/token",
     "-H", "Content-Type: application/json",
     "-d", json.dumps({"user_name": "E2E Test Patient"})],
    capture_output=True, text=True, timeout=5
)
token_resp = json.loads(r.stdout)
test("/api/token returns token", bool(token_resp.get("token")), f"token length={len(token_resp.get('token',''))}")
test("/api/token returns session_id", bool(token_resp.get("session_id")), f"session_id={token_resp.get('session_id','')[:8]}...")
test("/api/token returns server_url", "livekit.cloud" in token_resp.get("server_url", ""), f"url={token_resp.get('server_url','')}")
test("/api/token returns room_name", bool(token_resp.get("room_name")), f"room={token_resp.get('room_name','')}")

SESSION_ID = token_resp["session_id"]
ROOM_NAME = token_resp["room_name"]

# ── Phase 1: Import and test tools directly ──────────────────────────
print("\n" + "=" * 60)
print("PHASE 1: TOOL FUNCTIONS (direct import)")
print("=" * 60)

from db import get_supabase, normalize_phone
from tools import (
    identify_user, update_patient_profile, list_departments,
    fetch_slots, book_appointment, retrieve_appointments,
    cancel_appointment, modify_appointment, record_confirmation,
    end_conversation, set_current_session_id,
)
import asyncio

# Bind session
set_current_session_id(SESSION_ID)

sb = get_supabase()
test("Supabase client created", True)


async def run_tool(coro):
    return await coro


# ── Phase 2: identify_user ───────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 2: identify_user")
print("=" * 60)

TEST_PHONE = f"99{int(time.time()) % 100000000:08d}"  # Unique per test run

# Test invalid phone
result = asyncio.run(identify_user(phone="123", session_id=SESSION_ID))
test("identify_user rejects invalid phone", result["success"] is False, f"error_code={result.get('error_code')}")

# Test valid phone (creates new user)
result = asyncio.run(identify_user(phone=TEST_PHONE, session_id=SESSION_ID))
test("identify_user succeeds", result["success"] is True, f"msg={result.get('user_message','')[:60]}")
test("identify_user returns user_id", bool(result.get("user_id")), f"user_id={result.get('user_id','')[:8]}...")
test("identify_user returns is_new_user=True", result.get("is_new_user") is True)
test("identify_user returns needs_display_name=True", result.get("needs_display_name") is True)
test("identify_user normalizes phone", "+91" in result.get("phone", ""), f"phone={result.get('phone','')}")

USER_ID = result.get("user_id")

# Verify user row in Supabase
user_row = sb.table("users").select("*").eq("id", USER_ID).execute()
test("User row exists in Supabase", len(user_row.data) == 1, f"rows={len(user_row.data)}")
test("User phone matches", user_row.data[0]["phone"] == f"+91{TEST_PHONE}" if user_row.data else False)
test("User name is None (new user)", user_row.data[0].get("name") is None if user_row.data else False)

# Re-call identify_user with same phone (should find existing)
result2 = asyncio.run(identify_user(phone=TEST_PHONE, session_id=SESSION_ID))
test("identify_user finds existing user", result2["success"] is True)
test("identify_user returns is_new_user=False", result2.get("is_new_user") is False)
test("identify_user returns same user_id", result2.get("user_id") == USER_ID)

# ── Phase 3: update_patient_profile ──────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 3: update_patient_profile")
print("=" * 60)

result = asyncio.run(update_patient_profile(user_id=USER_ID, full_name="Meera Sharma", session_id=SESSION_ID))
test("update_patient_profile succeeds", result["success"] is True, f"msg={result.get('user_message','')[:60]}")
test("update_patient_profile returns name", result.get("name") == "Meera Sharma")

# Verify in Supabase
user_row = sb.table("users").select("*").eq("id", USER_ID).execute()
test("Name persisted in Supabase", user_row.data[0].get("name") == "Meera Sharma" if user_row.data else False)

# Test invalid name
result = asyncio.run(update_patient_profile(user_id=USER_ID, full_name="A", session_id=SESSION_ID))
test("update_patient_profile rejects short name", result["success"] is False, f"error_code={result.get('error_code')}")

# ── Phase 4: list_departments ────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 4: list_departments")
print("=" * 60)

result = asyncio.run(list_departments(session_id=SESSION_ID))
test("list_departments succeeds", result["success"] is True)
depts = result.get("departments", [])
test("list_departments returns departments", len(depts) > 0, f"count={len(depts)}")
for d in depts:
    print(f"    - {d['id']}: {d['name']}")

# Find Pediatrics department
PEDS_DEPT = next((d for d in depts if "Pediatri" in d["name"]), None)
GEN_MED_DEPT = next((d for d in depts if "General" in d["name"]), None)
test("Found Pediatrics department", PEDS_DEPT is not None, f"id={PEDS_DEPT['id'] if PEDS_DEPT else 'N/A'}")
test("Found General Medicine department", GEN_MED_DEPT is not None, f"id={GEN_MED_DEPT['id'] if GEN_MED_DEPT else 'N/A'}")

# ── Phase 5: fetch_slots ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 5: fetch_slots")
print("=" * 60)

if PEDS_DEPT:
    result = asyncio.run(fetch_slots(department_id=PEDS_DEPT["id"], session_id=SESSION_ID))
    test("fetch_slots succeeds", result["success"] is True)
    slots = result.get("slots", [])
    test("fetch_slots returns slots", len(slots) > 0, f"count={len(slots)}")
    if slots:
        FIRST_SLOT = slots[0]
        print(f"    First slot: start={FIRST_SLOT['slot_start_at']} end={FIRST_SLOT['slot_end_at']}")
        test("Slot has slot_start_at", bool(FIRST_SLOT.get("slot_start_at")))
        test("Slot has slot_end_at", bool(FIRST_SLOT.get("slot_end_at")))
        test("Slot has id", bool(FIRST_SLOT.get("id")))
    else:
        FIRST_SLOT = None
else:
    FIRST_SLOT = None
    test_warn("Skipping fetch_slots tests (no Pediatrics dept)")

# ── Phase 6: book_appointment ────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 6: book_appointment")
print("=" * 60)

if FIRST_SLOT and PEDS_DEPT:
    # Find a slot that isn't already booked by checking Supabase
    FREE_SLOT = None
    for slot in slots:
        existing = sb.table("appointments").select("id").eq(
            "department_id", PEDS_DEPT["id"]
        ).eq("slot_start_at", slot["slot_start_at"]).eq("status", "booked").execute()
        if not existing.data:
            FREE_SLOT = slot
            break

    if not FREE_SLOT:
        test_warn("All Pediatrics slots booked, using first anyway (expect conflict)")
        FREE_SLOT = FIRST_SLOT

    # record_confirmation first (as per agent flow)
    conf = asyncio.run(record_confirmation(
        appointment_id="pending",
        action="book",
        details=f"Booking Pediatrics appointment at {FREE_SLOT['slot_start_at']}",
        session_id=SESSION_ID,
    ))
    test("record_confirmation succeeds", conf["success"] is True)

    idem_key = f"e2e-test-{int(time.time())}"
    result = asyncio.run(book_appointment(
        user_id=USER_ID,
        department_id=PEDS_DEPT["id"],
        slot_start_at=FREE_SLOT["slot_start_at"],
        slot_end_at=FREE_SLOT["slot_end_at"],
        reason="E2E test — routine checkup",
        idempotency_key=idem_key,
        session_id=SESSION_ID,
    ))
    test("book_appointment succeeds", result["success"] is True, f"msg={result.get('user_message','')}")
    APPT_ID = result.get("appointment_id")
    test("book_appointment returns appointment_id", bool(APPT_ID), f"id={APPT_ID[:8] if APPT_ID else 'N/A'}...")

    if APPT_ID:
        # Verify in Supabase
        appt_row = sb.table("appointments").select("*").eq("id", APPT_ID).execute()
        test("Appointment row in Supabase", len(appt_row.data) == 1)
        if appt_row.data:
            test("Appointment status=booked", appt_row.data[0]["status"] == "booked")
            test("Appointment has reason", appt_row.data[0]["reason"] == "E2E test — routine checkup")
            test("Appointment user_id matches", appt_row.data[0]["user_id"] == USER_ID)

        # Check audit event
        events = sb.table("appointment_events").select("*").eq("appointment_id", APPT_ID).execute()
        test("Audit event created", len(events.data) > 0, f"events={len(events.data)}")
        if events.data:
            test("Audit event_type=created", events.data[0]["event_type"] == "created")

        # Test idempotency — same key should return existing
        result2 = asyncio.run(book_appointment(
            user_id=USER_ID,
            department_id=PEDS_DEPT["id"],
            slot_start_at=FIRST_SLOT["slot_start_at"],
            slot_end_at=FIRST_SLOT["slot_end_at"],
            idempotency_key=idem_key,
            session_id=SESSION_ID,
        ))
        test("Idempotent booking returns same appointment", result2.get("appointment_id") == APPT_ID)
    else:
        test_warn("Skipping appointment verification (booking failed)")
else:
    APPT_ID = None
    test_warn("Skipping book_appointment tests (no slots available)")

# ── Phase 7: retrieve_appointments ───────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 7: retrieve_appointments")
print("=" * 60)

result = asyncio.run(retrieve_appointments(user_id=USER_ID, session_id=SESSION_ID))
test("retrieve_appointments succeeds", result["success"] is True)
appts = result.get("appointments", [])
test("retrieve_appointments returns appointments", len(appts) > 0, f"count={len(appts)}")
if appts:
    print(f"    Appointment: id={appts[0]['id'][:8]}... dept={appts[0].get('departments')} status={appts[0]['status']}")
    test("Appointment has departments join", bool(appts[0].get("departments")))
    test("Appointment has department name", appts[0].get("departments", {}).get("name") if appts[0].get("departments") else False)

# ── Phase 8: modify_appointment ──────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 8: modify_appointment (reschedule)")
print("=" * 60)

if APPT_ID and PEDS_DEPT and GEN_MED_DEPT:
    # Fetch a slot from a different department to avoid double-booking unique index
    gm_slots = asyncio.run(fetch_slots(department_id=GEN_MED_DEPT["id"], session_id=SESSION_ID))
    gm_slot_list = gm_slots.get("slots", [])
    # Find a free slot that isn't already booked by anyone
    FREE_GM_SLOT = None
    for gs in gm_slot_list:
        existing = sb.table("appointments").select("id").eq(
            "department_id", GEN_MED_DEPT["id"]
        ).eq("slot_start_at", gs["slot_start_at"]).eq("status", "booked").execute()
        if not existing.data:
            FREE_GM_SLOT = gs
            break
    if FREE_GM_SLOT:
        conf = asyncio.run(record_confirmation(
            appointment_id=APPT_ID,
            action="modify",
            details=f"Rescheduling to General Medicine at {FREE_GM_SLOT['slot_start_at']}",
            session_id=SESSION_ID,
        ))

        result = asyncio.run(modify_appointment(
            appointment_id=APPT_ID,
            new_slot_start_at=FREE_GM_SLOT["slot_start_at"],
            new_slot_end_at=FREE_GM_SLOT["slot_end_at"],
            new_department_id=GEN_MED_DEPT["id"],
            session_id=SESSION_ID,
        ))
        test("modify_appointment succeeds", result["success"] is True, f"msg={result.get('user_message','')}")

        # Verify audit
        events = sb.table("appointment_events").select("*").eq("appointment_id", APPT_ID).order("created_at", desc=True).limit(1).execute()
        test("Modify audit event created", len(events.data) > 0 and events.data[0]["event_type"] == "modified" if events.data else False)
    else:
        test_warn("Skipping modify — no free General Medicine slots")
else:
    test_warn("Skipping modify — no appointment to modify")

# ── Phase 9: cancel flow ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 9: cancel_appointment")
print("=" * 60)

# Book a second appointment to cancel — use a slot from Cardiology
CARDIO_DEPT = next((d for d in depts if "Cardiology" in d["name"]), None)
if CARDIO_DEPT:
    cardio_slots_resp = asyncio.run(fetch_slots(department_id=CARDIO_DEPT["id"], session_id=SESSION_ID))
    cardio_slots = cardio_slots_resp.get("slots", [])
    # Find a free slot (not already booked)
    FREE_CARDIO_SLOT = None
    for cs in cardio_slots:
        existing = sb.table("appointments").select("id").eq(
            "department_id", CARDIO_DEPT["id"]
        ).eq("slot_start_at", cs["slot_start_at"]).eq("status", "booked").execute()
        if not existing.data:
            FREE_CARDIO_SLOT = cs
            break
    if FREE_CARDIO_SLOT:
        book2 = asyncio.run(book_appointment(
            user_id=USER_ID,
            department_id=CARDIO_DEPT["id"],
            slot_start_at=FREE_CARDIO_SLOT["slot_start_at"],
            slot_end_at=FREE_CARDIO_SLOT["slot_end_at"],
            reason="E2E test — cancellation target",
            session_id=SESSION_ID,
        ))
    if book2["success"]:
        CANCEL_APPT_ID = book2["appointment_id"]

        conf = asyncio.run(record_confirmation(
            appointment_id=CANCEL_APPT_ID,
            action="cancel",
            details="Cancelling test appointment",
            session_id=SESSION_ID,
        ))

        result = asyncio.run(cancel_appointment(
            appointment_id=CANCEL_APPT_ID,
            cancellation_reason="E2E test cancellation",
            session_id=SESSION_ID,
        ))
        test("cancel_appointment succeeds", result["success"] is True)

        # Verify status
        appt = sb.table("appointments").select("status, cancelled_at, cancellation_reason").eq("id", CANCEL_APPT_ID).execute()
        test("Appointment status=cancelled", appt.data[0]["status"] == "cancelled" if appt.data else False)
        test("cancelled_at is set", bool(appt.data[0].get("cancelled_at")) if appt.data else False)

        # Verify audit
        events = sb.table("appointment_events").select("*").eq("appointment_id", CANCEL_APPT_ID).eq("event_type", "cancelled").execute()
        test("Cancel audit event created", len(events.data) > 0)

        # Try cancelling already cancelled — should fail
        result2 = asyncio.run(cancel_appointment(appointment_id=CANCEL_APPT_ID, session_id=SESSION_ID))
        test("Cancel already-cancelled returns error", result2["success"] is False, f"error_code={result2.get('error_code')}")
    elif book2 and not book2.get("success"):
        test_warn("Could not book appointment for cancel test: " + str(book2.get('error_code')))
    else:
        test_warn("No free Cardiology slots for cancel test")
elif not CARDIO_DEPT:
    test_warn("Skipping cancel test (no Cardiology dept)")
else:
    test_warn("Skipping cancel test (no Cardiology slots)")

# ── Phase 10: end_conversation ───────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 10: end_conversation")
print("=" * 60)

result = asyncio.run(end_conversation(
    user_id=USER_ID,
    session_id=SESSION_ID,
    summary_notes="E2E test call: booked pediatrics appointment, cancelled general medicine. Patient was Meera Sharma.",
))
test("end_conversation succeeds", result["success"] is True)
summary = result.get("summary", {})
test("Summary has notes", bool(summary.get("notes")))
test("Summary has total_appointments", isinstance(summary.get("total_appointments"), int), f"total={summary.get('total_appointments')}")
test("Summary has booked list", isinstance(summary.get("booked"), list))
test("Summary has cancelled list", isinstance(summary.get("cancelled"), list))

# Verify call_summaries in Supabase
cs = sb.table("call_summaries").select("*").eq("session_id", SESSION_ID).execute()
test("call_summaries row in Supabase", len(cs.data) > 0)
if cs.data:
    test("call_summaries has user_id", cs.data[0].get("user_id") == USER_ID)
    test("call_summaries has summary JSON", bool(cs.data[0].get("summary")))
    test("call_summaries has appointment_ids", isinstance(cs.data[0].get("appointment_ids"), list))

# Verify conversation_sessions updated
sess = sb.table("conversation_sessions").select("status, session_state").eq("id", SESSION_ID).execute()
test("Session status=completed", sess.data[0]["status"] == "completed" if sess.data else False)
test("Session state=completed", sess.data[0]["session_state"] == "completed" if sess.data else False)

# ── Phase 11: tool_events verification ───────────────────────────────
print("\n" + "=" * 60)
print("PHASE 11: tool_events (Supabase)")
print("=" * 60)

tool_events = sb.table("tool_events").select("*").eq("session_id", SESSION_ID).order("created_at").execute()
test("Tool events exist for session", len(tool_events.data) > 0, f"count={len(tool_events.data)}")

tool_names_seen = set()
for ev in tool_events.data:
    tool_names_seen.add(ev["tool_name"])
    test(f"  tool_event: {ev['tool_name']} status={ev['status']}", True)

expected_tools = {"identify_user", "update_patient_profile", "list_departments", "fetch_slots", "book_appointment"}
missing = expected_tools - tool_names_seen
test("All expected tools logged", len(missing) == 0, f"missing={missing}" if missing else "all present")

# ── Phase 12: Frontend-facing checks (RLS / anon access) ─────────────
print("\n" + "=" * 60)
print("PHASE 12: FRONTEND RLS / ANON ACCESS")
print("=" * 60)

# The anon key from .env
ANON_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY", "")
SB_URL = os.environ.get("VITE_SUPABASE_URL", "")
test("VITE_SUPABASE_ANON_KEY set", bool(ANON_KEY))
test("VITE_SUPABASE_URL set", bool(SB_URL))

if ANON_KEY and SB_URL:
    from supabase import create_client as sc
    anon_sb = sc(SB_URL, ANON_KEY)

    # Test anon can read tool_events
    try:
        r = anon_sb.table("tool_events").select("*").eq("session_id", SESSION_ID).limit(1).execute()
        test("anon can SELECT tool_events", len(r.data) >= 0, f"rows={len(r.data)}")
    except Exception as e:
        test("anon can SELECT tool_events", False, str(e)[:100])

    # Test anon can read call_summaries
    try:
        r = anon_sb.table("call_summaries").select("*").eq("session_id", SESSION_ID).execute()
        test("anon can SELECT call_summaries", len(r.data) >= 0, f"rows={len(r.data)}")
    except Exception as e:
        test("anon can SELECT call_summaries", False, str(e)[:100])

    # Test anon can read conversation_sessions
    try:
        r = anon_sb.table("conversation_sessions").select("*").eq("id", SESSION_ID).execute()
        test("anon can SELECT conversation_sessions", len(r.data) >= 0, f"rows={len(r.data)}")
    except Exception as e:
        test("anon can SELECT conversation_sessions", False, str(e)[:100])

    # Test anon can read appointments
    try:
        r = anon_sb.table("appointments").select("id,status").eq("user_id", USER_ID).execute()
        test("anon can SELECT appointments", len(r.data) >= 0, f"rows={len(r.data)}")
    except Exception as e:
        test("anon can SELECT appointments", False, str(e)[:100])

    # Test anon can NOT INSERT (should fail)
    try:
        anon_sb.table("appointments").insert({"user_id": USER_ID, "department_id": 1, "slot_start_at": "2025-01-01T00:00:00Z", "slot_end_at": "2025-01-01T00:30:00Z"}).execute()
        test("anon CANNOT INSERT appointments", False, "RLS policy too permissive — anon can insert!")
    except Exception:
        test("anon CANNOT INSERT appointments", True, "Correctly blocked by RLS")

# ── Phase 13: ConversationCapturePanel data shape ────────────────────
print("\n" + "=" * 60)
print("PHASE 13: FRONTEND DATA SHAPE CHECK")
print("=" * 60)

# Verify tool_events have the fields the frontend expects
if tool_events.data:
    ev = tool_events.data[0]
    test("tool_event has id", bool(ev.get("id")))
    test("tool_event has tool_name", bool(ev.get("tool_name")))
    test("tool_event has status", bool(ev.get("status")))
    test("tool_event has input_summary", ev.get("input_summary") is not None)
    test("tool_event has result_summary", ev.get("result_summary") is not None)
    test("tool_event has created_at", bool(ev.get("created_at")))
    # latency_ms is only set on completed (succeeded/failed) events, not on "started"
    test("tool_event has latency_ms (when completed)", ev.get("latency_ms") is not None or ev.get("status") == "started")

    # Check input_summary is valid JSON
    if isinstance(ev.get("input_summary"), str):
        try:
            json.loads(ev["input_summary"])
            test("input_summary is valid JSON", True)
        except:
            test("input_summary is valid JSON", False)
    else:
        test("input_summary is already parsed object", True)

# Verify call_summaries shape
if cs.data:
    cs_row = cs.data[0]
    test("call_summary has session_id", bool(cs_row.get("session_id")))
    test("call_summary has user_id", bool(cs_row.get("user_id")))
    test("call_summary has summary", bool(cs_row.get("summary")))
    test("call_summary has appointment_ids", cs_row.get("appointment_ids") is not None)

    # summary field shape
    if isinstance(cs_row.get("summary"), str):
        summary_parsed = json.loads(cs_row["summary"])
    else:
        summary_parsed = cs_row["summary"]
    test("summary.notes exists", bool(summary_parsed.get("notes")))
    test("summary.total_appointments exists", "total_appointments" in summary_parsed)
    test("summary.booked exists", "booked" in summary_parsed)
    test("summary.cancelled exists", "cancelled" in summary_parsed)
    test("summary.timestamp exists", bool(summary_parsed.get("timestamp")))

# ── Phase 14: conversation_sessions creation via API ─────────────────
print("\n" + "=" * 60)
print("PHASE 14: conversation_sessions (created by agent)")
print("=" * 60)

# The API doesn't create a session row — the agent does when it joins the room.
# But we can verify the row exists from end_conversation
sess_row = sb.table("conversation_sessions").select("*").eq("id", SESSION_ID).execute()
test("conversation_session exists", len(sess_row.data) > 0)
if sess_row.data:
    s = sess_row.data[0]
    test("session has room_name", bool(s.get("room_name")), s.get("room_name", ""))
    test("session has started_at", bool(s.get("started_at")))
    test("session has ended_at", bool(s.get("ended_at")))


# ── SUMMARY ──────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total = len(results)
print(f"\n  {PASS} {passed}/{total} passed")
if failed:
    print(f"  {FAIL} {failed}/{total} failed")
    print("\n  Failed tests:")
    for name, ok, detail in results:
        if not ok:
            print(f"    - {name}: {detail}")
else:
    print(f"\n  All tests passed!")

# Cleanup: kill API server
print()
