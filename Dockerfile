FROM python:3.13-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --user --no-cache-dir -r requirements.txt

FROM python:3.13-slim

WORKDIR /app

# Python's zoneinfo module delegates to the operating-system timezone
# database. The slim image omits it, which would make every saved-search IANA
# timezone fail validation in the deployed web and worker containers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /root/.local /root/.local
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Kolkata'); ZoneInfo('Asia/Calcutta')"
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY job_hunt_agent/ ./job_hunt_agent/
COPY config/ ./config/
COPY scripts/ ./scripts/

EXPOSE 8000

CMD ["sh", "-c", "uvicorn job_hunt_agent.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
