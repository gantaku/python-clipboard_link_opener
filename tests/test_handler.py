"""Handler: clipboard text -> launch plan, end to end with launch stubbed."""

import json

import pytest

import src.app as app


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "a.txt").write_text("x", encoding="utf-8")
    (home / "knowledge" / "run.bat").write_text("x", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"allowed_roots": ["~/knowledge"], "source_apps": []}), encoding="utf-8"
    )
    monkeypatch.setattr(app, "obsidian_config_path", lambda: tmp_path / "none.json")
    launched = []
    monkeypatch.setattr(app, "launch", launched.append)
    return home, config, launched


def test_allowed_file_is_launched(env):
    home, config, launched = env
    app.Handler(config, home)("~/knowledge/a.txt")
    assert [p.arg for p in launched] == [str(home / "knowledge" / "a.txt")]


def test_executable_is_revealed(env):
    home, config, launched = env
    app.Handler(config, home)("~/knowledge/run.bat")
    assert launched[0].arg[0].lower().endswith("explorer.exe")
    assert launched[0].arg[1] == "/select,"


def test_plain_text_and_missing_files_do_nothing(env):
    home, config, launched = env
    handler = app.Handler(config, home)
    handler("hello")
    handler("~/knowledge/missing.txt")
    handler("~/elsewhere.txt")
    assert launched == []


def test_paused_does_nothing(env):
    home, config, launched = env
    handler = app.Handler(config, home)
    handler.paused.set()
    handler("~/knowledge/a.txt")
    assert launched == []


def test_burst_of_same_text_launches_once(env):
    home, config, launched = env
    handler = app.Handler(config, home)
    handler("~/knowledge/a.txt")
    handler("~/knowledge/a.txt")
    assert len(launched) == 1


def test_config_change_is_picked_up(env):
    home, config, launched = env
    handler = app.Handler(config, home)
    handler("https://example.com")
    assert len(launched) == 1
    config.write_text(
        json.dumps({"allowed_roots": ["~/knowledge"], "open_urls": False}),
        encoding="utf-8",
    )
    import os

    stat = config.stat()
    os.utime(config, (stat.st_atime, stat.st_mtime + 10))
    handler("https://example.org")
    assert len(launched) == 1


def test_launch_error_is_swallowed(env, monkeypatch):
    home, config, _ = env

    def boom(_plan):
        raise OSError("no association")

    monkeypatch.setattr(app, "launch", boom)
    app.Handler(config, home)("~/knowledge/a.txt")


def use_terminal_only(config):
    config.write_text(
        json.dumps({"allowed_roots": ["~/knowledge"], "source_apps": ["windowsterminal.exe"]}),
        encoding="utf-8",
    )


def test_copy_from_other_app_is_ignored(env):
    home, config, launched = env
    use_terminal_only(config)
    handler = app.Handler(config, home)
    handler("~/knowledge/a.txt", "chrome.exe")
    handler("https://example.com", "slack.exe")
    assert launched == []


def test_copy_from_terminal_opens(env):
    home, config, launched = env
    use_terminal_only(config)
    app.Handler(config, home)("~/knowledge/a.txt", "WindowsTerminal.exe")
    assert len(launched) == 1
