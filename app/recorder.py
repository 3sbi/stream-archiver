import os
import time
import shutil
import subprocess
import traceback
import logging
from pathlib import Path
from datetime import datetime, timezone
import threading
from typing import Optional
from zoneinfo import ZoneInfo

from app.uploader import uploader
from app.config import Config
from app.database import db


class Recorder:
    def __init__(self) -> None:
        Path(Config.SEGMENTS_DIR).mkdir(parents=True, exist_ok=True)
        self.running: bool = False
        self.current_session: Optional[str] = None
        self.current_title: Optional[str] = None
        self.started_at: str = ""
        self.streamlink: Optional[subprocess.Popen[bytes]] = None
        self.ffmpeg: Optional[subprocess.Popen[bytes]] = None

    def free_space_gb(self) -> float:
        usage = shutil.disk_usage(Config.SEGMENTS_DIR)
        return usage.free / 1024 / 1024 / 1024

    def check_disk_space(self) -> None:
        free = self.free_space_gb()
        if free < Config.MIN_FREE_DISK_GB:
            raise RuntimeError(f"Disk space low ({free:.2f}GB)")

    def start_recording(self, url: str, title: str, started_at: str):
        self.check_disk_space()
        self.current_title = title
        self.started_at = started_at
        self.current_session = datetime.now(timezone.utc).strftime(
            f"{Config.TWITCH_CHANNEL}_%Y-%m-%dT%H:%M:%S"
        )
        db.create_stream(self.current_session, title, started_at)
        segment_pattern: str = os.path.join(
            Config.SEGMENTS_DIR, f"{self.current_session}_%04d.mp4"
        )
        streamlink_cmd: list[str] = [
            "streamlink",
            "--retry-streams",
            "30",
            "--retry-max",
            "0",
            "--stdout",
            url,
            "720p,720p48,720p60,best",
        ]

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            f"{Config.SEGMENT_TIME}",
            "-reset_timestamps",
            "1",
            "-segment_format",
            "mp4",
            segment_pattern,
        ]
        self.streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        self.ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=self.streamlink.stdout)
        self.running = True
        threading.Thread(target=self.segment_watcher, daemon=True).start()
        logging.info(f"Recording started: {title}")

    def stop_recording(self) -> None:
        self.running = False
        if self.streamlink:
            self.streamlink.terminate()
        if self.ffmpeg:
            self.ffmpeg.terminate()
        try:
            if self.streamlink:
                self.streamlink.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        try:
            if self.ffmpeg:
                self.ffmpeg.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        logging.info("Recording stopped")

    def segment_watcher(self) -> None:
        session = self.current_session
        uploaded = {str(Path(Config.SEGMENTS_DIR) / f) for f in db.get_uploaded_files()}
        pending: set[str] = set()
        lock = threading.Lock()

        def on_uploaded(filename: str, success: bool = False) -> None:
            with lock:
                pending.discard(filename)
                if success:
                    uploaded.add(filename)
                    db.mark_uploaded(filename, None)

        while self.running:
            try:
                files = sorted(
                    Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4")
                )
                # Upload completed files, skipping any modified within the last
                # segment duration (ffmpeg may still be writing them).
                now = time.time()
                for file in files:
                    age = now - file.stat().st_mtime
                    if age < Config.SEGMENT_TIME:
                        continue
                    with lock:
                        if str(file) in uploaded or str(file) in pending:
                            continue
                    caption = self.build_caption(file.name, ended=False)
                    uploader.enqueue(str(file), caption, on_uploaded)
                    with lock:
                        pending.add(str(file))
                time.sleep(10)
            except Exception:
                logging.error("segment_watcher error")
                traceback.print_exc()
                time.sleep(10)
        self.upload_remaining(uploaded, pending, lock, session=session)

    def upload_remaining(
        self,
        uploaded: set[str],
        pending: set[str],
        lock: threading.Lock,
        session: str | None = None,
    ) -> None:
        if session is None:
            session = self.current_session

        if self.ffmpeg and self.ffmpeg.poll() is None:
            try:
                self.ffmpeg.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(1)

        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))

        def on_uploaded(filename: str, success: bool = False) -> None:
            if success:
                db.mark_uploaded(filename, None)

        remaining: list[Path] = []
        for file in files:
            with lock:
                if str(file) not in uploaded and str(file) not in pending:
                    remaining.append(file)

        for index, file in enumerate(remaining, start=1):
            caption = self.build_caption(
                file.name,
                ended=(index == len(remaining)),
            )
            uploader.enqueue(str(file), caption, on_uploaded)

    def build_caption(self, filename: str, ended: bool = False) -> str:
        part = Path(filename).stem.split("_")[-1]
        date = datetime.fromisoformat(self.started_at).astimezone(
            ZoneInfo(Config.TIMEZONE)
        )
        caption = f"{self.current_title}\n{date.strftime('%d.%m.%Y')}\n\nPart {part}"
        if ended:
            caption += "\n🏁 Stream ended"
        return caption[:1024]


recorder = Recorder()
