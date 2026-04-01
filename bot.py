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

# Optional: set a Yandex Music token for full-length HQ downloads.
# Without it the bot sends 30-second previews at 128 kbps.
# Get a token: https://github.com/MarshalX/yandex-music-api/discussions/513
YANDEX_TOKEN = ""

TRACK_URL_RE = re.compile(r"music\.yandex\.(ru|com)/album/(\d+)/track/(\d+)")
API_BASE = "https://api.music.yandex.net"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _api_headers() -> dict:
    headers = {"User-Agent": "Yandex-Music-API", "Accept": "application/json"}
    if YANDEX_TOKEN:
        headers["Authorization"] = f"OAuth {YANDEX_TOKEN}"
    return headers


def get_track_info(track_id: str, album_id: str, tld: str = "ru") -> dict:
    track_url = f"https://music.yandex.{tld}/album/{album_id}/track/{track_id}"
    r = requests.get(
        f"https://music.yandex.{tld}/handlers/track.jsx",
        params={"track": f"{track_id}:{album_id}", "lang": "en"},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": track_url,
            "X-Retpath-Y": track_url,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if "track" not in data:
        raise ValueError(f"No track data: {data}")
    return data["track"]


def get_audio_url(track_id: str, album_id: str) -> tuple[str, bool]:
    """Returns (signed_mp3_url, is_preview)."""
    r = requests.get(
        f"{API_BASE}/tracks/{track_id}:{album_id}/download-info",
        headers=_api_headers(),
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if not results:
        raise ValueError("No download info available")

    # Prefer highest bitrate mp3
    mp3s = [x for x in results if x.get("codec") == "mp3"]
    best = max(mp3s or results, key=lambda x: x.get("bitrateInKbps", 0))
    is_preview = best.get("preview", True)

    # Fetch file location JSON
    loc_r = requests.get(best["downloadInfoUrl"] + "&format=json", timeout=15)
    loc_r.raise_for_status()
    fd = loc_r.json()

    key = hashlib.md5(
        ("XGRlBW9FXlekgbPrRHuSiA" + fd["path"][1:] + fd["s"]).encode()
    ).hexdigest()
    url = "https://{}/get-mp3/{}/{}".format(fd["host"], key, fd["ts"] + fd["path"])
    return url, is_preview


def download_to_file(url: str, dest: str) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
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

    tld, album_id, track_id = match.group(1), match.group(2), match.group(3)
    status_msg = await update.message.reply_text("Downloading...")

    try:
        loop = asyncio.get_event_loop()

        track_info, (audio_url, is_preview) = await asyncio.gather(
            loop.run_in_executor(None, get_track_info, track_id, album_id, tld),
            loop.run_in_executor(None, get_audio_url, track_id, album_id),
        )

        title = track_info.get("title", "Unknown")
        artists = ", ".join(
            a.get("name", "") for a in track_info.get("artists", [])
        )
        duration_ms = track_info.get("durationMs")
        duration = int(duration_ms / 1000) if duration_ms else None

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = str(Path(tmpdir) / f"{track_id}.mp3")
            await loop.run_in_executor(None, download_to_file, audio_url, dest)

            file_size = Path(dest).stat().st_size
            if file_size > 50 * 1024 * 1024:
                await status_msg.edit_text("File too large to send via Telegram (> 50 MB).")
                return

            caption = "⚠️ Preview (30s) — set YANDEX_TOKEN for full tracks" if is_preview else None
            await status_msg.edit_text("Uploading...")
            with open(dest, "rb") as f:
                await update.message.reply_audio(
                    audio=f,
                    title=title,
                    performer=artists,
                    duration=duration,
                    caption=caption,
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
