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

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
