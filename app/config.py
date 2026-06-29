import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # We use os.environ instead of os.getenv to raise error if any of these are not present
    TWITCH_CHANNEL = os.environ["TWITCH_CHANNEL"]
    TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
    TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")

    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
    TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
    TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]

    TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "https://api.telegram.org")
    TELEGRAM_UPLOAD_MODE = os.getenv("TELEGRAM_UPLOAD_MODE", "video")
    TELEGRAM_WATERMARK_TEXT = os.getenv("TELEGRAM_WATERMARK_TEXT", "")

    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
    MIN_FREE_DISK_GB = int(os.getenv("MIN_FREE_DISK_GB", "2"))
    DB_PATH = os.getenv("DB_PATH", "data/recorder.db")
    SEGMENTS_DIR = os.getenv("SEGMENTS_DIR", "data/segments")

    # Default segment duration is chosen empirically.
    # Each segment should be as large as possible without exceeding the 2 GiB limit of Telegram uploads.
    # For example, with an average stream bitrate of 6200kbps, a 2630-second segment results in a file size of ~ 1,9 GiB.
    # 6_200_000 * 2630 / 8 = 2_038_250_000 bytes ~ 1.9 GiB
    SEGMENT_TIME = int(os.getenv("SEGMENT_TIME", "2630"))

    TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
