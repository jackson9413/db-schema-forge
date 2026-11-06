#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="$(printenv PORT || echo 8765)"
echo "db-schema-forge -> http://127.0.0.1:$PORT"
exec python3 app.py
