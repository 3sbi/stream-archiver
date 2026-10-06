from typing import Any, TypedDict


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


class HelixUser(TypedDict):
    id: str
    login: str
    display_name: str


class HelixUsersResponse(TypedDict):
    data: list[HelixUser]


class EventSubMetadata(TypedDict, total=False):
    message_id: str
    message_type: str
    message_timestamp: str


class EventSubSession(TypedDict, total=False):
    id: str
    status: str
    connected_at: str
    keepalive_timeout_seconds: int
    reconnect_url: str


class EventSubSubscription(TypedDict, total=False):
    id: str
    type: str
    version: str
    status: str
    condition: dict[str, Any]
    transport: dict[str, Any]


class EventSubMessage(TypedDict, total=False):
    metadata: EventSubMetadata
    payload: dict[
        str, Any
    ]  # welcome/reconnect: {"session": ...}, notification: {"subscription": ..., "event": ...}


class StreamOnlineEvent(TypedDict, total=False):
    id: str
    broadcaster_user_id: str
    broadcaster_user_login: str
    broadcaster_user_name: str
    type: str
    started_at: str


class StreamOfflineEvent(TypedDict, total=False):
    broadcaster_user_id: str
    broadcaster_user_login: str
    broadcaster_user_name: str
