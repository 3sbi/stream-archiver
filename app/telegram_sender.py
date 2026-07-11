import os
import time
import json
import shutil
import subprocess
import tempfile
import logging
import requests
from typing import BinaryIO, TypedDict, NotRequired
from pathlib import Path
from app.config import Config

MAX_THUMBNAIL_SIZE = 200 * 1024  # 200 KB


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

        if not shutil.which("ffmpeg"):
            logging.error("ffmpeg not found, cannot generate thumbnail")
            return None

        if not os.path.isfile(video_path):
            logging.error(f"Video file not found for thumbnail: {video_path}")
            return None

        if watermark_text:
            logging.info(
                f"Generating thumbnail with watermark '{watermark_text}' for video {video_path}"
            )
        else:
            logging.info(f"Generating thumbnail for video {video_path}")

        thumb_dir = Path(tempfile.gettempdir()) / "video_thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = str(thumb_dir / f"{Path(video_path).stem}.jpg")

        vf_parts: list[str] = []
        if watermark_text:
            vf_parts.append(
                f"drawtext="
                f"text='{watermark_text}':"
                f"x=20:"
                f"y=h-text_h-20:"
                f"fontsize=60:"
                f"fontcolor=white:"
                f"borderw=2:"
                f"shadowx=2:"
                f"shadowy=2:"
                f"bordercolor=black"
            )

        for quality in [5, 10, 15, 20, 25, 30]:
            cmd: list[str] = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "5",
                "-i",
                video_path,
            ]

            if vf_parts:
                cmd.extend(["-vf", ",".join(vf_parts)])

            cmd.extend(
                [
                    "-frames:v",
                    "1",
                    "-q:v",
                    f"{quality}",
                    thumb_path,
                ]
            )

            try:
                logging.debug(f"Running ffmpeg: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd, capture_output=True, timeout=40, check=False
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode("utf-8", errors="replace")
                    logging.warning(
                        f"ffmpeg thumbnail failed (exit code {result.returncode}): "
                        f"{stderr.strip()}"
                    )
                    continue
                file_size = os.path.getsize(thumb_path)
                if file_size >= MAX_THUMBNAIL_SIZE:
                    logging.debug(
                        f"Generated thumbnail size is too big. Max thumbnail image size for telegram: {(MAX_THUMBNAIL_SIZE / 1024):.2f}KB. Generated thubnail image size: {(file_size / 1024):.2f}KB "
                    )
                    continue
                if file_size == 0:
                    logging.info(f"Thumbnail file is empty: {thumb_path}")
                    continue
                logging.debug(f"Successfully generated thumbnail for {video_path}")
                return thumb_path

            except Exception:
                logging.exception("Unexpected error generating thumbnail")
                return None
        return None

    def _upload_video(self, file_path: str, caption: str) -> TelegramMessage:
        logging.info("Uploading segment as a video...")
        data: dict[str, object] = {
            "chat_id": Config.TELEGRAM_CHANNEL_ID,
            "caption": caption,
            "video": f"file://{file_path}",
            "supports_streaming": True,
        }
        files: dict[str, tuple[str, BinaryIO, str]] = {}
        thumb_path = self._generate_thumbnail(file_path)
        thumb_fh: BinaryIO | None = None
        if thumb_path:
            data["thumbnail"] = "attach://thumbnail"
            thumb_fh = open(thumb_path, "rb")
            files["thumbnail"] = ("thumb.jpg", thumb_fh, "image/jpeg")

        try:
            response = requests.post(
                url=f"{self.base_url}/sendVideo",
                data=data,
                timeout=1800,
                files=files,
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
        data: dict[str, object] = {
            "chat_id": Config.TELEGRAM_CHANNEL_ID,
            "caption": caption,
            "document": f"file://{file_path}",
        }
        response = requests.post(
            f"{self.base_url}/sendDocument",
            data=data,
            timeout=1800,
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
                    except requests.exceptions.ReadTimeout:
                        raise
                    except Exception:
                        logging.exception(
                            "Video upload failed, fallback to document", exc_info=True
                        )
                        return self._upload_document(file_path, caption)
                else:
                    return self._upload_document(file_path, caption)
            except requests.exceptions.ReadTimeout:
                logging.warning(
                    f"Telegram upload timed out (attempt {attempt + 1}/{max_retries}, retry in {delay}s)"
                )
                time.sleep(delay)
                delay = min(delay * 2, 600)
            except Exception:
                logging.exception(
                    f"Upload failed (attempt {attempt + 1}/{max_retries}, retry in {delay}s)"
                )
                time.sleep(delay)
                delay = min(delay * 2, 600)
        return None

    def upload_media_group(
        self,
        files: list[tuple[str, str]],
    ) -> list[TelegramMessage] | None:
        media: list[dict[str, str | bool]] = []
        upload_files: dict[str, tuple[str, BinaryIO, str]] = {}
        thumb_paths: list[str] = []
        thumb_handles: list[BinaryIO] = []
        file_type = "video" if Config.TELEGRAM_UPLOAD_MODE == "video" else "document"
        for i, (file_path, caption) in enumerate(files):
            item: dict[str, str | bool] = {
                "type": file_type,
                "media": f"file://{file_path}",
            }

            # if you add captions to multiple or all items,
            # telegram will hide them and only show them when individual media files are tapped
            if i == 0:
                item["caption"] = caption

            if file_type == 'video':
                item["supports_streaming"] = True

            thumb_path = self._generate_thumbnail(file_path)
            if thumb_path:
                attach_key = f"thumb{i}"
                item["thumbnail"] = f"attach://{attach_key}"
                fh = open(thumb_path, "rb")
                upload_files[attach_key] = (
                    f"thumb{i}.jpg",
                    fh,
                    "image/jpeg",
                )
                thumb_handles.append(fh)
                thumb_paths.append(thumb_path)

            media.append(item)

        data: dict[str, str | int] = {
            "chat_id": Config.TELEGRAM_CHANNEL_ID,
            "media": json.dumps(media),
        }
        delay = 10
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/sendMediaGroup",
                    data=data,
                    timeout=(30, 1800),
                    files=upload_files,
                )
                response.raise_for_status()
                return response.json()["result"]
            except requests.exceptions.ReadTimeout:
                logging.warning("Telegram media group upload timed out, retrying...")
            except Exception:
                logging.exception(
                    f"Media group upload failed "
                    f"(attempt {attempt + 1}/{max_retries}, retry in {delay}s)"
                )
                time.sleep(delay)
                delay = min(delay * 2, 600)
            finally:
                for fh in thumb_handles:
                    try:
                        fh.close()
                    except Exception:
                        pass
                for p in thumb_paths:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        return None

    @staticmethod
    def get_message_id(result: TelegramMessage) -> int | None:
        try:
            return result["message_id"]
        except (KeyError, TypeError):
            return None


telegram: TelegramSender = TelegramSender()
