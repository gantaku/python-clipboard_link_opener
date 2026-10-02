"""Watch the Windows clipboard and hand new text to a callback."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

POLL_SECONDS = 0.2
DEDUP_SECONDS = 2.0


class Deduper:
    """Suppress the same text repeated within ``window`` seconds (copyOnSelect bursts)."""

    def __init__(
        self, window: float = DEDUP_SECONDS, clock: Callable[[], float] = time.monotonic
    ):
        self._window = window
        self._clock = clock
        self._last_text: str | None = None
        self._last_at = float("-inf")

    def should_fire(self, text: str) -> bool:
        now = self._clock()
        repeated = text == self._last_text and now - self._last_at < self._window
        self._last_text, self._last_at = text, now
        return not repeated


class ClipboardWatcher(threading.Thread):
    """Poll the clipboard sequence number; read text only when it changes."""

    def __init__(self, on_text: Callable[[str], None], poll: float = POLL_SECONDS):
        super().__init__(name="clipboard-watcher", daemon=True)
        self._on_text = on_text
        self._poll = poll
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        import win32clipboard

        # Start from the current content: never open what was copied before launch.
        last = _sequence_number(win32clipboard)
        while not self._stop_event.wait(self._poll):
            current = _sequence_number(win32clipboard)
            if current is None or current == last:
                continue
            try:
                text = read_clipboard_text()
            except ClipboardBusy:
                continue  # keep `last` so this copy is read again on the next tick
            except Exception:
                log.exception("clipboard read failed")
                text = None
            last = current
            try:
                if text is not None:
                    self._on_text(text)
            except Exception:  # a dead watcher thread would silently stop the app
                log.exception("clipboard handling failed")


class ClipboardBusy(Exception):
    """Another window (or thread) holds the clipboard; try again later."""


def _sequence_number(win32clipboard) -> int | None:
    try:
        return win32clipboard.GetClipboardSequenceNumber()
    except Exception:  # never let the watcher thread die
        log.exception("GetClipboardSequenceNumber failed")
        return None


def read_clipboard_text(retries: int = 5, delay: float = 0.05) -> str | None:
    import pywintypes
    import win32clipboard
    import win32con

    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
        except pywintypes.error:
            # Another process holds the clipboard right after copying.
            time.sleep(delay)
            continue
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return None
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except TypeError:
            return None
        except pywintypes.error:
            # Closed under us by another opener; treat as busy.
            time.sleep(delay)
            continue
        finally:
            try:
                win32clipboard.CloseClipboard()
            except pywintypes.error:
                # Already closed by someone sharing this NULL-window open; nothing to undo.
                pass
    raise ClipboardBusy()
