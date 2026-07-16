from typing import TypedDict


class KickLivestream(TypedDict, total=False):
    id: int
    stream_title: str
    started_at: str
    is_live: bool
    viewer_count: int
    thumbnail: str


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