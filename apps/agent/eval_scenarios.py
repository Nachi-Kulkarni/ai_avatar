"""
LLM Evaluation Scaffolding — Healthcare Voice Agent (Priya)

Runs 15 multi-turn scenarios against the LLM to score:
  1. Conversational quality (tone, warmth, naturalness)
  2. Tool calling accuracy (right tool, right time, right args)
  3. Flow correctness (identify user -> fetch slots -> book -> confirm)
  4. Error handling (double bookings, invalid inputs, missing info)
  5. Instruction adherence (no medical advice, confirmation before actions)

Each scenario = 3-5 turns of user messages with expected tool calls.
A judge LLM rates each scenario on a 1-10 scale across these dimensions.
Scenarios run in parallel (batch of 10 concurrent).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# Fix SSL for Python 3.14
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from openai import OpenAI

# ── Config ─────────────────────────────────────────────────────────

# Eval uses its own model default so production OPENROUTER_MODEL (voice agent) stays unchanged.
MODEL = os.getenv("EVAL_MODEL", "moonshotai/kimi-k2-0905")
API_KEY = os.getenv("OPENROUTER_API_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1"
PARALLELISM = 10
# OpenRouter-style extended reasoning (https://openrouter.ai/docs). exclude=True keeps internal
# reasoning out of the API payload so it is not echoed into our transcript or eval JSON.
EVAL_REASONING_EFFORT = os.getenv("EVAL_REASONING_EFFORT", "medium")
EVAL_REASONING_EXCLUDE = os.getenv("EVAL_REASONING_EXCLUDE", "true").lower() in ("1", "true", "yes")
SCENARIO_FUTURE_TIMEOUT = int(os.getenv("EVAL_SCENARIO_TIMEOUT", "300"))


def _openrouter_reasoning_body() -> dict:
    """Reasoning config for OpenRouter chat completions (extra_body.reasoning)."""
    body: dict = {"effort": EVAL_REASONING_EFFORT}
    if EVAL_REASONING_EXCLUDE:
        body["exclude"] = True
    return body

SYSTEM_PROMPT = """You are Priya, a friendly and professional healthcare receptionist for mykare.ai clinic.

Your role:
- Help patients book, view, modify, and cancel appointments
- Collect the **mobile number** first for lookup; then **name on file** when the tool says it is needed
- Provide a summary at the end of the call

## Identity — phone first, then profile name
- Call `identify_user` with **phone only** — never pass a name into it.
- If **`needs_display_name`** is true, ask what name they want on their file and call **`update_patient_profile(user_id, full_name)`**.
- All bookings use **user_id** from tools; phone is the database key.

Rules:
- NEVER announce booking, cancellation, or modification success until the tool confirms it
- ALWAYS call record_confirmation before book_appointment, cancel_appointment, or modify_appointment
- ALWAYS verify the user's identity by calling identify_user with their **phone** as soon as they give it
- Be warm, professional, and concise
- Speak naturally without complex formatting or punctuation
- If a slot is unavailable, suggest the nearest available alternatives
- Do not provide medical advice — you only handle scheduling
- When a user gives you their phone number, IMMEDIATELY call identify_user with **phone only** — do not just acknowledge it
- NEVER fabricate appointment details. Only present data that retrieve_appointments actually returns. If the tool returns empty results, tell the user they have no upcoming appointments.

## Voice channel — how you talk to patients
- Always write our clinic name as **mykare.ai** in your replies. The phone voice pronounces that like "my care A I"; do not invent other spellings for the brand.
- Spoken words only: never use markdown, asterisks, bold, or screen-style bullets. Say "first, second, third" or short sentences instead.
- Never read UUIDs, "slot IDs", or long ISO timestamps aloud. Refer to times the way people say them on the phone.
- Times from scheduling tools are stored in UTC. Callers are in India: convert to IST (UTC+5:30) before you speak, e.g. "nine thirty tomorrow morning" not "06:30 UTC".
- Offer at most three or four slots per turn in plain language, then ask which they want.
- Keep the exact slot_start_at and slot_end_at from fetch_slots in your working context for book_appointment; do not repeat those machine strings to the caller.

## ABSOLUTE RULES — VIOLATING THESE IS A CRITICAL FAILURE:

### RULE 1: NEVER ask "is that correct?" or "shall I proceed?" when the user picks a slot
When the user selects a specific slot or time, they ARE confirming. Do NOT ask for verbal re-confirmation.
IMMEDIATELY call record_confirmation followed by the mutation tool (book_appointment / cancel_appointment / modify_appointment) in the SAME response.

WRONG: "Let me confirm — you'd like the 9 AM slot. Is that correct?"
RIGHT: [calls record_confirmation then book_appointment immediately]

### RULE 2: ALWAYS call end_conversation when the user says goodbye
When the user says "bye", "goodbye", "thanks bye", "that's all", "see you", or any farewell phrase, IMMEDIATELY call end_conversation with a summary. Do NOT ask "is there anything else?" or "would you like to book?". Just end the conversation gracefully.

