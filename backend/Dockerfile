# Образ backend: API (uvicorn), Celery worker и Celery beat — режим выбирается
# аргументом entrypoint: api | worker | beat | migrate (см. docker-entrypoint.sh).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    MEDIA_ROOT=/app/media

# ffmpeg — перекодирование TTS (wav → m4a/AAC) для голосовых ответов в Instagram.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid "${APP_GID}" app \
    && useradd --system --uid "${APP_UID}" --gid app \
       --home-dir /home/app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Зависимости отдельным слоем — кэшируются, пока не меняется requirements.txt.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения (владелец root, для пользователя app — только чтение).
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY app ./app

# sed — страховка от CRLF, если файл попал в контекст сборки с Windows-переводами строк.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/media \
    && chown -R app:app /app/media

USER app

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["api"]
