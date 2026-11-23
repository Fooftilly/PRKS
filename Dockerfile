# Produce an offline-ready frontend distribution in the builder stage.
# Runtime images still require only Python; no Node/npm is installed.
FROM python:3.15-slim AS frontend-builder
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

FROM python:3.15-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends qpdf \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. UID/GID 1000 matches docker-compose.yml's
# default `user:` so a bind-mounted data directory keeps one owner.
ARG PRKS_UID=1000
ARG PRKS_GID=1000
RUN groupadd --system --gid "$PRKS_GID" prks \
    && useradd --system --uid "$PRKS_UID" --gid prks --home-dir /app \
       --no-create-home --shell /usr/sbin/nologin prks \
    && mkdir -p /app /data/pdfs /app/data/pdfs \
    && chown -R prks:prks /data /app/data \
    && chmod -R a+rX /app

COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY requirements.txt requirements-lock.txt ./
# requirements-lock.txt is requirements.txt resolved with every transitive
# package and its sha256 (scripts/lock_runtime_requirements.py; the dependency
# gate keeps the two in step). Wheels only and hash-checked: no sdist build
# scripts run, and no pip config or PIP_* environment from the builder.
RUN pip install --no-cache-dir --isolated --disable-pip-version-check \
    --no-input --only-binary=:all: --require-hashes -r requirements-lock.txt

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

USER prks

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0"]
