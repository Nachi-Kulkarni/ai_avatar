import secrets
import logging
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Ensure env is loaded when this module is imported without going through main.py
# (e.g. `python main.py api` child process, uvicorn api:app).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from livekit.api import AccessToken, VideoGrants
from config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="Healthcare Voice Agent API")

# Explicit origins from env + regex so any local Vite port (5173, 5174, …) works without redeploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TokenRequest(BaseModel):
    room_name: str | None = None
    user_name: str | None = None


class TokenResponse(BaseModel):
    token: str
    room_name: str
    server_url: str
    session_id: str


@app.post("/api/token", response_model=TokenResponse)
async def create_token(req: TokenRequest):
    session_id = str(uuid.uuid4())
    # Embed the DB session UUID in the room name so the agent worker can log live tool events
    # against the same session id that the frontend subscribes to.
    room_name = req.room_name or f"consultation-{session_id}"
    identity = f"patient-{secrets.token_hex(4)}"

    grants = VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    )

    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(req.user_name or identity)
        .with_grants(grants)
        .to_jwt()
    )

    # Pre-create the conversation_sessions row so that tool_events / call_summaries FK
    # constraints are satisfied even if the agent hasn't joined the room yet.
    try:
        from db import get_supabase
        sb = get_supabase()
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
        logger.info("Token created for room %s — session row upserted, agent will auto-join", room_name)
    except Exception as e:
        logger.warning("Token: session row upsert failed (non-fatal): %s", e)

    return TokenResponse(
        token=token,
        room_name=room_name,
        server_url=settings.livekit_url,
        session_id=session_id,
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "demo_mode": settings.demo_mode_enabled}


@app.get("/api/health/detailed")
async def health_detailed():
    """Test every external service and API key."""
    import ssl
    import socket
    import time

    checks = {}

    # 1. LiveKit Cloud connectivity (SSL + TCP)
    _lk_host = settings.livekit_url.replace("wss://", "").replace("/", "")
    _lk_start = time.perf_counter()
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=_lk_host) as s:
            s.settimeout(5)
            s.connect((_lk_host, 443))
        checks["livekit_ssl"] = {"ok": True, "ms": round((time.perf_counter() - _lk_start) * 1000)}
    except Exception as e:
        checks["livekit_ssl"] = {"ok": False, "error": str(e)}

    # 2. LiveKit API — list rooms (validates api_key + api_secret)
    _lk_api_start = time.perf_counter()
    try:
        lkapi = LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        rooms = await lkapi.room.list_rooms()
        await lkapi.aclose()
        checks["livekit_api"] = {
            "ok": True,
            "ms": round((time.perf_counter() - _lk_api_start) * 1000),
            "rooms_count": len(rooms),
        }
    except Exception as e:
        checks["livekit_api"] = {"ok": False, "error": str(e)}

    # 3. Supabase — query a table
    _sb_start = time.perf_counter()
    try:
        from db import get_supabase
        sb = get_supabase()
        sb.table("conversation_sessions").select("id").limit(1).execute()
        checks["supabase"] = {"ok": True, "ms": round((time.perf_counter() - _sb_start) * 1000)}
    except Exception as e:
        checks["supabase"] = {"ok": False, "error": str(e)}

    # 4. OpenRouter LLM
    _llm_start = time.perf_counter()
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        resp = client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=10,
        )
        checks["openrouter_llm"] = {
            "ok": bool(resp.choices[0].message.content),
            "ms": round((time.perf_counter() - _llm_start) * 1000),
            "model": settings.openrouter_model,
            "content_preview": (resp.choices[0].message.content or "None")[:50],
        }
    except Exception as e:
        checks["openrouter_llm"] = {"ok": False, "error": str(e), "model": settings.openrouter_model}

    # 5. Deepgram STT
    _dg_start = time.perf_counter()
    try:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {settings.deepgram_api_key}"},
                timeout=10,
            )
            checks["deepgram"] = {
                "ok": r.status_code == 200,
                "ms": round((time.perf_counter() - _dg_start) * 1000),
                "status": r.status_code,
            }
    except Exception as e:
        checks["deepgram"] = {"ok": False, "error": str(e)}

    # 6. Cartesia TTS
    _cart_start = time.perf_counter()
    try:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://api.cartesia.com/voices",
                headers={"X-API-Key": settings.cartesia_api_key, "Cartesia-Version": "2024-06-10"},
                timeout=10,
            )
            checks["cartesia"] = {
                "ok": r.status_code == 200,
                "ms": round((time.perf_counter() - _cart_start) * 1000),
                "status": r.status_code,
            }
    except Exception as e:
        checks["cartesia"] = {"ok": False, "error": str(e)}

    # 7. Bey avatar
    checks["bey"] = {
        "ok": bool(settings.bey_api_key),
        "configured": bool(settings.bey_api_key),
        "avatar_id": settings.bey_avatar_id,
    }

    # 8. Env summary
    checks["config"] = {
        "livekit_url": settings.livekit_url,
        "openrouter_model": settings.openrouter_model,
        "reasoning_enabled": settings.openrouter_reasoning_enabled,
        "supabase_url_set": bool(settings.supabase_url),
        "deepgram_key_set": bool(settings.deepgram_api_key),
        "cartesia_key_set": bool(settings.cartesia_api_key),
        "bey_key_set": bool(settings.bey_api_key),
    }

    all_ok = all(c.get("ok", False) for c in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
