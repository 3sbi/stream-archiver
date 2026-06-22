import os
import time
import traceback

from .config import Config
from .twitch import twitch
from .recorder import recorder
from .uploader import uploader
from .health import start_health_server, heartbeat

MAX_SIZE = 1_900_000_000  # 1.9 GiB


def main():
    print(f"Watching Twitch channel: {Config.TWITCH_CHANNEL}")
    start_health_server()
    uploader.start()
    stream_live: bool = False
    current_file: str | None = None

    # TODO: add Kick support
    url = f"https://twitch.tv/{Config.TWITCH_CHANNEL}"

    while True:
        try:
            heartbeat()
            info = twitch.get_stream_info()
            # Stream just started
            if info is not None and not stream_live:
                print("STEAM LIVE DETECTED")
                current_file = recorder.start_recording(url, info.title, info.startedAt)
                stream_live = True

            # Stream ended
            elif info is None and stream_live:
                print("STREAM ENDED")
                recorder.stop_recording()
                stream_live = False
                current_file = None

            # Stream still live
            elif info is not None and stream_live:
                # stop recording if size is near the limit
                if current_file is not None:
                    size = os.path.getsize(current_file)
                    if size >= MAX_SIZE:
                        recorder.stop_recording()
                        current_file = None

                # Detect unexpected recorder crash
                if recorder.ffmpeg is not None and recorder.ffmpeg.poll() is not None:
                    print("FFMPEG EXITED UNEXPECTEDLY")
                    recorder.stop_recording()
                    current_file = None
                    time.sleep(15)
                    current_file = recorder.start_recording(
                        url, info.title, info.startedAt
                    )
            time.sleep(Config.CHECK_INTERVAL)

        except Exception as e:
            print("MAIN LOOP ERROR")
            print(e)
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
