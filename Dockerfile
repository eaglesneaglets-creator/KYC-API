# KYC service — image. Same shape as the ONA backend's: dependencies built
# once, copied into a lean runtime that runs as a non-root user.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

FROM base AS production
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --shell /bin/bash kyc
COPY --chown=kyc:kyc . .
RUN chmod +x start.sh start-worker.sh
# Static files for the admin, collected at build time with the local
# settings (throwaway keys; no database or secrets are touched).
RUN DJANGO_SETTINGS_MODULE=config.settings.local python manage.py collectstatic --noinput
USER kyc
EXPOSE 8000
CMD ["./start.sh"]
