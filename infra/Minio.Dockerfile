# The pinned conda-forge rebuild is the same static amd64 binary verified in the
# local integration demo. Download integrity is checked before extraction.
FROM python:3.12-slim-bookworm AS extract
RUN python -m pip install --no-cache-dir zstandard==0.23.0
COPY infra/extract_demo_minio.py /extract_demo_minio.py
RUN python /extract_demo_minio.py --output /out

FROM debian:bookworm-slim
LABEL org.opencontainers.image.title="MinIO component demo storage" \
      org.opencontainers.image.version="2025.10.15.17.29.55-hbcca054_0" \
      org.opencontainers.image.source="https://github.com/conda-forge/minio-server-feedstock" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 minio \
    && useradd --uid 10001 --gid minio --home-dir /data minio \
    && mkdir /data \
    && chown minio:minio /data
COPY --from=extract /out/bin/minio /usr/local/bin/minio
COPY --from=extract /out/licenses/ /usr/share/doc/minio/licenses/
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
