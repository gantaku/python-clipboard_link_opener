"""Watch the X11 CLIPBOARD and PRIMARY selections (Linux).

Plugs into ``ClipboardWatcher`` as its ``sequence`` / ``reader`` pair:
XFixes owner-change events bump the sequence, and a read returns the text
together with the executable of the process that owns the selection.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from src.watcher import ClipboardBusy

log = logging.getLogger(__name__)

CONVERT_TIMEOUT = 1.0
# PRIMARY changes while the mouse drags, so it is read only once the buttons are
# up and the text has stayed the same for STABLE_SECONDS.
STABLE_SECONDS = 0.5
RELEASE_TIMEOUT = 30.0
STABLE_TRIES = 10
TICK = 0.05


class X11Clipboard:
    """``sequence()`` and ``read()`` for ClipboardWatcher. Use from one thread only."""

    def __init__(self, watch_primary: Callable[[], bool] = lambda: True):
        self._watch_primary = watch_primary
        self._display = None
        self._count = 0
        self._last_selection = None

    # --- ClipboardWatcher interface -------------------------------------------

    def sequence(self) -> int:
        self._ensure_open()
        self._pump()
        return self._count

    def read(self) -> tuple[str | None, str | None]:
        self._ensure_open()
        selection = self._last_selection
        if selection is None:
            return None, None
        if selection == self._primary:
            return self._read_primary()
        return self._read_once(selection)

    # --- setup -----------------------------------------------------------------

    def open(self) -> None:
        """Connect now, so a missing display or extension fails at startup."""
        self._ensure_open()

    def _ensure_open(self) -> None:
        if self._display is not None:
            return
        from Xlib import X, display
        from Xlib.ext import xfixes

        # Opened in the watcher thread, which is the only one that touches it.
        self._display = display.Display()
        if not self._display.has_extension("XFIXES"):
            raise RuntimeError("the X server has no XFIXES extension")
        self._display.xfixes_query_version()
        root = self._display.screen().root
        self._root = root
        # Unmapped helper window that receives converted selections.
        self._window = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        atom = self._display.intern_atom
        self._clipboard = atom("CLIPBOARD")
        self._primary = atom("PRIMARY")
        self._utf8 = atom("UTF8_STRING")
        self._incr = atom("INCR")
        self._property = atom("CLIPBOARD_LINK_OPENER")
        self._net_wm_pid = atom("_NET_WM_PID")
        for selection in (self._clipboard, self._primary):
            self._display.xfixes_select_selection_input(
                root, selection, xfixes.XFixesSetSelectionOwnerNotifyMask
            )
        self._display.flush()

    # --- events ----------------------------------------------------------------

    def _pump(self, until: Callable | None = None):
        """Handle queued events; return the first event ``until`` accepts, if any."""
        found = None
        while self._display.pending_events():
            event = self._display.next_event()
            # By name: python-xlib loads extension modules a second time, so the
            # event class is not the one `from Xlib.ext import xfixes` gives.
            if type(event).__name__ == "SetSelectionOwnerNotify":
                self._on_owner_change(event)
            elif found is None and until is not None and until(event):
                found = event
        return found

    def _on_owner_change(self, event) -> None:
        if not event.owner:
            return  # selection cleared
        if event.selection == self._primary and not self._watch_primary():
            return
        self._count += 1
        self._last_selection = event.selection

    # --- reading ---------------------------------------------------------------

    def _read_once(self, selection: int) -> tuple[str | None, str | None]:
        owner = self._owner(selection)
        if not owner:
            return None, None
        source = self._owner_exe(owner)
        text = self._convert(selection)
        if self._owner(selection) != owner:
            raise ClipboardBusy()  # replaced while reading; read the new one next tick
        return text, source

    def _read_primary(self) -> tuple[str | None, str | None]:
        if not self._wait_for_release():
            log.info("PRIMARY ignored: mouse button still held")
            return None, None
        text, source = self._read_once(self._primary)
        for _ in range(STABLE_TRIES):
            self._sleep(STABLE_SECONDS)
            if not self._buttons_up():
                if not self._wait_for_release():
                    return None, None
            again, again_source = self._read_once(self._primary)
            if again == text and again_source == source:
                return text, source
            text, source = again, again_source
        return None, None

    def _buttons_up(self) -> bool:
        from Xlib import X

        mask = self._root.query_pointer().mask
        return not mask & (X.Button1Mask | X.Button2Mask | X.Button3Mask)

    def _wait_for_release(self) -> bool:
        deadline = time.monotonic() + RELEASE_TIMEOUT
        while time.monotonic() < deadline:
            if self._buttons_up():
                return True
            self._sleep(TICK)
        return False

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)
        self._pump()

    def _owner(self, selection: int) -> int:
        owner = self._display.get_selection_owner(selection)
        return getattr(owner, "id", owner) or 0

    def _convert(self, selection: int) -> str | None:
        from Xlib import X

        self._window.delete_property(self._property)
        self._window.convert_selection(selection, self._utf8, self._property, X.CurrentTime)
        self._display.flush()
        deadline = time.monotonic() + CONVERT_TIMEOUT
        notify = None
        while notify is None and time.monotonic() < deadline:
            notify = self._pump(
                lambda e: e.type == X.SelectionNotify and e.requestor.id == self._window.id
            )
            if notify is None:
                time.sleep(0.01)
        if notify is None or notify.property == X.NONE:
            return None
        prop = self._window.get_full_property(self._property, X.AnyPropertyType)
        self._window.delete_property(self._property)
        self._display.flush()
        if prop is None or prop.property_type == self._incr:
            return None  # INCR means hundreds of KB: far beyond a single path
        value = prop.value
        if isinstance(value, str):
            return value
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _owner_exe(self, window: int) -> str | None:
        pid = self._client_pid(window) or self._window_pid(window)
        return exe_name(pid) if pid else None

    def _client_pid(self, window: int) -> int | None:
        # X-Resource asks the server which local process holds the connection that
        # created the window; GTK's clipboard window carries no _NET_WM_PID.
        try:
            from Xlib.ext import res

            if not self._display.has_extension("X-Resource"):
                return None
            reply = self._display.res_query_client_ids(
                [{"client": window, "mask": res.LocalClientPIDMask}]
            )
            for client in reply.ids:
                if client.spec.mask == res.LocalClientPIDMask and client.value:
                    return int(client.value[0])
        except Exception:  # never let the watcher thread die
            log.exception("X-Resource PID lookup failed")
        return None

    def _window_pid(self, window: int) -> int | None:
        from Xlib import Xatom

        try:
            prop = self._display.create_resource_object("window", window).get_full_property(
                self._net_wm_pid, Xatom.CARDINAL
            )
        except Exception:
            log.exception("_NET_WM_PID lookup failed")
            return None
        return int(prop.value[0]) if prop is not None and len(prop.value) else None


def exe_name(pid: int, readlink: Callable[[str], str] = os.readlink) -> str | None:
    """Lower-case executable name of ``pid``. None when it cannot be read."""
    try:
        target = readlink(f"/proc/{pid}/exe")
    except OSError:
        return None
    # An executable replaced by an upgrade still runs; the kernel marks it.
    target = target.removesuffix(" (deleted)")
    return os.path.basename(target).lower() or None


def session_problem(environ: dict | None = None) -> str | None:
    """Why this session cannot be watched safely, or None."""
    environ = os.environ if environ is None else environ
    if environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or environ.get("WAYLAND_DISPLAY"):
        # Through XWayland only X clients are visible, so most copies would have no owner.
        return "Wayland では動作しません（X11 のセッションでログインしてください）"
    if not environ.get("DISPLAY"):
        return "X11 のディスプレイがありません（DISPLAY が未設定）"
    return None
