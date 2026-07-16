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

    def get_stream_info(self) -> StreamInfo | None:
        try:
            response = requests.get(
                f"{self.API_BASE}/channels/{Config.CHANNEL}",
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            logging.warning("Kick API connection failed (network/DNS error)")
            raise
        except requests.exceptions.RequestException:
            logging.warning("Kick API request failed")
            return None

        payload: KickChannelResponse = response.json()
        livestream = payload.get("livestream")
        if livestream is None:
            return None

        title = livestream.get("stream_title", "")
        started_at = livestream.get("started_at", "")
        if not title or not started_at:
            return None

        return StreamInfo(title=title, startedAt=started_at)


kick = KickClient()