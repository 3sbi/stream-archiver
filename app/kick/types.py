from typing import TypedDict


class KickLivestream(TypedDict):
    id: int
    session_title: str
    created_at: str


class KickUser(TypedDict):
    id: int
    username: str


class KickChannelResponse(TypedDict):
    id: int
    slug: str
    name: str
    user: KickUser
    livestream: KickLivestream | None
    playback_url: str