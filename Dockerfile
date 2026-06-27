FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl gosu && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV INBOX_LENS_DATA_DIR=/app/src/data

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY entrypoint.sh /entrypoint.sh

RUN useradd -m appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
