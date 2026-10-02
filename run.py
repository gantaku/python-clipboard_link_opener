"""PyInstaller / 直接実行用のエントリポイント.

`src` パッケージを import するための launcher。`pythonw run.py` で窓なしで常駐する。
"""

from __future__ import annotations

import sys

from src.app import main

if __name__ == "__main__":
    sys.exit(main())
