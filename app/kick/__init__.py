import re
from streamlink import Streamlink
from streamlink.session.http import SSLContextAdapter
from ssl import OP_NO_TICKET
from streamlink.plugin.api import useragents

from app.config import Config
from app.types import StreamInfo


class KickAdapter(SSLContextAdapter):
    def get_ssl_context(self):
        ctx = super().get_ssl_context()
        ctx.options &= ~OP_NO_TICKET
        return ctx


class KickClient:
    def __init__(self):
        self.session = Streamlink()
        self.session.http.mount("https://kick.com/", KickAdapter())

    def _get_api_headers(self):
        ua = useragents.CHROME
        m = re.search(r"Chrome/(?P<full>(?P<main>\d+)\S+)", ua)
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "sec-ch-ua": f'"Not:A-Brand";v="24", "Chromium";v="{m["main"]}"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{m["full"]}"',
            "sec-ch-ua-full-version-list": f'"Not:A-Brand";v="24.0.0.0", "Chromium";v="{m["full"]}"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-model": '""',
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": '"6.14.0"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
        }

    def get_stream_info(self) -> StreamInfo | None:
        url = f"https://kick.com/api/v2/channels/{Config.KICK_CHANNEL}/livestream"
        headers = self._get_api_headers()
        headers["Referer"] = f"https://kick.com/{Config.KICK_CHANNEL}"
        headers["User-Agent"] = useragents.CHROME

        response = self.session.http.get(url, headers=headers, raise_for_status=False)
        if response.status_code != 200:
            return None

        data = response.json()
        stream = data.get("data")
        if stream is None:
            return None

        title = stream.get("session_title") or stream.get("title", "")

        started_at = stream.get("started_at") or stream.get("created_at", "")
        if started_at and "T" not in started_at:
            started_at = started_at.replace(" ", "T") + "Z"

        return StreamInfo(title=title, startedAt=started_at)


kick = KickClient()
