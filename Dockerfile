# ---- Stage 1: install Python dependencies into a virtual environment -------
FROM python:3.13-slim-bookworm AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: minimal runtime image -----------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# Stockfish comes from the Debian archive as a separate GPLv3 program
# (see THIRD_PARTY_NOTICES.md). The app runs as an unprivileged user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin chess \
    && mkdir -p /data \
    && chown chess:chess /data

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    STOCKFISH_PATH=/usr/games/stockfish

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
COPY web ./web

USER chess
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"]

CMD ["python", "-m", "app"]
