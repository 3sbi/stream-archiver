from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import requests
import websocket

from app.config import Config
from app.twitch.types import EventSubMessage

if TYPE_CHECKING:
    from app.twitch import TwitchClient

logger = logging.getLogger(__name__)

WS_URL = "wss://eventsub.wss.twitch.tv/ws"
SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
SUBSCRIPTION_TYPES = ("stream.online", "stream.offline")
KEEPALIVE_MARGIN = 15
MAX_BACKOFF = 60
SUBSCRIBE_RETRY_INTERVAL = 30
WATCHDOG_INTERVAL = 5


class EventSubListener:
    """
    Listens for Twitch EventSub events over WebSocket (stream.online / stream.offline).
    Requires TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET (app access token).
    """

    def __init__(
        self,
        client: TwitchClient,
        on_stream_online: Callable[[dict[str, Any]], None],
        on_stream_offline: Callable[[dict[str, Any]], None],
    ):
        self._client = client
        self._on_stream_online = on_stream_online
        self._on_stream_offline = on_stream_offline
        self._stop = threading.Event()
        self._ws: Any = None
        self._session_id: str | None = None
        self._reconnect_url: str | None = None
        self._keepalive_timeout = 10
        self._deadline = 0.0
        self._subscribed_session: str | None = None
        self._subscribe_lock = threading.Lock()
        self._last_subscribe_attempt = 0.0

    def start(self):
        threading.Thread(target=self._run, name="eventsub", daemon=True).start()
        threading.Thread(
            target=self._watchdog, name="eventsub-watchdog", daemon=True
        ).start()

    def stop(self):
        self._stop.set()
        self._close_ws()

    def _run(self):
        url = WS_URL
        backoff = 1
        while not self._stop.is_set():
            self._session_id = None
            self._deadline = 0.0
            ws = self._create_ws(url)
            self._ws = ws
            logger.info("Connecting to EventSub")
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                logger.exception("EventSub connection crashed")
            if self._stop.is_set():
                break
            if self._reconnect_url:
                url = self._reconnect_url
                self._reconnect_url = None
                backoff = 1
                logger.info("Reconnecting to EventSub reconnect URL")
                continue
            logger.warning("EventSub disconnected, retrying in %ds", backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    def _create_ws(self, url: str):
        return websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def _watchdog(self):
        while not self._stop.wait(WATCHDOG_INTERVAL):
            if self._deadline and time.time() > self._deadline:
                logger.warning("EventSub keepalive timeout, forcing reconnect")
                self._deadline = 0.0
                self._close_ws()
            if (
                self._session_id
                and self._subscribed_session != self._session_id
                and time.time() - self._last_subscribe_attempt
                > SUBSCRIBE_RETRY_INTERVAL
            ):
                self._spawn_subscribe()

    def _on_error(self, ws, error):
        logger.warning("EventSub error: %s", error)

    def _on_close(self, ws, close_status_code=None, close_msg=None):
        self._session_id = None
        self._deadline = 0.0
        if not self._stop.is_set():
            logger.info(
                "EventSub connection closed (code=%s, msg=%s)",
                close_status_code,
                close_msg,
            )

    def _on_message(self, ws, raw):
        try:
            message: EventSubMessage = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            logger.warning("EventSub received invalid JSON")
            return
        metadata = message.get("metadata") or {}
        payload = message.get("payload") or {}
        message_type = metadata.get("message_type")

        if message_type == "session_welcome":
            session = payload.get("session") or {}
            self._session_id = session.get("id")
            self._keepalive_timeout = int(
                session.get("keepalive_timeout_seconds") or 10
            )
            self._refresh_deadline()
            logger.info("EventSub session established: %s", self._session_id)
            self._spawn_subscribe()

        elif message_type == "session_keepalive":
            self._refresh_deadline()

        elif message_type == "session_reconnect":
            session = payload.get("session") or {}
            self._reconnect_url = session.get("reconnect_url")
            logger.info("EventSub session reconnect requested")
            self._close_ws()

        elif message_type == "notification":
            subscription = payload.get("subscription") or {}
            event = payload.get("event") or {}
            sub_type = subscription.get("type")
            if sub_type == "stream.online":
                logger.info("EventSub stream.online event: %s", event)
                self._safe_callback(self._on_stream_online, event)
            elif sub_type == "stream.offline":
                logger.info("EventSub stream.offline event: %s", event)
                self._safe_callback(self._on_stream_offline, event)

        elif message_type == "revocation":
            subscription = payload.get("subscription") or {}
            logger.warning(
                "EventSub subscription revoked: %s (%s)",
                subscription.get("type"),
                subscription.get("status"),
            )
            with self._subscribe_lock:
                if self._subscribed_session == self._session_id:
                    self._subscribed_session = None
            self._spawn_subscribe()

    def _spawn_subscribe(self):
        threading.Thread(
            target=self._subscribe, name="eventsub-subscribe", daemon=True
        ).start()

    def _subscribe(self):
        with self._subscribe_lock:
            session_id = self._session_id
            if not session_id or self._subscribed_session == session_id:
                return
            self._last_subscribe_attempt = time.time()
            broadcaster_id = self._client.get_user_id()
            if not broadcaster_id:
                logger.warning(
                    "EventSub subscription skipped: broadcaster id unavailable"
                )
                return
            client_id = Config.TWITCH_CLIENT_ID
            token = self._client.get_token()
            if not client_id or not token:
                logger.warning(
                    "EventSub subscription skipped: client id or token unavailable"
                )
                return
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}",
            }
            all_created = True
            for sub_type in SUBSCRIPTION_TYPES:
                body = {
                    "type": sub_type,
                    "version": "1",
                    "condition": {"broadcaster_user_id": broadcaster_id},
                    "transport": {"method": "websocket", "session_id": session_id},
                }
                try:
                    response = requests.post(
                        SUBSCRIPTIONS_URL, headers=headers, json=body, timeout=15
                    )
                except requests.exceptions.RequestException as exc:
                    logger.warning(
                        "EventSub %s subscription request failed: %s", sub_type, exc
                    )
                    all_created = False
                    continue
                if response.status_code == 409:
                    logger.info("EventSub %s subscription already exists", sub_type)
                elif response.ok:
                    logger.info("EventSub %s subscription created", sub_type)
                else:
                    logger.warning(
                        "EventSub %s subscription failed: %s %s",
                        sub_type,
                        response.status_code,
                        response.text,
                    )
                    all_created = False
            if all_created:
                self._subscribed_session = session_id

    def _refresh_deadline(self):
        if self._session_id:
            self._deadline = time.time() + self._keepalive_timeout + KEEPALIVE_MARGIN

    def _safe_callback(self, callback: Callable[[dict[str, Any]], None], event):
        try:
            callback(event)
        except Exception:
            logger.exception("EventSub callback failed")

    def _close_ws(self):
        ws = self._ws
        if ws is None:
            return
        try:
            ws.close()
        except Exception:
            logger.debug("EventSub ws close failed", exc_info=True)
