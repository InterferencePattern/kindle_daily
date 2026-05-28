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

CRON_LINE="0 5 * * * $PWD/run.sh >> $PWD/logs/run.log 2>&1"
if crontab -l 2>/dev/null | grep -Fq "$PWD/run.sh"; then
    echo "cron line already installed:"
    crontab -l | grep -F "$PWD/run.sh"
else
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
    echo "added cron line: $CRON_LINE"
fi

if [[ ! -f .env ]]; then
    echo
    echo "next: cp .env.example .env  &&  edit .env  &&  chmod 600 .env"
    echo "then test with: ./run.sh --dry-run"
fi
