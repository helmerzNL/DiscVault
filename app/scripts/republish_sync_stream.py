#!/usr/bin/env python3
"""Source-checkout wrapper for the canonical sync-republish CLI."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend.scripts.republish_sync_stream import main


if __name__ == "__main__":
    raise SystemExit(main())
