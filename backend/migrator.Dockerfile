# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300

COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 -r /app/requirements.txt

COPY alembic.ini /app/alembic.ini
COPY backend /app/backend

CMD ["alembic", "upgrade", "head"]
