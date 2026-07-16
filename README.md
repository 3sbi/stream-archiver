# Twitch to Telegram archiver

This script detects when a live stream begins, records it in real time, and archives the recorded segments in a Telegram channel.

Optimized for and tested on low-spec machine (i.e. VPS with 1 GiB RAM and 1 vCPU), requires no more than 220Mb.

## Setup

Copy `.env.example` to `.env` and fill in the values.

### Environment Variables

| Variable                  | Required | Default                    | Description                                                                                                            |
| ------------------------- | -------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `TWITCH_CHANNEL`          | Yes      | —                          | Twitch channel name to monitor and record                                                                              |
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
| `CHECK_INTERVAL`          | No       | `10`                       | Seconds between live-status checks                                                                                     |
| `GRACE_PERIOD`            | No       | `240`                      | Seconds to wait after stream interruption before finalizing upload. If the stream resumes within this window, recording continues in the same session (files will be uploaded as the same media group) |
| `MIN_FREE_DISK_GB`        | No       | `2`                        | Minimum free disk space in GiB; recording stops when this is reached                                                   |
| `SEGMENT_TIME`            | No       | `2630`                     | Target segment duration in seconds. Each segment should stay under Telegram's 2 GiB upload limit (~2630s at 6200 kbps) |
| `DB_PATH`                 | No       | `/data/recorder.db`        | Path to the SQLite database file                                                                                       |
| `SEGMENTS_DIR`            | No       | `/data/segments`           | Directory for temporary segment files                                                                                  |
| `TIMEZONE`                | No       | `Europe/Moscow`            | Timezone for log timestamps                                                                                            |
| `LOG_LEVEL`               | No       | `INFO`                     | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`                                                                     |

## Running

### Docker (recommended)

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
python -m app.main
```
