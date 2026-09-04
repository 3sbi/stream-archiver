import logging
from dataclasses import dataclass
from typing import ClassVar

import requests

from app.config import Config
from app.wtv.types import WTVProfileResponse, WTVStreamsResponse

logger = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    title: str
    startedAt: str


class WTVClient:
    API_PROFILES: ClassVar = "https://profiles-service.w.tv/api/v1/profiles/by-nickname/{nickname}"
    API_STREAMS: ClassVar = "https://streams-search-service.w.tv/api/v1/channels/{channel_id}/streams"
    HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://w.tv/",
        "Origin": "https://w.tv",
    }

    def _get_user_id(self) -> str | None:
        try:
            response = requests.get(
                self.API_PROFILES.format(nickname=Config.WTV_CHANNEL),
                headers=self.HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data: WTVProfileResponse = response.json()
            profile = data.get("profile")
            if profile is None:
                return None
            return profile.get("userId")
        except requests.exceptions.ConnectionError:
            logger.warning("W.TV profiles API connection failed (network/DNS error)")
            raise
        except requests.exceptions.RequestException as e:
            logger.warning("W.TV profiles API request failed: %s", e)
            return None

    def get_stream_info(self) -> StreamInfo | None:
        user_id = self._get_user_id()
        if user_id is None:
            return None

        try:
            response = requests.get(
                self.API_STREAMS.format(channel_id=user_id),
                headers=self.HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data: WTVStreamsResponse = response.json()
        except requests.exceptions.ConnectionError:
            logger.warning("W.TV streams API connection failed (network/DNS error)")
            raise
        except requests.exceptions.RequestException as e:
            logger.warning("W.TV streams API request failed: %s", e)
            return None

        streams_data = data.get("data", [])
        for stream in streams_data:
            if stream.get("state") == "started":
                stream_id = stream.get("streamId", "")
                if not stream_id:
                    return None
                return StreamInfo(title=stream.get("title") or Config.WTV_CHANNEL, startedAt=stream_id)

        logger.debug("W.TV: no stream with state 'started' found")
        return None


wtv = WTVClient()
