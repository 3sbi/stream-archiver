import os
import time
import shutil
import subprocess
import tempfile
import logging
import requests
from typing import BinaryIO, TypedDict, NotRequired
from pathlib import Path
from app.config import Config


class TelegramMessage(TypedDict):
    message_id: int
    date: int
    text: NotRequired[str]
    caption: NotRequired[str]


class TelegramResponse(TypedDict):
    ok: bool
    result: TelegramMessage


class TelegramSender:
    def __init__(self) -> None:
        logging.info(
            f"Stream parts will be sent to TG channel {Config.TELEGRAM_CHANNEL_ID}"
        )
        self.base_url: str = f"{Config.TELEGRAM_API_URL}/bot{Config.TELEGRAM_BOT_TOKEN}"

    def _generate_thumbnail(self, video_path: str) -> str | None:
        watermark_text: str = Config.TELEGRAM_WATERMARK_TEXT
        if not watermark_text:
            return None

        if not shutil.which("ffmpeg"):
            logging.error("ffmpeg not found, cannot generate watermark thumbnail")
            return None

        if not os.path.isfile(video_path):
            logging.error(f"Video file not found for watermark: {video_path}")
            return None

        logging.info(f"Adding watermark '{watermark_text}' for video {video_path}")
        thumb_dir = Path(tempfile.gettempdir()) / "video_thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = str(thumb_dir / f"{Path(video_path).stem}.jpg")
        watermark_filter = (
            f"scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,"
            f"drawtext="
            f"text='{watermark_text}':"
            f"x=20:"
            f"y=h-text_h-20:"
            f"fontsize=60:"
            f"fontcolor=white:"
            f"borderw=2:"
            f"bordercolor=black,"
            f"format=yuv420p"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "5",
            "-i",
            video_path,
            "-vf",
            watermark_filter,
            "-vframes",
            "1",
            "-q:v",
            "2",
            thumb_path,
        ]

        try:
            logging.debug(f"Running ffmpeg: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=40, check=False)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                if "font" in stderr.lower():
                    logging.warning(
                        "Font-related error in ffmpeg watermark. "
                        "Ensure fonts-dejavu-core (or similar) is installed. "
                        f"ffmpeg stderr: {stderr.strip()}"
                    )
                else:
                    logging.warning(
                        f"ffmpeg watermark failed (exit code {result.returncode}): "
                        f"{stderr.strip()}"
                    )
                return None
            if os.path.getsize(thumb_path) > 0:
                return thumb_path
            logging.warning(f"Thumbnail file is empty: {thumb_path}")
            return None
        except subprocess.TimeoutExpired:
            logging.warning(
                f"ffmpeg watermark timed out after 40s for {video_path}. "
                "The video file may be corrupted or the drawtext filter hung."
            )
            return None
        except Exception:
            logging.exception("Unexpected error generating watermark thumbnail")
            return None

    def _upload_video(self, file_path: str, caption: str) -> TelegramMessage:
        logging.info("Uploading segment as a video...")
        thumb_path = self._generate_thumbnail(file_path)
        thumb_fh = None
        try:
            with open(file_path, "rb") as file:
                files: dict[str, BinaryIO | tuple[str, BinaryIO, str]] = {"video": file}
                data: dict[str, object] = {
                    "chat_id": Config.TELEGRAM_CHANNEL_ID,
                    "caption": caption,
                    "supports_streaming": True,
                }
                if thumb_path:
                    data["thumbnail"] = "attach://thumbnail"
                    thumb_fh = open(thumb_path, "rb")
                    files["thumbnail"] = ("thumb.jpg", thumb_fh, "image/jpeg")
                response = requests.post(
                    url=f"{self.base_url}/sendVideo",
                    data=data,
                    files=files,
                    timeout=7200,
                )
                response.raise_for_status()
                payload: TelegramResponse = response.json()
                return payload["result"]
        finally:
            if thumb_fh:
                thumb_fh.close()
            if thumb_path:
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

    def _upload_document(self, file_path: str, caption: str):
        logging.info("Uploading segment as a document...")
        with open(file_path, "rb") as f:
            files: dict[str, BinaryIO | tuple[str, BinaryIO, str]] = {"document": f}
            data: dict[str, object] = {
                "chat_id": Config.TELEGRAM_CHANNEL_ID,
                "caption": caption,
            }
            response = requests.post(
                f"{self.base_url}/sendDocument",
                data=data,
                files=files,
                timeout=7200,
            )
            response.raise_for_status()
            payload: TelegramResponse = response.json()
            return payload["result"]


    def upload(self, file_path: str, caption: str) -> TelegramMessage | None:
        delay = 10
        max_retries = 5
        for attempt in range(max_retries):
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
                logging.exception(
                    f"Upload failed (attempt {attempt + 1}/{max_retries}, retry in {delay}s)",
                    exc_info=True,
                )
                time.sleep(delay)
                delay = min(delay * 2, 600)
        return None

    @staticmethod
    def get_message_id(result: TelegramMessage) -> int | None:
        try:
            return result["message_id"]
        except (KeyError, TypeError):
            return None


telegram: TelegramSender = TelegramSender()