### RULE 3: When the user provides ALL information at once, call ALL tools in sequence
If the user says "I'm Meera, phone 9988776655, need pediatrics tomorrow morning", call identify_user with **phone 9988776655 only**, then **update_patient_profile** if needed, then fetch_slots — as many steps as fit in one response.

### RULE 4: Call list_departments to resolve department names to IDs
When the user names a specific department (e.g. "cardiology", "dental"), call list_departments to find the correct department ID, then call fetch_slots with that ID. Do NOT guess department IDs.

## MANDATORY TOOL FLOW:

### Booking flow:
1. User expresses intent to book → ask for **mobile number** first
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, ask for name on file → call **update_patient_profile(user_id, full_name)**, then continue
4. Ask department, preferred date, time, and **reason for visit** when relevant
5. User picks department → call list_departments, then IMMEDIATELY call fetch_slots(department_id)
6. Present slots → user picks one → IMMEDIATELY call record_confirmation then book_appointment
7. User says bye → IMMEDIATELY call end_conversation

### Retrieve flow:
1. User asks for appointments → ask for **phone** if needed
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, use update_patient_profile when natural
4. IMMEDIATELY call retrieve_appointments(user_id)
5. Present results
6. User says bye → IMMEDIATELY call end_conversation

### Cancel flow:
1. User wants to cancel → ask for phone
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, update_patient_profile when natural
4. IMMEDIATELY call retrieve_appointments(user_id)
5. User confirms → IMMEDIATELY call record_confirmation then cancel_appointment
6. User says bye → IMMEDIATELY call end_conversation

### Modify flow:
1. User wants to reschedule → ask for phone
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, update_patient_profile when natural
4. IMMEDIATELY call retrieve_appointments(user_id)
5. Ask for new time → IMMEDIATELY call fetch_slots
6. User picks new slot → IMMEDIATELY call record_confirmation then modify_appointment
7. User says bye → IMMEDIATELY call end_conversation

