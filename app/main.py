import time
import traceback
import logging
from typing import Callable
from app.config import Config
from app.twitch import twitch
from app.kick import kick
from app.uploader import uploader
from app.recorder import Recorder
from app.health import start_health_server, heartbeat
from app.types import StreamInfo

logging.basicConfig(level=logging.INFO, force=True)


def monitor_platform(
    name: str,
    url: str,
    client: Callable[[], StreamInfo | None],
    recorder: Recorder,
):
    try:
        heartbeat()
        info = client()
        if info is not None and not recorder.running:
            logging.info(f"[{name}] STREAM LIVE DETECTED")
            recorder.start_recording(url, info.title, info.startedAt)
        elif info is None and recorder.running:
            logging.info(f"[{name}] STREAM ENDED")
            recorder.stop_recording()
        elif info is not None and recorder.running:
            if recorder.ffmpeg is not None and recorder.ffmpeg.poll() is not None:
                logging.info(f"[{name}] FFMPEG EXITED UNEXPECTEDLY")
                recorder.stop_recording()
                time.sleep(15)
                recorder.start_recording(url, info.title, info.startedAt)
    except Exception:
        logging.exception(f"[{name}] MONITOR ERROR")
        traceback.print_exc()


def main():
    platforms: list[tuple[str, str, Callable[[], StreamInfo | None], Recorder]] = []

    twitch_recorder = Recorder(f"twitch-{Config.TWITCH_CHANNEL}")
    platforms.append(
        (
            "Twitch",
            f"https://twitch.tv/{Config.TWITCH_CHANNEL}",
            twitch.get_stream_info,
            twitch_recorder,
        )
    )
    logging.info(f"Watching Twitch channel: {Config.TWITCH_CHANNEL}")

    if Config.KICK_CHANNEL:
        kick_recorder = Recorder(f"kick-{Config.KICK_CHANNEL}")
        platforms.append(
            (
                "Kick",
                f"https://kick.com/{Config.KICK_CHANNEL}",
                kick.get_stream_info,
                kick_recorder,
            )
        )
        logging.info(f"Watching Kick channel: {Config.KICK_CHANNEL}")

    start_health_server()
    uploader.start()

    while True:
        for name, url, client_fn, recorder in platforms:
            monitor_platform(name, url, client_fn, recorder)
        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
