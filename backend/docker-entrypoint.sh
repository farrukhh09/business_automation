#!/bin/sh
# Entrypoint образа backend.
#   api      — миграции (с ожиданием БД) → создание первого администратора → uvicorn
#   worker   — Celery worker
#   beat     — Celery beat (файл расписания в /tmp)
#   migrate  — только миграции (docker compose run --rm backend migrate)
#   <другое> — выполнить переданную команду как есть
#              (например: docker compose run --rm backend python -m scripts.seed_demo)
set -e

MIGRATION_ATTEMPTS=30
MIGRATION_DELAY_SECONDS=2

log() {
    echo "[entrypoint] $*"
}

run_migrations() {
    attempt=1
    while [ "$attempt" -le "$MIGRATION_ATTEMPTS" ]; do
        if alembic upgrade head; then
            log "migrations applied"
            return 0
        fi
        log "alembic upgrade head failed (attempt ${attempt}/${MIGRATION_ATTEMPTS}); database may not be ready yet, retrying in ${MIGRATION_DELAY_SECONDS}s"
        attempt=$((attempt + 1))
        sleep "$MIGRATION_DELAY_SECONDS"
    done
    log "ERROR: could not apply migrations after ${MIGRATION_ATTEMPTS} attempts"
    return 1
}

create_first_admin() {
    if [ -z "${FIRST_ADMIN_PASSWORD:-}" ]; then
        log "FIRST_ADMIN_PASSWORD is empty, skipping first admin creation"
        return 0
    fi
    if ! python -m scripts.create_admin; then
        log "WARNING: scripts.create_admin failed, starting API anyway"
    fi
    return 0
}

MODE="${1:-api}"

# X-Forwarded-For/-Proto are trusted only from loopback and private networks
# (reverse proxy on the host via docker-proxy, frontend container, compose network).
# A client connecting to the published port from a public IP cannot spoof its
# address (rate limiting). Override with FORWARDED_ALLOW_IPS in .env if needed.
TRUSTED_PROXY_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7}"

case "$MODE" in
    api)
        run_migrations || exit 1
        create_first_admin
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port 8000 \
            --proxy-headers \
            --forwarded-allow-ips "$TRUSTED_PROXY_IPS"
        ;;
    worker)
        exec celery -A app.tasks.celery_app worker -l INFO --concurrency 2
        ;;
    beat)
        exec celery -A app.tasks.celery_app beat -l INFO --schedule /tmp/celerybeat-schedule
        ;;
    migrate)
        run_migrations || exit 1
        ;;
    *)
        exec "$@"
        ;;
esac