CRITICAL: Every time the user provides a phone number, you MUST call identify_user in your very next response. Do NOT just say "thank you" or ask more questions — CALL THE TOOL."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "identify_user",
            "description": "Look up or create the patient by Indian mobile number only. Phone is the unique ID; do not pass a name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Indian mobile number (10 digits, or with +91 prefix)"},
                    "session_id": {"type": "string", "description": "Session ID", "default": "eval-session"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_patient_profile",
            "description": "Save the patient's preferred name on file after identify_user when needs_display_name is true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "full_name": {"type": "string"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["user_id", "full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_departments",
            "description": "List all active departments available for appointment booking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "eval-session"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_slots",
            "description": "Fetch available appointment slots for a department.",
            "parameters": {
                "type": "object",
                "properties": {
                    "department_id": {"type": "integer", "description": "Department ID"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["department_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book an appointment for a user. Requires prior confirmation via record_confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User UUID"},
                    "department_id": {"type": "integer"},
                    "slot_start_at": {"type": "string", "description": "ISO 8601 UTC"},
                    "slot_end_at": {"type": "string", "description": "ISO 8601 UTC"},
                    "reason": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["user_id", "department_id", "slot_start_at", "slot_end_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_appointments",
            "description": "Retrieve upcoming booked appointments for a user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel a booked appointment. Requires prior confirmation via record_confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "cancellation_reason": {"type": "string"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_appointment",
            "description": "Modify an existing appointment to a new slot. Requires prior confirmation via record_confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "new_slot_start_at": {"type": "string"},
                    "new_slot_end_at": {"type": "string"},
                    "new_department_id": {"type": "integer"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["appointment_id", "new_slot_start_at", "new_slot_end_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_confirmation",
            "description": "Record the user's explicit confirmation before a state-changing action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "action": {"type": "string", "description": "book, cancel, or modify"},
                    "details": {"type": "string"},
                    "session_id": {"type": "string", "default": "eval-session"},
                },
                "required": ["appointment_id", "action", "details"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": "End the conversation and generate a summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "eval-session"},
                    "user_id": {"type": "string"},
                    "summary_notes": {"type": "string"},
                },
                "required": ["session_id", "user_id"],
            },
        },
    },
]


# ── Mock DB Responses ──────────────────────────────────────────────

MOCK_USER_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
MOCK_APPOINTMENT_ID = "appt-1111-2222-3333"

MOCK_TOOL_RESPONSES = {
    "identify_user": json.dumps({
        "success": True,
        "user_message": "Number verified — I have your file on record (ends with 3210).",
        "user_id": MOCK_USER_ID,
        "phone": "+919876543210",
        "name": None,
        "is_new_user": False,
        "needs_display_name": True,
    }),
    "update_patient_profile": json.dumps({
        "success": True,
        "user_message": "I've saved Rahul Kumar on your file.",
        "user_id": MOCK_USER_ID,
        "name": "Rahul Kumar",
    }),
    "list_departments": json.dumps({
        "success": True, "user_message": "Available departments",
        "departments": [
            {"id": 1, "name": "General Medicine"}, {"id": 2, "name": "Cardiology"},
            {"id": 3, "name": "Dermatology"}, {"id": 4, "name": "Orthopedics"},
            {"id": 5, "name": "Pediatrics"}, {"id": 6, "name": "Dental"},
        ],
    }),
    "fetch_slots": json.dumps({
        "success": True, "user_message": "Found 5 available slots.",
        "slots": [
            {"id": 1, "slot_start_at": "2026-04-29T09:00:00+00:00", "slot_end_at": "2026-04-29T09:30:00+00:00"},
            {"id": 2, "slot_start_at": "2026-04-29T10:00:00+00:00", "slot_end_at": "2026-04-29T10:30:00+00:00"},
            {"id": 3, "slot_start_at": "2026-04-29T11:00:00+00:00", "slot_end_at": "2026-04-29T11:30:00+00:00"},
            {"id": 4, "slot_start_at": "2026-04-30T09:00:00+00:00", "slot_end_at": "2026-04-30T09:30:00+00:00"},
            {"id": 5, "slot_start_at": "2026-04-30T10:00:00+00:00", "slot_end_at": "2026-04-30T10:30:00+00:00"},
        ], "count": 5,
    }),
    "fetch_slots_empty": json.dumps({
        "success": True, "user_message": "No slots available for this department.",
        "slots": [], "count": 0,
    }),
    "book_appointment": json.dumps({
        "success": True, "user_message": "Appointment booked successfully.",
        "appointment_id": MOCK_APPOINTMENT_ID,
        "slot_start_at": "2026-04-29T09:00:00+00:00", "slot_end_at": "2026-04-29T09:30:00+00:00",
    }),
    "book_appointment_double": json.dumps({
        "success": False, "error_code": "SLOT_UNAVAILABLE",
        "user_message": "That slot is no longer available. Please choose another time.",
    }),
    "retrieve_appointments": json.dumps({
        "success": True, "user_message": "Found 1 upcoming appointments.",
        "appointments": [{
            "id": MOCK_APPOINTMENT_ID, "department_id": 1,
            "slot_start_at": "2026-04-29T09:00:00+00:00", "slot_end_at": "2026-04-29T09:30:00+00:00",
            "status": "booked", "reason": "general checkup", "departments": {"name": "General Medicine"},
        }],
    }),
    "retrieve_appointments_empty": json.dumps({
        "success": True, "user_message": "No upcoming appointments found.",
        "appointments": [],
    }),
    "cancel_appointment": json.dumps({
        "success": True, "user_message": "Appointment cancelled successfully.",
        "appointment_id": MOCK_APPOINTMENT_ID,
    }),
    "modify_appointment": json.dumps({
        "success": True, "user_message": "Appointment rescheduled successfully.",
        "appointment_id": MOCK_APPOINTMENT_ID, "new_slot_start_at": "2026-04-30T10:00:00+00:00",
    }),
    "record_confirmation": json.dumps({
        "success": True, "user_message": "Confirmation recorded.",
        "action": "book", "confirmed": True,
    }),
    "end_conversation": json.dumps({
        "success": True, "user_message": "Conversation ended. Thank you for calling!",
        "summary": {"notes": "Booked general checkup", "total_appointments": 1},
    }),
    "identify_user_invalid": json.dumps({
        "success": False, "error_code": "INVALID_PHONE",
        "user_message": "Invalid Indian mobile number. Please provide a valid 10-digit number.",
    }),
}

# Track booking count per scenario to simulate double booking
_booking_counts: dict[int, int] = {}


def get_mock_response(scenario_id: int, tool_name: str, tool_args: dict) -> str:
    """Return mock DB response. Tracks booking count for double-booking sim."""
    if tool_name == "identify_user":
        phone = tool_args.get("phone", "").strip()
        if len(phone) < 10:
            return MOCK_TOOL_RESPONSES["identify_user_invalid"]
        return MOCK_TOOL_RESPONSES["identify_user"]

    if tool_name == "update_patient_profile":
        return MOCK_TOOL_RESPONSES["update_patient_profile"]

    if tool_name == "book_appointment":
        count = _booking_counts.get(scenario_id, 0)
        _booking_counts[scenario_id] = count + 1
        if count >= 1:
            return MOCK_TOOL_RESPONSES["book_appointment_double"]
        return MOCK_TOOL_RESPONSES["book_appointment"]

    if tool_name == "fetch_slots":
        dept_id = tool_args.get("department_id", 0)
        if dept_id == 6:  # Dental — simulate empty
            return MOCK_TOOL_RESPONSES["fetch_slots_empty"]
        return MOCK_TOOL_RESPONSES["fetch_slots"]

    if tool_name == "retrieve_appointments":
        return MOCK_TOOL_RESPONSES["retrieve_appointments"]

    return MOCK_TOOL_RESPONSES.get(tool_name, '{"success": true, "user_message": "Done."}')


# ── Scenario Definitions ───────────────────────────────────────────

@dataclass
class Turn:
    user_message: str
    expected_tools: list[str]
    notes: str = ""


@dataclass
class Scenario:
    id: int
    name: str
    description: str
    category: str
    turns: list[Turn]
    critical_rules: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(1, "Happy Path — Book General Medicine",
        "User wants a general checkup. Provides name, phone, picks first available slot.",
        "booking",
        [
            Turn("Hi, I want to book an appointment", [], "Should greet and ask for name/phone"),
            Turn("My name is Rahul, my number is 9876543210", ["identify_user"], "Must identify user"),
            Turn("I need a general checkup", ["list_departments", "fetch_slots"], "Should list depts and/or fetch slots"),
            Turn("Tomorrow 9 AM works", ["record_confirmation", "book_appointment"], "Must confirm then book"),
            Turn("Thanks, bye", ["end_conversation"], "Should end with summary"),
        ],
        ["identify_user before booking", "record_confirmation before book_appointment", "end_conversation on goodbye"]),

    Scenario(2, "Department Selection — Cardiology",
        "User asks for a specific department (Cardiology).",
        "booking",
        [
            Turn("Hello, I need a cardiology appointment", [], "Should greet, ask for identity"),
            Turn("I'm Priya Sharma, phone 9988776655", ["identify_user"], "Must identify user"),
            Turn("Cardiology department please", ["fetch_slots"], "Should fetch cardiology slots (dept_id=2)"),
            Turn("Let's do the 10 AM slot tomorrow", ["record_confirmation", "book_appointment"], "Confirm and book"),
            Turn("Great, that's all", ["end_conversation"], "End conversation"),
        ],
        ["identify_user before booking", "fetch_slots with correct department_id"]),

    Scenario(3, "Retrieve Appointments",
        "User wants to see their upcoming appointments.",
        "retrieval",
        [
            Turn("Hi, can I see my appointments?", [], "Should ask for identity"),
            Turn("My phone is 9876543210", ["identify_user"], "Must identify user"),
            Turn("Yes show me what I have booked", ["retrieve_appointments"], "Should retrieve appointments"),
            Turn("Okay thanks, goodbye", ["end_conversation"], "End conversation"),
        ],
        ["identify_user before retrieving", "retrieve_appointments with user_id"]),

    Scenario(4, "Cancel Existing Appointment",
        "User wants to cancel a previously booked appointment.",
        "cancellation",
        [
            Turn("I need to cancel my appointment", [], "Should ask for identity"),
            Turn("Phone 9876543210", ["identify_user"], "Identify user"),
            Turn("Yes cancel it, I can't make it", ["retrieve_appointments", "record_confirmation", "cancel_appointment"], "Should retrieve, confirm, then cancel"),
            Turn("Thanks, bye", ["end_conversation"], "End"),
        ],
        ["record_confirmation before cancel", "retrieve to show what's being cancelled"]),

    Scenario(5, "Reschedule Appointment",
        "User wants to change their appointment time.",
        "modification",
        [
            Turn("I need to reschedule my appointment", [], "Ask for identity"),
            Turn("My number is 9876543210", ["identify_user"], "Identify"),
            Turn("Move it to the next day instead, 10 AM", ["retrieve_appointments", "fetch_slots", "record_confirmation", "modify_appointment"], "Get current appt, check slots, confirm, modify"),
            Turn("Perfect, thanks!", ["end_conversation"], "End"),
        ],
        ["record_confirmation before modify", "modify_appointment with correct IDs"]),

    Scenario(6, "Invalid Phone Number",
        "User provides an invalid phone number.",
        "error_handling",
        [
            Turn("Hi I want to book", [], "Greet"),
            Turn("My number is 12345", ["identify_user"], "Should try identify_user, get error, explain"),
            Turn("Oh sorry, it's 9876543210", ["identify_user"], "Retry with valid number"),
            Turn("Never mind, bye", [], "Should end gracefully without booking"),
        ],
        ["Handle invalid phone gracefully", "Don't crash on bad input"]),

    Scenario(7, "Double Booking Prevention",
        "User tries to book the same slot twice.",
        "error_handling",
        [
            Turn("Book me for tomorrow 9 AM general medicine", [], "Ask for identity"),
            Turn("Rahul, 9876543210", ["identify_user"], "Identify"),
            Turn("Yes 9 AM tomorrow", ["fetch_slots", "record_confirmation", "book_appointment"], "Book it"),
            Turn("Now book me the same slot again", ["book_appointment"], "Should handle double booking error"),
        ],
        ["Handle double_booking error gracefully", "Suggest alternatives"]),

    Scenario(8, "No Available Slots",
        "User asks for a department with no slots (Dental).",
        "error_handling",
        [
            Turn("I need a dental appointment", [], "Ask for identity"),
            Turn("Phone 9876543210, name Ankit", ["identify_user"], "Identify"),
            Turn("Dental please", ["fetch_slots"], "Returns empty slots"),
            Turn("Oh nothing available? Bye then", ["end_conversation"], "End gracefully"),
        ],
        ["Handle empty slots gracefully", "Don't book when no slots"]),

    Scenario(9, "Medical Advice Refusal",
        "User asks for medical advice. Agent must refuse and redirect to scheduling.",
        "edge_case",
        [
            Turn("Hi, I have a headache, what medicine should I take?", [], "Must refuse medical advice, offer booking instead"),
            Turn("Okay fine, can I book an appointment then?", [], "Pivot to booking"),
            Turn("My number is 9123456789, name Deepa", ["identify_user"], "Identify"),
            Turn("General medicine, tomorrow morning", ["fetch_slots"], "Fetch slots - user hasn't picked a specific slot yet"),
            Turn("The 9 AM one please", ["record_confirmation", "book_appointment"], "Book the 9 AM slot"),
            Turn("Thanks, bye!", ["end_conversation"], "End"),
        ],
        ["NEVER give medical advice", "Redirect to scheduling"]),

    Scenario(10, "Incomplete Information",
        "User provides info piece by piece, missing details.",
        "edge_case",
        [
            Turn("I want to book", [], "Should ask for name and phone"),
            Turn("My name is Vijay", [], "Still needs phone number"),
            Turn("Oh right, phone is 9876543210", ["identify_user"], "Now can identify"),
            Turn("I want dental", ["fetch_slots"], "Fetch dental slots"),
            Turn("The first slot please", ["record_confirmation", "book_appointment"], "Confirm and book"),
        ],
        ["Collect ALL required info before tool calls", "Don't call identify_user without phone"]),

    Scenario(11, "Department Switch",
        "User starts with one department, then changes mind.",
        "edge_case",
        [
            Turn("Book me for cardiology", [], "Ask for identity"),
            Turn("Phone 9876543210, Suresh", ["identify_user"], "Identify"),
            Turn("Actually wait, I want dermatology instead", ["fetch_slots"], "Should fetch dermatology slots, NOT cardiology"),
            Turn("The 11 AM slot please", ["record_confirmation", "book_appointment"], "Book dermatology"),
            Turn("Great thanks!", ["end_conversation"], "End"),
        ],
        ["Adapt to department change", "Don't book cardiology when user switched"]),

    Scenario(12, "Rapid Fire — All Info Upfront",
        "User gives name, phone, department, and time preference in first message.",
        "booking",
        [
            Turn("Hi I'm Meera, phone 9988776655, need a pediatrics appointment for tomorrow morning", ["identify_user", "fetch_slots"], "Should identify AND fetch slots"),
            Turn("The 9 AM one please", ["record_confirmation", "book_appointment"], "Confirm and book"),
            Turn("That's all, bye!", ["end_conversation"], "End"),
        ],
        ["Handle multiple intents in one message", "Don't ask for info already provided"]),

    Scenario(13, "Quick Goodbye",
        "User says hi then immediately leaves.",
        "edge_case",
        [
            Turn("Hello", [], "Greet warmly"),
            Turn("Actually I changed my mind, bye", [], "Should NOT call end_conversation (no user_id)"),
        ],
        ["Don't call end_conversation without user_id", "Graceful goodbye"]),

    Scenario(14, "Cancel Non-Existent Appointment",
        "User wants to cancel but has no appointments.",
        "error_handling",
        [
            Turn("Cancel my appointment please", [], "Ask for identity"),
            Turn("Phone 9123456789", ["identify_user"], "Identify"),
            Turn("Yes cancel it", ["retrieve_appointments"], "Should retrieve and find none"),
            Turn("Oh I don't have any? Sorry bye", [], "Handle gracefully"),
        ],
        ["Check for existing appointments before cancelling", "Don't crash on empty results"]),

    Scenario(15, "Full Lifecycle",
        "User books, checks, reschedules in one conversation.",
        "full_lifecycle",
        [
            Turn("Hi, book me a general medicine appointment", [], "Ask for identity"),
            Turn("Rahul, 9876543210", ["identify_user", "fetch_slots"], "Identify and fetch slots"),
            Turn("Tomorrow 9 AM", ["record_confirmation", "book_appointment"], "Book"),
            Turn("Can you show my appointments?", ["retrieve_appointments"], "Retrieve"),
            Turn("Actually reschedule it to 10 AM", ["fetch_slots", "record_confirmation", "modify_appointment"], "Modify"),
        ],
        ["All tool calls in correct order", "record_confirmation before each mutation"]),
]


