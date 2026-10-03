"""ClipboardWatcher against the real Windows clipboard.

Opt-in: these overwrite the clipboard (only plain text is put back), so they run
only with CLO_LIVE_CLIPBOARD=1. The regular suite covers the watcher with fakes.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CLO_LIVE_CLIPBOARD") != "1",
    reason="set CLO_LIVE_CLIPBOARD=1 to run real clipboard tests (Windows only)",
)

# Writes from another process, as a real copy does. With OWNED=1 the clipboard is
# opened with a real window, so that window's process becomes the owner.
_WRITER = """
import os, sys, time, pywintypes, win32clipboard, win32con, win32gui
text = sys.stdin.read()
hwnd = win32gui.CreateWindowEx(0, "STATIC", "", 0, 0, 0, 0, 0, 0, 0, 0, None) if os.environ.get("OWNED") == "1" else None
for _ in range(50):
    try:
        win32clipboard.OpenClipboard(hwnd)
        break
    except pywintypes.error:
        time.sleep(0.02)
else:
    sys.exit(1)
try:
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
finally:
    win32clipboard.CloseClipboard()
"""


def set_clipboard(text: str, owned: bool = False) -> None:
    env = {**os.environ, "OWNED": "1" if owned else "0"}
    subprocess.run(
        [sys.executable, "-c", _WRITER],
        input=text,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )


@pytest.fixture
def saved_clipboard():
    from src.watcher import ClipboardBusy, read_clipboard_text

    try:
        original = read_clipboard_text()
    except ClipboardBusy:
        original = None
    yield
    if original is not None:
        set_clipboard(original)


def wait_for(received, count, timeout=2.0):
    event = threading.Event()
    for _ in range(int(timeout / 0.05)):
        if len(received) >= count:
            return True
        event.wait(0.05)
    return len(received) >= count


def test_new_copy_is_delivered_with_owner_but_existing_content_is_not(saved_clipboard):
    from src.watcher import ClipboardWatcher

    set_clipboard("before-start")
    received = []
    watcher = ClipboardWatcher(
        lambda text, source: received.append((text, source)), poll=0.02
    )
    watcher.start()
    try:
        threading.Event().wait(0.1)
        set_clipboard("after-start", owned=True)
        assert wait_for(received, 1)
    finally:
        watcher.stop()
        watcher.join(2.0)
    assert received == [("after-start", Path(sys.executable).name.lower())]


def test_unknown_owner_gives_no_source(saved_clipboard):
    from src.watcher import read_clipboard

    set_clipboard("no-owner", owned=False)
    assert read_clipboard() == ("no-owner", None)
