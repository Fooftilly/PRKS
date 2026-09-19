FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends qpdf \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app /data/pdfs /app/data/pdfs \
    && chmod -R a+rX /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Runtime gate (ensure_runtime_or_exit) reads python_min_version and package
# pins from this inventory before storage/DB startup — must be in the image.
COPY dependency-inventory.json ./
COPY prks_app.py ./
COPY backend ./backend
COPY frontend ./frontend

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
# Reliable container marker for dependency-gate remediation (do not rely only on /.dockerenv).
ENV PRKS_CONTAINER=1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
