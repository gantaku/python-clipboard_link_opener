"""pytest 共通設定 — src をインポート可能にする."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tests written with Windows paths (C:\..., explorer.exe) or Win32 behaviour.
windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows paths / Win32 behaviour"
)
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux paths / X11 behaviour"
)
