import requests
import logging
from dataclasses import dataclass

from app.config import Config
from app.kick.types import KickChannelResponse


@dataclass
class StreamInfo:
    title: str
    startedAt: str


class KickClient:
    API_BASE = "https://kick.com/api/v1"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://kick.com/",
        "Origin": "https://kick.com",
    }

    def get_stream_info(self) -> StreamInfo | None:
        try:
            response = requests.get(
                f"{self.API_BASE}/channels/{Config.CHANNEL}",
                headers=self.HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data: KickChannelResponse = response.json()
            livestream = data["livestream"]
            if livestream is None:
                return None

            title = livestream["session_title"]
            created_at = livestream["created_at"]
            if not title or not created_at:
                return None

            return StreamInfo(title=title, startedAt=created_at)
        except requests.exceptions.ConnectionError:
            logging.warning("Kick API connection failed (network/DNS error)")
            raise
        except requests.exceptions.RequestException as e:
            response = getattr(e, "response", None)
            if response is not None:
                status = response.status_code
                body = response.text
                logging.warning(
                    "Kick API request failed: status=%s body=%s", status, body
                )
            else:
                logging.warning("Kick API request failed: %s", e)
            return None


kick = KickClient()
