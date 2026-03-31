#!/bin/sh
set -e
mkdir -p /data/pdfs
exec python /app/prks_app.py
