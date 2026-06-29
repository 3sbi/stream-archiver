import os
import queue
import threading
import logging
from typing import Callable
from .database import db
from .telegram_sender import telegram
from .health import heartbeat


class UploadWorker:
    def __init__(self) -> None:
        self.queue: queue.Queue[tuple[str, str, Callable[[str, bool], None] | None]] = (
            queue.Queue()
        )
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self) -> None:
        self.thread.start()

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
            heartbeat()
            file_path, caption, callback = self.queue.get()
            success = False
            try:
                filename = os.path.basename(file_path)
                if db.is_uploaded(filename):
                    logging.info(f"Already uploaded: {filename}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    self.queue.task_done()
                    heartbeat()
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
            heartbeat()


uploader: UploadWorker = UploadWorker()
