import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # We use os.environ instead of os.getenv to raise error if any of these are not present 
    TWITCH_CHANNEL = os.environ["TWITCH_CHANNEL"]
    TWITCH_CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
    TWITCH_CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

    TELEGRAM_UPLOAD_MODE = os.getenv("TELEGRAM_UPLOAD_MODE", "video")
    SEGMENT_SECONDS = int(os.getenv("SEGMENT_SECONDS", "900"))
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
    MIN_FREE_DISK_GB = int(os.getenv("MIN_FREE_DISK_GB", "2"))
    DB_PATH = os.getenv("DB_PATH", "data/recorder.db")
    SEGMENTS_DIR = os.getenv("SEGMENTS_DIR", "data/segments")
