#!/usr/bin/env bash
# Cron entrypoint: activates the venv, sources .env, runs the pipeline.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    echo "no .venv found; run ./install.sh first" >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    echo "no .env found; copy .env.example to .env and fill in values" >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
set -a
# shellcheck disable=SC1091
source .env
set +a

mkdir -p logs out

# `run.sh check-config` is a shorthand for the pre-flight env validator.
# Anything else is forwarded to the main pipeline.
if [[ "${1:-}" == "check-config" ]]; then
    shift
    exec python -m kindle_daily.check_config "$@"
fi

# Under cron (no tty) log to a dated file; interactive runs print to the terminal.
[[ -t 1 ]] || exec >> "logs/run-$(date +%F).log" 2>&1

# Keep RETENTION_DAYS of EPUBs and logs (#7). Old single logs/run.log is
# matched too, so it ages out once nothing appends to it.
RETENTION_DAYS="${RETENTION_DAYS:-30}"
if [[ ! "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "RETENTION_DAYS must be a positive integer, got '$RETENTION_DAYS'" >&2
    exit 1
fi
find out -name '*.epub' -mtime +"$RETENTION_DAYS" -delete
find logs -name 'run*.log' -mtime +"$RETENTION_DAYS" -delete

python -m kindle_daily.main "$@"
