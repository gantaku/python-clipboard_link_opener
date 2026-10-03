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
from src.policy import decide, source_allowed
from src.watcher import ClipboardWatcher, Deduper

log = logging.getLogger("clipboard_link_opener")

MUTEX_NAME = "Local\\clipboard_link_opener"
ERROR_ALREADY_EXISTS = 183


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
    import win32api
    import win32event

    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return mutex


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
    handler: Handler, watcher: ClipboardWatcher, config_path: Path, log_path: Path
) -> None:
    import pystray

    title = f"Clipboard Link Opener {__version__}"

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
        return lambda _icon, _item: os.startfile(str(path))

    def quit_app(icon, _item):
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


if __name__ == "__main__":
    sys.exit(main())
