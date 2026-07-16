import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # We use os.environ instead of os.getenv to raise error if any of these are not present
    CHANNEL = os.environ["CHANNEL"]
    PLATFORM = os.environ["PLATFORM"]
    TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
    TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")

    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
    TELEGRAM_SECOND_CHANNEL_ID = os.getenv(
        "TELEGRAM_SECOND_CHANNEL_ID"
    )  # used to store raw files as documents since telegram compresses video files that were sent as media attachments

    TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "https://api.telegram.org")
    TELEGRAM_UPLOAD_MODE = os.getenv("TELEGRAM_UPLOAD_MODE", "video")
    TELEGRAM_WATERMARK_TEXT = os.getenv("TELEGRAM_WATERMARK_TEXT", "")

    GROUP_SEGMENTS = os.getenv("GROUP_SEGMENTS", "false").lower() == "true"

    METAINFO_CHECK_INTERVAL = int(os.getenv("METAINFO_CHECK_INTERVAL", "120"))
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "10"))
    GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", "240"))
    MIN_FREE_DISK_GB = int(os.getenv("MIN_FREE_DISK_GB", "2"))
    DB_PATH = os.getenv("DB_PATH", "/data/recorder.db")
    SEGMENTS_DIR = os.getenv("SEGMENTS_DIR", "/data/segments")

    # Default segment duration is chosen empirically.
    # Each segment should be as large as possible without exceeding the 2 GiB limit of Telegram uploads.
    # For example, with an average stream bitrate of 6200kbps, a
    #  2630-second 1920x1080 video segment results in a file size of ~ 1,9 GiB.
    # 6_200_000 * 2630 / 8 = 2_038_250_000 bytes ~ 1.9 GiB
    SEGMENT_TIME = int(os.getenv("SEGMENT_TIME", "2630"))

    TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
