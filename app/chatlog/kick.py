import logging
import time
from pathlib import Path
from threading import Event

import requests

from app.chatlog.base import ChatMessage, format_chat_line, now_local
from app.config import Config

logger = logging.getLogger(__name__)

KICK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://kick.com/",
    "Origin": "https://kick.com",
}


def _resolve_channel_id(channel: str) -> int | None:
    try:
        response = requests.get(
            f"https://kick.com/api/v2/channels/{channel}",
            headers=KICK_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("id")
    except (requests.exceptions.RequestException, ValueError):
        logger.warning("Kick channel ID resolution failed, falling back to slug")
        return None


def _fetch_messages(channel: str, channel_id: int | None) -> list[dict]:
    path = channel_id if channel_id is not None else channel
    response = requests.get(
        f"https://kick.com/api/v2/channels/{path}/messages",
        headers=KICK_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json().get("data", [])
    if isinstance(payload, dict):
        payload = payload.get("messages", [])
    return list(payload) if isinstance(payload, list) else []


def capture_kick(channel: str, file_path: Path, stop_event: Event) -> None:
    channel_id = _resolve_channel_id(channel)
    seen: set[str] = set()
    next_poll = 0.0
    while not stop_event.is_set():
        try:
            if time.time() < next_poll:
                time.sleep(0.5)
                continue
            messages = _fetch_messages(channel, channel_id)
            next_poll = time.time() + Config.KICK_CHAT_POLL_INTERVAL
        except requests.exceptions.RequestException as exc:
            logger.warning("Kick chat poll failed: %s", exc)
            time.sleep(Config.KICK_CHAT_POLL_INTERVAL)
            continue
        except Exception:
            logger.exception("Kick chat capture error")
            time.sleep(Config.KICK_CHAT_POLL_INTERVAL)
            continue

        if not messages:
            continue
        with open(file_path, "a", encoding="utf-8") as fh:
            for message in reversed(messages):
                message_id = str(message.get("id") or message.get("uuid") or "")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                if message.get("type") not in ("message", "reply"):
                    continue
                content = message.get("content") or ""
                if not content:
                    continue
                sender = message.get("sender") or {}
                user = sender.get("username") or sender.get("slug") or "unknown"
                fh.write(
                    format_chat_line(
                        ChatMessage(
                            timestamp=now_local(),
                            user=user,
                            content=content,
                        )
                    )
                )
                fh.flush()
