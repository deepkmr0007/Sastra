# SASTRA server image. Build from the repository root:
#   docker build -t sastra -f deploy/Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SASTRA_DATA_DIR=/data SASTRA_HOST=0.0.0.0 SASTRA_PORT=8787 SASTRA_ENV=production

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sastra \
    && mkdir -p /data && chown sastra:sastra /data

# Docker CLI (client only) so the server can start sandbox containers through the
# socket proxy. Set --build-arg DOCKER_CLI=0 to skip (code tools then refuse to run).
ARG DOCKER_CLI=1
ARG DOCKER_CLI_VERSION=27.3.1
RUN if [ "$DOCKER_CLI" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends curl && \
      curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_CLI_VERSION}.tgz" \
        | tar -xz -C /usr/local/bin --strip-components=1 docker/docker && \
      apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
    fi

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
