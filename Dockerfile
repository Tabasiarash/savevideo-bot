# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
        fonts-noto-core fonts-noto-extra fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Deno: yt-dlp's recommended JS runtime for YouTube's signature/PO-token
# challenges (the shipped image would otherwise have no JS engine).
ARG DENO_VERSION=2.5.4
RUN curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
        -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/deno \
    && rm -f /tmp/deno.zip \
    && deno --version

# Dedicated virtualenv so the non-root user can self-update yt-dlp at runtime.
RUN python -m venv /opt/venv \
    && pip install --upgrade pip setuptools wheel

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Non-root runtime user (uid/gid 10001). The venv is writable by it.
RUN groupadd -g 10001 bot \
    && useradd -u 10001 -g bot --create-home bot \
    && mkdir -p /data /tmp/savevideo \
    && chown -R bot:bot /opt/venv /app /data /tmp/savevideo

USER bot

ENV DB_PATH=/data/savevideo.db \
    TEMP_DIR=/tmp/savevideo \
    LOG_LEVEL=INFO

HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD python -c "import bot.main, yt_dlp" || exit 1

CMD ["python", "-m", "bot.main"]
