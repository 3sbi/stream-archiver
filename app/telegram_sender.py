import time
import requests
from typing import TypedDict, NotRequired
from .config import Config


class TelegamMessage(TypedDict):
    message_id: int
    date: int
    text: NotRequired[str]
    caption: NotRequired[str]


class TelegramSender:
    def __init__(self) -> None:
        self.base_url: str = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"

    def _upload_video(self, file_path: str, caption: str) -> TelegamMessage:
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{self.base_url}/sendVideo",
                data={
                    "chat_id": Config.TELEGRAM_CHANNEL_ID,
                    "caption": caption,
                    "supports_streaming": True,
                },
                files={"video": f},
                timeout=7200,
            )
        response.raise_for_status()
        payload: TelegamMessage = response.json()
        return payload

    def _upload_document(self, file_path: str, caption: str):
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
                    except Exception as e:
                        print("Video upload failed, fallback to document:", e)
                        return self._upload_document(file_path, caption)
                else:
                    return self._upload_document(file_path, caption)
            except Exception as e:
                print(f"Upload failed (retry in {delay}s):", e)
                time.sleep(delay)
                delay = min(delay * 2, 600)

    @staticmethod
    def get_message_id(result: TelegamMessage) -> int | None:
        try:
            return result["message_id"]
        except (KeyError, TypeError):
            return None


telegram: TelegramSender = TelegramSender()
