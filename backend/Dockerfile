FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MODEL_DIR=/app/model/artifacts

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# AWS deployment (option B, infra/aws/docker): pg_dump 16 and mongodump for real backups to S3
# (DEV_FAKE_BACKUP=false). Off by default so the local demo image (infra/demo-compose.yml) is unchanged.
# The MongoDB Debian repository provides these tools for amd64 only.
ARG INSTALL_BACKUP_TOOLS=false
RUN if [ "$INSTALL_BACKUP_TOOLS" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends curl gnupg \
        && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
           | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
        && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
           > /etc/apt/sources.list.d/pgdg.list \
        && curl -fsSL https://pgp.mongodb.com/server-7.0.asc \
           | gpg --dearmor -o /usr/share/keyrings/mongodb-7.0.gpg \
        && echo "deb [signed-by=/usr/share/keyrings/mongodb-7.0.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
           > /etc/apt/sources.list.d/mongodb-org-7.0.list \
        && apt-get update \
        && apt-get install -y --no-install-recommends postgresql-client-16 mongodb-database-tools \
        && apt-get purge -y gnupg && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
# Explicit CPU wheels avoid downloading CUDA libraries for a component demo.
# These versions satisfy backend/requirements.txt; keep the two versions paired.
RUN python -m pip install --upgrade pip \
    && python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r requirements.txt \
    && python -m pip check

COPY backend/ ./
COPY model/artifacts/ /app/model/artifacts/
COPY infra/init_demo_storage.py infra/start_demo_api.sh /app/infra/
RUN groupadd --gid 10001 autosound \
    && useradd --uid 10001 --gid autosound --home-dir /app/backend autosound \
    && mkdir -p /app/backend/.local_edge/demo /app/backend/.local_backups /app/backend/.local_bootstrap \
    && chown -R autosound:autosound /app \
    && chmod +x /app/infra/start_demo_api.sh
USER autosound

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=12 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["/app/infra/start_demo_api.sh"]
