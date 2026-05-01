"""
Healthcare Voice Agent — main entry point.

Usage:
  python main.py agent   — Start the LiveKit agent worker
  python main.py api     — Start the FastAPI token server
  python main.py all     — Start both (agent + API)
"""
from __future__ import annotations

import os
import sys
import logging
import threading

import lk_logging_shim  # noqa: F401 — patch Logger.trace before any livekit getLogger

# Fix SSL certificate verification for Python 3.14 on macOS
# Must patch ssl.create_default_context so websockets (LiveKit SDK) uses certifi CA bundle.
try:
    import certifi
    import ssl

    _certifi_ca = certifi.where()
    os.environ["SSL_CERT_FILE"] = _certifi_ca
    os.environ["REQUESTS_CA_BUNDLE"] = _certifi_ca

    # Patch the default HTTPS context so all ssl.create_default_context() calls pick up certifi.
    _original_create_default = ssl.create_default_context

    def _patched_create_default_context(*args, **kwargs):
        if "cafile" not in kwargs:
            kwargs["cafile"] = _certifi_ca
        return _original_create_default(*args, **kwargs)

    ssl.create_default_context = _patched_create_default_context
except ImportError:
    pass

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from config import settings
from logging_config import configure_logging, silence_hpack_loggers, _add_stderr_mirror

configure_logging(settings)

# Module-level server and entrypoint (required for livekit-agents worker pickling)
from livekit import agents
from livekit.agents import AgentServer, JobExecutorType

# Dependencies may register hpack after configure_logging(); keep terminal readable.
silence_hpack_loggers()

# On macOS LiveKit defaults to PROCESS per job — logs from the child often **vanish** beside
# Uvicorn/Rich in Cursor. THREAD runs the room in this process so ``AGENT |`` / ``PIPELINE`` / tools show here.
server = AgentServer(job_executor_type=JobExecutorType.THREAD)


def _on_worker_started():
    logging.getLogger(__name__).info("WORKER EVENT | worker_started — process is alive, about to register")


def _on_worker_registered():
    logging.getLogger(__name__).info("WORKER EVENT | worker_registered — connected to LiveKit Cloud, ready for jobs")


server.on("worker_started", _on_worker_started)
server.on("worker_registered", _on_worker_registered)

# Eager import: ``livekit.plugins.*`` register in ``agent`` at import time and **must** run on
# this process's main thread. Importing inside ``entrypoint`` runs on the job thread and raises
# ``RuntimeError: Plugins must be registered on the main thread``.
from agent import create_session_and_room  # noqa: E402


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    log = logging.getLogger(__name__)
    log.info("JOB | entrypoint fired — room=%s job_id=%s", ctx.room.name, ctx.job.id)
    try:
        await create_session_and_room(ctx)
        log.info("JOB | create_session_and_room completed for room=%s", ctx.room.name)
    except Exception:
        log.exception("JOB | FATAL: create_session_and_room raised for room=%s", ctx.room.name)
        raise


def run_agent(*, embed_api: bool = False):
    """Start the LiveKit agent worker.

    When ``embed_api=True`` (``python main.py all``), the token API runs in a daemon thread in
    the same process. LiveKit ``dev`` with file reload spawns a **separate** worker process, so
    ``AGENT |`` lines would not appear here — we always pass ``--no-reload`` in that case.
    For ``python main.py agent`` only, set ``MYKARE_AGENT_RELOAD=1`` to opt into reload.
    """
    import sys as _sys

    _args = [_sys.argv[0], "dev"]
    _want_file_reload = os.getenv("MYKARE_AGENT_RELOAD", "").lower() in ("1", "true", "yes")
    # Never fork the worker when Uvicorn shares this terminal; ignore MYKARE_AGENT_RELOAD then.
    _no_reload = embed_api or not _want_file_reload
    if _no_reload:
        _args.append("--no-reload")
    _sys.argv = _args
    silence_hpack_loggers()
    log = logging.getLogger(__name__)
    log.info(
        "Starting LiveKit agent worker (job_executor=THREAD; LiveKit dev %s).",
        "--no-reload" if _no_reload else "reload=ON (MYKARE_AGENT_RELOAD)",
    )
    log.info("WORKER | LIVEKIT_URL=%s", settings.livekit_url)
    log.info("WORKER | API_KEY=%s...%s", settings.livekit_api_key[:6], settings.livekit_api_key[-4:])

    # Force LiveKit agent SDK to log at INFO so we see worker registration/dispatch
    logging.getLogger("livekit.agents").setLevel(logging.INFO)
    _add_stderr_mirror("livekit.agents", level=logging.INFO)

    # Verify SSL works before LiveKit SDK tries to connect
    try:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        import socket
        with ctx.wrap_socket(socket.socket(), server_hostname="nachiket-5tp0afd9.livekit.cloud") as s:
            s.settimeout(5)
            s.connect(("nachiket-5tp0afd9.livekit.cloud", 443))
        log.info("WORKER | SSL handshake to LiveKit Cloud OK")
    except Exception as e:
        log.error("WORKER | SSL test FAILED: %s — agent will not connect!", e)

    agents.cli.run_app(server)


def run_api():
    """Start the FastAPI token server."""
    import uvicorn
    from api import app

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "agent":
        run_agent()
    elif mode == "api":
        run_api()
    elif mode == "all":
        # Use a thread (not a separate process) so token API + agent worker logs share one terminal.
        api_thread = threading.Thread(target=run_api, name="token-api", daemon=True)
        api_thread.start()
        logging.getLogger(__name__).info(
            "Token API thread running on :8000 (agent worker starts next)."
        )
        try:
            run_agent(embed_api=True)
        finally:
            # Daemon thread exits with the process; uvicorn has no cooperative shutdown here.
            pass
    else:
        print(f"Unknown mode: {mode}. Use 'agent', 'api', or 'all'")
        sys.exit(1)
