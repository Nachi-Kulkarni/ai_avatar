"""Patches stdlib ``logging.Logger`` with ``trace``/``dev`` bits livekit-agents expects.

``livekit.agents.log`` registers a custom ``Logger`` subclass only when that module loads.
If ``logging.getLogger("livekit.agents")`` ran earlier (e.g. ``configure_logging`` in ``main``),
the cached logger is a plain ``Logger`` and STT fails with ``AttributeError: trace``.
Import this module before importing ``livekit`` in any entrypoint (``main`` or ``agent``).
"""
from __future__ import annotations

import logging

_LK_TRACE = 5
_LK_DEV = 23


def install() -> None:
    logging.addLevelName(_LK_TRACE, "TRACE")
    logging.addLevelName(_LK_DEV, "DEV")
    if not hasattr(logging.Logger, "trace"):

        def trace(self, msg, *args, **kwargs):
            if self.isEnabledFor(_LK_TRACE):
                self._log(_LK_TRACE, msg, args, **kwargs)

        logging.Logger.trace = trace  # type: ignore[attr-defined]
    if not hasattr(logging.Logger, "dev"):

        def dev(self, msg, *args, **kwargs):
            if self.isEnabledFor(_LK_DEV):
                self._log(_LK_DEV, msg, args, **kwargs)

        logging.Logger.dev = dev  # type: ignore[attr-defined]


install()
