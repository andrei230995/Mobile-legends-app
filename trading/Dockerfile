# Tradebot backend + web app. Build context: the trading/ directory.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 tradebot
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/tradebot backend/tradebot
COPY docs/validation docs/validation
RUN mkdir -p /data && chown tradebot:tradebot /data
USER tradebot
WORKDIR /app/backend
ENV TRADEBOT_DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# One worker: the trading engine runs in a single background thread of this process.
CMD ["uvicorn", "tradebot.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
