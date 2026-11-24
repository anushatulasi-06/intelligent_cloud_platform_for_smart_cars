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

# AWS deployment (option B, infra/aws/docker): PostgreSQL 16 and MongoDB dump/restore tools
# (DEV_FAKE_BACKUP=false). Off by default so the local demo image (infra/demo-compose.yml) is unchanged.
# MongoDB's 7.0 apt repository uses a SHA-1 key rejected by Debian 12. Use the official
# Debian 12 amd64 archive with its published SHA-256 instead; never weaken apt verification.
# Version/checksum source: https://downloads.mongodb.org/tools/db/release.json
ARG INSTALL_BACKUP_TOOLS=false
ARG MONGODB_TOOLS_VERSION=100.20.1
ARG MONGODB_TOOLS_SHA256=8f25dcd1d769560fae1b5e724dc0c5eba54f2410dd293a9bab62be055507e521
RUN if [ "$INSTALL_BACKUP_TOOLS" = "true" ]; then \
        test "$(dpkg --print-architecture)" = amd64 \
        || { echo "Backup tools require an amd64 image; use an x86_64 host." >&2; exit 1; }; \
        apt-get update \
        && apt-get install -y --no-install-recommends curl gnupg libgssapi-krb5-2 \
        && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
           | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
        && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
           > /etc/apt/sources.list.d/pgdg.list \
        && apt-get update \
        && apt-get install -y --no-install-recommends postgresql-client-16 \
        && curl -fsSL --retry 3 -o /tmp/mongodb-tools.tgz \
           "https://fastdl.mongodb.org/tools/db/mongodb-database-tools-debian12-x86_64-${MONGODB_TOOLS_VERSION}.tgz" \
        && echo "${MONGODB_TOOLS_SHA256}  /tmp/mongodb-tools.tgz" | sha256sum -c - \
        && mkdir /tmp/mongodb-tools \
        && tar -xzf /tmp/mongodb-tools.tgz -C /tmp/mongodb-tools --strip-components=1 \
        && install -m 0755 /tmp/mongodb-tools/bin/mongodump /tmp/mongodb-tools/bin/mongorestore /usr/local/bin/ \
        && install -d /usr/local/share/doc/mongodb-database-tools \
        && install -m 0644 /tmp/mongodb-tools/LICENSE.md /tmp/mongodb-tools/THIRD-PARTY-NOTICES \
           /tmp/mongodb-tools/README.md /usr/local/share/doc/mongodb-database-tools/ \
        && pg_dump --version && pg_restore --version && mongodump --version && mongorestore --version \
        && apt-get purge -y gnupg && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/* /tmp/mongodb-tools /tmp/mongodb-tools.tgz; \
    elif [ "$INSTALL_BACKUP_TOOLS" != "false" ]; then \
        echo "INSTALL_BACKUP_TOOLS must be true or false." >&2; exit 1; \
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
