import logging
import threading
from pathlib import Path

from app.chatlog.base import build_chat_caption
from app.chatlog.kick import capture_kick
from app.chatlog.twitch import capture_twitch
from app.config import Config
from app.uploader import uploader

logger = logging.getLogger(__name__)


class ChatRecorder:
    def __init__(self, platform: str, channel: str) -> None:
        self.platform = platform
        self.channel = channel
        self.session_id: str | None = None
        self.title: str | None = None
        self.file_path: Path | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, session_id: str, title: str) -> None:
        if not Config.CHAT_LOGGING:
            return
        if self.active:
            logger.warning("Chat recorder already running, skipping start")
            return
        self.session_id = session_id
        self.title = title
        self.file_path = Path(Config.SEGMENTS_DIR) / f"{session_id}_chat.txt"
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="chat-recorder"
        )
        self._thread.start()
        logger.info("Chat recording started: %s", self.file_path.name)

    def _run(self) -> None:
        try:
            if self.platform == "twitch":
                capture_twitch(self.channel, self.file_path, self._stop_event)
            elif self.platform == "kick":
                capture_kick(self.channel, self.file_path, self._stop_event)
            else:
                logger.error("Unsupported platform for chat logging: %s", self.platform)
        except Exception:
            logger.exception("Chat capture crashed")
        finally:
            logger.info(
                "Chat capture finished: %s",
                self.file_path.name if self.file_path else None,
            )

    def stop(self) -> None:
        if not self.active:
            return
        self._stop_event.set()
        self._thread.join(timeout=60)
        if self._thread.is_alive():
            logger.warning("Chat thread did not exit within 60s")
        self._thread = None
        self._enqueue_upload()

    def _enqueue_upload(self) -> None:
        if not self.file_path or not self.file_path.exists():
            return
        if self.file_path.stat().st_size == 0:
            logger.info("Chat log is empty, removing %s", self.file_path.name)
            self.file_path.unlink(missing_ok=True)
            return
        caption = build_chat_caption(self.title or self.channel)
        uploader.enqueue_document(str(self.file_path), caption)
