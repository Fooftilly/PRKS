FROM python:3.12-slim

WORKDIR /app

RUN mkdir -p /app /data/pdfs /app/data/pdfs \
    && chmod -R a+rX /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY prks_app.py ./
COPY backend ./backend
COPY frontend ./frontend

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
