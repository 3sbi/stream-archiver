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
# Re-enqueue if upload hasn't completed within this many multiples of SEGMENT_TIME
STALE_UPLOAD_MULTIPLIER = 3


class Recorder:
    def __init__(self) -> None:
        Path(Config.SEGMENTS_DIR).mkdir(parents=True, exist_ok=True)
        self.running: bool = False
        self.in_grace_period: bool = False
        self.current_session: Optional[str] = None
        self.current_title: Optional[str] = None
        self.started_at: str = ""
        self.streamlink: Optional[subprocess.Popen[bytes]] = None
        self.ffmpeg: Optional[subprocess.Popen[bytes]] = None
        self._watcher_thread: Optional[threading.Thread] = None

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

    def _build_streamlink_cmd(self, url: str) -> list[str]:
        return [
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

    def _build_ffmpeg_cmd(
        self, segment_pattern: str, start_number: int = 0, title: str | None = None
    ) -> list[str]:
        cmd: list[str] = [
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
        ]
        if title:
            cmd += ["-metadata", f"title={title}"]
        cmd += [
            "-f",
            "segment",
            "-segment_time",
            f"{Config.SEGMENT_TIME}",
            "-reset_timestamps",
            "1",
            "-segment_format",
            "mp4",
        ]
        if start_number > 0:
            cmd += ["-segment_start_number", str(start_number)]
        cmd.append(segment_pattern)
        return cmd

    def _stop_process(
        self,
        proc: subprocess.Popen[bytes] | None,
        name: str,
        timeout: int = 30,
        terminate: bool = True,
    ) -> None:
        if proc is None or proc.poll() is not None:
            return
        if terminate:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
            logging.info("%s exited (rc=%d)", name, proc.returncode)
        except subprocess.TimeoutExpired:
            if terminate:
                logging.warning("%s did not exit within %ds, killing", name, timeout)
                proc.kill()
                proc.wait()
            else:
                logging.warning("%s did not exit within %ds timeout", name, timeout)

    def _launch_processes(
        self,
        url: str,
        segment_pattern: str,
        start_number: int = 0,
        title: str | None = None,
    ) -> None:
        streamlink_cmd = self._build_streamlink_cmd(url)
        ffmpeg_cmd = self._build_ffmpeg_cmd(segment_pattern, start_number, title)
        self.streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        self.ffmpeg = subprocess.Popen(
            ffmpeg_cmd, stdin=self.streamlink.stdout, stderr=subprocess.PIPE
        )
        self.running = True

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
        self._launch_processes(url, segment_pattern, title=title)
        self._start_watcher_thread()
        if self.streamlink and self.ffmpeg:
            logging.info(
                f"Recording started: {title} | free={self.free_space_gb():.2f}GB | pid_streamlink={self.streamlink.pid} pid_ffmpeg={self.ffmpeg.pid}"
            )

    def update_title(self, title: str) -> None:
        if self.current_title != title and self.current_session:
            logging.info(f'Stream title changed: "{self.current_title}" → "{title}"')
            self.current_title = title
            db.update_title(self.current_session, title)

    def _start_watcher_thread(self) -> None:
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logging.warning("Watcher thread already running, skipping duplicate start")
            return
        self._watcher_thread = threading.Thread(
            target=self.segment_watcher, daemon=True
        )
        self._watcher_thread.start()

    def stop_recording(self) -> None:
        self.running = False
        self._stop_process(self.streamlink, "streamlink")
        self._stop_process(self.ffmpeg, "ffmpeg")
        if self.current_session:
            ended_at = datetime.now(timezone.utc).isoformat()
            db.finish_stream(self.current_session, ended_at)
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=60)
            if self._watcher_thread.is_alive():
                logging.warning("Watcher thread did not exit within 60s")
        self._watcher_thread = None
        logging.info("Recording stopped")

    def restart_recording(self, url: str, title: str):
        self._stop_process(self.streamlink, "streamlink", timeout=10)
        self._stop_process(self.ffmpeg, "ffmpeg")

        segments = [
            int(f.stem.split("_")[-1])
            for f in Path(Config.SEGMENTS_DIR).glob(f"{self.current_session}_*.mp4")
        ]
        start_number = max(segments) + 1 if segments else 0

        segment_pattern: str = os.path.join(
            Config.SEGMENTS_DIR, f"{self.current_session}_%d.mp4"
        )
        self._launch_processes(url, segment_pattern, start_number, self.current_title)
        logging.info(
            f"Recording restarted: {title} | segment_start_number={start_number}"
        )

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
        pending: dict[str, float] = {}
        lock = threading.Lock()

        def on_uploaded(filename: str, success: bool = False) -> None:
            with lock:
                pending.pop(filename, None)
                if success:
                    uploaded.add(filename)

        stale_threshold = Config.SEGMENT_TIME * STALE_UPLOAD_MULTIPLIER

        while self.running:
            try:
                files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
                now = time.time()
                for file in files:
                    age = now - file.stat().st_mtime
                    if age < Config.SEGMENT_TIME:
                        continue
                    with lock:
                        if str(file) in uploaded:
                            continue
                        if str(file) in pending:
                            if now - pending[str(file)] < stale_threshold:
                                continue
                            logging.warning(
                                "_individual_watcher: retrying stale upload %s",
                                file.name,
                            )
                            pending.pop(str(file), None)
                    caption = self.build_caption(file.name)
                    logging.debug(
                        "_individual_watcher: queuing %s for upload", file.name
                    )
                    uploader.enqueue(str(file), caption, on_uploaded)
                    with lock:
                        pending[str(file)] = now
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

        self._finalize_stream(group, uploaded, session)

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
            caption = self.build_caption(file.name)
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

    def _finalize_stream(
        self, group: list[tuple[str, str]], uploaded: set[str], session: str
    ) -> None:
        self._stop_process(self.ffmpeg, "ffmpeg", terminate=False)

        files = sorted(Path(Config.SEGMENTS_DIR).glob(f"{session}_*.mp4"))
        group_paths = {f for f, _ in group}
        for file in files:
            file_str = str(file)
            if file_str in uploaded or file_str in group_paths:
                continue
            caption = self.build_caption(file.name)
            group.append((file_str, caption))
            logging.debug("_finalize_stream: collected remaining segment %s", file.name)

        logging.info(
            "_finalize_stream: uploading %d remaining segments in batches of %d",
            len(group),
            MAX_GROUP_UPLOAD_SIZE,
        )

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

    def upload_remaining(
        self,
        uploaded: set[str],
        pending: dict[str, float],
        lock: threading.Lock,
        session: str | None = None,
    ) -> None:
        if session is None:
            session = self.current_session

        self._stop_process(self.ffmpeg, "ffmpeg", terminate=False)

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
        for file in remaining:
            caption = self.build_caption(file.name)
            uploader.enqueue(str(file), caption)

    def build_caption(self, filename: str) -> str:
        part = str(int(Path(filename).stem.split("_")[-1]) + 1)
        date = datetime.fromisoformat(self.started_at).astimezone(
            ZoneInfo(Config.TIMEZONE)
        )
        caption = f"{self.current_title}\n{date.strftime('%d.%m.%Y')}\n\nPart №{part}"
        return caption[:1024]


recorder = Recorder()
