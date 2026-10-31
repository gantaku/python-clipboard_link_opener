"""Load user settings and the Obsidian vault list."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.osinfo import IS_WINDOWS
from src.policy import BUILTIN_REVEAL_EXTENSIONS, Policy

log = logging.getLogger(__name__)

APP_DIR_NAME = "clipboard_link_opener"
DEFAULT_ROOTS = ("~",)
# Terminals and editors where tools print paths. Browsers and chat apps are left
# out so copying a link there to paste elsewhere does not open it.
WINDOWS_SOURCE_APPS = (
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "wezterm-gui.exe",
    "alacritty.exe", "mintty.exe", "tabby.exe", "code.exe", "cursor.exe",
    "windsurf.exe", "claude.exe",
)  # fmt: skip
# Executable names as /proc/<pid>/exe shows them. Terminals written in Python
# (Terminator, Guake) are left out: their executable is the shared interpreter.
LINUX_SOURCE_APPS = (
    "gnome-terminal-server", "kgx", "ptyxis", "konsole", "xfce4-terminal",
    "mate-terminal", "lxterminal", "qterminal", "tilix", "kitty", "alacritty",
    "wezterm-gui", "xterm", "urxvt", "st", "sakura", "terminology", "tabby",
    "code", "code-insiders", "codium", "cursor", "windsurf", "claude", "claude-desktop",
)  # fmt: skip
DEFAULT_SOURCE_APPS = WINDOWS_SOURCE_APPS if IS_WINDOWS else LINUX_SOURCE_APPS


def app_dir() -> Path:
    if not IS_WINDOWS:
        return _xdg_config_home() / APP_DIR_NAME
    return Path(os.environ.get("APPDATA", Path.home())) / APP_DIR_NAME


def obsidian_config_path() -> Path:
    if not IS_WINDOWS:
        native = _xdg_config_home() / "obsidian" / "obsidian.json"
        flatpak = (
            Path.home() / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian" / "obsidian.json"
        )
        return flatpak if not native.exists() and flatpak.exists() else native
    return Path(os.environ.get("APPDATA", Path.home())) / "obsidian" / "obsidian.json"


def _xdg_config_home() -> Path:
    # The spec says a relative value must be ignored.
    value = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(value) if os.path.isabs(value) else Path.home() / ".config"


class ConfigError(ValueError):
    """The settings file is unreadable or invalid. Callers keep the last good policy."""


def default_settings() -> dict:
    settings = {
        "open_urls": True,
        "allowed_roots": list(DEFAULT_ROOTS),
        "source_apps": list(DEFAULT_SOURCE_APPS),
        # Added to the built-in list, which always applies.
        "reveal_extensions": [],
    }
    if not IS_WINDOWS:
        # Selecting text in a terminal (X11 PRIMARY), not only Ctrl+Shift+C.
        settings["watch_primary"] = True
    return settings


def load_config(path: Path, home: Path) -> Policy:
    """Strict: any invalid value raises ConfigError instead of falling back to a wider default."""
    data = _read_or_create(path)
    defaults = default_settings()
    settings = {key: data.get(key, value) for key, value in defaults.items()}

    for key in ("open_urls", "watch_primary"):
        if key in settings and not isinstance(settings[key], bool):
            raise ConfigError(f"{key} must be true or false")
    for key in ("allowed_roots", "source_apps", "reveal_extensions"):
        if not _is_str_list(settings[key]):
            raise ConfigError(f"{key} must be a list of strings")

    roots = tuple(_expand(r, home) for r in settings["allowed_roots"])
    if any(not r.strip() for r in settings["allowed_roots"]) or not all(r.is_absolute() for r in roots):
        raise ConfigError("allowed_roots entries must be absolute paths or start with ~")

    return Policy(
        allowed_roots=roots,
        open_urls=settings["open_urls"],
        reveal_extensions=BUILTIN_REVEAL_EXTENSIONS | {e.lower() for e in settings["reveal_extensions"]},
        source_apps=frozenset(a.lower() for a in settings["source_apps"]),
        watch_primary=settings.get("watch_primary", False),
    )


def load_obsidian_vaults(path: Path) -> tuple[Path, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            Path(v["path"]) for v in data.get("vaults", {}).values() if "path" in v
        )
    except (OSError, ValueError, AttributeError, TypeError) as error:
        log.info("obsidian vaults unavailable: %s", error)
        return ()


def _read_or_create(path: Path) -> dict:
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(default_settings(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as error:
            log.warning("config: cannot create %s: %s", path, error)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError("the settings file must be a JSON object")
    return data


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _expand(root: str, home: Path) -> Path:
    if root == "~":
        return home
    if root.startswith(("~/", "~\\")):
        return home / root[2:]
    return Path(root)
