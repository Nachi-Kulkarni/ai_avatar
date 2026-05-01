from __future__ import annotations

import lk_logging_shim  # noqa: F401 — before livekit so ``livekit.agents`` logger has ``.trace``

import asyncio
import logging
import uuid
import time
import re
import json

from config import settings
from logging_config import configure_logging

configure_logging(settings)

from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    ChatMessage,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    TurnHandlingOptions,
    UserInputTranscribedEvent,
    room_io,
)
from livekit.plugins import silero, bey, openai, cartesia

from tts_brand import BrandSpeechTTS
from db import get_supabase
from tools import ALL_TOOLS, set_current_session_id

logger = logging.getLogger(__name__)
# High-signal console lines — ``logging_config.configure_logging`` (run in worker + main).
pipeline = logging.getLogger("mykare.pipeline")


def _one_line(text: str | None, max_len: int = 900) -> str:
    if not text:
        return ""
    s = " ".join(text.split())
    return s if len(s) <= max_len else f"{s[: max_len - 3]}..."


def _summarize_tool_log(tool_name: str, raw: str | None, max_len: int = 420) -> str:
    """Avoid multi‑KB JSON in console for tools like fetch_slots."""
    if not raw:
        return ""
    t = raw.strip()
    if tool_name == "fetch_slots":
        try:
            d = json.loads(t)
            n = int(d.get("count", len(d.get("slots") or [])))
            return f"ok count={n} (slot payloads omitted from log)"
        except (json.JSONDecodeError, TypeError, ValueError):
            return _one_line(t, max_len)
    if tool_name == "retrieve_appointments":
        try:
            d = json.loads(t)
            apps = d.get("appointments") or []
            return f"ok appointments={len(apps)} (detail omitted from log)"
        except (json.JSONDecodeError, TypeError, ValueError):
            return _one_line(t, max_len)
    if tool_name == "list_departments":
        try:
            d = json.loads(t)
            depts = d.get("departments") or []
            return f"ok departments={len(depts)} (detail omitted from log)"
        except (json.JSONDecodeError, TypeError, ValueError):
            return _one_line(t, max_len)
    if tool_name == "update_patient_profile":
        try:
            d = json.loads(t)
            nm = d.get("name", "")
            return f"ok name={nm[:80]!r}"
        except (json.JSONDecodeError, TypeError, ValueError):
            return _one_line(t, max_len)
    return _one_line(t, max_len)

