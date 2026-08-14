# Twitch/Kick to Telegram archiver

Detects when a live stream begins on Twitch or Kick, records it in real time, and archives the recorded segments in a Telegram channel.

Optimized for and tested on low-spec machine (i.e. VPS with 1 GiB RAM and 1 vCPU), requires no more than 220Mb.

## Setup

Copy `.env.example` to `.env` and fill in the values.

### Environment Variables

| Variable                  | Required | Default                    | Description                                                                                                            |
| ------------------------- | -------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `CHANNEL`                 | Yes      | —                          | Channel name to monitor and record                                                                                     |
| `PLATFORM`                | Yes      | —                          | Streaming platform: `twitch` or `kick`                                                                                 |
| `TWITCH_CLIENT_ID`        | No       | —                          | Twitch app client ID (optional — GraphQL API is used by default)                                                       |
| `TWITCH_CLIENT_SECRET`    | No       | —                          | Twitch app client secret (optional — GraphQL API is used by default)                                                   |
| `TELEGRAM_BOT_TOKEN`      | Yes      | —                          | Bot token from [@BotFather](https://t.me/BotFather)                                                                    |
| `TELEGRAM_CHANNEL_ID`     | Yes      | —                          | Target channel/chat ID or public handle starting with @                                                                |
| `TELEGRAM_API_ID`         | Yes      | —                          | App API ID from [my.telegram.org](https://my.telegram.org)                                                             |
| `TELEGRAM_API_HASH`       | Yes      | —                          | App API hash from [my.telegram.org](https://my.telegram.org)                                                           |
| `TELEGRAM_API_URL`        | No       | `https://api.telegram.org` | Telegram Bot API server URL (use `http://telegram-bot-api:8081` with docker)                                           |
| `TELEGRAM_LOCAL`          | No       | `true`                     | Should always be `true` — allows uploading local files without loading them entirely into memory                       |
| `TELEGRAM_UPLOAD_MODE`    | No       | `video`                    | Upload format: `video` or `document`                                                                                   |
| `TELEGRAM_WATERMARK_TEXT` | No       | —                          | Text to render as a watermark on the video (set to `channel_name` to use the channel name, or leave empty to disable)  |
| `GROUP_SEGMENTS`          | No       | `false`                    | When `true`, segments are collected and sent as a media group once the stream ends or disk space is low                |
| `CHAT_LOGGING`            | No       | `true`                     | Capture chat during the stream and upload it as a `.txt` document when the stream ends                                |
| `TWITCH_CHAT_OAUTH_TOKEN` | No       | —                          | Optional Twitch user access token (with `chat:read`); when empty, chat is read anonymously                             |
| `TWITCH_CHAT_USERNAME`    | No       | —                          | Lowercased login of the account the `TWITCH_CHAT_OAUTH_TOKEN` belongs to (required when a token is set)                |
| `KICK_CHAT_POLL_INTERVAL` | No       | `3`                        | Seconds between Kick chat polls. Kick has no IRC, so chat is polled via its undocumented `/api/v2` endpoint            |
| `CHECK_INTERVAL`          | No       | `10`                       | Seconds between live-status checks                                                                                     |
| `GRACE_PERIOD`            | No       | `240`                      | Seconds to wait after a stream interruption or an unexpected recorder crash before finalizing the upload. If the stream resumes within this window, recording continues in the same session (files will be uploaded as the same media group) |
| `MIN_FREE_DISK_GB`        | No       | `2`                        | Minimum free disk space in GiB; recording stops when this is reached                                                   |
| `SEGMENT_TIME`            | No       | `2630`                     | Target segment duration in seconds. Each segment should stay under Telegram's 2 GiB upload limit (~2630s at 6200 kbps) |
| `DB_PATH`                 | No       | `/data/recorder.db`        | Path to the SQLite database file                                                                                       |
| `SEGMENTS_DIR`            | No       | `/data/segments`           | Directory for temporary segment files                                                                                  |
| `TIMEZONE`                | No       | `Europe/Moscow`            | Timezone for log timestamps                                                                                            |
| `LOG_LEVEL`               | No       | `INFO`                     | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`                                                                     |

## Chat logging

While a stream is live, chat is captured to `{session}_chat.txt` and uploaded to the channel as a document when the stream ends.

- **Twitch** uses IRC over WebSocket (`wss://irc-ws.chat.twitch.tv:443`). By default it connects anonymously; set `TWITCH_CHAT_OAUTH_TOKEN` (with `chat:read` scope) and `TWITCH_CHAT_USERNAME` for an authenticated connection.
- **Kick** has no IRC or public chat API, so chat is polled every `KICK_CHAT_POLL_INTERVAL` seconds from Kick's undocumented `/api/v2` endpoint. Heavy chats may drop messages between polls.

## Chat subtitles

`scripts/chat_to_ass.py` converts a chat log into an **ASS** subtitle that renders a Twitch-style chat overlay in the top-right corner of the video: a panel (default up to 15% of the width and 50% of the height) that holds as many recent messages as fit. New messages appear at the bottom of the panel and push older ones up; the oldest message that no longer fits is hidden. ASS is required because plain SRT cannot do positioning and boxes; VLC loads it the same way.

```bash
# Basic usage — writes <chat>.ass next to the chat log
python scripts/chat_to_ass.py data/twitch/segments/rostislav_999_2026-08-14T04-09-02_chat.txt

# With the matching video: picks up resolution from it, aligns timestamps to
# that segment's start, and names the output <segment>.ass so VLC auto-loads it
python scripts/chat_to_ass.py data/twitch/segments/rostislav_999_2026-08-14T04-09-02_chat.txt \
    --segment data/twitch/segments/rostislav_999_2026-08-14T04-09-02_0.mp4
```

In VLC: open the video, then `Subtitle → Add Subtitle File...` and pick the `.ass` file (or drop both files in the same folder with matching names).

Options: `--resolution WxH`, `--font-size`, `--display` (seconds each message stays, default 6), `--box-width`, `--box-height` (percent of the video), `--tz` (chat timestamp timezone, defaults to `TIMEZONE` env / `Europe/Moscow`), `--offset` (manual sync shift in seconds), `--out`.

## Running

### Docker (recommended)

Each platform runs as a separate service with its own environment file and data directory.

```bash
docker compose up -d
```

### Manually

##### Requirements

- Python 3.12+
- ffmpeg
- Telegram Bot API server

```bash
pip install -r requirements.txt
CHANNEL=xqc PLATFORM=twitch python -m app.main
```
