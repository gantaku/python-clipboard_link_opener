"""Watch the Windows clipboard and hand new text to a callback."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

POLL_SECONDS = 0.2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
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
    """Poll the clipboard sequence number; read text and its source only when it changes."""

    def __init__(
        self,
        on_text: Callable[[str, str | None], None],
        poll: float = POLL_SECONDS,
        sequence: Callable[[], int] | None = None,
        reader: Callable[[], tuple[str | None, str | None]] | None = None,
    ):
        super().__init__(name="clipboard-watcher", daemon=True)
        self._on_text = on_text
        self._poll = poll
        self._sequence = sequence or sequence_number
        self._reader = reader or read_clipboard
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # Start from the current content: never open what was copied before launch.
        last = self._safe_sequence()
        while not self._stop_event.wait(self._poll):
            current = self._safe_sequence()
            if current is None or current == last:
                continue
            try:
                text, source = self._reader()
            except ClipboardBusy:
                continue  # keep `last` so this copy is read again on the next tick
            except Exception:
                log.exception("clipboard read failed")
                text, source = None, None
            last = current
            try:
                if text is not None:
                    self._on_text(text, source)
            except Exception:  # a dead watcher thread would silently stop the app
                log.exception("clipboard handling failed")

    def _safe_sequence(self) -> int | None:
        try:
            return self._sequence()
        except Exception:  # never let the watcher thread die
            log.exception("clipboard sequence number failed")
            return None


class ClipboardBusy(Exception):
    """Another window (or thread) holds the clipboard, or it changed while reading."""


def sequence_number() -> int:
    import win32clipboard

    return win32clipboard.GetClipboardSequenceNumber()


def read_clipboard(retries: int = 5, delay: float = 0.05) -> tuple[str | None, str | None]:
    """(text, source exe) read inside one open, retried if the clipboard changes meanwhile."""
    import pywintypes
    import win32clipboard
    import win32con

    for _ in range(retries):
        before = sequence_number()
        try:
            win32clipboard.OpenClipboard()
        except pywintypes.error:
            # Another process holds the clipboard right after copying.
            time.sleep(delay)
            continue
        try:
            source = clipboard_source()
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            else:
                text = None
        except TypeError:
            text = None
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
        if sequence_number() == before:
            return text, source
    raise ClipboardBusy()


def read_clipboard_text() -> str | None:
    return read_clipboard()[0]


def clipboard_source() -> str | None:
    """Lower-case exe name of the clipboard owner. None when the owner is unknown.

    The foreground window is not used as a fallback: it may be a different app
    from the one that copied (Codex review #4).
    """
    import ctypes
    from ctypes import wintypes

    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardOwner.argtypes = []
        user32.GetClipboardOwner.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]  # fmt: skip
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        hwnd = user32.GetClipboardOwner()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
        finally:
            kernel32.CloseHandle(handle)
        return buffer.value.rsplit("\\", 1)[-1].lower()
    except Exception:  # never let the watcher thread die
        log.exception("clipboard source lookup failed")
        return None
