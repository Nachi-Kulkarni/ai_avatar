# Healthcare Voice Receptionist — mykare.ai

A live-testable healthcare voice receptionist demo using LiveKit Agents, Deepgram STT, Cartesia TTS, OpenRouter LLM, bey lip-sync avatar, Supabase, and Vite React.

## Architecture

```
Browser → LiveKit (WebRTC audio/video)
         → Python Agent (STT → LLM → TTS pipeline)
         → Supabase (appointments, realtime events)
         → bey (avatar video)
```

## Quick Start

### 1. Set up environment

```bash
cp .env.example .env
# Fill in your API keys:
# - Supabase URL + keys
# - LiveKit Cloud URL + keys
# - Deepgram, Cartesia, OpenRouter, bey keys
```

### 2. Set up Supabase

Run migrations in Supabase SQL editor (in order):
1. `supabase/migrations/001_initial_schema.sql`
2. `supabase/migrations/002_seed_data.sql`

Enable Realtime on: `tool_events`, `appointments`, `call_summaries`, `conversation_sessions`.

### 3. Start the backend

```bash
cd apps/agent
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

This starts both the FastAPI token server (port 8000) and the LiveKit agent worker.

### 4. Start the frontend

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:5173

## Features

- Real WebRTC voice call through LiveKit
- AI receptionist (Priya) powered by OpenRouter GPT-4o
- Deepgram STT for speech recognition
- Cartesia TTS for natural voice synthesis
- bey avatar with lip-sync video
- Real-time tool activity panel (visible booking/confirmation flow)
- Appointment booking, retrieval, modification, cancellation
- Double-booking prevention via database constraints
- End-of-call summary with appointment recap
- Supabase Realtime for live UI updates

## Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `VITE_SUPABASE_URL` | Frontend | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Frontend | Supabase anon/public key |
| `VITE_LIVEKIT_URL` | Frontend | LiveKit Cloud WebSocket URL |
| `VITE_API_BASE_URL` | Frontend | Backend API base URL |
| `SUPABASE_URL` | Backend | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend | Supabase service role key |
| `LIVEKIT_URL` | Backend | LiveKit Cloud WebSocket URL |
| `LIVEKIT_API_KEY` | Backend | LiveKit API key |
| `LIVEKIT_API_SECRET` | Backend | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Backend | Deepgram STT API key |
| `CARTESIA_API_KEY` | Backend | Cartesia TTS API key |
| `OPENROUTER_API_KEY` | Backend | OpenRouter LLM API key |
| `OPENROUTER_MODEL` | Backend | LLM model (default: openai/gpt-4o) |
| `bey_API_KEY` | Backend | bey avatar API key |
| `bey_REPLICA_ID` | Backend | bey replica ID |
| `bey_PERSONA_ID` | Backend | bey persona ID |

## Security

- Browser receives only short-lived LiveKit tokens and Supabase anon key
- All privileged writes go through the Python agent (service role)
- Phone numbers are normalized to E.164, never used as participant identity
- RLS enabled on all tables containing patient-like data
- No secrets exposed to Vite build or browser network calls
- Demo uses synthetic data only

## Cost

~$0.59 per 5-minute call (including bey avatar). See [docs/cost-breakdown.md](docs/cost-breakdown.md).

## Demo

See [docs/demo-script.md](docs/demo-script.md) for the golden-path demo walkthrough.
# ai_avatar
