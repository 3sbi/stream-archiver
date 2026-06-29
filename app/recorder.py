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
from app.health import heartbeat


class Recorder:
    def __init__(self, file_prefix: str) -> None:
        Path(Config.SEGMENTS_DIR).mkdir(parents=True, exist_ok=True)
        self.file_prefix = file_prefix
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
            f"{self.file_prefix}_%Y-%m-%dT%H:%M:%S"
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
            "-metadata",
            f"duration={Config.SEGMENT_TIME}",
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
        uploaded = {str(Path(Config.SEGMENTS_DIR) / f) for f in db.get_uploaded_files()}
        pending: set[str] = set()

        def on_uploaded(filename: str) -> None:
            pending.discard(filename)
            uploaded.add(filename)
            db.mark_uploaded(filename, None)

        while self.running:
            try:
                heartbeat()
                files = sorted(
                    Path(Config.SEGMENTS_DIR).glob(f"{self.current_session}_*.mp4")
                )
                # Upload all completed files.
                # Skip newest because ffmpeg may still be writing it.
                for file in files[:-1]:
                    if str(file) in uploaded or str(file) in pending:
                        continue
                    caption = self.build_caption(file.name, ended=False)
                    uploader.enqueue(str(file), caption, on_uploaded)
                    pending.add(str(file))
                time.sleep(10)
            except Exception:
                logging.error("segment_watcher error")
                traceback.print_exc()
                time.sleep(10)
        self.upload_remaining(uploaded, pending)

    def upload_remaining(self, uploaded: set[str], pending: set[str]) -> None:
        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{self.current_session}_*.mp4"))
        total = len(files)

        def on_uploaded(filename: str) -> None:
            db.mark_uploaded(filename, None)

        for index, file in enumerate(files, start=1):
            if str(file) in uploaded or str(file) in pending:
                continue
            caption = self.build_caption(
                file.name,
                ended=(index == total),
            )
            uploader.enqueue(str(file), caption, on_uploaded)

    def build_caption(self, filename: str, ended: bool = False) -> str:
        part = Path(filename).stem.split("_")[-1]
        date = datetime.fromisoformat(self.started_at).astimezone(
            ZoneInfo("Europe/Moscow")
        )
        caption = f"{self.current_title}\n{date.strftime('%d.%m.%Y')}\n\nPart {part}"
        if ended:
            caption += "\n🏁 Stream ended"
        return caption[:1024]
