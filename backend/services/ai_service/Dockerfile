FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend

CMD ["uvicorn", "services.ai_service.main:app", "--host", "0.0.0.0", "--port", "8005"]
