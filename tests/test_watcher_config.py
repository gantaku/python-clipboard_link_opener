"""Deduper (copyOnSelect burst suppression) and config loading."""

import json
from pathlib import Path

import pytest

from src.config import DEFAULT_ROOTS, ConfigError, load_config, load_obsidian_vaults
from src.watcher import Deduper

HOME = Path(r"C:\Users\me")


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_first_text_fires():
    assert Deduper(window=2.0, clock=FakeClock()).should_fire("a")


def test_same_text_within_window_is_suppressed():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    assert dedup.should_fire("a")
    clock.now += 1.0
    assert not dedup.should_fire("a")


def test_same_text_after_window_fires_again():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    dedup.should_fire("a")
    clock.now += 2.5
    assert dedup.should_fire("a")


def test_different_text_fires_immediately():
    clock = FakeClock()
    dedup = Deduper(window=2.0, clock=clock)
    dedup.should_fire("a")
    assert dedup.should_fire("b")


def test_missing_config_is_created_with_defaults(tmp_path):
    path = tmp_path / "cfg" / "config.json"
    policy = load_config(path, HOME)
    assert path.exists()
    assert policy.open_urls is True
    assert policy.allowed_roots == (HOME,)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["open_urls"] is True


def test_config_values_are_read(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "open_urls": False,
                "allowed_roots": ["~/x", r"D:\data"],
                "reveal_extensions": [".foo"],
            }
        ),
        encoding="utf-8",
    )
    policy = load_config(path, HOME)
    assert policy.open_urls is False
    assert policy.allowed_roots == (HOME / "x", Path(r"D:\data"))
    # User entries are added to the built-in list, never replace it (Codex review #2).
    assert ".foo" in policy.reveal_extensions
    assert ".bat" in policy.reveal_extensions


def test_broken_config_raises_instead_of_widening(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, HOME)
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    "data",
    [
        {"open_urls": "yes"},
        {"allowed_roots": "~/x"},
        {"allowed_roots": ["foo"]},
        {"allowed_roots": [""]},
        {"source_apps": "code.exe"},
        {"reveal_extensions": [1]},
        ["not", "an", "object"],
    ],
)
def test_invalid_values_raise(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, HOME)


def test_obsidian_vaults_are_read(tmp_path):
    path = tmp_path / "obsidian.json"
    path.write_text(
        json.dumps(
            {
                "vaults": {
                    "a": {"path": r"C:\Users\me\knowledge"},
                    "b": {"path": r"C:\w"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_obsidian_vaults(path) == (Path(r"C:\Users\me\knowledge"), Path(r"C:\w"))


def test_obsidian_missing_or_broken_returns_empty(tmp_path):
    assert load_obsidian_vaults(tmp_path / "none.json") == ()
    broken = tmp_path / "broken.json"
    broken.write_text("[", encoding="utf-8")
    assert load_obsidian_vaults(broken) == ()




def test_source_apps_default_and_override(tmp_path):
    from src.config import DEFAULT_SOURCE_APPS

    path = tmp_path / "config.json"
    assert load_config(path, HOME).source_apps == frozenset(DEFAULT_SOURCE_APPS)
    path.write_text(json.dumps({"source_apps": ["Foo.EXE"]}), encoding="utf-8")
    assert load_config(path, HOME).source_apps == frozenset({"foo.exe"})
    path.write_text(json.dumps({"source_apps": []}), encoding="utf-8")
    assert load_config(path, HOME).source_apps == frozenset()


def test_default_roots_are_generic():
    assert DEFAULT_ROOTS == ("~",)
