"""
Rewrite clinic branding for text sent to TTS only.

The UI and chat transcripts should stay ``mykare.ai``; Cartesia otherwise
tends to spell letters (“m y k a r e dot a i”). Spoken output uses a short
phrase that models read as “my care A I”.
"""

from __future__ import annotations

import re
from typing import Any

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

# Spoken form — spaces encourage “my care” + letter names for “A I”.
SPOKEN_CLINIC_BRAND = "My Care A I"

_DOT_AI = re.compile(r"mykare\.ai", re.IGNORECASE)
_SPACE_AI = re.compile(r"mykare\s+ai\b", re.IGNORECASE)


def expand_brand_for_tts(text: str) -> str:
    """Replace visual-domain branding with a TTS-friendly phrase."""
    if not text:
        return text
    out = _DOT_AI.sub(SPOKEN_CLINIC_BRAND, text)
    return _SPACE_AI.sub(SPOKEN_CLINIC_BRAND, out)


class _SegmentBufferBrandStream:
    """
    Buffers ``push_text`` until ``flush`` / ``end_input`` so ``mykare.ai`` is
    never split across WebSocket chunks (which would break substring replace).
    """

    def __init__(self, inner: tts.SynthesizeStream) -> None:
        self._inner = inner
        self._segment = ""

    def push_text(self, token: str) -> None:
        if not token:
            return
        self._segment += token

    def flush(self) -> None:
        if self._segment:
            self._inner.push_text(expand_brand_for_tts(self._segment))
            self._segment = ""
        self._inner.flush()

    def end_input(self) -> None:
        if self._segment:
            self._inner.push_text(expand_brand_for_tts(self._segment))
            self._segment = ""
        self._inner.end_input()

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> Any:
        return self._inner.__aiter__()

    async def __anext__(self) -> Any:
        return await self._inner.__anext__()

    async def __aenter__(self) -> _SegmentBufferBrandStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._inner.__aexit__(exc_type, exc, tb)


class BrandSpeechTTS(tts.TTS):
    """Delegates to an inner TTS after rewriting clinic branding in the audio path."""

    def __init__(self, inner: tts.TTS) -> None:
        super().__init__(
            capabilities=inner.capabilities,
            sample_rate=inner.sample_rate,
            num_channels=inner.num_channels,
        )
        self._inner = inner
        self._inner.on("metrics_collected", self._forward_metrics)

    def _forward_metrics(self, *args: Any, **kwargs: Any) -> None:
        self.emit("metrics_collected", *args, **kwargs)

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def provider(self) -> str:
        return self._inner.provider

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return self._inner.synthesize(expand_brand_for_tts(text), conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.SynthesizeStream:
        return _SegmentBufferBrandStream(self._inner.stream(conn_options=conn_options))

    def prewarm(self) -> None:
        self._inner.prewarm()

    async def aclose(self) -> None:
        self._inner.off("metrics_collected", self._forward_metrics)
        await self._inner.aclose()
