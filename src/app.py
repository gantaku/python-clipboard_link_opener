"""Tray-resident app: watch the clipboard and open copied paths / URLs."""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src import __version__
from src.config import (
    ConfigError,
    app_dir,
    load_config,
    load_obsidian_vaults,
    obsidian_config_path,
)
from src.normalize import normalize
from src.opener import launch, plan_launch
from src.osinfo import IS_WINDOWS
from src.policy import decide, source_allowed
from src.watcher import ClipboardWatcher, Deduper

log = logging.getLogger("clipboard_link_opener")

MUTEX_NAME = "Local\\clipboard_link_opener"
ERROR_ALREADY_EXISTS = 183
LOCK_NAME = "clipboard_link_opener.lock"


class Handler:
    """Clipboard text -> normalize -> source/policy -> dedup -> launch.

    Settings and the Obsidian vault list are reloaded when either file changes.
    An invalid settings file never widens the policy: the last good one stays,
    and nothing opens until the first valid one is read.
    """

    def __init__(self, config_path: Path, home: Path):
        self._config_path = config_path
        self._home = home
        self._dedup = Deduper()
        self._loaded_key: tuple | None = None
        self._policy = None
        self._vaults: tuple[Path, ...] = ()
        self.paused = threading.Event()
        self._reload_if_changed()

    def watch_primary(self) -> bool:
        """Linux: whether X11 PRIMARY (selection without an explicit copy) is watched."""
        self._reload_if_changed()
        return self._policy is not None and self._policy.watch_primary

    def __call__(self, text: str, source: str | None = None) -> None:
        if self.paused.is_set():
            return
        target = normalize(text, self._home)
        if target is None:
            return
        self._reload_if_changed()
        shown = target.url or str(target.path)
        if self._policy is None:
            log.warning("ignored (no valid settings yet): %s", shown)
            return
        if not source_allowed(source, self._policy):
            log.info("ignored (copied from %s): %s", source, shown)
            return
        decision = decide(target, self._policy)
        if decision.action == "reject":
            log.info("rejected (%s): %s", decision.reason, shown)
            return
        plan = plan_launch(decision, self._vaults)
        # Dedup only what is about to open, so an ignored or rejected copy
        # never suppresses the next real one (Codex review #6).
        if plan is None or not self._dedup.should_fire(shown):
            return
        try:
            launch(plan)
            log.info("%s (from %s): %s", decision.action, source, shown)
        except (OSError, ValueError) as error:
            log.warning("launch failed for %s: %s", shown, error)

    def _change_key(self) -> tuple:
        return (_mtime_ns(self._config_path), _mtime_ns(obsidian_config_path()))

    def _reload_if_changed(self) -> None:
        before = self._change_key()
        if before == self._loaded_key:
            return
        try:
            policy = load_config(self._config_path, self._home)
        except ConfigError as error:
            kept = "keeping the previous settings" if self._policy else "nothing will open"
            log.error("invalid settings (%s); %s", error, kept)
            policy = None
        vaults = load_obsidian_vaults(obsidian_config_path())
        if policy is not None:
            self._policy = policy
        self._vaults = vaults
        # A save during the read leaves the key unrecorded, so the next copy reloads.
        after = self._change_key()
        self._loaded_key = after if after == before and policy is not None else None
        if policy is not None:
            log.info(
                "config loaded: roots=%s open_urls=%s sources=%d vaults=%d",
                [str(r) for r in policy.allowed_roots],
                policy.open_urls,
                len(policy.source_apps),
                len(vaults),
            )


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def setup_logging(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "app.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def acquire_single_instance():
    if not IS_WINDOWS:
        return acquire_lock_file(lock_path())
    import win32api
    import win32event

    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return mutex


def lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return (Path(runtime) if os.path.isabs(runtime) else app_dir()) / LOCK_NAME


def acquire_lock_file(path: Path):
    """Open file holding an exclusive flock, or None if another instance holds it.

    The kernel drops the lock when the process exits, so a crash leaves nothing stale.
    """
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 - kept open for the life of the process
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def open_with_default_app(path: Path) -> None:
    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606
    else:
        from src.opener import LaunchPlan

        launch(LaunchPlan("shell", str(path)))


def import_pystray():
    """pystray, or None when no tray backend works (the app then runs without a tray)."""
    try:
        import pystray

        return pystray
    except Exception as error:  # noqa: BLE001 - a missing GI namespace raises ValueError
        if IS_WINDOWS or os.environ.get("PYSTRAY_BACKEND"):
            log.warning("tray unavailable: %s", error)
            return None
    # Without the AppIndicator GI bindings, fall back to a plain X11 tray icon.
    os.environ["PYSTRAY_BACKEND"] = "xorg"
    try:
        import pystray

        return pystray
    except Exception as error:  # noqa: BLE001
        log.warning("tray unavailable: %s", error)
        return None


def make_icon_image(paused: bool):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (128, 128, 128, 255) if paused else (37, 99, 235, 255)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=color)
    # Two chain links
    draw.rounded_rectangle((12, 24, 36, 40), radius=8, outline="white", width=5)
    draw.rounded_rectangle((28, 24, 52, 40), radius=8, outline="white", width=5)
    return image


