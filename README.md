# Twitch to Telegram archiver

Records Twitch streams live and uploads them to a Telegram channel.

## Setup

Copy `.env.example` to `.env` and fill in the values.

- register a new app at https://my.telegram.org/ to get `TELEGRAM_API_HASH` and `TELEGRAM_API_ID`
- `TELEGRAM_LOCAL` should always be `true` since we are referencing local files during telegram upload process. This is necessary to avoid loading the whole file to memory while making a request to a telegram bot API instance
- `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` are not necessary since we use Twitch's GraphQL API but it is unstable so it may break in the future

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

