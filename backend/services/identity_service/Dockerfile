FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=10
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_PROGRESS_BAR=off

COPY requirements.txt /app/requirements.txt
COPY backend/services/identity_service/requirements.txt /app/identity_requirements.txt
RUN pip install --no-cache-dir --retries 10 --timeout 300 -r /app/requirements.txt && \
	pip install --no-cache-dir --retries 10 --timeout 300 torch==2.5.1+cpu --extra-index-url https://download.pytorch.org/whl/cpu && \
	pip install --no-cache-dir --retries 10 --timeout 300 -r /app/identity_requirements.txt

COPY backend /app/backend

CMD ["uvicorn", "services.identity_service.main:app", "--host", "0.0.0.0", "--port", "8010"]
