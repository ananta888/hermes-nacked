#!/usr/bin/env python3
"""Host/operator entry point for the SOLID control-plane package."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/usr/local/lib")

from control_plane.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
