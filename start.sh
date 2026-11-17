#!/usr/bin/env bash
# KYC service — web entrypoint: refuse unsafe settings, migrate, serve.
set -euo pipefail

case "${DJANGO_SETTINGS_MODULE:-}" in
    config.settings.staging|config.settings.production) ;;
    *)
        echo "!!! DJANGO_SETTINGS_MODULE must be config.settings.staging or config.settings.production"
        echo "    (got '${DJANGO_SETTINGS_MODULE:-<unset>}'). The local settings use public keys."
        exit 1 ;;
esac

echo "==> Running migrations..."
python manage.py migrate --noinput

echo "==> Starting gunicorn on 0.0.0.0:${PORT:-8000}"
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
