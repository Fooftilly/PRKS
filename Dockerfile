# Produce an offline-ready frontend distribution in the builder stage.
# Runtime images still require only Python; no Node/npm is installed.
FROM python:3.12-slim AS frontend-builder
WORKDIR /build
COPY frontend ./frontend
COPY scripts/benchmark_classic_script_bundle.py scripts/build_classic_distribution.py ./scripts/
ARG PRKS_CLASSIC_BUNDLES=1
RUN mkdir -p dist \
    && if [ "$PRKS_CLASSIC_BUNDLES" = "1" ]; then \
         python -m scripts.build_classic_distribution --root /build --output /build/dist/frontend; \
       elif [ "$PRKS_CLASSIC_BUNDLES" = "0" ]; then \
         cp -a frontend dist/frontend; \
       else \
         echo "PRKS_CLASSIC_BUNDLES must be 0 or 1" >&2; exit 1; \
       fi

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends qpdf \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app /data/pdfs /app/data/pdfs \
    && chmod -R a+rX /app

COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Runtime gate (ensure_runtime_or_exit) reads python_min_version and package
# pins from this inventory before storage/DB startup — must be in the image.
COPY dependency-inventory.json ./
COPY prks_app.py ./
COPY backend ./backend
COPY --from=frontend-builder /build/dist/frontend ./frontend

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
# Reliable container marker for dependency-gate remediation (do not rely only on /.dockerenv).
ENV PRKS_CONTAINER=1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