RECEPTIONIST_INSTRUCTIONS = """You are Priya, a friendly and professional healthcare receptionist for mykare.ai clinic.

Your role:
- Help patients book, view, modify, and cancel appointments
- Collect patient information before handling appointment actions
- Provide a summary at the end of the call

## Identity — phone first, then profile name
- **Phone** is the only key we use inside tools to find or create the row. **Always** call `identify_user` with **phone only** (never pass a name into that tool).
- When `identify_user` returns **`needs_display_name`: true** (new file **or** name still empty on file), **you must ask** what name they want on their record — warm and clear, one question. When they answer, call **`update_patient_profile(user_id, full_name)`** with the **user_id** from `identify_user`. If speech-to-text might be wrong, offer to confirm spelling once.
- When `needs_display_name` is **false** and `name` is present, you may greet them naturally by that stored name.
- For every booking / lookup / cancel / modify, use **user_id** from tools — never trust *only* the spoken name for operations.

Rules:
- NEVER announce booking, cancellation, or modification success until the tool confirms it
- ALWAYS call record_confirmation before book_appointment, cancel_appointment, or modify_appointment
- ALWAYS verify the user's identity by calling identify_user with their **phone** as soon as they give it
- Be warm, professional, and concise
- Speak naturally without complex formatting or punctuation
- Ask one clear question at a time unless the caller already gave multiple details
- After phone + profile name are settled (or waived only if they firmly refuse a name — still try once), collect **visit details** for bookings: department or specialty, preferred date, preferred time, and **reason for visit** when booking so the chart has context
- If the caller has not shared their phone number, ask for it early: "May I have your mobile number?"
- If a slot is unavailable, suggest the nearest available alternatives
- Do not provide medical advice — you only handle scheduling
- When a user gives you their phone number, IMMEDIATELY call identify_user with **phone only** — do not just acknowledge it
- NEVER fabricate appointment details. Only present data that retrieve_appointments actually returns. If the tool returns empty results, tell the user they have no upcoming appointments.
- If **identify_user** returns `success: false`, read **`user_message`** and **`error_code`** to the patient (e.g. wrong digit count). Do **not** invent vague "system is down" apologies unless the tool explicitly says the backend failed.

## Voice channel — how you talk to patients
- Always write our clinic name as **mykare.ai** (one word, dot, ai) in your replies. The phone voice pronounces that name like "my care A I"; you must not invent other spellings for the brand.
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
If the user says "I'm Meera, phone 9988776655, need pediatrics tomorrow morning", call identify_user with **phone 9988776655 only**. If **needs_display_name** or they gave a name, call **update_patient_profile** with that name, then list_departments / fetch_slots and present slots — as many steps as fit in one response.

### RULE 4: Call list_departments to resolve department names to IDs
When the user names a specific department (e.g. "cardiology", "dental"), call list_departments to find the correct department ID, then call fetch_slots with that ID. Do NOT guess department IDs.

## MANDATORY TOOL FLOW:

### Booking flow:
1. User expresses intent to book → ask for **mobile number** first
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**: ask what name they want on file → call update_patient_profile(user_id, full_name) → then continue; if **false**, skip straight to visit details
4. Ask for department or reason, preferred date, preferred time
5. User picks a department → call list_departments to resolve it, then IMMEDIATELY call fetch_slots(department_id)
6. Present matching slots → user picks one → IMMEDIATELY call record_confirmation then book_appointment (include **reason** when relevant)
7. User says bye → IMMEDIATELY call end_conversation

### Retrieve flow:
1. User asks for appointments → ask for **phone** if you do not have it
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, complete name with update_patient_profile before or after showing appointments (prefer **after** retrieve if they are in a hurry to hear dates)
4. IMMEDIATELY call retrieve_appointments(user_id)
5. Present results
6. User says bye → IMMEDIATELY call end_conversation

### Cancel flow:
1. User wants to cancel → ask for phone
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, collect name with update_patient_profile when natural
4. IMMEDIATELY call retrieve_appointments(user_id) to show what they have
5. User confirms which one → IMMEDIATELY call record_confirmation then cancel_appointment (NO verbal confirmation step!)
6. User says bye → IMMEDIATELY call end_conversation

### Modify flow:
1. User wants to reschedule → ask for phone
2. User gives phone → IMMEDIATELY call identify_user(phone)
3. If **`needs_display_name`**, collect name with update_patient_profile when natural
4. IMMEDIATELY call retrieve_appointments(user_id) to show current booking
5. Ask for new time → IMMEDIATELY call fetch_slots for the new date
6. User picks new slot → IMMEDIATELY call record_confirmation then modify_appointment (NO verbal confirmation step!)
7. User says bye → IMMEDIATELY call end_conversation

CRITICAL: Every time the user provides a phone number, you MUST call identify_user in your very next response. Do NOT just say "thank you" or ask more questions — CALL THE TOOL."""


_SESSION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _session_id_from_room(room_name: str) -> str:
    """Reuse the API-generated UUID so Supabase realtime filters match the frontend."""
    match = _SESSION_ID_RE.search(room_name)
    return match.group(0) if match else str(uuid.uuid4())


class ReceptionistAgent(Agent):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            instructions=RECEPTIONIST_INSTRUCTIONS,
            tools=ALL_TOOLS,
        )
        self.session_id = session_id


