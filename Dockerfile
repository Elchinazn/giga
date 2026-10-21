FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SUBS_FILE=/data/subscriptions.json

WORKDIR /app

# tzdata — база часовых поясов для BOT_TIMEZONE, в slim-образе её может не быть
COPY requirements.txt .
RUN pip install -r requirements.txt tzdata

COPY bot ./bot
COPY main.py .

# Бот работает не от root; /data — том для файла подписок
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot
VOLUME ["/data"]

CMD ["python", "main.py"]
