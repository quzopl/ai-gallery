#!/usr/bin/env bash
# AI Gallery — uruchom FastAPI + WebSocket
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Brak 'uv'. Zainstaluj: https://docs.astral.sh/uv/"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo ">> Tworzę venv (Python 3.12)…"
  uv venv --python 3.12 .venv
fi

echo ">> Synchronizuję zależności…"
uv pip install -r requirements.txt

PORT="${PORT:-8923}"
echo ""
echo ">> http://127.0.0.1:${PORT}"
echo ""
exec .venv/bin/python -m uvicorn backend.server:app --host 127.0.0.1 --port "${PORT}" --reload
