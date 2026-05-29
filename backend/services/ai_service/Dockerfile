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

# Pre-download all-MiniLM-L6-v2 (ONNX ~80 MB) into the image layer so
# containers start with the model ready — no runtime download needed.
RUN python -c "\
from fastembed import TextEmbedding; \
m = TextEmbedding('sentence-transformers/all-MiniLM-L6-v2'); \
list(m.embed(['warmup'])); \
print('all-MiniLM-L6-v2 ready')"

COPY backend /app/backend

CMD ["uvicorn", "services.ai_service.main:app", "--host", "0.0.0.0", "--port", "8005"]
