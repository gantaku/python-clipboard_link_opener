"""Which OS-specific layer to use. Everything else is shared."""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
