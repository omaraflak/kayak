# --- Stage 1: Build Frontend ---
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# --- Stage 2: Final Backend & Web Server ---
FROM python:3.11-slim

# Install system dependencies including docker CLI and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    bash \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend and seed data (secrets and runtime state are excluded via .dockerignore)
COPY backend/ ./backend/
COPY data/ ./data/

# Copy built frontend from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Stamped by the release workflow. A container cannot read the version label of
# the image it is running from, so the value is baked in for the app to report.
ARG KAYAK_VERSION=dev
ENV KAYAK_VERSION=${KAYAK_VERSION}

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Binding to all interfaces is safe here because Docker isolates the container
# network; publishing the port is an explicit decision made by docker-compose.
ENV KAYAK_HOST=0.0.0.0
ENV KAYAK_PORT=8000
ENV KAYAK_IN_DOCKER=true

EXPOSE 8000

# `--timeout-graceful-shutdown` is what lets the app actually shut down. The UI
# holds a server-sent-events stream open for as long as it is on screen, and
# uvicorn's default is to wait forever for open connections to close before
# running the shutdown hook. A stream nobody is going to close means that hook
# never runs, so the sandbox containers it stops outlived every quit.
#
# One second rather than something more generous: waiting achieves nothing for the
# streams, which will not close on their own, and everything after this point has
# to finish inside the grace period the daemon allows -- as little as three
# seconds on a stock Docker Desktop.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "1"]
