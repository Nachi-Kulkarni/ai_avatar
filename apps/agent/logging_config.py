"""
Shared logging setup for ``main`` and for LiveKit **job worker** processes.

Workers import ``agent`` directly and never execute ``main.py``, so any logging config
only in ``main`` means pipeline (STT / LLM / TOOL) lines never show from the worker.

LiveKit ``dev`` installs a **Rich** handler on the **root** logger. Those records are
drawn inside the interactive UI, so Cursor's normal terminal scrollback often shows
**only** Uvicorn lines. We always mirror ``agent``, ``tools``, and ``mykare.pipeline``
to **stderr** in plain text so STT / LLM / TOOL and session lines stay visible.

Key: ``mykare.pipeline`` propagates to root so LiveKit's Rich handler renders
STT / LLM / TOOL lines in the dev UI.
"""
from __future__ import annotations

import logging
import os
import sys

from config import Settings

_applied = False

# Tag we put on our handlers so re-entry does not duplicate them.
_MIRROR_ATTR = "_mykare_stderr_mirror"


class _FlushingStderrHandler(logging.StreamHandler):
    """stderr + flush so line-oriented terminals (e.g. Cursor) show logs immediately."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def silence_hpack_loggers() -> None:
    for name in ("hpack", "hpack.hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _already_mirrored(lg: logging.Logger) -> bool:
    return any(getattr(h, _MIRROR_ATTR, False) for h in lg.handlers)


def _add_stderr_mirror(*names: str, level: int = logging.INFO) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    for name in names:
        lg = logging.getLogger(name)
        if _already_mirrored(lg):
            continue
        sh = _FlushingStderrHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        setattr(sh, _MIRROR_ATTR, True)
        lg.addHandler(sh)


def configure_logging(settings: Settings) -> None:
    global _applied
    if _applied:
        return
    _applied = True

    level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(message)s",
        force=True,
    )

    verbose = os.getenv("VERBOSE_AGENT_LOGS", "").lower() in ("1", "true", "yes")
    # Optional: framework STT/session lines on stderr (noisy but complete).
    plain_livekit = os.getenv("MYKARE_STDERR_LIVEKIT", "").lower() in ("1", "true", "yes")

    if not verbose:
        for name in (
            "livekit",
            "livekit.agents",
            "livekit.plugins",
            "httpx",
            "httpcore",
            "httpcore.http2",
            "hpack",
            "hpack.hpack",
            "openai",
            "urllib3",
            "uvicorn",
            "uvicorn.access",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)
    else:
        for name in (
            "livekit",
            "livekit.agents",
            "livekit.plugins",
            "httpx",
            "httpcore",
            "openai",
        ):
            logging.getLogger(name).setLevel(logging.DEBUG)

    if plain_livekit and not verbose:
        logging.getLogger("livekit.agents").setLevel(logging.INFO)

    silence_hpack_loggers()

    _pipeline = logging.getLogger("mykare.pipeline")
    _pipeline.setLevel(logging.INFO)
    # propagate=True (default) so LiveKit's Rich root handler renders pipeline logs
    # (STT / LLM / TOOL) in the dev UI.  The stderr mirror below ensures they also
    # survive Rich's terminal refresh.
    _pipeline.propagate = True
    _pipeline.handlers.clear()
    _ph = _FlushingStderrHandler(sys.stderr)
    _ph.setFormatter(logging.Formatter("%(asctime)s PIPELINE %(message)s"))
    _ph.setLevel(logging.INFO)
    setattr(_ph, _MIRROR_ATTR, True)
    _pipeline.addHandler(_ph)

    for app_log in ("agent", "tools", "__main__"):
        logging.getLogger(app_log).setLevel(logging.DEBUG if verbose else logging.INFO)

    # Plain-text copy on stderr — Rich dev UI eats root logs from scrollback.
    _add_stderr_mirror("agent", "tools", "__main__", level=logging.INFO)
    if plain_livekit:
        _add_stderr_mirror("livekit.agents", level=logging.INFO)
