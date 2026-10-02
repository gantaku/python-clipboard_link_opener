"""Tray-resident app: watch the clipboard and open copied paths / URLs."""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src import __version__
from src.config import app_dir, load_config, load_obsidian_vaults, obsidian_config_path
from src.normalize import normalize
from src.opener import launch, plan_launch
from src.policy import decide
from src.watcher import ClipboardWatcher, Deduper

log = logging.getLogger("clipboard_link_opener")

MUTEX_NAME = "Local\\clipboard_link_opener"
ERROR_ALREADY_EXISTS = 183


class Handler:
    """Clipboard text -> normalize -> policy -> launch. Reloads config when it changes."""

    def __init__(self, config_path: Path, home: Path):
        self._config_path = config_path
        self._home = home
        self._dedup = Deduper()
        self._mtime: float | None = None
        self._policy = None
        self._vaults: tuple[Path, ...] = ()
        self.paused = threading.Event()
        self._reload_if_changed()

    def __call__(self, text: str) -> None:
        if self.paused.is_set() or not self._dedup.should_fire(text):
            return
        target = normalize(text, self._home)
        if target is None:
            return
        self._reload_if_changed()
        decision = decide(target, self._policy)
        shown = target.url or str(target.path)
        if decision.action == "reject":
            log.info("rejected (%s): %s", decision.reason, shown)
            return
        plan = plan_launch(decision, self._vaults)
        if plan is None:
            return
        try:
            launch(plan)
            log.info("%s: %s", decision.action, shown)
        except (OSError, ValueError) as error:
            log.warning("launch failed for %s: %s", shown, error)

    def _reload_if_changed(self) -> None:
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            mtime = None
        if self._policy is not None and mtime == self._mtime:
            return
        self._policy = load_config(self._config_path, self._home)
        self._vaults = load_obsidian_vaults(obsidian_config_path())
        try:
            self._mtime = self._config_path.stat().st_mtime
        except OSError:
            self._mtime = None
        log.info(
            "config loaded: roots=%s open_urls=%s vaults=%d",
            [str(r) for r in self._policy.allowed_roots],
            self._policy.open_urls,
            len(self._vaults),
        )


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
