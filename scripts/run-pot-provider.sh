#!/usr/bin/env bash
# Run (or restart) the bgutil YouTube PO-token provider next to the bot.
#
# The bot's yt-dlp talks to this server on http://127.0.0.1:4416 (default) or,
# in docker-compose, http://bgutil:4416. Keep the image tag in sync with the
# bgutil-ytdlp-pot-provider pin in requirements.txt.
set -euo pipefail

VERSION="${BGUTIL_VERSION:-2.0.2}"
IMAGE="brainicism/bgutil-ytdlp-pot-provider:${VERSION}"
PORT="${BGUTIL_PORT:-127.0.0.1:4416}"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found" >&2
    exit 1
fi

docker rm -f bgutil-provider >/dev/null 2>&1 || true
docker run -d \
    --name bgutil-provider \
    --restart unless-stopped \
    -p "${PORT}:4416" \
    "${IMAGE}" >/dev/null

sleep 3
echo "bgutil provider ${VERSION} listening on ${PORT}"
docker ps --filter name=bgutil-provider --format '{{.Names}} {{.Status}} {{.Ports}}'
