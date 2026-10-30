import logging
import os
import queue
import threading
from collections.abc import Callable

from .config import Config
from .database import db
from .telegram_sender import telegram

logger = logging.getLogger(__name__)


class UploadWorker:
    def __init__(self) -> None:
        self.queue: queue.Queue[tuple[str, str, Callable[[str, bool], None] | None]] = (
            queue.Queue()
        )
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self._first_message_id: int | None = None

    def start(self) -> None:
        self.thread.start()

    def reset_thread_anchor(self) -> None:
        self._first_message_id = None

    @property
    def first_message_id(self) -> int | None:
        return self._first_message_id

    @first_message_id.setter
    def first_message_id(self, value: int | None) -> None:
        self._first_message_id = value

    def _send_to_second_channel(self, files: list[tuple[str, str]]) -> None:
        if not Config.TELEGRAM_SECOND_CHANNEL_ID:
            return
        try:
            if len(files) == 1:
                file_path, caption = files[0]
                telegram.upload_document_to_chat(
                    file_path, caption, Config.TELEGRAM_SECOND_CHANNEL_ID
                )
            else:
                telegram.upload_media_group(
                    files,
                    chat_id=Config.TELEGRAM_SECOND_CHANNEL_ID,
                    file_type="document",
                )
        except Exception:
            logger.exception("Failed to upload to second Telegram channel")

    def upload_group(self, files: list[tuple[str, str]]) -> set[str]:
        uploaded_set: set[str] = set()
        if not files:
            return uploaded_set

        try:
            uploaded_set = {
                path for path, _ in files if db.is_uploaded(os.path.basename(path))
            }
            files = [
                (path, caption) for path, caption in files if path not in uploaded_set
            ]
            if not files:
                return uploaded_set
            if not db.claim_group_upload([os.path.basename(path) for path, _ in files]):
                logger.warning(
                    "Group upload already attempted; verify delivery before retrying. "
                    "Keeping local files: %s",
                    [os.path.basename(path) for path, _ in files],
                )
                return uploaded_set

            file_type = (
                "video" if Config.TELEGRAM_UPLOAD_MODE == "video" else "document"
            )
            result = telegram.upload_media_group(
                files=files,
                chat_id=Config.TELEGRAM_CHANNEL_ID,
                file_type=file_type,
            )
            if not result:
                logger.warning(
                    "Group delivery unconfirmed; keeping local files and blocking "
                    "automatic retries until delivery is verified"
                )
            if result:
                first_id = telegram.get_message_id(result[0])
                if self._first_message_id is None and first_id is not None:
                    self._first_message_id = first_id

                for i, (file_path, _) in enumerate(files):
                    filename = os.path.basename(file_path)
                    msg_id = (
                        telegram.get_message_id(result[i]) if i < len(result) else None
                    )
                    db.mark_uploaded(filename, msg_id)
                    uploaded_set.add(file_path)

                self._send_to_second_channel(files)

                for file_path, _ in files:
                    if os.path.exists(file_path):
                        os.remove(file_path)
        except Exception:
            logger.exception("Upload group error")
            # Don't delete files on error to avoid re-uploading duplicates if they were actually sent
            pass

        return uploaded_set

    def enqueue(
        self,
        file_path: str,
        caption: str,
        callback: Callable[[str, bool], None] | None = None,
    ) -> None:
        filename = os.path.basename(file_path)
        logger.info(f"Added new file to telegram upload queue: {filename}")
        self.queue.put((file_path, caption, callback))

    def _worker(self) -> None:
        while True:
            file_path, caption, callback = self.queue.get()
            success = False
            try:
                filename = os.path.basename(file_path)
                if db.is_uploaded(filename):
                    logger.info(f"Already uploaded: {filename}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if callback:
                        callback(file_path, False)
                    self.queue.task_done()
                    continue

                file_size_gb = os.path.getsize(file_path) / 1024 / 1024 / 1024
                logger.info(f"Uploading: {filename}, size: {file_size_gb:.2f}GiB")
                result = telegram.upload(file_path, caption)
                if result:
                    message_id = telegram.get_message_id(result)
                    logger.info(f"Uploaded file: {filename}")
                    db.mark_uploaded(filename, message_id)
                    try:
                        self._send_to_second_channel([(file_path, caption)])
                    except Exception:
                        logger.exception("Failed to upload to second channel")
                    if os.path.exists(file_path):
                        logger.info(f"Removing uploaded file: {filename}")
                        os.remove(file_path)
                    success = True
            except Exception:
                logger.exception("Upload worker error")
            if callback:
                try:
                    callback(file_path, success)
                except (KeyError, RuntimeError, TypeError, ValueError):
                    logger.exception("Upload callback failed")
            self.queue.task_done()


uploader: UploadWorker = UploadWorker()
