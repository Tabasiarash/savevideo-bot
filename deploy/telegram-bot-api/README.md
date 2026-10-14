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
the server opens the file itself. `BOT_TMP` in `.env` must equal the bot's
`TEMP_DIR` (default `/opt/savevideo-bot/tmp`).

## 3. Start it

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

Then **log the bot out of the cloud API once** (cloud and local servers cannot
run simultaneously) and restart:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/logOut"
sudo systemctl restart savevideo-bot
sudo journalctl -u savevideo-bot -f
```

`/health` should now show `Mode: local`, `Bot API: ok @yourbot`, and
`Upload limit: 2000 MB`. Full step-by-step (including rollback) is in
[`docs/MIGRATION.md`](../../docs/MIGRATION.md).
