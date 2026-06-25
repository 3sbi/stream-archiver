import time
import traceback
import logging
from app.config import Config
from app.twitch import twitch
from app.uploader import uploader
from app.recorder import recorder
from app.health import start_health_server, heartbeat

logging.basicConfig(level=logging.INFO, force=True)


def main():
    logging.info(f"Watching Twitch channel: {Config.TWITCH_CHANNEL}")
    start_health_server()
    uploader.start()
    stream_live: bool = False

    # TODO: add Kick support
    url = f"https://twitch.tv/{Config.TWITCH_CHANNEL}"

    while True:
        try:
            heartbeat()
            info = twitch.get_stream_info()
            # Stream just started
            if info is not None and not stream_live:
                logging.info("STREAM LIVE DETECTED")
                recorder.start_recording(url, info.title, info.startedAt)
                stream_live = True

            # Stream ended
            elif info is None and stream_live:
                logging.info("STREAM ENDED")
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