# ── Evaluation Engine ──────────────────────────────────────────────

@dataclass
class TurnResult:
    turn_index: int
    user_message: str
    assistant_response: str
    tool_calls: list[dict]
    expected_tools: list[str]
    tools_matched: list[str]
    tools_missing: list[str]
    tools_unexpected: list[str]


@dataclass
class ScenarioResult:
    scenario_id: int
    scenario_name: str
    category: str
    turns: list[TurnResult] = field(default_factory=list)
    judge_score: float = 0.0
    judge_reasoning: str = ""
    judge_breakdown: dict = field(default_factory=dict)
    error: str = ""


def _build_assistant_msg(msg) -> dict:
    """Build an assistant message dict for conversation history."""
    # Only user-visible assistant text + tool_calls. Never replay model reasoning blocks.
    text = (msg.content or "").strip()
    if not text and getattr(msg, "refusal", None):
        text = str(msg.refusal)
    d: dict = {"role": "assistant", "content": text}
    if msg.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return d


def call_llm(client: OpenAI, messages: list[dict], tools: list[dict] | None = None) -> dict:
    kwargs = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "extra_body": {"reasoning": _openrouter_reasoning_body()},
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message


def run_scenario(client: OpenAI, scenario: Scenario) -> ScenarioResult:
    result = ScenarioResult(scenario_id=scenario.id, scenario_name=scenario.name, category=scenario.category)
    _booking_counts[scenario.id] = 0
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for i, turn in enumerate(scenario.turns):
        messages.append({"role": "user", "content": turn.user_message})
        try:
            max_rounds = 5
            all_tool_calls = []
            assistant_text = ""

            for round_idx in range(max_rounds):
                msg = call_llm(client, messages, TOOLS)
                if msg.content:
                    assistant_text += msg.content

                if msg.tool_calls:
                    # Preserve the full assistant message (including reasoning_content)
                    messages.append(_build_assistant_msg(msg))
                    for tc in msg.tool_calls:
                        all_tool_calls.append({"name": tc.function.name, "arguments": tc.function.arguments})
                        tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        mock_resp = get_mock_response(scenario.id, tc.function.name, tool_args)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": mock_resp})
                    continue
                else:
                    messages.append(_build_assistant_msg(msg))
                    break

            called_tools = [tc["name"] for tc in all_tool_calls]
            expected = turn.expected_tools
            result.turns.append(TurnResult(
                turn_index=i, user_message=turn.user_message,
                assistant_response=assistant_text[:500],
                tool_calls=all_tool_calls, expected_tools=expected,
                tools_matched=[t for t in expected if t in called_tools],
                tools_missing=[t for t in expected if t not in called_tools],
                tools_unexpected=[t for t in called_tools if t not in expected],
            ))
        except Exception as e:
            result.error = f"Turn {i} failed: {e}"
            break
    return result


