FROM python:3.13-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --user --no-cache-dir -r requirements.txt

FROM python:3.13-slim

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /root/.local /root/.local
COPY job_hunt_agent/ ./job_hunt_agent/
COPY config/ ./config/

EXPOSE 8000

CMD ["sh", "-c", "uvicorn job_hunt_agent.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
