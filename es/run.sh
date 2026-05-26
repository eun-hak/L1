#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate 2>/dev/null || true
pip install -q -r es/requirements.txt
cd es
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