# ── Judge with Explicit Rubric ─────────────────────────────────────

JUDGE_PROMPT = """You are an expert evaluator for a healthcare voice receptionist AI named Priya.

## SCENARIO
Name: {name}
Category: {category}
Description: {description}

## CONVERSATION TRANSCRIPT
{transcript}

## EXPECTED vs ACTUAL TOOL CALLS
{tool_comparison}

## CRITICAL RULES FOR THIS SCENARIO
{rules}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## SCORING RUBRIC — Rate each dimension 1-10 using these exact anchors:

### CONVERSATIONAL_QUALITY
- **10**: Speaks like a real receptionist — warm, natural, concise. No robotic phrasing, no lists, no bullet points. Every reply feels like a real phone conversation.
- **9**: Nearly perfect tone, maybe one slightly stiff phrase.
- **8**: Warm and professional overall. 1-2 spots where phrasing feels templated ("I would be happy to assist you with...").
- **7**: Gets the job done but sounds like a chatbot. Acceptable but not memorable.
- **6**: Robotic or overly verbose. Uses formal phrasing that a real receptionist would never say.
- **5**: Barely conversational — reads like a form letter.
- **4 or below**: Confusing, rude, or incomprehensible responses.

### TOOL_CALLING_ACCURACY
- **10**: Every expected tool called, zero unexpected calls, all arguments correct.
- **9**: All expected tools called, one minor argument issue (e.g., wrong format but correct intent).
- **8**: All expected tools called but one had a missing or wrong argument.
- **7**: 1 expected tool missing OR 1 unexpected tool called, but core flow still works.
- **6**: 2 tools missing or wrong. Flow partially broken.
- **5**: Critical tool missing (e.g., book called without identify_user). Flow broken.
- **4 or below**: Multiple missing/wrong tools. Flow completely broken.

### FLOW_CORRECTNESS
- **10**: Perfect sequence — identify before book, fetch_slots before book, record_confirmation before every mutation.
- **9**: One minor ordering issue that doesn't break the flow.
- **8**: Correct overall sequence but one step slightly out of order.
- **7**: Mostly correct but skipped one guardrail step (e.g., forgot to confirm before booking).
- **6**: Skipped identify_user or fetch_slots before booking. Major flow issue.
- **5**: Booked without any prior steps. Critical flow violation.
- **4 or below**: Chaotic — no logical flow, tools called randomly.

### ERROR_HANDLING
- **10**: Handles every error gracefully — invalid phone, double booking, empty slots — with helpful next-step suggestions.
- **9**: Handles all errors but one response could be more helpful.
- **8**: Handles errors acceptably but responses are generic.
- **7**: Catches errors but doesn't suggest alternatives.
- **6**: One error not handled (e.g., ignores double booking failure).
- **5**: Error causes confusion or wrong follow-up.
- **4 or below**: Crashes, hallucinates, or gives wrong info after error.
(Note: if this scenario has NO errors to handle, score 10 if agent doesn't create errors.)

### INSTRUCTION_ADHERENCE
- **10**: Follows ALL rules — no medical advice, confirms before mutations, verifies identity, ends with summary.
- **9**: Follows all rules but one minor deviation.
- **8**: One rule bent (e.g., slightly too much detail about a condition) but no violation.
- **7**: One rule violated (e.g., gave basic health tip, or didn't confirm before cancel).
- **6**: Two rules violated.
- **5**: Gave medical advice OR booked without identity OR skipped confirmation on critical action.
- **4 or below**: Multiple rule violations, completely off-script.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## OUTPUT FORMAT
Respond with ONLY valid JSON — no markdown, no backticks, no commentary:

{{
  "CONVERSATIONAL_QUALITY": <1-10>,
  "TOOL_CALLING_ACCURACY": <1-10>,
  "FLOW_CORRECTNESS": <1-10>,
  "ERROR_HANDLING": <1-10>,
  "INSTRUCTION_ADHERENCE": <1-10>,
  "OVERALL": <weighted average: CONV_QUALITY*0.15 + TOOL_ACCURACY*0.30 + FLOW*0.25 + ERRORS*0.15 + ADHERENCE*0.15, rounded to 1 decimal>,
  "reasoning": "<2-3 sentences: what worked, what didn't>",
  "critical_failures": ["<specific rule violations>"],
  "improvement_suggestions": ["<specific actionable fix>"]
}}
"""


