import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import requests

from app.config import Config

logger = logging.getLogger(__name__)

MAX_THUMBNAIL_SIZE = 200 * 1024  # 200 KB


class UploadError(Exception):
    """Base class for upload failures."""


class PermanentUploadError(UploadError):
    """Telegram rejected the request; nothing was delivered."""


class RateLimitUploadError(UploadError):
    """Telegram rate-limited the request; nothing was delivered."""


class AmbiguousUploadError(UploadError):
    """The request may have been delivered; avoid resending blindly."""


class TelegramMessage(TypedDict):
    message_id: int
    date: int
    text: NotRequired[str]
    caption: NotRequired[str]


class TelegramSender:
    def __init__(self) -> None:
        logger.info(
            f"Stream parts will be sent to TG channel {Config.TELEGRAM_CHANNEL_ID}"
        )
        if Config.TELEGRAM_SECOND_CHANNEL_ID:
            logger.info(
                f"Files will also be sent as documents to TG channel {Config.TELEGRAM_SECOND_CHANNEL_ID}"
            )
        self.base_url: str = f"{Config.TELEGRAM_API_URL}/bot{Config.TELEGRAM_BOT_TOKEN}"

    def _generate_thumbnail(self, video_path: str) -> str | None:
        watermark_text: str = Config.TELEGRAM_WATERMARK_TEXT

        if not shutil.which("ffmpeg"):
            logger.error("ffmpeg not found, cannot generate thumbnail")
            return None

        if not os.path.isfile(video_path):
            logger.error(f"Video file not found for thumbnail: {video_path}")
            return None

        if watermark_text:
            logger.info(
                f"Generating thumbnail with watermark '{watermark_text}' for video {video_path}"
            )
        else:
            logger.info(f"Generating thumbnail for video {video_path}")

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
                logger.debug(f"Running ffmpeg: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd, capture_output=True, timeout=40, check=False
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode("utf-8", errors="replace")
                    logger.warning(
                        f"ffmpeg thumbnail failed (exit code {result.returncode}): "
                        f"{stderr.strip()}"
                    )
                    continue
                file_size = os.path.getsize(thumb_path)
                if file_size >= MAX_THUMBNAIL_SIZE:
                    logger.debug(
                        f"Generated thumbnail size is too big. Max thumbnail image size for telegram: {(MAX_THUMBNAIL_SIZE / 1024):.2f}KB. Generated thubnail image size: {(file_size / 1024):.2f}KB "
                    )
                    continue
                if file_size == 0:
                    logger.warning(f"Thumbnail file is empty: {thumb_path}")
                    continue
                logger.info(f"Successfully generated thumbnail for {video_path}")
                return thumb_path

            except Exception:
                logger.exception("Unexpected error generating thumbnail")
                return None
        return None

    def _get_video_duration(self, video_path: str) -> int | None:
        if not shutil.which("ffprobe"):
            logger.warning("ffprobe not found, cannot determine video duration")
            return None

        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                logger.warning(
                    f"ffprobe failed for {video_path} "
                    f"(exit code {result.returncode}): {stderr.strip()}"
                )
                return None
            duration = round(float(result.stdout.decode().strip()))
            if duration <= 0:
                logger.warning(
                    f"Invalid duration detected for {video_path}: {duration}"
                )
                return None
            logger.debug(f"Detected duration of {duration}s for {video_path}")
            return duration
        except Exception:
            logger.exception(f"Unexpected error detecting duration for {video_path}")
            return None

    @staticmethod
    def _http_error_details(exc: requests.HTTPError) -> str:
        response = exc.response
        if response is None:
            return str(exc)
        description = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                description = str(
                    payload.get("description") or payload.get("error_code") or ""
                )
        except ValueError:
            description = (response.text or "").strip()
        details = f"HTTP {response.status_code}"
        if description:
            details = f"{details}: {description}"
        return details

    @staticmethod
    def _retry_after(response: requests.Response | None) -> int:
        if response is None:
            return 0
        try:
            payload = response.json()
            return int(payload.get("parameters", {}).get("retry_after", 0))
        except AttributeError, TypeError, ValueError:
            return 0

    def _classify_error(self, exc: Exception) -> UploadError:
        if isinstance(exc, UploadError):
            return exc
        if isinstance(exc, requests.HTTPError):
            status = exc.response.status_code if exc.response is not None else None
            details = self._http_error_details(exc)
            if status == 429:
                return RateLimitUploadError(details)
            if status is not None and 400 <= status < 500:
                return PermanentUploadError(details)
            return AmbiguousUploadError(details)
        return AmbiguousUploadError(f"{type(exc).__name__}: {exc}")

    def _post(
        self,
        url: str,
        *,
        data: dict[str, object],
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | tuple[float, float],
    ) -> requests.Response:
        delay = 5
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(url, data=data, files=files, timeout=timeout)
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and attempt < max_attempts:
                    wait = max(self._retry_after(exc.response), delay)
                    logger.warning(
                        "Telegram rate limit hit (%s, attempt %d/%d), retrying in %ss",
                        url.rsplit("/", 1)[-1],
                        attempt,
                        max_attempts,
                        wait,
                    )
                    time.sleep(wait)
                    delay = min(delay * 2, 60)
                    continue
                raise self._classify_error(exc) from exc
            except Exception as exc:
                raise self._classify_error(exc) from exc
        raise RateLimitUploadError("rate limit retries exhausted")

    def _request_json(
        self,
        url: str,
        *,
        data: dict[str, object],
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | tuple[float, float],
    ) -> Any:
        response = self._post(url, data=data, files=files, timeout=timeout)
        try:
            return response.json()
        except ValueError as exc:
            raise AmbiguousUploadError("unreadable response from Telegram") from exc

    def _upload_video(
        self, file_path: str, caption: str, chat_id: str | None = None
    ) -> TelegramMessage:
        logger.info("Uploading segment as a video...")
        data: dict[str, object] = {
            "chat_id": chat_id if chat_id is not None else Config.TELEGRAM_CHANNEL_ID,
            "caption": caption,
            "video": f"file://{file_path}",
            "supports_streaming": True,
        }
        duration = self._get_video_duration(file_path)
        if duration is not None:
            data["duration"] = duration
        files: dict[str, tuple[str, bytes, str]] = {}
        thumb_path = self._generate_thumbnail(file_path)
        if thumb_path:
            data["thumbnail"] = "attach://thumbnail"
            with open(thumb_path, "rb") as thumb_fh:
                files["thumbnail"] = ("thumb.jpg", thumb_fh.read(), "image/jpeg")

        try:
            payload = self._request_json(
                f"{self.base_url}/sendVideo",
                data=data,
                files=files,
                timeout=1800,
            )
        finally:
            if thumb_path:
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

        try:
            return payload["result"]
        except (KeyError, TypeError) as exc:
            raise AmbiguousUploadError("unexpected sendVideo response") from exc

    def _upload_document(self, file_path: str, caption: str, chat_id: str):
        logger.info(f"Uploading segment as a document to {chat_id}...")
        data: dict[str, object] = {
            "chat_id": chat_id,
            "caption": caption,
            "document": f"file://{file_path}",
        }
        payload = self._request_json(
            f"{self.base_url}/sendDocument",
            data=data,
            timeout=1800,
        )
        try:
            return payload["result"]
        except (KeyError, TypeError) as exc:
            raise AmbiguousUploadError("unexpected sendDocument response") from exc

    def upload(self, file_path: str, caption: str) -> TelegramMessage | None:
        chat_id = Config.TELEGRAM_CHANNEL_ID
        try:
            if Config.TELEGRAM_UPLOAD_MODE == "video":
                try:
                    return self._upload_video(file_path, caption)
                except PermanentUploadError:
                    logger.warning(
                        "Video upload rejected, falling back to document",
                        exc_info=True,
                    )
                    return self._upload_document(file_path, caption, chat_id)
            return self._upload_document(file_path, caption, chat_id)
        except UploadError as exc:
            logger.error("Upload failed: %s", exc)
            return None
        except Exception:
            logger.exception("Upload failed")
            return None

    def upload_document_to_chat(
        self, file_path: str, caption: str, chat_id: str
    ) -> TelegramMessage | None:
        try:
            return self._upload_document(file_path, caption, chat_id)
        except UploadError as exc:
            logger.error("Second channel upload failed: %s", exc)
            return None
        except Exception:
            logger.exception("Second channel upload failed")
            return None

    def upload_media_group(
        self,
        files: list[tuple[str, str]],
        chat_id: str,
        file_type: str,
    ) -> list[TelegramMessage]:
        if not 1 <= len(files) <= 10:
            raise ValueError("Upload batches must contain 1 to 10 files")
        if len(files) == 1:
            file_path, caption = files[0]
            if file_type == "video":
                message = self._upload_video(file_path, caption, chat_id)
            else:
                message = self._upload_document(file_path, caption, chat_id)
            return [message]

        media: list[dict[str, str | bool | int]] = []
        upload_files: dict[str, tuple[str, bytes, str]] = {}
        thumb_paths: list[str] = []

        for i, (file_path, caption) in enumerate(files):
            item: dict[str, str | bool | int] = {
                "type": file_type,
                "media": f"file://{file_path}",
            }

            # if you add captions to multiple or all items,
            # telegram will hide them and only show them when individual media files are tapped
            if i == 0:
                item["caption"] = caption

            if file_type == "video":
                item["supports_streaming"] = True
                duration = self._get_video_duration(file_path)
                if duration is not None:
                    item["duration"] = duration

            thumb_path = self._generate_thumbnail(file_path)
            if thumb_path:
                attach_key = f"thumb{i}"
                item["thumbnail"] = f"attach://{attach_key}"
                with open(thumb_path, "rb") as fh:
                    upload_files[attach_key] = (
                        f"thumb{i}.jpg",
                        fh.read(),
                        "image/jpeg",
                    )
                thumb_paths.append(thumb_path)

            media.append(item)

        data: dict[str, str | int] = {
            "chat_id": chat_id,
            "media": json.dumps(media),
        }
        try:
            payload = self._request_json(
                f"{self.base_url}/sendMediaGroup",
                data=data,
                files=upload_files,
                timeout=(30, 1800),
            )
        finally:
            for p in thumb_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

        try:
            return payload["result"]
        except (KeyError, TypeError) as exc:
            raise AmbiguousUploadError("unexpected sendMediaGroup response") from exc

    @staticmethod
    def get_message_id(result: TelegramMessage) -> int | None:
        try:
            return result["message_id"]
        except KeyError, TypeError:
            return None


telegram: TelegramSender = TelegramSender()
