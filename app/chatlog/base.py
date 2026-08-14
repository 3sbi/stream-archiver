from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import Config


@dataclass(frozen=True)
class ChatMessage:
    timestamp: str
    user: str
    content: str


def now_local() -> str:
    return datetime.now(UTC).isoformat()


def format_chat_line(message: ChatMessage) -> str:
    return f"[{message.timestamp}] {message.user}: {message.content}\n"


def build_chat_caption(title: str) -> str:
    date = datetime.now(ZoneInfo(Config.TIMEZONE)).strftime("%d.%m.%Y")
    caption = f"{title}\n{date}\n\nChat log"
    return caption[:1024]