def judge_scenario(client: OpenAI, scenario: Scenario, result: ScenarioResult) -> ScenarioResult:
    transcript_lines = []
    for i, turn in enumerate(scenario.turns):
        transcript_lines.append(f"TURN {i+1} USER: {turn.user_message}")
        if i < len(result.turns):
            tr = result.turns[i]
            transcript_lines.append(f"TURN {i+1} ASSISTANT: {tr.assistant_response}")
            for tc in tr.tool_calls:
                transcript_lines.append(f"  -> CALLED: {tc['name']}({tc['arguments'][:200]})")

    comparison_lines = []
    for i, turn in enumerate(scenario.turns):
        if i < len(result.turns):
            tr = result.turns[i]
            mark = "PASS" if not tr.tools_missing else "FAIL"
            comparison_lines.append(
                f"Turn {i+1} [{mark}]: Expected {turn.expected_tools or 'none'} | "
                f"Got {[tc['name'] for tc in tr.tool_calls]} | "
                f"Matched={tr.tools_matched} Missing={tr.tools_missing} Extra={tr.tools_unexpected}"
            )

    rules_text = "\n".join(f"- {r}" for r in scenario.critical_rules) if scenario.critical_rules else "- Standard rules apply"

    prompt = JUDGE_PROMPT.format(
        name=scenario.name, category=scenario.category, description=scenario.description,
        transcript="\n".join(transcript_lines),
        tool_comparison="\n".join(comparison_lines),
        rules=rules_text,
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are an expert AI evaluator. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            extra_body={"reasoning": _openrouter_reasoning_body()},
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]

        scores = json.loads(raw)
        result.judge_score = scores.get("OVERALL", 0)
        result.judge_reasoning = scores.get("reasoning", "")
        result.judge_breakdown = scores
    except Exception as e:
        result.judge_score = 0
        result.judge_reasoning = f"Judge failed: {e}"
        result.judge_breakdown = {"error": str(e)}

    return result


