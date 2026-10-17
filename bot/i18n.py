"""User-facing strings. Languages: English (en), German (de), Farsi (fa).

Every key is defined in all three dictionaries. The helper ``get_text``
resolves a user's language with a fallback chain: user language -> configured
default language -> English.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGES = ("en", "de", "fa")

EN = "en"
DE = "de"
FA = "fa"

LANGUAGE_NAMES = {EN: "English", DE: "Deutsch", FA: "فارسی"}

_STRINGS: dict[str, dict[str, str]] = {
    EN: {
        "start": (
            "Hi {name}! 👋\n\n"
            "Send me a link and I'll download the <b>video</b>, the <b>audio</b> or the "
            "<b>cover image</b> — from YouTube, Instagram, TikTok, X/Twitter, Facebook, "
            "Reddit, Vimeo, Pinterest, SoundCloud and many other sites.\n\n"
            "I can also add <b>subtitles</b> and download <b>playlists</b>.\n\n"
            "New here? Tap <b>📖 Tutorial</b> to see everything I can do and my limits.\n\n"
            "❕ Disclaimer: you are responsible for respecting the copyright of the "
            "content and the terms of service of the respective site."
        ),
        "help": (
            "How to use me:\n\n"
            "• Send me a link and I'll ask which quality you want.\n"
            "• Choose <b>Video</b>, <b>Audio</b> or <b>Thumbnail</b>.\n"
            "• <b>/playlist</b> &lt;url&gt; — download up to {playlist} items of a playlist.\n"
            "• <b>/tutorial</b> — full guide: abilities and limits.\n"
            "• <b>/language</b> — switch language.\n"
            "• <b>/settings</b> — choose how subtitles are delivered.\n"
            "• Inline mode: type @{botname} + a URL in any chat.\n\n"
            "Sources: YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit, Vimeo, "
            "Pinterest, SoundCloud, Spotify (matched), podcasts, and more."
        ),
        "tutorial": (
            "📖 <b>Guide — what I can do</b>\n\n"
            "① <b>Send a link</b>\n"
            "Paste a video/audio URL and pick <b>Video</b> (choose quality), "
            "<b>Audio</b> (MP3/M4A) or <b>Cover</b>. Works in this chat and inline "
            "(@{botname} + a URL in any chat).\n\n"
            "② <b>Subtitles</b>\n"
            "When the source has captions you can add them and pick how to deliver:\n"
            "• <b>Burned-in</b> — text baked into the picture (visible everywhere).\n"
            "• <b>Embedded</b> — a toggleable subtitle track.\n"
            "• <b>.srt</b> — a separate subtitle file.\n"
            "Set the default with /settings. For videos longer than {burn_min} minutes, "
            "burned-in mode asks you to pick a window of up to {burn_min} minutes.\n\n"
            "③ <b>Playlists &amp; galleries</b>\n"
            "/playlist &lt;url&gt; downloads up to {playlist} items. Instagram/TikTok "
            "carousels are sent as media groups.\n\n"
            "④ <b>Spotify &amp; podcasts</b>\n"
            "A Spotify track is matched to a downloadable copy (Spotify audio itself is "
            "DRM-protected and never downloaded). Podcast episodes are fetched from the "
            "publisher's feed when available.\n\n"
            "⑤ <b>Big files</b>\n"
            "Files up to {upload_mb} MB are sent directly. Larger ones come back as a "
            "temporary download link (valid {link_hours} h).\n\n"
            "⚠️ <b>Limits</b>\n"
            "• Length: up to {max_min} minutes per video.\n"
            "• One download at a time; {cooldown} s between requests; {daily} per day.\n"
            "• Playlists: max {playlist} items.\n"
            "• Burned-in subtitles on long videos: max {burn_min} minutes.\n\n"
            "💡 <b>Tips</b>\n"
            "• YouTube may ask to \"confirm you're not a bot\" from datacenter IPs.\n"
            "• Some sites need a login (private/age-restricted posts may fail).\n"
            "• Available qualities and subtitles depend on the source.\n"
            "• Links to private/internal addresses are blocked for safety.\n\n"
            "❕ Respect copyright and each site's terms of service."
        ),
        "button_tutorial": "📖 Tutorial",
        "button_language": "🌐 Language",
        "button_settings": "⚙️ Settings",
        "choose_language": "Please choose your language:",
        "language_set": "Language set to {lang}.",
        "unknown_command": "Unknown command. Use /help to see what I can do.",
        "url_fetching": "⏳ Fetching info…",
        "url_invalid": "That doesn't look like a valid http(s) URL. Please send a link.",
        "url_blocked": "This URL points to a private or reserved address and is not allowed for security reasons.",
        "url_only_http": "Only http:// and https:// links are supported.",
        "url_multiple": "I found {count} links. Processing the first one.",
        "choose_format": (
            "📹 <b>{title}</b>\n"
            "⏱ {duration} · 👤 {uploader}\n\n"
            "Pick a format:"
        ),
        "choose_format_no_meta": "Pick a format:",
        "no_formats": "No downloadable formats found for this URL.",
        "duration_too_long": "This video is {minutes} minutes long, which exceeds the maximum of {max} minutes.",
        "processing_playlist": "Playlist mode: downloading up to {count} items.",
        "playlist_too_long": "Playlist is too long; downloading the first {count} items.",
        "downloading": "⬇️ Downloading…",
        "downloading_position": "⬇️ Downloading… (queue position: {pos})",
        "progress": "⬇️ Downloading… {pct}%",
        "preparing_upload": "📦 Preparing upload…",
        "link_ready": "✅ This file is larger than the {limit} MB upload limit.\n\n📥 Download link (valid for {hours} h):\n{url}",
        "link_unavailable": "❌ This file is too large to send ({limit} MB max) and download links are not configured.",
        "link_failed": "❌ Could not create a download link. Please try again later.",
        "link_quota_user": "❌ You already have {max} active download links. Wait for one to expire.",
        "link_quota_total": "❌ The server's link storage is full. Try again later.",
        "link_marker": " · link",
        "disk_low": "❌ The server is low on free disk space. Try again later.",
        "thumb_sent": "🖼 Here is the cover image.",
        "error_info": "❌ Could not fetch info: {error}",
        "error_download": "❌ Download failed: {error}",
        "error_upload": "❌ Upload failed: {error}",
        "error_cache_miss": "The cached file is no longer available, downloading again.",
        "youtube_signin": (
            "⚠️ YouTube is asking to \"confirm you're not a bot\". "
            "This usually happens on datacenter IPs. See the README section "
            "\"Cookies / proxy for YouTube\" and configure a cookies file or proxy."
        ),
        "rate_limited": "⏳ Please wait {seconds}s between requests.",
        "daily_limit": "❌ Daily download limit of {limit} reached. It resets at midnight (UTC).",
        "banned": "You are not allowed to use this bot.",
        "queue_position": "You are in position {pos} in the queue.",
        "cancel": "Cancelled.",
        "cancel_no_active": "Nothing to cancel.",
        "not_allowed": "⛔ You are not an admin.",
        "stats": (
            "📊 <b>Stats</b>\n"
            "Users: {total} (24h: {active24}, 7d: {active7})\n"
            "New users: {new24} today, {new7} this week\n"
            "Downloads: {today} today, {total_downloads} total\n"
            "Thumbnails: {thumbnails}\n"
            "Cache hit rate: {cache_hit}%\n"
            "Top sites: {sites}\n"
            "Failures: {failures}\n"
            "Temp dir size: {tmp_size}"
        ),
        "stats_empty": "No data yet.",
        "users_title": (
            "👥 <b>Users</b> — {total} total "
            "(24h: {active24}, 7d: {active7}, new today: {new24})\n"
            "Showing {count} of most recent (limit {limit}):"
        ),
        "users_empty": "No users yet.",
        "cleanup_running": "🧹 Cleaning temp directory…",
        "cleanup_done": (
            "🧹 <b>Cleanup done</b>\n"
            "Removed {removed} entries, freed {freed}.\n"
            "Temp dir: {temp_before} → {temp_now}\n"
            "Free disk: {free_before} → {free_now}"
        ),
        "cleanup_empty": (
            "🧹 <b>Nothing to clean</b>\n"
            "Temp dir is already tidy ({temp_now} in use).\n"
            "Free disk: {free_now}"
        ),
        "broadcast_running": "Broadcasting…",
        "broadcast_done": "Broadcast finished: {ok} sent, {fail} failed, {blocked} blocked.",
        "broadcast_no_text": "Usage: /broadcast &lt;text&gt;",
        "ban_done": "User {user_id} banned.",
        "unban_done": "User {user_id} un-banned.",
        "ytdlp_updated": "yt-dlp updated: {old} → {new}.",
        "ytdlp_up_to_date": "yt-dlp is up to date ({version}).",
        "ytdlp_update_failed": "yt-dlp update failed: {error}",
        "health": (
            "🩺 <b>Health</b>\n"
            "yt-dlp: {ytdlp}\n"
            "ffmpeg: {ffmpeg}\n"
            "Free disk: {disk}\n"
            "Queue: {queue_len} queued / {active} active\n"
            "Mode: {mode}\n"
            "Upload limit: {limit} MB\n"
            "Bot API: {api}\n"
            "Data dir: {data_dir}\n"
            "Proxy: {proxy}\n"
            "Cookies: {cookies}\n"
            "JS runtime: {js_runtime}\n"
            "curl_cffi: {curl_cffi}\n"
            "IP family: {ip_family}\n"
            "Spotify: {spotify}\n"
            "Podcasts: {podcasts}\n"
            "Extra args: {extra_args}\n"
            "Egress: {egress}\n"
            "Proxy exit: {proxy_exit}"
        ),
        "inline_deeplink_title": "Download {title}",
        "inline_deeplink_desc": "Tap here to open the bot and choose a format.",
        "inline_no_results": "Send a link to download.",
        "deeplink_expired": "That link expired. Send me the URL again.",
        "deeplink_start": "Sending you download options.",
        "cache_immediate": "⚡ From cache, resending…",
        "media_group": "Sending {count} media…",
        "button_audio_mp3": "Audio (MP3)",
        "button_audio_m4a": "Audio (M4A)",
        "button_thumbnail": "Thumbnail",
        "button_cancel": "Cancel",
        "button_best": "Best",
        "button_try_another": "🎲 Try another match",
        "spotify_no_more_matches": "❌ No other close match found.",
        "active_job": "You already have an active download. Please wait until it finishes.",
        "spotify_disabled": (
            "🎵 Spotify links can be matched to a downloadable copy, but this feature is "
            "currently disabled. Spotify audio itself is DRM-protected and never downloaded."
        ),
        "spotify_reading": "🎵 Reading Spotify metadata…",
        "spotify_searching": "🔎 Finding this track on a downloadable source…",
        "spotify_bad_link": "❌ I couldn't understand that Spotify link.",
        "spotify_no_metadata": "❌ Could not read metadata for this Spotify item.",
        "spotify_no_match": "❌ I couldn't find a matching downloadable copy of this track.",
        "spotify_pick": "🎵 <b>{title}</b>\n👤 {artist}\n\nFound on {source}. Pick an audio format:",
        "spotify_collections_disabled": "❌ Album and playlist links are disabled. Send a single track link.",
        "spotify_collection_needs_api": (
            "❌ Expanding albums or playlists needs Spotify API credentials "
            "(SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)."
        ),
        "spotify_collection_found": "🎵 This collection has {count} items; processing up to {max}.",
        "spotify_collection_done": "✅ Done: {ok} track(s) sent, {fail} failed.",
        "spotify_match_source": "🔗 Matched on {source}.",
        "podcast_disabled": (
            "🎙️ Podcast episodes can be fetched from the publisher's own feed, but this "
            "feature is currently disabled. Spotify audio itself is DRM-protected and never downloaded."
        ),
        "podcast_show_link": "🎙️ That's a show link. Send a link to a single episode.",
        "podcast_reading": "🎙️ Reading episode metadata…",
        "podcast_searching": "🔎 Locating the publisher's audio for this episode…",
        "podcast_downloading": "⬇️ Downloading the episode from the publisher…",
        "podcast_no_metadata": "❌ Could not read metadata for this episode.",
        "podcast_no_match": (
            "❌ I couldn't find this episode's audio outside Spotify. It may be a "
            "Spotify-exclusive. Spotify audio itself is never downloaded."
        ),
        "podcast_too_long": "❌ This episode is longer than {max} minutes, which is the limit.",
        "podcast_too_large_link": (
            "⚠️ This episode's audio is larger than the {max} MB the bot will fetch, so it was "
            "not downloaded. You can get it from the publisher here:\n{url}"
        ),
        "podcast_download_failed": "❌ Downloading the episode from the publisher failed.",
        "podcast_fallback_pick": (
            "🎙️ <b>{title}</b>\n👤 {show}\n\n"
            "⚠️ No publisher copy was found, so this is a matched copy from {source} — it may "
            "differ from the episode. Pick an audio format:"
        ),
        "expired_selection": "This selection expired. Send the link again.",
        "sending_from_cache": "⚡ Resending from cache…",
        "inline_article_title": "Open in bot to download",
        "inline_article_desc": "Tap to open @{botname} and choose a format.",
        "choose_subtitle": "💬 <b>{title}</b>\n\nWhich subtitles should be added?",
        "button_no_subtitles": "🚫 No subtitles",
        "button_more": "More…",
        "subtitle_auto": " (auto)",
        "subtitle_auto_translated": " (auto-translated)",
        "choose_subtitle_mode": "💬 <b>{language}</b>\n\nHow should the subtitles be delivered?",
        "button_mode_burn": "🔥 Burned-in",
        "button_mode_embed": "📎 Embedded track",
        "button_mode_srt": "📄 .srt file",
        "subtitle_mode_hint": "Burned-in is the only mode that shows subtitles inside Telegram.",
        "subtitle_burn_too_long": (
            "This video is longer than {max} minutes, so the subtitles were embedded as a "
            "track instead of burned in."
        ),
        "subtitle_failed": "⚠️ The subtitles could not be downloaded; sending the video without them.",
        "subtitle_window_prompt": (
            "🕒 This video is {duration} long — a burn-in is limited to {max} min.\n"
            "Window to burn: <b>{start}</b> → <b>{end}</b>\n\n"
            "Move the start, then confirm."
        ),
        "button_seg_minus15": "⏪ 15 min",
        "button_seg_minus5": "◀️ 5 min",
        "button_seg_plus5": "5 min ▶️",
        "button_seg_plus15": "15 min ⏩",
        "button_seg_burn": "🔥 Burn this window",
        "settings_title": "⚙️ <b>Settings</b>\n\nDefault subtitle mode: {mode}",
        "settings_prompt": "Choose the default subtitle mode:",
        "settings_saved": "Default subtitle mode set to {mode}.",
    },
    DE: {
        "start": (
            "Hallo {name}! 👋\n\n"
            "Schick mir einen Link und ich lade das <b>Video</b>, die <b>Audiodatei</b> "
            "oder das <b>Cover-Bild</b> herunter — von YouTube, Instagram, TikTok, "
            "X/Twitter, Facebook, Reddit, Vimeo, Pinterest, SoundCloud und vielen "
            "weiteren Seiten.\n\n"
            "Ich kann auch <b>Untertitel</b> hinzufügen und <b>Playlists</b> laden.\n\n"
            "Neu hier? Tippe auf <b>📖 Anleitung</b>, um alles zu sehen, was ich kann, "
            "und wo meine Grenzen liegen.\n\n"
            "❕ Hinweis: Du bist dafür verantwortlich, das Urheberrecht der Inhalte und die "
            "Nutzungsbedingungen der jeweiligen Seite zu respektieren."
        ),
        "help": (
            "So benutzt du mich:\n\n"
            "• Schick mir eine URL und ich frage nach der gewünschten Qualität.\n"
            "• Wähle <b>Video</b>, <b>Audio</b> oder <b>Cover-Bild</b>.\n"
            "• <b>/playlist</b> &lt;url&gt; — lade bis zu {playlist} Elemente einer Playlist.\n"
            "• <b>/tutorial</b> — vollständige Anleitung: Funktionen und Grenzen.\n"
            "• <b>/language</b> — Sprache ändern.\n"
            "• <b>/settings</b> — festlegen, wie Untertitel bereitgestellt werden.\n"
            "• Inline-Modus: tippe @{botname} + eine URL in einen beliebigen Chat.\n\n"
            "Quellen: YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit, Vimeo, Pinterest, SoundCloud u. v. m."
        ),
        "tutorial": (
            "📖 <b>Anleitung — was ich kann</b>\n\n"
            "① <b>Link senden</b>\n"
            "Füge eine Video-/Audio-URL ein und wähle <b>Video</b> (Qualität wählen), "
            "<b>Audio</b> (MP3/M4A) oder <b>Cover</b>. Funktioniert im Chat und im "
            "Inline-Modus (@{botname} + URL in einem beliebigen Chat).\n\n"
            "② <b>Untertitel</b>\n"
            "Wenn Untertitel vorhanden sind, kannst du sie hinzufügen und die Ausgabe wählen:\n"
            "• <b>Eingebrannt</b> — Text fest im Bild (überall sichtbar).\n"
            "• <b>Eingebettet</b> — zuschaltbare Untertitelspur.\n"
            "• <b>.srt</b> — separate Untertiteldatei.\n"
            "Lege den Standard mit /settings fest. Bei Videos über {burn_min} Minuten "
            "fragt der eingebrannte Modus nach einem Ausschnitt von bis zu {burn_min} Minuten.\n\n"
            "③ <b>Playlists & Galerien</b>\n"
            "/playlist &lt;url&gt; lädt bis zu {playlist} Elemente. Instagram-/TikTok-"
            "Karussells kommen als Mediengruppe.\n\n"
            "④ <b>Spotify & Podcasts</b>\n"
            "Ein Spotify-Titel wird einer herunterladbaren Kopie zugeordnet (Spotify-Audio "
            "selbst ist DRM-geschützt und wird nie geladen). Podcast-Folgen werden, wenn "
            "möglich, vom Feed des Publishers geladen.\n\n"
            "⑤ <b>Große Dateien</b>\n"
            "Dateien bis {upload_mb} MB werden direkt gesendet. Größere kommen als zeitlich "
            "begrenzter Download-Link ({link_hours} h gültig).\n\n"
            "⚠️ <b>Grenzen</b>\n"
            "• Länge: bis zu {max_min} Minuten pro Video.\n"
            "• Ein Download gleichzeitig; {cooldown} s zwischen Anfragen; {daily} pro Tag.\n"
            "• Playlists: max. {playlist} Elemente.\n"
            "• Eingebrannte Untertitel bei langen Videos: max. {burn_min} Minuten.\n\n"
            "💡 <b>Tipps</b>\n"
            "• YouTube kann von Rechenzentrums-IPs aus „Bestätige, dass du kein Bot bist“ verlangen.\n"
            "• Manche Seiten brauchen einen Login (private/altersbeschränkte Beiträge scheitern ggf.).\n"
            "• Verfügbare Qualitäten und Untertitel hängen von der Quelle ab.\n"
            "• Links auf private/interne Adressen sind aus Sicherheitsgründen blockiert.\n\n"
            "❕ Respektiere Urheberrecht und Nutzungsbedingungen der jeweiligen Seite."
        ),
        "button_tutorial": "📖 Anleitung",
        "button_language": "🌐 Sprache",
        "button_settings": "⚙️ Einstellungen",
        "choose_language": "Bitte wähle deine Sprache:",
        "language_set": "Sprache auf {lang} gesetzt.",
        "unknown_command": "Unbekannter Befehl. Nutze /help, um zu sehen, was ich kann.",
        "url_fetching": "⏳ Informationen werden geladen…",
        "url_invalid": "Das sieht nicht nach einer gültigen http(s)-URL aus. Sende bitte einen Link.",
        "url_blocked": "Diese URL zeigt auf eine private oder reservierte Adresse und ist aus Sicherheitsgründen nicht erlaubt.",
        "url_only_http": "Nur http://- und https://-Links werden unterstützt.",
        "url_multiple": "Ich habe {count} Links gefunden. Der erste wird verarbeitet.",
        "choose_format": (
            "📹 <b>{title}</b>\n"
            "⏱ {duration} · 👤 {uploader}\n\n"
            "Wähle ein Format:"
        ),
        "choose_format_no_meta": "Wähle ein Format:",
        "no_formats": "Für diese URL wurden keine herunterladbaren Formate gefunden.",
        "duration_too_long": "Dieses Video ist {minutes} Minuten lang, das Maximum liegt bei {max} Minuten.",
        "processing_playlist": "Playlist-Modus: Es werden bis zu {count} Elemente heruntergeladen.",
        "playlist_too_long": "Die Playlist ist zu lang; es werden die ersten {count} Elemente heruntergeladen.",
        "downloading": "⬇️ Wird heruntergeladen…",
        "downloading_position": "⬇️ Wird heruntergeladen… (Warteposition: {pos})",
        "progress": "⬇️ Wird heruntergeladen… {pct}%",
        "preparing_upload": "📦 Upload wird vorbereitet…",
        "link_ready": "✅ Diese Datei ist größer als das Upload-Limit von {limit} MB.\n\n📥 Download-Link (gültig für {hours} h):\n{url}",
        "link_unavailable": "❌ Diese Datei ist zum Senden zu groß (max. {limit} MB) und Download-Links sind nicht konfiguriert.",
        "link_failed": "❌ Download-Link konnte nicht erstellt werden. Bitte später erneut versuchen.",
        "link_quota_user": "❌ Du hast bereits {max} aktive Download-Links. Bitte warte, bis sie ablaufen.",
        "link_quota_total": "❌ Der Link-Speicher des Servers ist voll. Bitte später erneut versuchen.",
        "disk_low": "❌ Auf dem Server ist zu wenig Speicherplatz frei. Bitte später erneut versuchen.",
        "link_marker": " · Link",
        "thumb_sent": "🖼 Hier ist das Cover-Bild.",
        "error_info": "❌ Informationen konnten nicht geladen werden: {error}",
        "error_download": "❌ Download fehlgeschlagen: {error}",
        "error_upload": "❌ Upload fehlgeschlagen: {error}",
        "error_cache_miss": "Die gecachte Datei ist nicht mehr verfügbar, lade erneut herunter.",
        "youtube_signin": (
            "⚠️ YouTube möchte, dass du bestätigst, kein Bot zu sein. "
            "Das passiert häufig bei Rechenzentrums-IPs. Siehe README-Abschnitt "
            "\"Cookies/Proxy für YouTube\" und konfiguriere eine Cookies-Datei oder einen Proxy."
        ),
        "rate_limited": "⏳ Bitte warte {seconds}s zwischen Anfragen.",
        "daily_limit": "❌ Tägliches Downloadlimit von {limit} erreicht. Es wird um Mitternacht (UTC) zurückgesetzt.",
        "banned": "Du darfst diesen Bot nicht verwenden.",
        "queue_position": "Du bist auf Position {pos} in der Warteschlange.",
        "cancel": "Abgebrochen.",
        "cancel_no_active": "Es gibt nichts abzubrechen.",
        "not_allowed": "⛔ Du bist kein Admin.",
        "stats": (
            "📊 <b>Statistiken</b>\n"
            "Nutzer: {total} (24h: {active24}, 7d: {active7})\n"
            "Neue Nutzer: {new24} heute, {new7} diese Woche\n"
            "Downloads: {today} heute, {total_downloads} insgesamt\n"
            "Cover-Bilder: {thumbnails}\n"
            "Cache-Trefferquote: {cache_hit}%\n"
            "Top-Quellen: {sites}\n"
            "Fehler: {failures}\n"
            "Größe des Temp-Ordners: {tmp_size}"
        ),
        "stats_empty": "Noch keine Daten.",
        "users_title": (
            "👥 <b>Nutzer</b> — {total} insgesamt "
            "(24h: {active24}, 7d: {active7}, neu heute: {new24})\n"
            "Zeige {count} der neuesten (Limit {limit}):"
        ),
        "users_empty": "Noch keine Nutzer.",
        "cleanup_running": "🧹 Temp-Verzeichnis wird bereinigt…",
        "cleanup_done": (
            "🧹 <b>Bereinigung abgeschlossen</b>\n"
            "{removed} Einträge entfernt, {freed} freigegeben.\n"
            "Temp-Verzeichnis: {temp_before} → {temp_now}\n"
            "Freier Speicher: {free_before} → {free_now}"
        ),
        "cleanup_empty": (
            "🧹 <b>Nichts zu bereinigen</b>\n"
            "Das Temp-Verzeichnis ist bereits aufgeräumt ({temp_now} belegt).\n"
            "Freier Speicher: {free_now}"
        ),
        "broadcast_running": "Sende Nachricht an alle…",
        "broadcast_done": "Übertragung abgeschlossen: {ok} gesendet, {fail} fehlgeschlagen, {blocked} blockiert.",
        "broadcast_no_text": "Verwendung: /broadcast &lt;text&gt;",
        "ban_done": "Nutzer {user_id} wurde gesperrt.",
        "unban_done": "Sperrung für Nutzer {user_id} aufgehoben.",
        "ytdlp_updated": "yt-dlp aktualisiert: {old} → {new}.",
        "ytdlp_up_to_date": "yt-dlp ist aktuell ({version}).",
        "ytdlp_update_failed": "yt-dlp-Update fehlgeschlagen: {error}",
        "health": (
            "🩺 <b>Systemstatus</b>\n"
            "yt-dlp: {ytdlp}\n"
            "ffmpeg: {ffmpeg}\n"
            "Freier Speicher: {disk}\n"
            "Warteschlange: {queue_len} wartend / {active} aktiv\n"
            "Modus: {mode}\n"
            "Upload-Limit: {limit} MB\n"
            "Bot API: {api}\n"
            "Datenverzeichnis: {data_dir}\n"
            "Proxy: {proxy}\n"
            "Cookies: {cookies}\n"
            "JS runtime: {js_runtime}\n"
            "curl_cffi: {curl_cffi}\n"
            "IP family: {ip_family}\n"
            "Spotify: {spotify}\n"
            "Podcasts: {podcasts}\n"
            "Extra args: {extra_args}\n"
            "Egress: {egress}\n"
            "Proxy exit: {proxy_exit}"
        ),
        "inline_deeplink_title": "{title} herunterladen",
        "inline_deeplink_desc": "Tippe hier, um den Bot zu öffnen und ein Format zu wählen.",
        "inline_no_results": "Sende einen Link zum Herunterladen.",
        "deeplink_expired": "Dieser Link ist abgelaufen. Sende die URL erneut.",
        "deeplink_start": "Ich sende dir die Download-Optionen.",
        "cache_immediate": "⚡ Aus dem Cache, erneut gesendet…",
        "media_group": "Sende {count} Medien…",
        "button_audio_mp3": "Audio (MP3)",
        "button_audio_m4a": "Audio (M4A)",
        "button_thumbnail": "Cover-Bild",
        "button_cancel": "Abbrechen",
        "button_best": "Beste",
        "button_try_another": "🎲 Anderen Treffer versuchen",
        "spotify_no_more_matches": "❌ Kein weiterer passender Treffer gefunden.",
        "active_job": "Du hast bereits einen aktiven Download. Bitte warte, bis er fertig ist.",
        "spotify_disabled": (
            "🎵 Spotify-Links können einer herunterladbaren Version zugeordnet werden, aber "
            "diese Funktion ist derzeit deaktiviert. Spotify-Audio selbst ist DRM-geschützt "
            "und wird nie heruntergeladen."
        ),
        "spotify_reading": "🎵 Spotify-Metadaten werden gelesen…",
        "spotify_searching": "🔎 Dieser Titel wird auf einer herunterladbaren Quelle gesucht…",
        "spotify_bad_link": "❌ Dieser Spotify-Link konnte nicht verstanden werden.",
        "spotify_no_metadata": "❌ Metadaten für dieses Spotify-Element konnten nicht gelesen werden.",
        "spotify_no_match": "❌ Es wurde keine passende herunterladbare Version dieses Titels gefunden.",
        "spotify_pick": "🎵 <b>{title}</b>\n👤 {artist}\n\nGefunden auf {source}. Wähle ein Audioformat:",
        "spotify_collections_disabled": "❌ Album- und Playlist-Links sind deaktiviert. Sende einen einzelnen Titel-Link.",
        "spotify_collection_needs_api": (
            "❌ Zum Auflösen von Alben oder Playlists werden Spotify-API-Zugangsdaten benötigt "
            "(SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)."
        ),
        "spotify_collection_found": "🎵 Diese Sammlung hat {count} Elemente; es werden bis zu {max} verarbeitet.",
        "spotify_collection_done": "✅ Fertig: {ok} Titel gesendet, {fail} fehlgeschlagen.",
        "spotify_match_source": "🔗 Zugeordnet auf {source}.",
        "podcast_disabled": (
            "🎙️ Podcast-Folgen können aus dem Feed des Herausgebers geladen werden, aber "
            "diese Funktion ist derzeit deaktiviert. Spotify-Audio selbst ist DRM-geschützt "
            "und wird nie heruntergeladen."
        ),
        "podcast_show_link": "🎙️ Das ist ein Show-Link. Sende einen Link zu einer einzelnen Folge.",
        "podcast_reading": "🎙️ Folgen-Metadaten werden gelesen…",
        "podcast_searching": "🔎 Die Audioquelle des Herausgebers für diese Folge wird gesucht…",
        "podcast_downloading": "⬇️ Die Folge wird vom Herausgeber heruntergeladen…",
        "podcast_no_metadata": "❌ Metadaten für diese Folge konnten nicht gelesen werden.",
        "podcast_no_match": (
            "❌ Die Audioquelle dieser Folge wurde außerhalb von Spotify nicht gefunden. "
            "Möglicherweise ist sie Spotify-exklusiv. Spotify-Audio wird nie heruntergeladen."
        ),
        "podcast_too_long": "❌ Diese Folge ist länger als {max} Minuten (Limit).",
        "podcast_too_large_link": (
            "⚠️ Das Audio dieser Folge ist größer als die {max} MB, die der Bot lädt; es wurde "
            "deshalb nicht heruntergeladen. Du kannst es hier direkt beim Anbieter holen:\n{url}"
        ),
        "podcast_download_failed": "❌ Das Herunterladen der Folge vom Herausgeber ist fehlgeschlagen.",
        "podcast_fallback_pick": (
            "🎙️ <b>{title}</b>\n👤 {show}\n\n"
            "⚠️ Es wurde keine Herausgeber-Version gefunden; dies ist ein zugeordneter Treffer "
            "von {source} — er kann von der Folge abweichen. Wähle ein Audioformat:"
        ),
        "expired_selection": "Diese Auswahl ist abgelaufen. Sende den Link erneut.",
        "sending_from_cache": "⚡ Erneut aus dem Cache gesendet…",
        "inline_article_title": "Im Bot öffnen und herunterladen",
        "inline_article_desc": "Tippen, um @{botname} zu öffnen und ein Format zu wählen.",
        "choose_subtitle": "💬 <b>{title}</b>\n\nWelche Untertitel sollen hinzugefügt werden?",
        "button_no_subtitles": "🚫 Keine Untertitel",
        "button_more": "Mehr…",
        "subtitle_auto": " (auto)",
        "subtitle_auto_translated": " (automatisch übersetzt)",
        "choose_subtitle_mode": "💬 <b>{language}</b>\n\nWie sollen die Untertitel bereitgestellt werden?",
        "button_mode_burn": "🔥 Eingebrannt",
        "button_mode_embed": "📎 Eingebettete Spur",
        "button_mode_srt": "📄 .srt-Datei",
        "subtitle_mode_hint": "Nur „Eingebrannt“ zeigt Untertitel direkt in Telegram an.",
        "subtitle_burn_too_long": (
            "Dieses Video ist länger als {max} Minuten, daher wurden die Untertitel als Spur "
            "eingebettet statt eingebrannt."
        ),
        "subtitle_failed": "⚠️ Die Untertitel konnten nicht geladen werden; das Video wird ohne sie gesendet.",
        "subtitle_window_prompt": (
            "🕒 Dieses Video ist {duration} lang — ein Einbrennen ist auf {max} Min. begrenzt.\n"
            "Fenster zum Einbrennen: <b>{start}</b> → <b>{end}</b>\n\n"
            "Verschiebe den Start und bestätige."
        ),
        "button_seg_minus15": "⏪ 15 Min.",
        "button_seg_minus5": "◀️ 5 Min.",
        "button_seg_plus5": "5 Min. ▶️",
        "button_seg_plus15": "15 Min. ⏩",
        "button_seg_burn": "🔥 Dieses Fenster einbrennen",
        "settings_title": "⚙️ <b>Einstellungen</b>\n\nStandard-Untertitelmodus: {mode}",
        "settings_prompt": "Wähle den Standard-Untertitelmodus:",
        "settings_saved": "Standard-Untertitelmodus auf {mode} gesetzt.",
    },
    FA: {
        "start": (
            "سلام {name}! 👋\n\n"
            "یک لینک برایم بفرست تا <b>ویدیو</b>، <b>صدا</b> یا <b>تصویر جلد</b> را "
            "دانلود کنم — از یوتیوب، اینستاگرام، تیک‌تاک، ایکس/توییتر، فیس‌بوک، "
            "ردیت، ویمیو، پینترست، ساندکلود و بسیاری سایت‌های دیگر.\n\n"
            "می‌توانم <b>زیرنویس</b> هم اضافه کنم و <b>پلی‌لیست</b> دانلود کنم.\n\n"
            "تازه‌وارد شده‌اید؟ روی <b>📖 آموزش</b> بزنید تا همهٔ امکانات و محدودیت‌هایم را ببینید.\n\n"
            "❕ یادآوری: شما مسئول رعایت کپی‌رایت محتوا و شرایط استفاده از هر سایت هستید."
        ),
        "help": (
            "نحوه استفاده:\n\n"
            "• یک لینک بفرستید تا کیفیت موردنظر را بپرسم.\n"
            "• <b>ویدیو</b>، <b>صدا</b> یا <b>جلد</b> را انتخاب کنید.\n"
            "• <b>/playlist</b> &lt;url&gt; — دانلود تا {playlist} مورد از یک پلی‌لیست.\n"
            "• <b>/tutorial</b> — راهنمای کامل: امکانات و محدودیت‌ها.\n"
            "• <b>/language</b> — تغییر زبان.\n"
            "• <b>/settings</b> — انتخاب روش ارائه زیرنویس.\n"
            "• حالت اینلاین: در هر چتی @{botname} و سپس یک URL بنویسید.\n\n"
            "منابع: یوتیوب، اینستاگرام، تیک‌تاک، ایکس/توییتر، فیس‌بوک، ردیت، ویمیو، پینترست، ساندکلود و بیشتر."
        ),
        "tutorial": (
            "📖 <b>آموزش — چه کارهایی می‌توانم انجام دهم</b>\n\n"
            "① <b>ارسال لینک</b>\n"
            "یک URL بفرستید و <b>ویدیو</b> (با انتخاب کیفیت)، <b>صدا</b> (MP3/M4A) یا "
            "<b>جلد</b> را انتخاب کنید. در چت و حالت اینلاین (@{botname} + URL در هر چت) کار می‌کند.\n\n"
            "② <b>زیرنویس</b>\n"
            "اگر ویدیو زیرنویس داشته باشد می‌توانید اضافه کنید و روش ارائه را انتخاب کنید:\n"
            "• <b>سوخته (Burned-in)</b> — متن روی تصویر حک می‌شود.\n"
            "• <b>جاسازی‌شده (Embedded)</b> — مسیر زیرنویس قابل روشن/خاموش کردن.\n"
            "• <b>.srt</b> — فایل زیرنویس جداگانه.\n"
            "حالت پیش‌فرض را با /settings تنظیم کنید. برای ویدیوهای بلندتر از {burn_min} دقیقه، "
            "حالت سوخته یک بازهٔ حداکثر {burn_min} دقیقه‌ای از شما می‌پرسد.\n\n"
            "③ <b>پلی‌لیست‌ها و گالری‌ها</b>\n"
            "/playlist &lt;url&gt; تا {playlist} مورد دانلود می‌کند. کاروسل‌های اینستاگرام/"
            "تیک‌تاک به‌صورت گروه رسانه ارسال می‌شوند.\n\n"
            "④ <b>اسپاتیفای و پادکست</b>\n"
            "آهنگ اسپاتیفای با یک نسخهٔ قابل‌دانلود تطبیق داده می‌شود (صدای خود اسپاتیفای "
            "با DRM محافظت می‌شود و هرگز دانلود نمی‌شود). قسمت‌های پادکست در صورت وجود از "
            "فید ناشر دریافت می‌شوند.\n\n"
            "⑤ <b>فایل‌های بزرگ</b>\n"
            "فایل‌های تا {upload_mb} مگابایت مستقیم ارسال می‌شوند. بزرگ‌ترها به‌صورت لینک "
            "دانلود موقت (اعتبار {link_hours} ساعت) ارائه می‌شوند.\n\n"
            "⚠️ <b>محدودیت‌ها</b>\n"
            "• طول: حداکثر {max_min} دقیقه برای هر ویدیو.\n"
            "• هر بار یک دانلود؛ {cooldown} ثانیه بین درخواست‌ها؛ {daily} در روز.\n"
            "• پلی‌لیست: حداکثر {playlist} مورد.\n"
            "• زیرنویس سوخته برای ویدیوهای بلند: حداکثر {burn_min} دقیقه.\n\n"
            "💡 <b>نکته‌ها</b>\n"
            "• یوتیوب ممکن است از IP سرورها بخواهد تأیید کنید که ربات نیستید.\n"
            "• برخی سایت‌ها نیاز به ورود دارند (پست‌های خصوصی/محدود ممکن است ناموفق باشند).\n"
            "• کیفیت‌ها و زیرنویس‌های موجود به منبع بستگی دارد.\n"
            "• لینک‌های آدرس‌های خصوصی/داخلی برای امنیت مسدود می‌شوند.\n\n"
            "❕ کپی‌رایت و شرایط استفادهٔ هر سایت را رعایت کنید."
        ),
        "button_tutorial": "📖 آموزش",
        "button_language": "🌐 زبان",
        "button_settings": "⚙️ تنظیمات",
        "choose_language": "لطفاً زبان خود را انتخاب کنید:",
        "language_set": "زبان به {lang} تغییر کرد.",
        "unknown_command": "دستور ناشناخته است. برای دیدن امکانات از /help استفاده کنید.",
        "url_fetching": "⏳ در حال دریافت اطلاعات…",
        "url_invalid": "این یک URL معتبر (http/https) نیست. لطفاً یک لینک بفرستید.",
        "url_blocked": "این URL به یک آدرس خصوصی یا رزرو شده اشاره دارد و به دلایل امنیتی مجاز نیست.",
        "url_only_http": "فقط لینک‌های http:// و https:// پشتیبانی می‌شوند.",
        "url_multiple": "{count} لینک پیدا شد. اولین مورد پردازش می‌شود.",
        "choose_format": (
            "📹 <b>{title}</b>\n"
            "⏱ {duration} · 👤 {uploader}\n\n"
            "فرمت موردنظر را انتخاب کنید:"
        ),
        "choose_format_no_meta": "فرمت موردنظر را انتخاب کنید:",
        "no_formats": "برای این URL فرمت قابل دانلودی پیدا نشد.",
        "duration_too_long": "این ویدیو {minutes} دقیقه است و از حداکثر {max} دقیقه بیشتر است.",
        "processing_playlist": "حالت پلی‌لیست: دانلود تا {count} مورد.",
        "playlist_too_long": "پلی‌لیست خیلی طولانی است؛ {count} مورد اول دانلود می‌شود.",
        "downloading": "⬇️ در حال دانلود…",
        "downloading_position": "⬇️ در حال دانلود… (وضعیت در صف: {pos})",
        "progress": "⬇️ در حال دانلود… {pct}٪",
        "preparing_upload": "📦 آماده‌سازی ارسال…",
        "link_ready": "✅ این فایل از محدودیت ارسال {limit} مگابایت بزرگ‌تر است.\n\n📥 لینک دانلود (معتبر برای {hours} ساعت):\n{url}",
        "link_unavailable": "❌ این فایل برای ارسال خیلی بزرگ است (حداکثر {limit} مگابایت) و لینک دانلود تنظیم نشده است.",
        "link_failed": "❌ ساخت لینک دانلود ممکن نشد. لطفاً بعداً دوباره تلاش کنید.",
        "link_quota_user": "❌ شما {max} لینک دانلود فعال دارید. لطفاً تا انقضای آن‌ها صبر کنید.",
        "link_quota_total": "❌ فضای ذخیره‌سازی لینک‌ها پر است. لطفاً بعداً دوباره تلاش کنید.",
        "disk_low": "❌ فضای دیسک سرور کم است. لطفاً بعداً دوباره تلاش کنید.",
        "link_marker": " · لینک",
        "thumb_sent": "🖼 این جلد تصویر است.",
        "error_info": "❌ دریافت اطلاعات ممکن نشد: {error}",
        "error_download": "❌ دانلود ناموفق بود: {error}",
        "error_upload": "❌ ارسال ناموفق بود: {error}",
        "error_cache_miss": "فایل کش‌شده دیگر در دسترس نیست؛ دوباره دانلود می‌کنم.",
        "youtube_signin": (
            "⚠️ یوتیوب می‌خواهد تأیید کنید ربات نیستید. "
            "این معمولاً در IPهای مرکز داده رخ می‌دهد. قسمت «کوکی/پروکسی برای یوتیوب» در README را ببینید "
            "و یک فایل کوکی یا پروکسی تنظیم کنید."
        ),
        "rate_limited": "⏳ لطفاً {seconds} ثانیه بین درخواست‌ها صبر کنید.",
        "daily_limit": "❌ سقف روزانه {limit} دانلود رسیده شد. این محدودیت نیمه‌شب (UTC) ریست می‌شود.",
        "banned": "شما اجازه استفاده از این ربات را ندارید.",
        "queue_position": "شما در جایگاه {pos} صف هستید.",
        "cancel": "لغو شد.",
        "cancel_no_active": "چیزی برای لغو وجود ندارد.",
        "not_allowed": "⛔ شما ادمین نیستید.",
        "stats": (
            "📊 <b>آمار</b>\n"
            "کاربران: {total} (۲۴ ساعت: {active24}، ۷ روز: {active7})\n"
            "کاربران جدید: {new24} امروز، {new7} این هفته\n"
            "دانلودها: {today} امروز، {total_downloads} کل\n"
            "جلدها: {thumbnails}\n"
            "نرخ کش: {cache_hit}٪\n"
            "سایت‌های برتر: {sites}\n"
            "خطاها: {failures}\n"
            "حجم پوشه موقت: {tmp_size}"
        ),
        "stats_empty": "هنوز داده‌ای وجود ندارد.",
        "users_title": (
            "👥 <b>کاربران</b> — {total} کل "
            "(۲۴ ساعت: {active24}، ۷ روز: {active7}، جدید امروز: {new24})\n"
            "نمایش {count} مورد از جدیدترین‌ها (حد {limit}):"
        ),
        "users_empty": "هنوز کاربری وجود ندارد.",
        "cleanup_running": "🧹 در حال پاک‌سازی پوشه موقت…",
        "cleanup_done": (
            "🧹 <b>پاک‌سازی انجام شد</b>\n"
            "{removed} مورد حذف شد، {freed} آزاد شد.\n"
            "پوشه موقت: {temp_before} → {temp_now}\n"
            "فضای آزاد: {free_before} → {free_now}"
        ),
        "cleanup_empty": (
            "🧹 <b>چیزی برای پاک‌سازی نیست</b>\n"
            "پوشه موقت از قبل مرتب است ({temp_now} در حال استفاده).\n"
            "فضای آزاد: {free_now}"
        ),
        "broadcast_running": "در حال ارسال همگانی…",
        "broadcast_done": "ارسال همگانی پایان یافت: {ok} ارسال، {fail} ناموفق، {blocked} مسدود.",
        "broadcast_no_text": "روش استفاده: /broadcast &lt;متن&gt;",
        "ban_done": "کاربر {user_id} مسدود شد.",
        "unban_done": "کاربر {user_id} از مسدودی خارج شد.",
        "ytdlp_updated": "yt-dlp به‌روزرسانی شد: {old} → {new}.",
        "ytdlp_up_to_date": "yt-dlp به‌روز است ({version}).",
        "ytdlp_update_failed": "به‌روزرسانی yt-dlp ناموفق بود: {error}",
        "health": (
            "🩺 <b>وضعیت سیستم</b>\n"
            "yt-dlp: {ytdlp}\n"
            "ffmpeg: {ffmpeg}\n"
            "فضای آزاد: {disk}\n"
            "صف: {queue_len} در انتظار / {active} فعال\n"
            "حالت: {mode}\n"
            "حد بارگذاری: {limit} مگابایت\n"
            "Bot API: {api}\n"
            "پوشه داده: {data_dir}\n"
            "پروکسی: {proxy}\n"
            "کوکی‌ها: {cookies}\n"
            "JS runtime: {js_runtime}\n"
            "curl_cffi: {curl_cffi}\n"
            "IP family: {ip_family}\n"
            "Spotify: {spotify}\n"
            "Podcasts: {podcasts}\n"
            "Extra args: {extra_args}\n"
            "Egress: {egress}\n"
            "Proxy exit: {proxy_exit}"
        ),
        "inline_deeplink_title": "دانلود {title}",
        "inline_deeplink_desc": "برای باز کردن ربات و انتخاب فرمت اینجا را لمس کنید.",
        "inline_no_results": "برای دانلود یک لینک بفرستید.",
        "deeplink_expired": "این لینک منقضی شده است. دوباره URL را بفرستید.",
        "deeplink_start": "گزینه‌های دانلود را برایتان می‌فرستم.",
        "cache_immediate": "⚡ از کش، ارسال مجدد…",
        "media_group": "در حال ارسال {count} رسانه…",
        "button_audio_mp3": "صوت (MP3)",
        "button_audio_m4a": "صوت (M4A)",
        "button_thumbnail": "جلد",
        "button_cancel": "لغو",
        "button_best": "بهترین",
        "button_try_another": "🎲 تطبیق دیگر را امتحان کن",
        "spotify_no_more_matches": "❌ تطبیق نزدیک دیگری پیدا نشد.",
        "active_job": "شما یک دانلود فعال دارید. لطفاً تا پایان آن صبر کنید.",
        "spotify_disabled": (
            "🎵 لینک‌های اسپاتیفای می‌توانند به یک نسخه قابل دانلود تطبیق داده شوند، اما این "
            "قابلیت فعلاً غیرفعال است. صدای خود اسپاتیفای با DRM محافظت می‌شود و هرگز دانلود نمی‌شود."
        ),
        "spotify_reading": "🎵 در حال خواندن اطلاعات اسپاتیفای…",
        "spotify_searching": "🔎 در حال یافتن این آهنگ در یک منبع قابل دانلود…",
        "spotify_bad_link": "❌ لینک اسپاتیفای قابل تشخیص نبود.",
        "spotify_no_metadata": "❌ خواندن اطلاعات این مورد اسپاتیفای ممکن نشد.",
        "spotify_no_match": "❌ نسخه قابل دانلود منطبقی برای این آهنگ پیدا نشد.",
        "spotify_pick": "🎵 <b>{title}</b>\n👤 {artist}\n\nدر {source} پیدا شد. یک فرمت صوتی انتخاب کنید:",
        "spotify_collections_disabled": "❌ لینک آلبوم و پلی‌لیست غیرفعال است. یک لینک تک‌آهنگ بفرستید.",
        "spotify_collection_needs_api": (
            "❌ برای بازکردن آلبوم‌ها یا پلی‌لیست‌ها به اطلاعات API اسپاتیفای نیاز است "
            "(SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)."
        ),
        "spotify_collection_found": "🎵 این مجموعه {count} مورد دارد؛ تا {max} مورد پردازش می‌شود.",
        "spotify_collection_done": "✅ انجام شد: {ok} آهنگ ارسال شد، {fail} ناموفق.",
        "spotify_match_source": "🔗 در {source} تطبیق داده شد.",
        "podcast_disabled": (
            "🎙️ قسمت‌های پادکست را می‌توان از فید خود ناشر دریافت کرد، اما این ویژگی "
            "در حال حاضر غیرفعال است. صدای خود اسپاتیفای محافظت‌شده (DRM) است و هرگز دانلود نمی‌شود."
        ),
        "podcast_show_link": "🎙️ این لینک یک برنامه است. لینک یک قسمت تکی را بفرستید.",
        "podcast_reading": "🎙️ در حال خواندن اطلاعات قسمت…",
        "podcast_searching": "🔎 در حال یافتن صدای ناشر برای این قسمت…",
        "podcast_downloading": "⬇️ در حال دانلود قسمت از ناشر…",
        "podcast_no_metadata": "❌ اطلاعات این قسمت خوانده نشد.",
        "podcast_no_match": (
            "❌ صدای این قسمت در خارج از اسپاتیفای پیدا نشد. ممکن است اختصاصی اسپاتیفای باشد. "
            "صدای اسپاتیفای هرگز دانلود نمی‌شود."
        ),
        "podcast_too_long": "❌ این قسمت طولانی‌تر از {max} دقیقه است (حد مجاز).",
        "podcast_too_large_link": (
            "⚠️ صدای این قسمت از {max} مگابایتی که ربات دانلود می‌کند بزرگ‌تر است، بنابراین "
            "دانلود نشد. می‌توانید آن را از خود ناشر بگیرید:\n{url}"
        ),
        "podcast_download_failed": "❌ دانلود قسمت از ناشر ناموفق بود.",
        "podcast_fallback_pick": (
            "🎙️ <b>{title}</b>\n👤 {show}\n\n"
            "⚠️ نسخهٔ ناشر پیدا نشد؛ این یک نسخهٔ منطبق از {source} است و ممکن است با قسمت متفاوت باشد. "
            "یک فرمت صوتی انتخاب کنید:"
        ),
        "expired_selection": "این انتخاب منقضی شده است. دوباره لینک را بفرستید.",
        "sending_from_cache": "⚡ ارسال مجدد از کش…",
        "inline_article_title": "برای دانلود در ربات باز کنید",
        "inline_article_desc": "برای باز کردن ربات و انتخاب فرمت اینجا را لمس کنید.",
        "choose_subtitle": "💬 <b>{title}</b>\n\nکدام زیرنویس اضافه شود؟",
        "button_no_subtitles": "🚫 بدون زیرنویس",
        "button_more": "بیشتر…",
        "subtitle_auto": " (خودکار)",
        "subtitle_auto_translated": " (ترجمهٔ خودکار)",
        "choose_subtitle_mode": "💬 <b>{language}</b>\n\nزیرنویس چگونه ارائه شود؟",
        "button_mode_burn": "🔥 حک‌شده",
        "button_mode_embed": "📎 مسیر جاسازی‌شده",
        "button_mode_srt": "📄 فایل .srt",
        "subtitle_mode_hint": "فقط حالت «حک‌شده» زیرنویس را داخل تلگرام نشان می‌دهد.",
        "subtitle_burn_too_long": (
            "این ویدیو از {max} دقیقه طولانی‌تر است، بنابراین زیرنویس به‌جای حک‌شدن به‌صورت مسیر جاسازی شد."
        ),
        "subtitle_failed": "⚠️ زیرنویس دانلود نشد؛ ویدیو بدون آن ارسال می‌شود.",
        "subtitle_window_prompt": (
            "🕒 این ویدیو {duration} است — حک کردن به {max} دقیقه محدود است.\n"
            "بازهٔ حک: <b>{start}</b> → <b>{end}</b>\n\n"
            "شروع را جابه‌جا کنید و تأیید کنید."
        ),
        "button_seg_minus15": "⏪ ۱۵ دقیقه",
        "button_seg_minus5": "◀️ ۵ دقیقه",
        "button_seg_plus5": "۵ دقیقه ▶️",
        "button_seg_plus15": "۱۵ دقیقه ⏩",
        "button_seg_burn": "🔥 همین بازه حک شود",
        "settings_title": "⚙️ <b>تنظیمات</b>\n\nحالت پیش‌فرض زیرنویس: {mode}",
        "settings_prompt": "حالت پیش‌فرض زیرنویس را انتخاب کنید:",
        "settings_saved": "حالت پیش‌فرض زیرنویس به {mode} تغییر کرد.",
    },
}

# Keep key sets in sync by construction — verified by a unit test.
assert set(_STRINGS[EN]) == set(_STRINGS[DE]) == set(_STRINGS[FA])


def language_code_to_code(language_code: str | None, default_language: str = "en") -> str:
    """Map a Telegram ``language_code`` to one of our supported languages."""
    if not language_code:
        return default_language
    code = language_code.lower().replace("_", "-")
    base = code.split("-")[0]
    if base in SUPPORTED_LANGUAGES:
        return base
    return default_language


def get_text(lang_code: str, key: str, default_language: str | None = None, **kwargs: Any) -> str:
    """Return a translated string, formatting it with ``kwargs``.

    ``lang_code`` should be either a Telegram language_code or one of our
    supported language codes; it is resolved through
    :func:`language_code_to_code`.
    """
    resolved = language_code_to_code(lang_code, default_language or "en")
    if key not in _STRINGS[resolved]:
        if key not in _STRINGS[EN]:
            return key
        resolved = EN
    text = _STRINGS[resolved][key]
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
