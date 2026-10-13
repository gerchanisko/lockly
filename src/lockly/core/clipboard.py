"""Clipboard handling with automatic clearing.

The toolkit-specific part (reading/writing the actual desktop clipboard) is
behind :class:`ClipboardBackend`; the GTK implementation lives in
``lockly.platform.gtk_clipboard``. Timers are provided by a scheduler
callable so this module can be tested without a display.

Lockly only clears the clipboard if it still holds the content Lockly put
there. If another application has replaced it in the meantime, Lockly leaves
it alone and reports that instead of claiming it cleared anything.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)


class ClearResult(enum.Enum):
    CLEARED = "cleared"
    REPLACED = "replaced"  # another app owns the clipboard now; left untouched
    FAILED = "failed"
    NOTHING = "nothing"


class ClipboardBackend(Protocol):
    def set_text(self, text: str, sensitive: bool) -> object:
        """Put text on the clipboard. Returns an opaque ownership token."""

    def owns(self, token: object) -> bool:
        """True if the clipboard still holds the content identified by token."""

    def clear(self) -> bool:
        """Empty the clipboard. Returns True only if this verifiably worked."""


Scheduler = Callable[[float, Callable[[], None]], Callable[[], None]]
"""scheduler(delay_seconds, callback) -> cancel function"""


class ClipboardGuard:
    def __init__(
        self,
        backend: ClipboardBackend,
        scheduler: Scheduler,
        on_cleared: Callable[[ClearResult], None] | None = None,
    ):
        self._backend = backend
        self._scheduler = scheduler
        self._on_cleared = on_cleared
        self._token: object | None = None
        self._cancel: Callable[[], None] | None = None

    @property
    def holds_secret(self) -> bool:
        return self._token is not None

    def copy(self, text: str, *, sensitive: bool, clear_after: float | None) -> None:
        """Copy text. ``clear_after`` of None/0 disables automatic clearing."""
        self._cancel_timer()
        token = self._backend.set_text(text, sensitive)
        if sensitive:
            self._token = token
            if clear_after:
                self._cancel = self._scheduler(clear_after, self._timeout)
        else:
            self._token = None

    def _timeout(self) -> None:
        self._cancel = None
        result = self.clear()
        if self._on_cleared is not None:
            self._on_cleared(result)

    def _cancel_timer(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    def clear(self) -> ClearResult:
        """Clear the clipboard if it still contains a secret we copied."""
        self._cancel_timer()
        token, self._token = self._token, None
        if token is None:
            return ClearResult.NOTHING
        try:
            if not self._backend.owns(token):
                return ClearResult.REPLACED
            return ClearResult.CLEARED if self._backend.clear() else ClearResult.FAILED
        except Exception:
            log.warning("Clearing the clipboard failed", exc_info=False)
            return ClearResult.FAILED