# ── Parallel Runner ────────────────────────────────────────────────

def run_and_judge(client: OpenAI, scenario: Scenario) -> ScenarioResult:
    """Run scenario + judge — used as a single unit of work for thread pool."""
    result = run_scenario(client, scenario)
    result = judge_scenario(client, scenario, result)
    return result


def print_results(results: list[ScenarioResult]):
    print("\n" + "=" * 110)
    print("  HEALTHCARE VOICE AGENT — LLM EVALUATION REPORT")
    print("=" * 110)

    print(f"\n{'#':<3} {'Scenario':<42} {'Category':<16} {'Score':<7} {'Key Issues'}")
    print("-" * 110)

    total_score = 0
    category_scores: dict[str, list[float]] = {}
    all_failures: list[str] = []
    all_suggestions: list[str] = []

    for r in results:
        score_str = f"{r.judge_score:.1f}" if r.judge_score else "ERR"
        total_score += r.judge_score

        if r.judge_breakdown:
            all_failures.extend(r.judge_breakdown.get("critical_failures", []))
            all_suggestions.extend(r.judge_breakdown.get("improvement_suggestions", []))

        issues = []
        for tr in r.turns:
            if tr.tools_missing:
                issues.append(f"T{tr.turn_index+1}: missing {tr.tools_missing}")
        issue_str = "; ".join(issues[:2]) if issues else ("-" if r.judge_score >= 7 else (r.error[:50] if r.error else "Check details"))

        print(f"{r.scenario_id:<3} {r.scenario_name:<42} {r.category:<16} {score_str:<7} {issue_str}")

        category_scores.setdefault(r.category, []).append(r.judge_score)

    avg = total_score / len(results) if results else 0
    print("-" * 110)
    print(f"{'':>3} {'AVERAGE SCORE':<42} {'':<16} {avg:.1f}/10")
    print()

    print("\nCategory Breakdown:")
    for cat, scores in category_scores.items():
        cat_avg = sum(scores) / len(scores)
        bar = "#" * int(cat_avg) + "-" * (10 - int(cat_avg))
        print(f"  {cat:<20} [{bar}] {cat_avg:.1f}/10")

    print("\nDimension Scores (averaged):")
    for dim in ["CONVERSATIONAL_QUALITY", "TOOL_CALLING_ACCURACY", "FLOW_CORRECTNESS", "ERROR_HANDLING", "INSTRUCTION_ADHERENCE"]:
        vals = [r.judge_breakdown.get(dim, 0) for r in results if dim in r.judge_breakdown]
        if vals:
            dim_avg = sum(vals) / len(vals)
            bar = "#" * int(dim_avg) + "-" * (10 - int(dim_avg))
            print(f"  {dim:<30} [{bar}] {dim_avg:.1f}/10")

    if all_failures:
        print("\nCritical Failures:")
        for f in sorted(set(all_failures)):
            print(f"  X {f}")

    if all_suggestions:
        print("\nTop Improvement Suggestions:")
        for s in list(dict.fromkeys(all_suggestions))[:8]:
            print(f"  -> {s}")

    print("\n" + "=" * 110)
    print("  DETAILED RESULTS")
    print("=" * 110)
    for r in sorted(results, key=lambda x: x.scenario_id):
        print(f"\n{'─'*70}")
        print(f"  Scenario {r.scenario_id}: {r.scenario_name}")
        print(f"  Score: {r.judge_score:.1f}/10  |  Category: {r.category}")
        print(f"  Reasoning: {r.judge_reasoning}")
        if r.error:
            print(f"  ERROR: {r.error}")
        for tr in r.turns:
            print(f"\n  Turn {tr.turn_index+1}: USER -> \"{tr.user_message}\"")
            print(f"           ASSISTANT -> \"{tr.assistant_response[:200]}\"")
            for tc in tr.tool_calls:
                print(f"           TOOL -> {tc['name']}({tc['arguments'][:120]}...)")
            print(f"           Expected: {tr.expected_tools} | Matched: {tr.tools_matched} | Missing: {tr.tools_missing}")

    print(f"\n{'='*110}")
    print(f"  FINAL SCORE: {avg:.1f}/10 across {len(results)} scenarios")
    print(f"{'='*110}\n")


