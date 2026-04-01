import re
import hashlib
import asyncio
import logging
import tempfile
from pathlib import Path

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8786211639:AAH9i6yTvG4WJcDGAMB9DkNxeO2VpCHQytU"

# Yandex Music session cookies for authenticated (full-length 320kbps) downloads.
# Refresh these when downloads stop working by copying cookies from browser DevTools.
YANDEX_COOKIES = {
    "yandex_login": "kasimov0501",
    "yandexuid": "2624206021759254873",
    "Session_id": "3:1775075272.5.0.1760863602007:O2fXUg:23fc.1.2:1|1395955742.0.2.3:1760863602|41:11813146.706645.IrHTfj5iFw26jFAQnleBcbXP8sQ",
    "sessionid2": "3:1766399127.5.0.1760863602007:O2fXUg:23fc.1.2:1|1395955742.0.2.3:1760863602|41:11527774.930919.fakesign0000000000000000000",
    "sessar": "1.1719225.CiAeRhJlNiYoT49cEZE6wiKWC1Cc_wk8DgiJl4nH662Pmg.vmEDQGhgkMJRHL9bBhqzJy5IJ4JhoAheJfuFg_c0yP8",
    "L": "STZ2XAUBfk1IVGNhWmhZXXVjZl5+TG1PBjQqMz0IGlYFeUs=.1760863602.1306561.325006.4ba025138118571d2b0fa2b3223346ec",
    "i": "cd2MezREWaGJJvI0ZPJuB+tKIhoFjW6WwGglwLxQKjHNCBrMIr4FAYIoN1DMbcG2sebR2Cz0k5Xd7oyhW/6IY8cPnw4=",
    "ys": "udn.cDprYXNpbW92MDUwMQ%3D%3D#c_chck.3590958915",
    "sso_status": "sso.passport.yandex.ru:synchronized",
    "pi": "ao9rMSgSBI9Vo4GMeyYBL+I2c7j5xia66Gc0f1q8o2c2/88/Q4M0jDL036tLhYHP3Dzdkl6CqCTMo7Shly6+MiJhsRo=",
}

TRACK_URL_RE = re.compile(r"music\.yandex\.(ru|com|uz|kz|by|ua)/album/(\d+)/track/(\d+)")
AUTH_DOMAIN = "uz"  # domain that works for authenticated downloads

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_session = requests.Session()
_session.cookies.update(YANDEX_COOKIES)
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "X-Yandex-Music-Client": "YandexMusicWebNext/1.0.0",
    "X-Yandex-Music-Multi-Auth-User-Id": "1395955742",
})


def get_track_info(track_id: str, album_id: str) -> dict:
    track_url = f"https://music.yandex.{AUTH_DOMAIN}/album/{album_id}/track/{track_id}"
    r = _session.get(
        f"https://music.yandex.{AUTH_DOMAIN}/handlers/track.jsx",
        params={"track": f"{track_id}:{album_id}", "lang": "en"},
        headers={"Referer": track_url, "X-Retpath-Y": track_url},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if "track" not in data:
        raise ValueError(f"No track data: {data}")
    return data["track"]


def get_audio_url(track_id: str, album_id: str) -> tuple[str, bool]:
    """Returns (mp3_url, is_preview)."""
    track_url = f"https://music.yandex.{AUTH_DOMAIN}/album/{album_id}/track/{track_id}"
    r = _session.get(
        f"https://music.yandex.{AUTH_DOMAIN}/api/v2.1/handlers/track"
        f"/{track_id}:{album_id}/web-album_track-track-track-main/download/m",
        params={"hq": 1},
        headers={"Referer": track_url, "X-Retpath-Y": track_url},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()

    src = data.get("src", "")
    is_preview = data.get("preview", True)

    if not src:
        raise ValueError(f"No src in response: {data}")

    # src comes as protocol-relative (//...), add https
    if src.startswith("//"):
        src = "https:" + src

    # Some responses give a direct MP3 URL; others need a second-step location fetch
    if "/get-mp3/" in src and "storage.mds" not in src:
        return src, is_preview

    # Two-step: fetch file location JSON to build the signed URL
    loc_r = _session.get(src + "&format=json", timeout=15)
    loc_r.raise_for_status()
    fd = loc_r.json()

    key = hashlib.md5(
        ("XGRlBW9FXlekgbPrRHuSiA" + fd["path"][1:] + fd["s"]).encode()
    ).hexdigest()
    url = "https://{}/get-mp3/{}/{}".format(fd["host"], key, fd["ts"] + fd["path"])
    return url, is_preview


def download_to_file(url: str, dest: str) -> None:
    with _session.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me a Yandex Music track link and I'll download it for you.\n\n"
        "Example:\nhttps://music.yandex.com/album/10572845/track/65366901"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    match = TRACK_URL_RE.search(text)
    if not match:
        await update.message.reply_text("Please send a valid Yandex Music track link.")
        return

    album_id, track_id = match.group(2), match.group(3)
    status_msg = await update.message.reply_text("Downloading...")

    try:
        loop = asyncio.get_event_loop()

        track_info, (audio_url, is_preview) = await asyncio.gather(
            loop.run_in_executor(None, get_track_info, track_id, album_id),
            loop.run_in_executor(None, get_audio_url, track_id, album_id),
        )

        title = track_info.get("title", "Unknown")
        artists = ", ".join(a.get("name", "") for a in track_info.get("artists", []))
        duration_ms = track_info.get("durationMs")
        duration = int(duration_ms / 1000) if duration_ms else None

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = str(Path(tmpdir) / f"{track_id}.mp3")
            await loop.run_in_executor(None, download_to_file, audio_url, dest)

            file_size = Path(dest).stat().st_size
            if file_size > 50 * 1024 * 1024:
                await status_msg.edit_text("File too large to send via Telegram (> 50 MB).")
                return

            await status_msg.edit_text("Uploading...")
            with open(dest, "rb") as f:
                await update.message.reply_audio(
                    audio=f,
                    title=title,
                    performer=artists,
                    duration=duration,
                )

        await status_msg.delete()

    except Exception as e:
        logger.exception("Error for track %s:%s", track_id, album_id)
        await status_msg.edit_text(f"Failed: {e}")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
