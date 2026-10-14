# Home SOCKS egress

A residential/home IP is the most reliable way to defeat YouTube's
"Sign in to confirm you're not a bot" wall from a datacenter VPS. This unit
opens an SSH dynamic-forward (`ssh -D`) SOCKS5 listener on the VPS loopback,
tunnelling yt-dlp traffic out through a machine on your home network.

Only the bot's yt-dlp traffic uses this proxy — it is **not** a full exit node,
so the rest of the VPS keeps its normal route.

## 1. Create the SOCKS key

```bash
sudo install -d -m 700 -o savevideo -g savevideo /etc/savevideo-bot
sudo ssh-keygen -t ed25519 -f /etc/savevideo-bot/id_home_socks -N "" -C savevideo-egress
sudo chown savevideo:savevideo /etc/savevideo-bot/id_home_socks*
# Authorise it on the home host:
sudo cat /etc/savevideo-bot/id_home_socks.pub   # append to ~/.ssh/authorized_keys on home
```

## 2. Configure

```bash
sudo cp home-socks.env.example /etc/savevideo-bot/home-socks.env
sudo $EDITOR /etc/savevideo-bot/home-socks.env   # host/user/key/port
sudo chmod 600 /etc/savevideo-bot/home-socks.env
```

Reach the home host over **Tailscale** (e.g. `100.x.y.z`) so no public SSH
exposure is required.

## 3. Install and start

```bash
sudo install -m 0644 home-socks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-socks
systemctl status home-socks --no-pager
journalctl -u home-socks -f
```

## 4. Point the bot at it

In `/opt/savevideo-bot/.env`:

```env
PROXY_URL=socks5h://127.0.0.1:1080
```

Then `sudo systemctl restart savevideo-bot` and check `/health`: the **Proxy**
line shows the masked proxy and **Egress** should report `✅ <title>`.

## Notes

- The systemd unit for the bot sets `RestrictAddressFamilies=AF_INET AF_INET6`,
  so `100.64.0.0/10` (Tailscale) and `127.0.0.1` are reachable as-is. Do **not**
  add an `IPAddressDeny=any` allow-list unless you also list `100.64.0.0/10`.
- `socks5h://` (not `socks5://`) makes yt-dlp resolve DNS on the home side,
  which is what you want for geolocation.
- If YouTube still walls you, the home connection may itself be flagged; refresh
  `COOKIES_FILE` and re-check `/health`.
