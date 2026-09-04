from typing import TypedDict


class WTVProfile(TypedDict):
    userId: str


class WTVProfileResponse(TypedDict):
    profile: WTVProfile | None


class WTVTag(TypedDict):
    tag: str
    text: str


class WTVStream(TypedDict):
    streamId: str
    title: str
    state: str
    playbackUrl: str
    tags: list[WTVTag]


class WTVStreamsResponse(TypedDict):
    data: list[WTVStream]
