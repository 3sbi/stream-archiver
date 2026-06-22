from typing import TypedDict


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


class StreamsApiResponse(TypedDict):
    data: list[StreamMetadata]
    pagination: Pagination


class Channel(TypedDict):
    id: str


class Stream(TypedDict):
    id: str
    createdAt: str


class BroadcastSettings(TypedDict):
    id: str
    title: str


class User(TypedDict):
    id: str
    displayName: str
    stream: Stream | None
    broadcastSettings: BroadcastSettings


class Data(TypedDict):
    user: User | None


class ComscoreStreamingQueryResponse(TypedDict):
    data: Data


type ComscoreStreamingQueryResponses = list[ComscoreStreamingQueryResponse]
