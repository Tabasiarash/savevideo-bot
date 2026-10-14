# Signed download links (nginx `secure_link`)

When a finished file exceeds the Bot API upload limit, the bot keeps it on disk
and returns a signed, expiring URL instead of splitting or re-encoding it. This
directory holds the nginx config; it is **not applied automatically** — review
and enable it yourself.

nginx must be built with `--with-http_secure_link_module` (check with
`nginx -V 2>&1 | grep -o with-http_secure_link_module`). Caddy has no equivalent
built-in; run nginx in front, or skip this feature (links simply stay disabled).

## How the token is computed

The bot signs the request path and the expiry, then encodes the MD5 as
**base64url** (nginx uses `ngx_decode_base64url`; `+`→`-`, `/`→`_`, `=`
optional):

```
token = base64url( md5( f"{expires}{uri} {secret}" ) )
link  = "{base_url}/dl/{id}/{filename}?md5={token}&expires={expires}"
```

nginx must hash the *same* string, in the *same* order, with the *same* spacing:

```nginx
secure_link $arg_md5,$arg_expires;
secure_link_md5 "$secure_link_expires$uri $download_link_secret";
```

If you change either side, change the other. `bot/services/download_links.py`
(`secure_link_token`) is the source of truth for the Python side.

## 1. Pick a hostname

Point `DOWNLOAD_LINK_BASE_URL` (and the `server_name` below) at a host that
serves `DOWNLOAD_LINK_DIR`. Prefer a domain you do not mind being seen publicly
(over-limit media is user-supplied). It does **not** have to be the bot's host.

## 2. Create the secret (outside the repo)

```bash
printf 'set $download_link_secret "%s";\n' "$(openssl rand -hex 32)" \
    | sudo tee /etc/nginx/savevideo-link-secret.conf >/dev/null
sudo chown root:www-data /etc/nginx/savevideo-link-secret.conf
sudo chmod 640 /etc/nginx/savevideo-link-secret.conf
```

The include uses `set`, so include it **inside** the `server {}` block (as the
provided `nginx.conf` does). Never commit the real value.

## 3. Install nginx config

```bash
sudo cp nginx.conf /etc/nginx/conf.d/savevideo-download-links.conf
sudo $EDITOR /etc/nginx/conf.d/savevideo-download-links.conf   # set server_name, root
sudo nginx -t && sudo systemctl reload nginx
```

`root` must equal `DOWNLOAD_LINK_DIR` (default `/srv/savevideo-links`).

The link dir must be readable/traversable by the nginx worker user (usually
`www-data`) while writable only by the bot user:

```bash
sudo install -d -o savevideo -g www-data -m 0750 /srv/savevideo-links
```

If the chosen hostname already has a vhost you want to reuse, do **not** add a
second `server_name`; instead paste the `location ^~ /dl/ {}` block from
`nginx-location.conf` into that existing `server {}`.

## 4. Configure the bot

```env
DOWNLOAD_LINK_BASE_URL=https://dl.example.com
DOWNLOAD_LINK_SECRET=<the hex string from step 2>
DOWNLOAD_LINK_DIR=/srv/savevideo-links   # same filesystem as TEMP_DIR
```

Links stay disabled until `DOWNLOAD_LINK_BASE_URL` and
`DOWNLOAD_LINK_SECRET` are both set. Restart the bot.

## 5. Verify with a real request

Put any file under the tree (the bot itself writes `/dl/<id>/<file>`), then:

```bash
URI=/dl/test/hello.txt
sudo mkdir -p /srv/savevideo-links/dl/test
echo hi | sudo tee /srv/savevideo-links/dl/test/hello.txt
URL=$(python3 mint_link.py https://dl.example.com "$URI" "$SECRET" 300)
curl -sSI "$URL" | head -n1     # expect: HTTP/2 200
curl -sSI "https://dl.example.com$URI" | head -n1   # expect: HTTP/2 403
```

A 403 on the signed URL means the two formulas disagree — re-check the order,
spacing and the base64url alphabet on both sides. An expired-but-otherwise-valid
URL returns **410**.

## 6. Cleanup

The bot purges expired links (and their files) at startup and hourly; rows in
SQLite are the source of truth. Deleting a row without the file (or vice versa)
is reconciled automatically.
