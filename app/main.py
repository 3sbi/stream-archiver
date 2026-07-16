import time
import subprocess
import traceback
import logging
from app.config import Config
from app.twitch import twitch
from app.uploader import uploader
from app.recorder import recorder
import os
import psutil


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


logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
    force=True,
)


def check_stream_via_streamlink(url: str) -> bool:
    try:
        result = subprocess.run(
            ["streamlink", "--json", url, "best"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logging.warning("Streamlink check timed out")
        return False
    except Exception:
        logging.warning("Streamlink check failed")
        return False


def main():
    logging.info(f"🟣 Watching Twitch channel: {Config.TWITCH_CHANNEL}")
    logging.debug(
        "Config: "
        f"upload_mode={Config.TELEGRAM_UPLOAD_MODE}, "
        f"segment_time={Config.SEGMENT_TIME}s, "
        f"streamlink_check_interval={Config.CHECK_INTERVAL}s, "
        f"min_free_disk={Config.MIN_FREE_DISK_GB}GiB, "
        f"timezone={Config.TIMEZONE}, "
        f"watermark={Config.TELEGRAM_WATERMARK_TEXT}"
    )
    uploader.start()
    stream_was_live: bool = False
    in_grace_period: bool = False
    grace_period_start: float = 0
    last_title_update: float = 0

    # TODO: add Kick support
    url = f"https://twitch.tv/{Config.TWITCH_CHANNEL}"

    while True:
        try:
            log_memory()

            live = check_stream_via_streamlink(url)

            if not live and not stream_was_live:
                logging.debug("No stream found for %s", Config.TWITCH_CHANNEL)

            # Stream just started
            if live and not stream_was_live:
                info = twitch.get_stream_info()
                if info:
                    logging.info("🚀 LIVE STREAM DETECTED")
                    recorder.start_recording(url, info.title, info.startedAt)
                    stream_was_live = True
                    last_title_update = time.time()
                    in_grace_period = False

            # Stream ended or interrupted
            elif not live and stream_was_live:
                if not in_grace_period:
                    in_grace_period = True
                    grace_period_start = time.time()
                    recorder.in_grace_period = True
                    logging.info(
                        "Stream interrupted, waiting %ds before finalizing...",
                        Config.GRACE_PERIOD,
                    )

                if recorder.streamlink and recorder.streamlink.poll() is not None:
                    logging.warning(
                        "Streamlink exited during grace period (rc=%d)",
                        recorder.streamlink.returncode,
                    )

                if time.time() - grace_period_start > Config.GRACE_PERIOD:
                    logging.info("🏁 Grace period expired, stream truly ended")
                    recorder.stop_recording()
                    stream_was_live = False
                    in_grace_period = False
                    recorder.in_grace_period = False

            # Stream still live or was resumed during grace period
            elif live and stream_was_live:
                if in_grace_period:
                    logging.info(
                        "Stream resumed after interruption, continuing recording"
                    )
                    in_grace_period = False
                    recorder.in_grace_period = False

                    if recorder.streamlink and recorder.streamlink.poll() is not None:
                        info = twitch.get_stream_info()
                        if info:
                            logging.info("Restarting recorder after stream resume")
                            recorder.restart_recording(url, info.title)
                            last_title_update = time.time()
                # Update title periodically via Twitch API
                if time.time() - last_title_update > Config.METAINFO_CHECK_INTERVAL:
                    info = twitch.get_stream_info()
                    if info:
                        recorder.update_title(info.title)
                        last_title_update = time.time()

                # Detect unexpected recorder crash
                if recorder.streamlink and recorder.streamlink.poll() is not None:
                    streamlink_rc = recorder.streamlink.returncode
                    logging.warning("Streamlink exited (rc=%d)", streamlink_rc)
                    recorder.stop_recording()
                    time.sleep(5)
                    if check_stream_via_streamlink(url):
                        info = twitch.get_stream_info()
                        if info:
                            logging.info("Restarting recorder")
                            recorder.start_recording(url, info.title, info.startedAt)
                            last_title_update = time.time()

        except Exception:
            logging.exception("MAIN LOOP ERROR")
            traceback.print_exc()

        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
