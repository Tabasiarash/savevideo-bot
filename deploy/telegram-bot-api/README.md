# Local Telegram Bot API server

Raises the upload ceiling from 50 MB to 2000 MB by running a self-hosted
[telegram-bot-api](https://github.com/tdlib/telegram-bot-api) server next to the
bot. This directory is for the **systemd** install; the Docker install already
ships an equivalent service under the `localapi` profile in the root
`docker-compose.yml`.

## 1. Credentials

Get an `api_id` / `api_hash` from <https://my.telegram.org> and put them in a
local `.env`:

```bash
cp .env.example .env
$EDITOR .env
```

## 2. Match the shared directory

The bot and the server must see the bot's download directory at the **same
absolute path** — in local mode the bot hands the server a filesystem path and
the server opens the file itself. So:

- `BOT_TMP` must equal the bot's `TEMP_DIR` (default `/opt/savevideo-bot/tmp`).
- `BOT_API_DATA` should equal the bot's `BOT_API_DATA_DIR` so `/health` can
  report the server's free space.

## 3. Start the server

```bash
docker compose up -d
docker compose logs -f telegram-bot-api
```

## 4. Point the bot at it

In `/opt/savevideo-bot/.env`:

```env
BOT_API_URL=http://127.0.0.1:8081
MAX_UPLOAD_MB=2000
```

Then log the bot out of the cloud API **once** (cloud and local servers cannot
be used at the same time) and restart:

```bash
TOKEN=$(grep -E '^BOT_TOKEN=' /opt/savevideo-bot/.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOKEN}/logOut"
sudo systemctl restart savevideo-bot
journalctl -u savevideo-bot -f
```

Run `/health` in Telegram: expect `Mode: local`, `Bot API: ok @yourbot`,
`Upload limit: 2000 MB`, and a `Data dir` line with free space. See
[docs/MIGRATION.md](../../docs/MIGRATION.md) for the full runbook and rollback.