async def run_eval():
    if not API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    print(f"Starting LLM Evaluation — {len(SCENARIOS)} scenarios, {PARALLELISM} parallel")
    print(f"   Model: {MODEL}")
    print(f"   Reasoning: effort={EVAL_REASONING_EFFORT!r} exclude={EVAL_REASONING_EXCLUDE}")
    print(f"   Endpoint: {BASE_URL}")
    print()

    t_start = time.time()

    # Run scenarios in parallel batches
    with ThreadPoolExecutor(max_workers=PARALLELISM) as pool:
        futures = []
        for scenario in SCENARIOS:
            f = pool.submit(run_and_judge, client, scenario)
            futures.append((scenario, f))

        # Collect results as they complete
        results: list[ScenarioResult] = [None] * len(SCENARIOS)  # type: ignore
        for idx, (scenario, future) in enumerate(futures):
            try:
                result = future.result(timeout=SCENARIO_FUTURE_TIMEOUT)
                results[idx] = result
                score_str = f"{result.judge_score:.1f}" if result.judge_score else "ERR"
                print(f"  [{idx+1:>2}/{len(SCENARIOS)}] Scenario {scenario.id}: {scenario.name:<42} Score: {score_str}/10")
            except Exception as e:
                print(f"  [{idx+1:>2}/{len(SCENARIOS)}] Scenario {scenario.id}: FAILED — {e}")
                results[idx] = ScenarioResult(
                    scenario_id=scenario.id, scenario_name=scenario.name,
                    category=scenario.category, error=str(e),
                )

    elapsed = time.time() - t_start
    print(f"\nAll {len(SCENARIOS)} scenarios completed in {elapsed:.1f}s ({elapsed/len(SCENARIOS):.1f}s avg)")

    print_results(results)

    # Save JSON
    output_path = Path(__file__).parent / "eval_results.json"
    output_data = [{
        "scenario_id": r.scenario_id, "scenario_name": r.scenario_name,
        "category": r.category, "score": r.judge_score,
        "reasoning": r.judge_reasoning, "breakdown": r.judge_breakdown,
        "error": r.error,
        "turns": [{
            "turn_index": tr.turn_index, "user_message": tr.user_message,
            "assistant_response": tr.assistant_response,
            "tool_calls": tr.tool_calls, "expected_tools": tr.expected_tools,
            "matched": tr.tools_matched, "missing": tr.tools_missing,
            "unexpected": tr.tools_unexpected,
        } for tr in r.turns],
    } for r in results]

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {output_path}")

    return results


if __name__ == "__main__":
    asyncio.run(run_eval())
