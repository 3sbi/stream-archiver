import os
import re
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

# Telegram allows max 10 files per group upload
MAX_GROUP_UPLOAD_SIZE = 10


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
        logging.debug(
            f"Disk space check: {free:.2f}GB free (min={Config.MIN_FREE_DISK_GB}GB)"
        )
        if free < Config.MIN_FREE_DISK_GB:
            raise RuntimeError(f"Disk space low ({free:.2f}GB)")

    def start_recording(self, url: str, title: str, started_at: str):
        self.check_disk_space()
        uploader.reset_thread_anchor()
        self.current_title = title
        self.started_at = started_at
        self.current_session = datetime.now(timezone.utc).strftime(
            f"{Config.TWITCH_CHANNEL}_%Y-%m-%dT%H:%M:%S"
        )
        db.create_stream(self.current_session, title, started_at)
        segment_pattern: str = os.path.join(
            Config.SEGMENTS_DIR, f"{self.current_session}_%d.mp4"
        )
        streamlink_cmd: list[str] = [
            "streamlink",
            "--retry-streams",
            "30",
            "--retry-max",
            "0",
            "--stdout",
            "--logformat",
            "{asctime} [{levelname}] {message}",
            "--logdateformat",
            "%Y-%m-%d %H:%M:%S %z",
            url,
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
            "-movflags",
            "+faststart",
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
        self.ffmpeg = subprocess.Popen(
            ffmpeg_cmd, stdin=self.streamlink.stdout, stderr=subprocess.PIPE
        )
        self.running = True
        threading.Thread(target=self.segment_watcher, daemon=True).start()
        logging.info(
            f"Recording started: {title} | free={self.free_space_gb():.2f}GB | pid_streamlink={self.streamlink.pid} pid_ffmpeg={self.ffmpeg.pid}"
        )

    def update_title(self, title: str) -> None:
        if self.current_title != title and self.current_session:
            logging.info(f'Stream title changed: "{self.current_title}" → "{title}"')
            self.current_title = title
            db.update_title(self.current_session, title)

    def stop_recording(self) -> None:
        self.running = False
        if self.streamlink and self.streamlink.poll() is None:
            self.streamlink.terminate()

        if self.ffmpeg and self.ffmpeg.poll() is None:
            self.ffmpeg.terminate()
        try:
            if self.streamlink:
                self.streamlink.wait(timeout=30)
                logging.info("streamlink exited (rc=%d)", self.streamlink.returncode)
        except subprocess.TimeoutExpired:
            logging.warning("streamlink did not exit within 30s timeout, killing")
            if self.streamlink:
                self.streamlink.kill()
                self.streamlink.wait()
        try:
            if self.ffmpeg:
                self.ffmpeg.wait(timeout=30)
                logging.info("ffmpeg exited (rc=%d)", self.ffmpeg.returncode)
        except subprocess.TimeoutExpired:
            logging.warning("ffmpeg did not exit within 30s timeout, killing")
            if self.ffmpeg:
                self.ffmpeg.kill()
                self.ffmpeg.wait()
        if self.current_session:
            ended_at = datetime.now(timezone.utc).isoformat()
            db.finish_stream(self.current_session, ended_at)
        logging.info("Recording stopped")

    def segment_watcher(self) -> None:
        session = self.current_session
        uploaded = {str(Path(Config.SEGMENTS_DIR) / f) for f in db.get_uploaded_files()}
        if not session:
            return
        if Config.GROUP_SEGMENTS:
            self._group_watcher(session, uploaded)
        else:
            self._individual_watcher(session, uploaded)

    def _individual_watcher(self, session: str, uploaded: set[str]) -> None:
        pending: set[str] = set()
        lock = threading.Lock()

        def on_uploaded(filename: str, success: bool = False) -> None:
            with lock:
                pending.discard(filename)
                if success:
                    uploaded.add(filename)

        while self.running:
            try:
                files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
                now = time.time()
                for file in files:
                    age = now - file.stat().st_mtime
                    if age < Config.SEGMENT_TIME:
                        continue
                    with lock:
                        if str(file) in uploaded or str(file) in pending:
                            continue
                    caption = self.build_caption(file.name, ended=False)
                    logging.debug(
                        "_individual_watcher: queuing %s for upload", file.name
                    )
                    uploader.enqueue(str(file), caption, on_uploaded)
                    with lock:
                        pending.add(str(file))
                time.sleep(10)
            except Exception:
                logging.error("_individual_watcher error")
                traceback.print_exc()
                time.sleep(10)
        self.upload_remaining(uploaded, pending, lock, session=session)

    def _group_watcher(self, session: str, uploaded: set[str]) -> None:
        group: list[tuple[str, str]] = []

        while self.running:
            try:
                self._collect_new_segments_for_group(session, uploaded, group)
                self._flush_group_if_needed(group, uploaded)
                time.sleep(10)
            except Exception:
                logging.error("_group_watcher error")
                traceback.print_exc()
                time.sleep(10)

        self._flush_group_ended(group, uploaded)
        self._upload_remaining_group(uploaded, session=session)

    def _collect_new_segments_for_group(
        self, session: str, uploaded: set[str], group: list[tuple[str, str]]
    ) -> None:
        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
        group_paths = {f for f, _ in group}
        now = time.time()
        for file in files:
            file_str = str(file)
            if file_str in uploaded or file_str in group_paths:
                continue
            age = now - file.stat().st_mtime
            if age < Config.SEGMENT_TIME:
                continue
            caption = self.build_caption(file.name, ended=False)
            group.append((file_str, caption))
            logging.debug("_group_watcher: collected %s", file.name)

    def _flush_group_if_needed(
        self, group: list[tuple[str, str]], uploaded: set[str]
    ) -> None:
        try:
            self.check_disk_space()
        except RuntimeError:
            logging.warning("Low disk space, flushing collected segments")
            uploaded_paths = self._upload_group_batch(group, uploaded)
            group[:] = [(p, c) for p, c in group if p not in uploaded_paths]
            return

        while len(group) >= MAX_GROUP_UPLOAD_SIZE:
            batch = group[:MAX_GROUP_UPLOAD_SIZE]
            uploaded_paths = self._upload_group_batch(batch, uploaded)
            group[:] = [(p, c) for p, c in group if p not in uploaded_paths]

    def _flush_group_ended(
        self, group: list[tuple[str, str]], uploaded: set[str]
    ) -> None:
        if not group:
            return
        file_path, caption = group[-1]
        group[-1] = (file_path, caption + "\n🏁 Stream ended")
        while group:
            batch = group[:MAX_GROUP_UPLOAD_SIZE]
            uploaded_paths = self._upload_group_batch(batch, uploaded)
            group[:] = [(p, c) for p, c in group if p not in uploaded_paths]
            if not uploaded_paths:
                time.sleep(10)

    def _upload_group_batch(
        self, batch: list[tuple[str, str]], uploaded: set[str]
    ) -> set[str]:
        if not batch:
            return set()

        if len(batch) > 1:
            parts: list[int] = []
            for _, caption in batch:
                m = re.search(r"Part №(\d+)", caption)
                if m:
                    parts.append(int(m.group(1)))
            if parts:
                first_path, first_caption = batch[0]
                min_part = min(parts)
                max_part = max(parts)
                batch[0] = (
                    first_path,
                    re.sub(
                        r"Part №(\d+)",
                        f"Part №{min_part}-{max_part}",
                        first_caption,
                        count=1,
                    ),
                )

        uploaded_paths = uploader.upload_group(batch)
        uploaded.update(uploaded_paths)
        return uploaded_paths

    def _upload_remaining_group(
        self, uploaded: set[str], session: str | None = None
    ) -> None:
        if session is None:
            session = self.current_session

        if self.ffmpeg and self.ffmpeg.poll() is None:
            logging.debug("_upload_remaining_group: waiting for ffmpeg to finish")
            try:
                self.ffmpeg.wait(timeout=30)
                logging.info(
                    "_upload_remaining_group: ffmpeg exited (rc=%d)",
                    self.ffmpeg.returncode,
                )
            except subprocess.TimeoutExpired:
                logging.warning(
                    "_upload_remaining_group: ffmpeg did not exit within 30s timeout"
                )
        time.sleep(1)

        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
        logging.info(
            "_upload_remaining_group: found %d total segments for %s",
            len(files),
            session,
        )

        remaining = [str(f) for f in files if str(f) not in uploaded]
        if not remaining:
            return

        logging.info(
            "_upload_remaining_group: uploading %d remaining segments",
            len(remaining),
        )

        all_items: list[tuple[str, str]] = []
        for file_path in remaining:
            caption = self.build_caption(Path(file_path).name, ended=False)
            all_items.append((file_path, caption))

        last_path, last_caption = all_items[-1]
        all_items[-1] = (last_path, last_caption + "\n🏁 Stream ended")

        while all_items:
            chunk = all_items[:MAX_GROUP_UPLOAD_SIZE]
            uploaded_paths = self._upload_group_batch(chunk, uploaded)
            all_items = [(p, c) for p, c in all_items if p not in uploaded_paths]
            if not uploaded_paths:
                time.sleep(10)

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
            logging.debug("upload_remaining: waiting for ffmpeg to finish")
            try:
                self.ffmpeg.wait(timeout=30)
                logging.info(
                    "upload_remaining: ffmpeg exited (rc=%d)", self.ffmpeg.returncode
                )
            except subprocess.TimeoutExpired:
                logging.warning(
                    "upload_remaining: ffmpeg did not exit within 30s timeout"
                )
        time.sleep(1)

        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
        logging.info(
            "upload_remaining: found %d total segments for %s", len(files), session
        )

        remaining: list[Path] = []
        for file in files:
            with lock:
                if str(file) not in uploaded and str(file) not in pending:
                    remaining.append(file)

        logging.info(
            "upload_remaining: uploading %d remaining segments", len(remaining)
        )
        for index, file in enumerate(remaining, start=1):
            caption = self.build_caption(
                file.name,
                ended=(index == len(remaining)),
            )
            uploader.enqueue(str(file), caption)

    def build_caption(self, filename: str, ended: bool = False) -> str:
        part = str(int(Path(filename).stem.split("_")[-1]) + 1)
        date = datetime.fromisoformat(self.started_at).astimezone(
            ZoneInfo(Config.TIMEZONE)
        )
        caption = f"{self.current_title}\n{date.strftime('%d.%m.%Y')}\n\nPart №{part}"
        if ended:
            caption += "\n🏁 Stream ended"
        return caption[:1024]


recorder = Recorder()
