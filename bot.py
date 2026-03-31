import re
import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from yandex_music import ClientAsync

BOT_TOKEN = "8786211639:AAH9i6yTvG4WJcDGAMB9DkNxeO2VpCHQytU"
# Get your token: https://github.com/MarshalX/yandex-music-api/discussions/513
YANDEX_TOKEN = ""

TRACK_URL_RE = re.compile(r"music\.yandex\.(ru|com)/album/(\d+)/track/(\d+)")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ym_client: ClientAsync | None = None


async def get_client() -> ClientAsync:
    global ym_client
    if ym_client is None:
        ym_client = ClientAsync(YANDEX_TOKEN)
        await ym_client.init()
    return ym_client


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me a Yandex Music track link and I'll download it for you.\n\n"
        "Example:\nhttps://music.yandex.com/album/10572845/track/65366901"
    )


async def download_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    match = TRACK_URL_RE.search(text)
    if not match:
        await update.message.reply_text("Please send a valid Yandex Music track link.")
        return

    album_id = match.group(2)
    track_id = match.group(3)

    status_msg = await update.message.reply_text("Downloading...")

    try:
        client = await get_client()

        tracks = await client.tracks([f"{track_id}:{album_id}"])
        if not tracks:
            await status_msg.edit_text("Track not found.")
            return

        track = tracks[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            filename = Path(tmpdir) / f"{track_id}.mp3"

            await track.download_async(str(filename))

            file_size = filename.stat().st_size
            if file_size > 50 * 1024 * 1024:
                await status_msg.edit_text("File is too large to send via Telegram (> 50 MB).")
                return

            title = track.title or "Unknown"
            artist = ", ".join(a.name for a in (track.artists or []))
            duration = int(track.duration_ms / 1000) if track.duration_ms else None

            await status_msg.edit_text("Uploading...")

            with open(filename, "rb") as audio_file:
                await update.message.reply_audio(
                    audio=audio_file,
                    title=title,
                    performer=artist,
                    duration=duration,
                )

        await status_msg.delete()

    except Exception as e:
        logger.exception("Error downloading track %s:%s", track_id, album_id)
        await status_msg.edit_text(f"Failed to download track: {e}")


def main() -> None:
    if not YANDEX_TOKEN:
        raise RuntimeError(
            "YANDEX_TOKEN is not set. "
            "Get your token from https://github.com/MarshalX/yandex-music-api/discussions/513 "
            "and set it in bot.py"
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_track))

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