def run_tray(
    handler: Handler,
    watcher: ClipboardWatcher | None,
    config_path: Path,
    log_path: Path,
    pystray=None,
    notice: str | None = None,
) -> None:
    if pystray is None:
        import pystray

    title = f"Clipboard Link Opener {__version__}"
    if notice:
        title += f"（{notice}）"

    def toggle_pause(icon, _item):
        if handler.paused.is_set():
            handler.paused.clear()
        else:
            handler.paused.set()
        paused = handler.paused.is_set()
        icon.icon = make_icon_image(paused)
        icon.title = f"{title}（一時停止中）" if paused else title
        log.info("paused" if paused else "resumed")

    def open_file(path: Path):
        return lambda _icon, _item: open_with_default_app(path)

    def quit_app(icon, _item):
        if watcher is not None:
            watcher.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "一時停止", toggle_pause, checked=lambda _item: handler.paused.is_set()
        ),
        pystray.MenuItem("設定ファイルを開く", open_file(config_path)),
        pystray.MenuItem("ログを開く", open_file(log_path)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("終了", quit_app),
    )
    pystray.Icon("clipboard_link_opener", make_icon_image(False), title, menu).run()


def main() -> int:
    if not IS_WINDOWS:
        return main_linux()
    directory = app_dir()
    log_path = setup_logging(directory)
    mutex = acquire_single_instance()
    if mutex is None:
        log.info("already running; exit")
        return 0
    log.info("start %s", __version__)
    config_path = directory / "config.json"
    handler = Handler(config_path, Path.home())
    watcher = ClipboardWatcher(handler)
    watcher.start()
    try:
        run_tray(handler, watcher, config_path, log_path)
    finally:
        watcher.stop()
        log.info("stop")
    return 0


def main_linux() -> int:
    import signal

    from src.watcher_x11 import X11Clipboard, session_problem

    directory = app_dir()
    log_path = setup_logging(directory)
    lock = acquire_single_instance()
    if lock is None:
        log.info("already running; exit")
        return 0
    log.info("start %s", __version__)
    config_path = directory / "config.json"
    handler = Handler(config_path, Path.home())

    problem = session_problem()
    watcher = None
    if problem:
        # The copying app cannot be identified, so nothing is opened at all.
        log.error("not watching: %s", problem)
    else:
        clipboard = X11Clipboard(watch_primary=handler.watch_primary)
        try:
            # Fail at start rather than logging the same error every poll.
            clipboard.open()
        except Exception as error:  # noqa: BLE001
            problem = f"X11 に接続できません: {error}"
            log.error("not watching: %s", problem)
        else:
            watcher = ClipboardWatcher(
                handler, sequence=clipboard.sequence, reader=clipboard.read
            )
            watcher.start()

    pystray = import_pystray()
    try:
        if pystray is not None:
            try:
                run_tray(handler, watcher, config_path, log_path, pystray, problem)
                return 0
            except Exception:  # noqa: BLE001 - no tray host: keep working without one
                log.exception("tray failed; running without a tray")
        if watcher is None:
            return 1
        stopped = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, lambda *_args: stopped.set())
        log.info("running without a tray")
        while not stopped.wait(1.0):
            if not watcher.is_alive():
                log.error("clipboard watcher stopped")
                return 1
        return 0
    finally:
        if watcher is not None:
            watcher.stop()
        log.info("stop")


if __name__ == "__main__":
    sys.exit(main())
