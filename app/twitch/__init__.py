import time
import requests
from typing import Mapping

from app.twitch.types import (
    ComscoreStreamingQueryResponses,
    AuthResponse,
    StreamsApiResponse,
)
from app.config import Config
from app.types import StreamInfo


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

    def get_stream_info(self) -> StreamInfo | None:
        if Config.TWITCH_CLIENT_ID and Config.TWITCH_CLIENT_SECRET:
            return self.get_stream_info_from_api(Config.TWITCH_CLIENT_ID)
        return self.get_stream_info_from_gql()

    def get_stream_info_from_api(self, id: str) -> StreamInfo | None:
        token = self.get_token()
        print(id)
        headers: Mapping[str, str] = {
            "Client-ID": id,
            "Authorization": f"Bearer {token}",
        }

        response = requests.get(
            "https://api.twitch.tv/helix/streams",
            params={"user_login": Config.TWITCH_CHANNEL},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload: StreamsApiResponse = response.json()
        if not payload["data"]:
            return None
        # since we track only one user there would be only one steam
        stream = payload["data"][0]
        return StreamInfo(title=stream["title"], startedAt=stream["started_at"])

    def get_stream_info_from_gql(self) -> StreamInfo | None:
        """
        since Twitch does not allow to use proper API calls without 2FA enabled
        this is a temporary workaround to get stream info from Twitch's GraphQL

        magic variables for request headers and body copied from https://github.com/Brisppy/twitch-archiver/blob/ee1093d5e6ef9fbb9a7d0dbac5dd02ae963344eb/twitcharchiver/channel.py#L153-L166
        """
        response = requests.post(
            "https://gql.twitch.tv/gql",
            headers={"Client-Id": "ue6666qo983tsx6so1t0vnawi233wa"},
            json=[
                {
                    "extensions": {
                        "persistedQuery": {
                            "sha256Hash": "e1edae8122517d013405f237ffcc124515dc6ded82480a88daef69c83b53ac01",
                            "version": 1,
                        }
                    },
                    "operationName": "ComscoreStreamingQuery",
                    "variables": {
                        "isClip": False,
                        "channel": f"{Config.TWITCH_CHANNEL}",
                        "isLive": True,
                        "clipSlug": "",
                        "isVodOrCollection": False,
                        "vodID": "",
                    },
                }
            ],
        )
        response.raise_for_status()
        payload: ComscoreStreamingQueryResponses = response.json()
        user = payload[0]["data"]["user"]
        if user is not None and user["stream"] is not None:
            return StreamInfo(
                title=user["broadcastSettings"]["title"],
                startedAt=user["stream"]["createdAt"],
            )


twitch = TwitchClient()
