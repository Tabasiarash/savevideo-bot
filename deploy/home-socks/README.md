# Home SOCKS egress

A residential/home/mobile IP is the most reliable way to defeat YouTube's
"Sign in to confirm you're not a bot" wall, TikTok's "IP address is blocked",
and similar anti-bot checks from a datacenter VPS. This unit opens an SSH
dynamic-forward (`ssh -D`) SOCKS5 listener on the VPS loopback, tunnelling
yt-dlp traffic out through a machine on your home network.

Only the bot's yt-dlp traffic uses this proxy — it is **not** a full exit node,
so the rest of the VPS keeps its normal route.

```
bot/yt-dlp → socks5h://127.0.0.1:1080 (VPS)
           → SSH over Tailscale → home device → internet via the home IP
```

## 1. Set up the home device

### Android (Termux, recommended for a phone on Wi-Fi/mobile data)

Install Termux from **F-Droid** (the Play Store build is outdated), plus the
**Termux:Boot** add-on. Then, in Termux:

```bash
pkg update && pkg install openssh
termux-wake-lock
mkdir -p ~/.ssh && chmod 700 ~/.ssh
sshd            # listens on port 8022
whoami          # note the username, e.g. u0_a497
```

Autostart after reboot — create `~/.termux/boot/start-sshd.sh`:

```sh
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sshd
```

```bash
chmod +x ~/.termux/boot/start-sshd.sh
```

Open the Termux:Boot app once so Android registers it. In Android settings set
battery usage to **Unrestricted** for both Tailscale and Termux (Android kills
background apps otherwise — this is the first suspect if the tunnel drops).

### Tailscale

Install the Tailscale app, log in to the same tailnet as the VPS, and note the
device's `100.x.y.z` address. Keep the VPN connection on and battery
*Unrestricted*.

### Any Linux host

Nothing special: reachable over Tailscale, SSH on port 22 (`HOME_SOCKS_PORT=22`).

## 2. Create the SOCKS key (on the VPS)

```bash
sudo install -d -m 700 -o savevideo -g savevideo /etc/savevideo-bot
sudo ssh-keygen -t ed25519 -f /etc/savevideo-bot/id_home_socks -N "" -C savevideo-egress
sudo chown savevideo:savevideo /etc/savevideo-bot/id_home_socks*
sudo touch /etc/savevideo-bot/home-socks.known_hosts
sudo chown savevideo:savevideo /etc/savevideo-bot/home-socks.known_hosts
```

Append the public key to `~/.ssh/authorized_keys` **on the home device**. Make
it tunnel-only by prefixing it with `restrict,port-forwarding` so the key cannot
open a shell or run commands:

```
restrict,port-forwarding ssh-ed25519 AAAA... savevideo-egress
```

(The `restrict` option disables forwarding, so `port-forwarding` must follow it
to re-enable the one thing we need.)

```bash
sudo cat /etc/savevideo-bot/id_home_socks.pub   # copy this line to the home device
```

## 3. Configure

```bash
sudo cp home-socks.env.example /etc/savevideo-bot/home-socks.env
sudo $EDITOR /etc/savevideo-bot/home-socks.env   # host/user/key/port
sudo chmod 600 /etc/savevideo-bot/home-socks.env
```

For Termux set `HOME_SOCKS_PORT=8022` and `HOME_SOCKS_USER=<whoami>`. Set
`HOME_SOCKS_LOCAL_PORT` to a free local port (the example uses `1090`).

## 4. Install and start

```bash
sudo install -m 0644 home-socks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-socks
systemctl status home-socks --no-pager
journalctl -u home-socks -f
```

## 5. Test (in this order)

Run these **on the VPS** (the SOCKS listener exists there, not on the phone —
running `curl ... 127.0.0.1:1080` inside Termux will just fail):

```bash
# Must print your HOME/mobile IP, not the VPS IP.
curl --socks5-hostname 127.0.0.1:1090 https://ifconfig.me

cd /root/savevideo-bot
./.venv/bin/yt-dlp --proxy socks5h://127.0.0.1:1090 -F "<public youtube url>"
./.venv/bin/yt-dlp --proxy socks5h://127.0.0.1:1090 -F "<public tiktok url>"
./.venv/bin/yt-dlp --proxy socks5h://127.0.0.1:1090 -F "<public x.com video url>"
```

Only after these work, point the bot at it. In `/opt/savevideo-bot/.env`:

```env
PROXY_URL=socks5h://127.0.0.1:1090
```

Then `sudo systemctl restart savevideo-bot` and check `/health`: the **Proxy**
line shows the masked proxy and **Proxy exit** shows the masked egress IP.

### Route only some sites through it

If some sites work directly, leave them off the (possibly metered) home
connection with per-platform overrides; each falls back to `PROXY_URL`:

```env
PROXY_URL_INSTAGRAM=
PROXY_URL_TIKTOK=
PROXY_URL_X=
```

## Notes

- **Pick a free local port.** `1080` is a common default and is often already
  bound (e.g. by another SOCKS server). This example uses `1090`; check with
  `ss -ltnp | grep 1090` before starting.
- The systemd unit for the bot sets `RestrictAddressFamilies=AF_INET AF_INET6`,
  so `100.64.0.0/10` (Tailscale) and `127.0.0.1` are reachable as-is. Do **not**
  add an `IPAddressDeny=any` allow-list unless you also list `100.64.0.0/10`.
- `socks5h://` (not `socks5://`) makes yt-dlp resolve DNS on the home side,
  which is what you want for geolocation.
- Bandwidth: the home connection's upload speed is the ceiling. A phone on
  mobile data shares a carrier IP (rarely blocked) but is metered — keep
  `YT_MAX_CONCURRENCY` low and consider per-platform overrides.
- If YouTube still walls you, the home connection may itself be flagged; refresh
  `COOKIES_FILE` and re-check `/health`.
