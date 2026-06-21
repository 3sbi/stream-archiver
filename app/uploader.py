import os
import queue
import threading
from .database import db
from .telegram_sender import telegram
from .health import heartbeat


class UploadWorker:
    def __init__(self) -> None:
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def enqueue(self, file_path: str, caption: str) -> None:
        self.queue.put((file_path, caption))

    def _worker(self) -> None:
        while True:
            heartbeat()
            file_path, caption = self.queue.get()
            try:
                filename = os.path.basename(file_path)
                if db.is_uploaded(filename):
                    print("Already uploaded:", filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    continue
                print("Uploading:", filename)
                result = telegram.upload(file_path, caption)
                message_id = telegram.get_message_id(result)
                db.mark_uploaded(filename, message_id)
                if os.path.exists(file_path):
                    os.remove(file_path)
                print("Uploaded:", filename)
            except Exception as e:
                print("Upload worker error:", e)
            finally:
                self.queue.task_done()
                heartbeat()


uploader: UploadWorker = UploadWorker()
