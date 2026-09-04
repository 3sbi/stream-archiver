FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
# https://streamlink.github.io/latest/cli/plugin-sideloading.html#sideloading-locations
COPY plugins/wtv.py /root/.local/share/streamlink/plugins/wtv.py

CMD ["python", "-m", "app.main"]