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

# run.sh writes its own dated logs under logs/, so no redirect here.
# Any existing line for this checkout is replaced (older installs redirected
# to logs/run.log, which never got rotated).
CRON_LINE="0 5 * * * $PWD/run.sh"
( crontab -l 2>/dev/null | grep -vF "$PWD/run.sh" || true; echo "$CRON_LINE" ) | crontab -
echo "cron line: $CRON_LINE"

if [[ ! -f .env ]]; then
    echo
    echo "next: cp .env.example .env  &&  edit .env  &&  chmod 600 .env"
    echo "then test with: ./run.sh --dry-run"
fi
