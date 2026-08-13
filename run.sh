#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🛶 Starting Kayak Agent Platform..."

if command -v docker &> /dev/null && [ "$1" != "--local" ]; then
    echo "🐳 Launching with Docker Compose..."
    docker compose up --build
else
    echo "⚡ Launching in Local Python Mode..."
    export PYTHONPATH="$DIR"
    # Bind to loopback by default. Kayak's agents get shell and filesystem access,
    # so exposing this port is equivalent to exposing a shell; override with
    # KAYAK_HOST and set KAYAK_AUTH_TOKEN if you genuinely need remote access.
    python3 -m uvicorn backend.app.main:app \
        --host "${KAYAK_HOST:-127.0.0.1}" \
        --port "${KAYAK_PORT:-8000}" \
        --reload
fi
