#!/usr/bin/env bash
# One-shot Pi setup: create venv, install deps, add a daily cron line.
# Idempotent: safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    echo "creating .venv"
    python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

mkdir -p out logs

# Cover fonts (cover.py). Without them the cover is silently skipped.
if [[ "$(uname)" == Linux && ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]]; then
    echo
    echo "WARNING: DejaVu fonts missing; the EPUB cover will be skipped."
    echo "         fix with: sudo apt install fonts-dejavu-core"
    echo
fi

# Daily run time comes from RUN_AT in .env (device-local, default 05:00).
# Parsed with python-dotenv, never sourced as bash (#5).
CRON_TIME="$(.venv/bin/python - <<'PY'
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(".env")
raw = os.environ.get("RUN_AT", "05:00")
for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
    try:
        t = datetime.strptime(raw.strip().upper(), fmt)
    except ValueError:
        continue
    print(t.minute, t.hour)
    break
else:
    sys.exit(f"RUN_AT must look like 17:00, 5:00 PM or 5PM, got {raw!r}")
PY
)"

# run.sh writes its own dated logs under logs/, so no redirect here.
# Any existing line for this checkout is replaced (older installs redirected
# to logs/run.log, which never got rotated).
CRON_LINE="$CRON_TIME * * * $PWD/run.sh"
( crontab -l 2>/dev/null | grep -vF "$PWD/run.sh" || true; echo "$CRON_LINE" ) | crontab -
echo "cron line: $CRON_LINE"

if [[ ! -f .env ]]; then
    echo
    echo "next: cp .env.example .env  &&  edit .env  &&  chmod 600 .env"
    echo "then test with: ./run.sh --dry-run"
fi
