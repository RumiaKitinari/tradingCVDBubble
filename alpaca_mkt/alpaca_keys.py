"""
alpaca/alpaca_keys.py — Alpaca API credentials.

Loaded from the project .env file (ALPACA_API_KEY, ALPACA_SECRET_KEY).
Used by all alpaca/*.py modules.  Never commit this file's values to git.

Free IEX feed: ~2.5% of total US equities volume.
Paid SIP feed: full consolidated tape (Algo Trader Plus subscription needed).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY: str = os.getenv("ALPACA_SECRET_KEY", "")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise RuntimeError(
        "ALPACA_API_KEY / ALPACA_SECRET_KEY not found in .env. "
        "Add them and re-run."
    )
