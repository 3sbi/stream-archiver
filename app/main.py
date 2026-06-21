import time
import traceback

from .config import Config
from .twitch import twitch
from .recorder import recorder
from .uploader import uploader
from .health import start_health_server, heartbeat


def main():
    print(f"Watching Twitch channel: {Config.TWITCH_CHANNEL}")
    start_health_server()
    uploader.start()
    stream_live = False
    while True:
        try:
            heartbeat()
            info = twitch.get_stream_info()
            # Stream just started
            if info is not None and not stream_live:
                print("STEAM LIVE DETECTED")
                recorder.start_recording(info["title"], info["started_at"])
                stream_live = True

            # Stream ended
            elif info is None and stream_live:
                print("STREAM ENDED")
                recorder.stop_recording()
                stream_live = False

            # Stream still live
            elif info is not None and stream_live:
                # Detect unexpected recorder crash
                if recorder.ffmpeg is not None and recorder.ffmpeg.poll() is not None:
                    print("FFMPEG EXITED UNEXPECTEDLY")
                    recorder.stop_recording()
                    time.sleep(15)
                    recorder.start_recording(info["title"], info["started_at"])
            time.sleep(Config.CHECK_INTERVAL)

        except Exception as e:
            print("MAIN LOOP ERROR")
            print(e)
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
