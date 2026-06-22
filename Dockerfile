FROM python:3.14-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        wget && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["python", "-m", "app.main"]