"""
$description Russian live-streaming platform for live video streams.
$url w.tv
$type live
$metadata id
$metadata author
$metadata title
$metadata category
"""

from __future__ import annotations

import re

from streamlink.plugin import Plugin, pluginmatcher
from streamlink.plugin.api import validate
from streamlink.stream.hls import HLSStream


def _find_category(tags: list[dict[str, str]]) -> str | None:
    for tag in tags:
        if tag["tag"].startswith("subcategory:"):
            return tag["text"]
    return None


@pluginmatcher(
    re.compile(r"https?://w\.tv/(?P<channel>[^/?#]+)"),
)
class WTV(Plugin):
    _API_PROFILES = "https://profiles-service.w.tv/api/v1/profiles/by-nickname/{nickname}"
    _API_STREAMS = "https://streams-search-service.w.tv/api/v1/channels/{channel_id}/streams"

    def _get_streams(self):
        channel = self.match.group("channel")

        user_id = self.session.http.get(
            self._API_PROFILES.format(nickname=channel),
            acceptable_status=(200, 400),
            schema=validate.Schema(
                validate.parse_json(),
                validate.any(
                    validate.all(
                        {"profile": {"userId": str}},
                        validate.get(("profile", "userId")),
                    ),
                    validate.all(
                        {"errors": list},
                        validate.transform(lambda _: None),
                    ),
                ),
            ),
        )
        if not user_id:
            return

        streams = self.session.http.get(
            self._API_STREAMS.format(channel_id=user_id),
            schema=validate.Schema(
                validate.parse_json(),
                {
                    "data": [
                        {
                            "streamId": str,
                            "title": str,
                            "state": str,
                            "playbackUrl": validate.url(),
                            "tags": [
                                {
                                    "tag": str,
                                    "text": str,
                                },
                            ],
                        },
                    ],
                },
                validate.get("data"),
            ),
        )

        for stream in streams:
            if stream["state"] == "started":
                self.id = stream["streamId"]
                self.title = stream["title"]
                self.author = channel
                self.category = _find_category(stream["tags"])
                return HLSStream.parse_variant_playlist(self.session, stream["playbackUrl"])


__plugin__ = WTV
