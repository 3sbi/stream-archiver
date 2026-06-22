import time
import requests
import logging
from typing import TypedDict, NotRequired
from app.config import Config


class TelegamMessage(TypedDict):
    message_id: int
    date: int
    text: NotRequired[str]
    caption: NotRequired[str]


class TelegramSender:
    def __init__(self) -> None:
        self.base_url: str = f"{Config.TELEGRAM_API_URL}/bot{Config.TELEGRAM_BOT_TOKEN}"

    def _upload_video(self, file_path: str, caption: str) -> TelegamMessage:
        logging.info("Uploading segment as a video...")
        with open(file_path, "rb") as file:
            response = requests.post(
                url=f"{self.base_url}/sendVideo",
                data={
                    "chat_id": Config.TELEGRAM_CHANNEL_ID,
                    "caption": caption,
                    "supports_streaming": True,
                },
                files={"video": file},
                timeout=7200,
            )
            response.raise_for_status()
            payload: TelegamMessage = response.json()
            return payload

    def _upload_document(self, file_path: str, caption: str):
        logging.info("Uploading segment as a document...")
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{self.base_url}/sendDocument",
                data={"chat_id": Config.TELEGRAM_CHANNEL_ID, "caption": caption},
                files={"document": f},
                timeout=7200,
            )
            response.raise_for_status()
            payload: TelegamMessage = response.json()
            return payload

    def upload(self, file_path: str, caption: str):
        delay = 10
        while True:
            try:
                if Config.TELEGRAM_UPLOAD_MODE == "video":
                    try:
                        return self._upload_video(file_path, caption)
                    except Exception:
                        logging.exception(
                            "Video upload failed, fallback to document", exc_info=True
                        )
                        return self._upload_document(file_path, caption)
                else:
                    return self._upload_document(file_path, caption)
            except Exception:
                logging.exception(f"Upload failed (retry in {delay}s)", exc_info=True)
                time.sleep(delay)
                delay = min(delay * 2, 600)

    @staticmethod
    def get_message_id(result: TelegamMessage) -> int | None:
        try:
            return result["message_id"]
        except (KeyError, TypeError):
            return None


telegram: TelegramSender = TelegramSender()
