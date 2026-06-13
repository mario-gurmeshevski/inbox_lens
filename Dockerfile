FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY entrypoint.sh /entrypoint.sh

RUN useradd -m appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app \
    && chmod +x /entrypoint.sh

USER appuser

ENV DB_PATH=/app/data/emails.db
ENV SECRET_KEY_PATH=/app/data/.secret.key
ENV KEYWORDS_FILE=/app/data/keywords.json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
