import time
import requests
from typing import TypedDict, Mapping
from .config import Config


class AuthResponse(TypedDict):
    access_token: str
    expires_in: int
    token_type: str


class StreamMetadata(TypedDict):
    id: str
    user_id: str
    user_login: str
    user_name: str
    game_id: str
    game_name: str
    type: str
    title: str
    tags: list[str]
    viewer_count: int
    started_at: str
    language: str
    thumbnail_url: str
    tag_ids: list[str]
    is_mature: bool


class Pagination(TypedDict):
    cursor: str


class StreamsResponse(TypedDict):
    data: list[StreamMetadata]
    pagination: Pagination


class TwitchClient:
    def __init__(self):
        self.token = None
        self.refresh_at = 0

    def refresh_token(self):
        response = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": Config.TWITCH_CLIENT_ID,
                "client_secret": Config.TWITCH_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()
        data: AuthResponse = response.json()
        self.token = data["access_token"]
        # refresh 5 minutes before expiry
        self.refresh_at = time.time() + data["expires_in"] - 300
        print("Twitch token refreshed")

    def get_token(self):
        if self.token is None or time.time() >= self.refresh_at:
            self.refresh_token()
        return self.token

    def get_stream_info(self) -> StreamMetadata | None:
        token = self.get_token()
        headers: Mapping[str, str] = {
            "Client-ID": Config.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        }

        response = requests.get(
            "https://api.twitch.tv/helix/streams",
            params={"user_login": Config.TWITCH_CHANNEL},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload: StreamsResponse = response.json()
        if not payload["data"]:
            return None
        # since we track only one user there would be only one steam
        stream = payload["data"][0]
        return stream

    def is_live(self):
        metadata = self.get_stream_info()
        return metadata is not None


twitch = TwitchClient()
