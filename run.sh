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
    python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
fi
