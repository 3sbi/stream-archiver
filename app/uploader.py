import os
import queue
import threading
import logging
from typing import Callable
from .database import db
from .telegram_sender import telegram


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

    def upload_group(self, files: list[tuple[str, str]]) -> set[str]:
        uploaded_set: set[str] = set()
        if not files:
            return uploaded_set

        try:
            result = telegram.upload_media_group(files)
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
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    uploaded_set.add(file_path)
        except Exception:
            logging.exception("Upload group error")
            for file_path, _ in files:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

        return uploaded_set

    def enqueue(
        self,
        file_path: str,
        caption: str,
        callback: Callable[[str, bool], None] | None = None,
    ) -> None:
        filename = os.path.basename(file_path)
        logging.info(f"Added new file to telegram upload queue: {filename}")
        self.queue.put((file_path, caption, callback))

    def _worker(self) -> None:
        while True:
            file_path, caption, callback = self.queue.get()
            success = False
            try:
                filename = os.path.basename(file_path)
                if db.is_uploaded(filename):
                    logging.info(f"Already uploaded: {filename}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if callback:
                        try:
                            callback(file_path, False)
                        except Exception:
                            pass
                    self.queue.task_done()
                    continue

                file_size_gb = os.path.getsize(file_path) / 1024 / 1024 / 1024
                logging.info(f"Uploading: {filename}, size: {file_size_gb:.2f}GiB")
                result = telegram.upload(file_path, caption)
                if result:
                    message_id = telegram.get_message_id(result)
                    logging.info(f"Uploaded file: {filename}")
                    db.mark_uploaded(filename, message_id)
                    if os.path.exists(file_path):
                        logging.info(f"Removing uploaded file: {filename}")
                        os.remove(file_path)
                    success = True
            except Exception:
                logging.exception("Upload worker error", exc_info=True)
            if callback:
                try:
                    callback(file_path, success)
                except Exception:
                    pass
            self.queue.task_done()


uploader: UploadWorker = UploadWorker()
