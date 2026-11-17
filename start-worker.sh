#!/usr/bin/env bash
# KYC service — background worker: webhook delivery and retries, document
# clean-up. Set CELERY_BEAT=1 on exactly ONE worker to run the schedule.
set -euo pipefail

case "${DJANGO_SETTINGS_MODULE:-}" in
    config.settings.staging|config.settings.production) ;;
    *)
        echo "!!! DJANGO_SETTINGS_MODULE must be config.settings.staging or config.settings.production"
        echo "    (got '${DJANGO_SETTINGS_MODULE:-<unset>}')."
        exit 1 ;;
esac

if [ -z "${REDIS_URL:-}" ]; then
    echo "!!! REDIS_URL is not set: the worker has no queue to read. Add Redis and reference it."
    exit 1
fi

BEAT_ARGS=()
if [ "${CELERY_BEAT:-0}" = "1" ]; then
    # The app directory is not writable by the service user, and the schedule
    # is rebuilt from settings on start anyway: keep its state file in /tmp.
    BEAT_ARGS=(--beat --schedule=/tmp/celerybeat-schedule)
fi

exec celery -A config worker \
    --loglevel="${CELERY_LOG_LEVEL:-info}" \
    --concurrency="${CELERY_CONCURRENCY:-2}" \
    "${BEAT_ARGS[@]}"
