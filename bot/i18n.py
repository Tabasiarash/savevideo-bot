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
            "I can download videos and audio from YouTube, Instagram, TikTok, X/Twitter, "
            "Facebook, Reddit, Vimeo, Pinterest and many other sites.\n\n"
            "Just send me a link.\n\n"
            "❕ Disclaimer: you are responsible for respecting the copyright of the "
            "content and the terms of service of the respective site."
        ),
        "help": (
            "How to use me:\n\n"
            "• Send me a video URL and I'll ask which quality you want.\n"
            "• Choose <b>Video</b>, <b>Audio</b> or <b>Thumbnail</b>.\n"
            "• <b>/playlist</b> &lt;url&gt; — download up to 10 items of a playlist.\n"
            "• <b>/language</b> — switch language.\n"
            "• Inline mode: type @{botname} + a URL in any chat.\n\n"
            "Sources: YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit, Vimeo, Pinterest, and more."
        ),
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
        "file_too_large_fallback": "⚠️ File is too large, trying a lower quality.",
        "reencoded": "⚠️ File is still too large, re-encoding to fit the limit ({size}).",
        "too_large_impossible": "❌ This file cannot be made to fit the {limit} MB upload limit. Try a shorter video.",
        "split_sending": "Sending part {part}/{total}…",
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
            "Downloads: {today} today, {total_downloads} total\n"
            "Thumbnails: {thumbnails}\n"
            "Cache hit rate: {cache_hit}%\n"
            "Top sites: {sites}\n"
            "Failures: {failures}\n"
            "Temp dir size: {tmp_size}"
        ),
        "stats_empty": "No data yet.",
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
            "Mode: {mode}"
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
        "active_job": "You already have an active download. Please wait until it finishes.",
        "expired_selection": "This selection expired. Send the link again.",
        "sending_from_cache": "⚡ Resending from cache…",
        "inline_article_title": "Open in bot to download",
        "inline_article_desc": "Tap to open @{botname} and choose a format.",
    },
    DE: {
        "start": (
            "Hallo {name}! 👋\n\n"
            "Ich lade Videos und Audiodateien von YouTube, Instagram, TikTok, X/Twitter, "
            "Facebook, Reddit, Vimeo, Pinterest und vielen weiteren Seiten herunter.\n\n"
            "Schick mir einfach einen Link.\n\n"
            "❕ Hinweis: Du bist dafür verantwortlich, das Urheberrecht der Inhalte und die "
            "Nutzungsbedingungen der jeweiligen Seite zu respektieren."
        ),
        "help": (
            "So benutzt du mich:\n\n"
            "• Schick mir eine Video-URL und ich frage nach der gewünschten Qualität.\n"
            "• Wähle <b>Video</b>, <b>Audio</b> oder <b>Cover-Bild</b>.\n"
            "• <b>/playlist</b> &lt;url&gt; — lade bis zu 10 Elemente einer Playlist.\n"
            "• <b>/language</b> — Sprache ändern.\n"
            "• Inline-Modus: tippe @{botname} + eine URL in einen beliebigen Chat.\n\n"
            "Quellen: YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit, Vimeo, Pinterest u. v. m."
        ),
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
        "file_too_large_fallback": "⚠️ Datei ist zu groß, versuche eine niedrigere Qualität.",
        "reencoded": "⚠️ Datei ist immer noch zu groß, sie wird neu codiert, um das Limit ({size}) einzuhalten.",
        "too_large_impossible": "❌ Diese Datei kann nicht auf das {limit}-MB-Uploadlimit passen. Versuche ein kürzeres Video.",
        "split_sending": "Sende Teil {part}/{total}…",
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
            "Downloads: {today} heute, {total_downloads} insgesamt\n"
            "Cover-Bilder: {thumbnails}\n"
            "Cache-Trefferquote: {cache_hit}%\n"
            "Top-Quellen: {sites}\n"
            "Fehler: {failures}\n"
            "Größe des Temp-Ordners: {tmp_size}"
        ),
        "stats_empty": "Noch keine Daten.",
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
            "Modus: {mode}"
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
        "active_job": "Du hast bereits einen aktiven Download. Bitte warte, bis er fertig ist.",
        "expired_selection": "Diese Auswahl ist abgelaufen. Sende den Link erneut.",
        "sending_from_cache": "⚡ Erneut aus dem Cache gesendet…",
        "inline_article_title": "Im Bot öffnen und herunterladen",
        "inline_article_desc": "Tippen, um @{botname} zu öffnen und ein Format zu wählen.",
    },
    FA: {
        "start": (
            "سلام {name}! 👋\n\n"
            "من ویدیو و صدا از یوتیوب، اینستاگرام، تیک‌تاک، ایکس/توییتر، فیس‌بوک، "
            "ردیت، ویمیو، پینترست و بسیاری از سایت‌های دیگر دانلود می‌کنم.\n\n"
            "فقط یک لینک برایم بفرستید.\n\n"
            "❕ یادآوری: شما مسئول رعایت کپی‌رایت محتوا و شرایط استفاده از هر سایت هستید."
        ),
        "help": (
            "نحوه استفاده:\n\n"
            "• یک لینک ویدیو بفرستید تا کیفیت موردنظر را بپرسم.\n"
            "• <b>ویدیو</b>، <b>صدا</b> یا <b>جلد</b> را انتخاب کنید.\n"
            "• <b>/playlist</b> &lt;url&gt; — دانلود تا ۱۰ مورد از یک پلی‌لیست.\n"
            "• <b>/language</b> — تغییر زبان.\n"
            "• حالت اینلاین: در هر چتی @{botname} و سپس یک URL بنویسید.\n\n"
            "منابع: یوتیوب، اینستاگرام، تیک‌تاک، ایکس/توییتر، فیس‌بوک، ردیت، ویمیو، پینترست و بیشتر."
        ),
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
        "file_too_large_fallback": "⚠️ فایل خیلی بزرگ است؛ کیفیت پایین‌تر را امتحان می‌کنم.",
        "reencoded": "⚠️ فایل هنوز خیلی بزرگ است؛ برای رسیدن به محدودیت ({size}) فشرده می‌شود.",
        "too_large_impossible": "❌ این فایل نمی‌تواند در محدودیت ارسال {limit} مگابایت جا بگیرد. ویدیوی کوتاه‌تری امتحان کنید.",
        "split_sending": "در حال ارسال بخش {part}/{total}…",
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
            "دانلودها: {today} امروز، {total_downloads} کل\n"
            "جلدها: {thumbnails}\n"
            "نرخ کش: {cache_hit}٪\n"
            "سایت‌های برتر: {sites}\n"
            "خطاها: {failures}\n"
            "حجم پوشه موقت: {tmp_size}"
        ),
        "stats_empty": "هنوز داده‌ای وجود ندارد.",
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
            "حالت: {mode}"
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
        "active_job": "شما یک دانلود فعال دارید. لطفاً تا پایان آن صبر کنید.",
        "expired_selection": "این انتخاب منقضی شده است. دوباره لینک را بفرستید.",
        "sending_from_cache": "⚡ ارسال مجدد از کش…",
        "inline_article_title": "برای دانلود در ربات باز کنید",
        "inline_article_desc": "برای باز کردن ربات و انتخاب فرمت اینجا را لمس کنید.",
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
