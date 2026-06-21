import os
import time
import shutil
import subprocess

from pathlib import Path
from datetime import datetime, timezone
from threading import Thread
from typing import Optional

from .config import Config
from .database import db
from .uploader import uploader
from .health import heartbeat


class Recorder:
    def __init__(self) -> None:
        Path(Config.SEGMENTS_DIR).mkdir(parents=True, exist_ok=True)
        self.running: bool = False
        self.current_session: Optional[str] = None
        self.current_title: Optional[str] = None
        self.started_at: Optional[str] = None
        self.streamlink: Optional[subprocess.Popen[bytes]] = None
        self.ffmpeg: Optional[subprocess.Popen[bytes]] = None

    def free_space_gb(self) -> float:
        usage = shutil.disk_usage(Config.SEGMENTS_DIR)
        return usage.free / 1024 / 1024 / 1024

    def check_disk_space(self) -> None:
        free = self.free_space_gb()
        if free < Config.MIN_FREE_DISK_GB:
            raise RuntimeError(f"Disk space low ({free:.2f}GB)")

    def build_segment_pattern(self, session_id: str) -> str:
        return os.path.join(Config.SEGMENTS_DIR, f"{session_id}_%04d.mp4")

    def start_recording(self, title: str, started_at: str) -> None:
        self.check_disk_space()
        self.current_title = title
        self.started_at = started_at
        self.current_session = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        db.create_stream(self.current_session, title, started_at)
        segment_pattern = self.build_segment_pattern(self.current_session)
        streamlink_cmd: list[str] = [
            "streamlink",
            "--retry-streams",
            "30",
            "--retry-max",
            "0",
            "--stdout",
            f"https://twitch.tv/{Config.TWITCH_CHANNEL}",
            "best",
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
            str(Config.SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            segment_pattern,
        ]
        self.streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        self.ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=self.streamlink.stdout)
        self.running = True
        Thread(target=self.segment_watcher, daemon=True).start()
        print("Recording started:", title)

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
        db.finish_stream(self.current_session, datetime.now(timezone.utc).isoformat())
        print("Recording stopped")

    def segment_watcher(self) -> None:
        uploaded = {str(Path(Config.SEGMENTS_DIR) / f) for f in db.get_uploaded_files()}
        while self.running:
            heartbeat()
            files = sorted(
                Path(Config.SEGMENTS_DIR).glob(f"{self.current_session}_*.mp4")
            )
            # Upload all completed files.
            # Skip newest because ffmpeg may still be writing it.
            for file in files[:-1]:
                if str(file) in uploaded:
                    continue
                caption = self.build_caption(file.name, ended=False)
                uploader.enqueue(str(file), caption)
                uploaded.add(str(file))
            time.sleep(10)
        self.upload_remaining(uploaded)

    def upload_remaining(self, uploaded: set[str]) -> None:
        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{self.current_session}_*.mp4"))
        total = len(files)
        for index, file in enumerate(files, start=1):
            if str(file) in uploaded:
                continue
            caption = self.build_caption(file.name, ended=(index == total))
            uploader.enqueue(str(file), caption)

    def build_caption(self, filename: str, ended: bool = False) -> str:
        part = filename.split("_")[-1].replace(".mp4", "")
        caption = (
            f"🎮 {self.current_title}\n\n"
            f"Channel: "
            f"{Config.TWITCH_CHANNEL}\n"
            f"Started: "
            f"{self.started_at}\n\n"
            f"Part {part}"
        )
        if ended:
            caption += "\n\n✅ Stream ended"
        return caption[:1024]


recorder = Recorder()