async def create_session_and_room(ctx: agents.JobContext):
    """Called by LiveKit when a new room is created."""
    logger.info("AGENT | create_session_and_room ENTERED")
    room_name = ctx.room.name
    session_id = _session_id_from_room(room_name)
    set_current_session_id(session_id)

    if not settings.supabase_url.strip() or not settings.supabase_service_role_key.strip():
        logger.error(
            "Supabase env missing: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env "
            "(worker loads repo-root .env). Tools like identify_user will fail until fixed."
        )

    logger.info("AGENT | new session room=%s session_id=%s", room_name, session_id)

    # Idempotent row (reconnect / worker retry must not 23505)
    sb = get_supabase()
    try:
        sb.table("conversation_sessions").upsert(
            {
                "id": session_id,
                "room_name": room_name,
                "status": "active",
                "session_state": "connecting",
            },
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        logger.info("AGENT | conversation_sessions upsert ok")
    except Exception as e:
        logger.warning("DB: session upsert failed: %s", e)

    # OpenRouter via OpenAI-compatible plugin (reasoning: OpenRouter extra_body, not OpenAI reasoning_effort)
    extra_body = None
    if settings.openrouter_reasoning_enabled and settings.openrouter_reasoning_effort.strip():
        rb: dict = {"effort": settings.openrouter_reasoning_effort.strip()}
        if settings.openrouter_reasoning_exclude:
            rb["exclude"] = True
        extra_body = {"reasoning": rb}
    logger.info(
        "AGENT | LLM model=%s reasoning_extra=%s",
        settings.openrouter_model,
        extra_body or "off",
    )
    llm = openai.LLM(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        **({"extra_body": extra_body} if extra_body else {}),
    )

    # Load VAD
    logger.info("AGENT | loading Silero VAD…")
    vad = silero.VAD.load()
    logger.info("AGENT | VAD ready; turn_detection=vad")

    # MultilingualModel (EOU ML) needs a LiveKit *inference executor* in the worker process;
    # local ``main.py all`` dev jobs often have none → "no inference executor". Silero VAD +
    # ``turn_detection="vad"`` endpointing avoids that and is enough for this demo.
    logger.info("AGENT | building AgentSession (Deepgram + OpenRouter + Cartesia TTS)")
    agent_session = AgentSession(
        stt="deepgram/nova-3:multi",
        llm=llm,
        tts=BrandSpeechTTS(
            cartesia.TTS(
                api_key=settings.cartesia_api_key,
                model="sonic-3",
                voice=settings.cartesia_voice_id,
                speed=settings.cartesia_speed,
            )
        ),
        vad=vad,
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            endpointing={"mode": "fixed", "min_delay": 0.55, "max_delay": 3.0},
        ),
    )

    # LiveKit 1.x event names — STT / assistant text / tool IO at INFO on ``mykare.pipeline`` only.
    @agent_session.on("user_input_transcribed")
    def on_user_input(ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final:
            return
        pipeline.info("STT | %s", _one_line(ev.transcript))

    @agent_session.on("conversation_item_added")
    def on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if isinstance(item, ChatMessage) and item.role == "assistant":
            text = item.text_content
            if text:
                pipeline.info("LLM | %s", _one_line(text))

    @agent_session.on("function_tools_executed")
    def on_tools_executed(ev: FunctionToolsExecutedEvent) -> None:
        for call, out in ev.zipped():
            args = _one_line(call.arguments, 600)
            result = ""
            if out is not None:
                result = _summarize_tool_log(call.name, out.output)
                if out.is_error:
                    result = f"ERROR {result}"
            pipeline.info("TOOL | %s(%s) -> %s", call.name, args, result or "(pending)")

    receptionist = ReceptionistAgent(session_id=session_id)

    logger.info("AGENT | AgentSession.start → room (audio pipeline connecting…)")
    await agent_session.start(
        room=ctx.room,
        agent=receptionist,
        room_options=room_io.RoomOptions(),
    )
    logger.info("AGENT | pipeline active — STT/LLM/TTS live; mykare.pipeline logs tool + transcript lines")

    # Update session state before the first utterance so the UI can correlate logs.
    try:
        sb.table("conversation_sessions").update({
            "session_state": "listening"
        }).eq("id", session_id).execute()
    except Exception:
        pass

    # Initial greeting BEFORE Bey: the Bey plugin re-routes TTS through a data stream that
    # waits for avatar video, which adds noticeable latency to the first spoken response.
    logger.info("AGENT | generating initial greeting…")
    _greet_t0 = time.perf_counter()
    try:
        await asyncio.wait_for(
            agent_session.generate_reply(
                instructions=(
                    "Greet the patient warmly as Priya from mykare.ai clinic. "
                    "Ask for their **mobile number** first. Say that once the number is on file, "
                    "you'll set up their **name on the record** if they're new or it's missing, "
                    "then you'll get visit details for scheduling."
                )
            ),
            timeout=20.0,
        )
        _greet_ms = (time.perf_counter() - _greet_t0) * 1000
        logger.info("AGENT | initial greeting done in %.0fms", _greet_ms)
    except asyncio.TimeoutError:
        logger.warning("AGENT | initial greeting timed out after 20s — pipeline still active, patient can speak")

    # Start bey (Beyond Presence) avatar after first audio so lip-sync does not block startup.
    if settings.bey_api_key:
        logger.info("AGENT | starting bey avatar…")
        try:
            avatar = bey.AvatarSession(
                api_key=settings.bey_api_key,
                avatar_id=settings.bey_avatar_id,
                avatar_participant_name="Priya",
            )
            await avatar.start(agent_session, room=ctx.room)
            logger.info("AGENT | bey avatar ready")
        except Exception as e:
            logger.warning("AVATAR: bey failed (voice-only): %s", e)
    else:
        logger.info("AGENT | no BEY_API_KEY — voice-only")
