#!/bin/sh
set -e
mkdir -p /data/pdfs
exec python /app/prks_app.py --host 0.0.0.0
