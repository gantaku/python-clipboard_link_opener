"""ClipboardWatcher against the real Windows clipboard (content is restored)."""

import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows clipboard only"
)


_WRITER = """
import sys, time, pywintypes, win32clipboard, win32con
text = sys.stdin.read()
for _ in range(50):
    try:
        win32clipboard.OpenClipboard()
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


def set_clipboard(text: str) -> None:
    # Write from another process, as a real copy does. In-process writes share the
    # NULL-window clipboard open with the watcher thread and close each other.
    import subprocess

    subprocess.run(
        [sys.executable, "-c", _WRITER],
        input=text,
        text=True,
        encoding="utf-8",
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


def test_new_copy_is_delivered_but_existing_content_is_not(saved_clipboard):
    from src.watcher import ClipboardWatcher

    set_clipboard("before-start")
    received = []
    got = threading.Event()

    def on_text(text):
        received.append(text)
        got.set()

    watcher = ClipboardWatcher(on_text, poll=0.02)
    watcher.start()
    try:
        threading.Event().wait(0.1)
        set_clipboard("after-start")
        assert got.wait(2.0)
    finally:
        watcher.stop()
        watcher.join(2.0)
    assert received == ["after-start"]


def test_handler_exception_does_not_stop_watching(saved_clipboard):
    from src.watcher import ClipboardWatcher

    received = []
    second = threading.Event()

    def on_text(text):
        received.append(text)
        if len(received) == 1:
            raise RuntimeError("boom")
        second.set()

    watcher = ClipboardWatcher(on_text, poll=0.02)
    watcher.start()
    try:
        threading.Event().wait(0.1)
        set_clipboard("one")
        threading.Event().wait(0.2)
        set_clipboard("two")
        assert second.wait(2.0)
    finally:
        watcher.stop()
        watcher.join(2.0)
    assert received == ["one", "two"]
