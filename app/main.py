import time
import traceback
import logging
from app.config import Config
from app.twitch import twitch
from app.uploader import uploader
from app.recorder import recorder
import signal
import os
import psutil
from types import FrameType


def log_memory():
    proc = psutil.Process(os.getpid())

    total = proc.memory_info().rss
    logging.debug(
        "Python: %.1f MB",
        total / 1024 / 1024,
    )

    for child in proc.children(recursive=True):
        try:
            rss = child.memory_info().rss
            total += rss
            logging.debug(
                "  %s (pid=%d): %.1f MB",
                child.name(),
                child.pid,
                rss / 1024 / 1024,
            )
        except psutil.NoSuchProcess:
            pass

    logging.debug("Total (Python + children): %.1f MB", total / 1024 / 1024)


def handler(signum: int, _frame: FrameType | None) -> None:
    logging.warning("Received signal %s", signum)


signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGINT, handler)

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
    force=True,
)


def main():
    logging.info(f"🟣 Watching Twitch channel: {Config.TWITCH_CHANNEL}")
    logging.debug(
        "Config: "
        f"upload_mode={Config.TELEGRAM_UPLOAD_MODE}, "
        f"segment_time={Config.SEGMENT_TIME}s, "
        f"check_interval={Config.CHECK_INTERVAL}s, "
        f"min_free_disk={Config.MIN_FREE_DISK_GB}GiB, "
        f"timezone={Config.TIMEZONE}, "
        f"watermark={Config.TELEGRAM_WATERMARK_TEXT}"
    )
    uploader.start()
    stream_live: bool = False

    # TODO: add Kick support
    url = f"https://twitch.tv/{Config.TWITCH_CHANNEL}"

    while True:
        try:
            log_memory()
            info = twitch.get_stream_info()
            # Stream just started
            if info is not None and not stream_live:
                logging.info("🚀 LIVE STREAM DETECTED")
                recorder.start_recording(url, info.title, info.startedAt)
                stream_live = True

            # Stream ended
            elif info is None and stream_live:
                logging.info("🏁 LIVE STREAM ENDED")
                recorder.stop_recording()
                stream_live = False

            # Stream still live
            elif info is not None and stream_live:
                # Detect unexpected recorder crash
                if recorder.ffmpeg is not None and recorder.ffmpeg.poll() is not None:
                    logging.info("FFMPEG EXITED UNEXPECTEDLY")
                    recorder.stop_recording()
                    time.sleep(15)
                    recorder.start_recording(url, info.title, info.startedAt)

        except Exception:
            logging.exception("MAIN LOOP ERROR")
            traceback.print_exc()

        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
