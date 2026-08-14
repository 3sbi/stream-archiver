import logging
import random
import time
from pathlib import Path
from threading import Event
from typing import TypedDict

from websocket import WebSocket, WebSocketTimeoutException, create_connection

from app.chatlog.base import ChatMessage, format_chat_line, now_local
from app.config import Config

logger = logging.getLogger(__name__)

TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
RECV_TIMEOUT = 5
RECONNECT_DELAY = 5


class ParsedIrcLine(TypedDict):
    tags: dict[str, str]
    prefix: str | None
    prefix_nick: str | None
    command: str | None
    params: list[str]


def parse_irc_line(line: str) -> ParsedIrcLine | None:
    line = line.strip()
    if not line:
        return None

    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        tags_raw, _, rest = rest[1:].partition(" ")
        for pair in tags_raw.split(";"):
            key, sep, value = pair.partition("=")
            if sep:
                tags[key] = value

    prefix: str | None = None
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")

    if " :" in rest:
        head, _, trailing = rest.partition(" :")
        params = head.split(" ")
        command = params.pop(0) if params else None
        params.append(trailing)
    else:
        parts = rest.split(" ")
        command = parts.pop(0) if parts else None
        params = parts

    prefix_nick = prefix.split("!", 1)[0] if prefix else None
    return {
        "tags": tags,
        "prefix": prefix,
        "prefix_nick": prefix_nick,
        "command": command,
        "params": params,
    }


def _connect(channel: str) -> WebSocket:
    nick = f"justinfan{random.randint(1000, 9999)}"
    password = "SCHMOOPIIE"
    if Config.TWITCH_CHAT_OAUTH_TOKEN:
        nick = Config.TWITCH_CHAT_USERNAME or Config.CHANNEL
        password = f"oauth:{Config.TWITCH_CHAT_OAUTH_TOKEN}"

    ws = create_connection(TWITCH_IRC_URL, timeout=RECV_TIMEOUT)
    ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
    ws.send(f"PASS {password}")
    ws.send(f"NICK {nick}")
    ws.send(f"JOIN #{channel.lower()}")
    return ws


def _wait(stop_event: Event, seconds: int) -> None:
    for _ in range(seconds):
        if stop_event.is_set():
            return
        time.sleep(1)


def capture_twitch(channel: str, file_path: Path, stop_event: Event) -> None:
    while not stop_event.is_set():
        ws: WebSocket | None = None
        try:
            logger.info("Connecting to Twitch chat: %s", channel)
            ws = _connect(channel)
            logger.info("Twitch chat connected: %s", channel)
            with open(file_path, "a", encoding="utf-8") as fh:
                while not stop_event.is_set():
                    try:
                        raw = ws.recv()
                    except WebSocketTimeoutException:
                        continue
                    except (ConnectionError, OSError) as exc:
                        logger.warning("Twitch chat connection error: %s", exc)
                        break

                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")

                    for line in raw.splitlines():
                        parsed = parse_irc_line(line)
                        if not parsed:
                            continue
                        command = parsed["command"]
                        if command == "PING":
                            ws.send("PONG :tmi.twitch.tv")
                        elif command == "PRIVMSG":
                            params = parsed["params"]
                            if not params:
                                continue
                            content = params[-1]
                            if not content:
                                continue
                            user = (
                                parsed["tags"].get("display-name")
                                or parsed["prefix_nick"]
                                or "unknown"
                            )
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
        except Exception:
            logger.exception("Twitch chat capture error")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    logger.debug("Error closing Twitch chat socket", exc_info=True)
        if not stop_event.is_set():
            logger.info("Reconnecting to Twitch chat in %ds...", RECONNECT_DELAY)
            _wait(stop_event, RECONNECT_DELAY)
