import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

BOT_TOKEN = "8786211639:AAH9i6yTvG4WJcDGAMB9DkNxeO2VpCHQytU"
YANDEX_MUSIC_PATTERN = re.compile(r"https?://music\.yandex\.(ru|com)/album/\d+/track/\d+")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me a Yandex Music track link and I'll download it for you.\n\n"
        "Example:\nhttps://music.yandex.com/album/10572845/track/65366901"
    )


async def download_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    # Extract URL from message (handles extra query params)
    match = YANDEX_MUSIC_PATTERN.search(text)
    if not match:
        await update.message.reply_text("Please send a valid Yandex Music track link.")
        return

    url = match.group(0)
    # Append original full URL to preserve track ID context
    full_url = text.split()[0]  # use full URL including query params

    status_msg = await update.message.reply_text("Downloading...")

    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }

        try:
            loop = asyncio.get_event_loop()

            def do_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_url, download=True)
                    return info

            info = await loop.run_in_executor(None, do_download)

            # Find the downloaded audio file
            all_files = list(Path(tmpdir).iterdir())

            if not all_files:
                await status_msg.edit_text("Download failed: no output file found.")
                return

            audio_path = all_files[0]
            file_size = audio_path.stat().st_size

            # Telegram bot API limit: 50 MB
            if file_size > 50 * 1024 * 1024:
                await status_msg.edit_text("File is too large to send via Telegram (> 50 MB).")
                return

            title = info.get("title", audio_path.stem)
            artist = info.get("artist") or info.get("uploader", "")
            duration = info.get("duration")

            await status_msg.edit_text("Uploading...")

            with open(audio_path, "rb") as audio_file:
                await update.message.reply_audio(
                    audio=audio_file,
                    title=title,
                    performer=artist,
                    duration=duration,
                )

            await status_msg.delete()

        except yt_dlp.utils.DownloadError as e:
            logger.error("yt-dlp error: %s", e)
            await status_msg.edit_text(
                f"Failed to download track.\n\nReason: {e}\n\n"
                "Make sure the track is publicly available."
            )
        except Exception as e:
            logger.exception("Unexpected error")
            await status_msg.edit_text(f"An unexpected error occurred: {e}")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_track))

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
