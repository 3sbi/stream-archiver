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
    DB_PATH = f"/data/{PLATFORM}/recorder.db"
    SEGMENTS_DIR = f"/data/{PLATFORM}/segments"

    # Each segment should be as large as possible without exceeding the 2 GiB limit of Telegram uploads.
    # The actual segment duration is computed from the stream bitrate at stream start:
    #   segment_time = MAX_SEGMENT_SIZE_BYTES * SEGMENT_SIZE_MARGIN * 8 / bitrate_bps
    # For example, with an average stream bitrate of 6200kbps:
    #   2_040_109_466 * 0.95 * 8 / 6_200_000 = 2501s -> ~1.84 GiB per segment.
    MAX_SEGMENT_SIZE_GB = float(os.getenv("MAX_SEGMENT_SIZE_GB", "1.9"))
    MAX_SEGMENT_SIZE_BYTES = int(MAX_SEGMENT_SIZE_GB * 1024**3)
    # Safety headroom for keyframe overshoot: ffmpeg -c copy cuts at the next keyframe
    # after the target, so the real segment can be slightly larger than the estimate.
    SEGMENT_SIZE_MARGIN = float(os.getenv("SEGMENT_SIZE_MARGIN", "0.95"))
    # How long to buffer the stream at start to measure its average bitrate.
    BITRATE_PROBE_SECONDS = int(os.getenv("BITRATE_PROBE_SECONDS", "10"))
    # Floor for the computed segment duration to avoid pathological tiny segments.
    MIN_SEGMENT_TIME = int(os.getenv("MIN_SEGMENT_TIME", "60"))
    # Fallback segment duration used when the bitrate cannot be measured.
    SEGMENT_TIME = int(os.getenv("SEGMENT_TIME", "2630"))

    TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
