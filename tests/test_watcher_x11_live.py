"""X11Clipboard against the real X server (Linux).

Opt-in: these overwrite CLIPBOARD and PRIMARY (only plain text is put back), so
they run only with CLO_LIVE_CLIPBOARD=1. A helper Python process owns the copy,
as a terminal would; xclip restores the previous text afterwards.
"""

import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or os.environ.get("CLO_LIVE_CLIPBOARD") != "1"
    or not os.environ.get("DISPLAY")
    or shutil.which("xclip") is None,
    reason="set CLO_LIVE_CLIPBOARD=1 on an X11 session with xclip to run real clipboard tests",
)

# Owns a selection and answers UTF8_STRING requests until another client takes it.
_OWNER = """
import sys, time
from Xlib import X, Xatom, display
from Xlib.protocol import event
selection_name, text = sys.argv[1], sys.stdin.read().encode()
d = display.Display()
w = d.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
sel, utf8, targets = (d.intern_atom(n) for n in (selection_name, "UTF8_STRING", "TARGETS"))
w.set_selection_owner(sel, X.CurrentTime)
d.flush()
print("owned", flush=True)
deadline = time.time() + 30
while time.time() < deadline:
    e = d.next_event()
    if e.type == X.SelectionClear:
        break
    if e.type != X.SelectionRequest:
        continue
    prop = e.property or e.target
    if e.target == utf8:
        e.requestor.change_property(prop, utf8, 8, text)
    elif e.target == targets:
        e.requestor.change_property(prop, Xatom.ATOM, 32, [targets, utf8])
    else:
        prop = X.NONE
    e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor,
        selection=e.selection, target=e.target, property=prop))
    d.flush()
"""

SOURCE = os.path.basename(os.path.realpath(sys.executable)).lower()


def xclip_get(selection):
    result = subprocess.run(
        ["xclip", "-selection", selection, "-o"], capture_output=True, timeout=5
    )
    return result.stdout.decode("utf-8", "replace") if result.returncode == 0 else None


@pytest.fixture
def owners():
    saved = {name: xclip_get(name) for name in ("clipboard", "primary")}
    started = []

    def own(selection, text):
        proc = subprocess.Popen(
            [sys.executable, "-c", _OWNER, selection],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(text)
        proc.stdin.close()
        assert proc.stdout.readline().strip() == "owned"
        started.append(proc)

    yield own
    for name, text in saved.items():
        if text is not None:
            subprocess.run(["xclip", "-selection", name, "-i"], input=text.encode(), timeout=5)
    for proc in started:
        proc.kill()
        proc.wait(5)


def wait_for_change(clipboard, before, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clipboard.sequence() != before:
            return True
        threading.Event().wait(0.05)
    return False


def test_clipboard_copy_gives_text_and_owner(owners):
    from src.watcher_x11 import X11Clipboard

    clipboard = X11Clipboard(watch_primary=lambda: False)
    clipboard.open()
    before = clipboard.sequence()
    owners("CLIPBOARD", "~/knowledge/日本語.md")
    assert wait_for_change(clipboard, before)
    assert clipboard.read() == ("~/knowledge/日本語.md", SOURCE)


def test_primary_is_read_once_stable(owners):
    from src.watcher_x11 import X11Clipboard

    clipboard = X11Clipboard(watch_primary=lambda: True)
    clipboard.open()
    before = clipboard.sequence()
    owners("PRIMARY", "/home/me/a.md")
    assert wait_for_change(clipboard, before)
    started = time.monotonic()
    assert clipboard.read() == ("/home/me/a.md", SOURCE)
    assert time.monotonic() - started >= 0.5  # waited for the text to settle


def test_primary_is_ignored_when_disabled(owners):
    from src.watcher_x11 import X11Clipboard

    clipboard = X11Clipboard(watch_primary=lambda: False)
    clipboard.open()
    before = clipboard.sequence()
    owners("PRIMARY", "/home/me/b.md")
    assert not wait_for_change(clipboard, before, timeout=1.0)
