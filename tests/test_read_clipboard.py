"""read_clipboard with fake Win32 modules: retries, busy and change-during-read."""

import sys
import types

import pytest

import src.watcher as watcher

CF_UNICODETEXT = 13


class FakeError(Exception):
    pass


class FakeWin32:
    def __init__(self, text="hello", open_failures=0, data_failures=0, seq_steps=None):
        self.text = text
        self.open_failures = open_failures
        self.data_failures = data_failures
        self.seq_steps = list(seq_steps or [])
        self.seq = 1
        self.closed = 0

    # win32clipboard
    def GetClipboardSequenceNumber(self):
        if self.seq_steps:
            self.seq = self.seq_steps.pop(0)
        return self.seq

    def OpenClipboard(self, hwnd=None):
        if self.open_failures:
            self.open_failures -= 1
            raise FakeError("busy")

    def CloseClipboard(self):
        self.closed += 1

    def IsClipboardFormatAvailable(self, fmt):
        return self.text is not None

    def GetClipboardData(self, fmt):
        if self.data_failures:
            self.data_failures -= 1
            raise FakeError("closed under us")
        return self.text


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        win = FakeWin32(**kwargs)
        clip = types.SimpleNamespace(
            GetClipboardSequenceNumber=win.GetClipboardSequenceNumber,
            OpenClipboard=win.OpenClipboard,
            CloseClipboard=win.CloseClipboard,
            IsClipboardFormatAvailable=win.IsClipboardFormatAvailable,
            GetClipboardData=win.GetClipboardData,
        )
        monkeypatch.setitem(sys.modules, "win32clipboard", clip)
        monkeypatch.setitem(
            sys.modules,
            "win32con",
            types.SimpleNamespace(CF_UNICODETEXT=CF_UNICODETEXT),
        )
        monkeypatch.setitem(
            sys.modules, "pywintypes", types.SimpleNamespace(error=FakeError)
        )
        monkeypatch.setattr(watcher, "clipboard_source", lambda: "windowsterminal.exe")
        monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
        return win

    return install


def test_reads_text_and_source(fake):
    win = fake()
    assert watcher.read_clipboard() == ("hello", "windowsterminal.exe")
    assert win.closed == 1


def test_non_text_content_gives_none(fake):
    fake(text=None)
    assert watcher.read_clipboard() == (None, "windowsterminal.exe")


def test_open_failure_is_retried(fake):
    fake(open_failures=2)
    assert watcher.read_clipboard()[0] == "hello"


def test_data_failure_is_retried(fake):
    win = fake(data_failures=1)
    assert watcher.read_clipboard()[0] == "hello"
    assert win.closed == 2


def test_change_during_read_is_retried(fake):
    # before=1, after=2 (changed) -> retry: before=2, after=2
    fake(seq_steps=[1, 2, 2, 2])
    assert watcher.read_clipboard()[0] == "hello"


def test_always_busy_raises(fake):
    fake(open_failures=99)
    with pytest.raises(watcher.ClipboardBusy):
        watcher.read_clipboard(retries=3)


def test_read_clipboard_text_returns_only_text(fake):
    fake()
    assert watcher.read_clipboard_text() == "hello"


def test_sequence_number_uses_win32(fake):
    win = fake(seq_steps=[7])
    assert watcher.sequence_number() == 7
    assert win.seq == 7
