FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# SQLite event log lives here; mount a volume so it survives restarts.
RUN mkdir -p /data
ENV DATAPULSE_DB_PATH=/data/datapulse_events.db

# Run as a non-root user.
RUN useradd --create-home --uid 10001 izgon && chown -R izgon:izgon /app /data
USER izgon

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
