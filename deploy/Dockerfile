# SASTRA server image. Build from the repository root:
#   docker build -t sastra -f deploy/Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SASTRA_DATA_DIR=/data SASTRA_HOST=0.0.0.0 SASTRA_PORT=8787 SASTRA_ENV=production

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sastra \
    && mkdir -p /data && chown sastra:sastra /data

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/sastra ./sastra

USER sastra
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "sastra", "serve"]
