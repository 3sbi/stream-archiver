FROM python:3.14-slim

RUN apt-get update && \
    apt-get install -y \
        ffmpeg \
        wget && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir streamlink

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["python", "-m", "app.main"]