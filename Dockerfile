FROM python:3.14-alpine

RUN apk add --no-cache su-exec

WORKDIR /app

ENV INBOX_LENS_DATA_DIR=/app/src/data

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY entrypoint.sh /entrypoint.sh

RUN sed -i 's/\r$//' /entrypoint.sh \
    && adduser -D -h /home/appuser -s /bin/sh appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD python3 /app/src/scripts/healthcheck.py

ENTRYPOINT ["/entrypoint.sh"]
