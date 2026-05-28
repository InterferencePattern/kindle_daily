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

python -m kindle_daily.main "$@"
