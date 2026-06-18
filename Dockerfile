FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV LOOP_UPLOAD_ROOT=/var/data/uploads
ENV LOOP_JOBS_DB=/var/data/jobs.db
ENV LOOP_PIPELINE_DB=/var/data/pipeline.db
ENV LOOP_UPLOAD_RETENTION_DAYS=30

CMD gunicorn apicalls.api:app --bind 0.0.0.0:${PORT:-5000} --timeout 1800 --workers 1
