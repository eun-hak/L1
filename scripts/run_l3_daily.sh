#!/usr/bin/env bash
# L3 bulk 일일 실행 — 로컬 cron / launchd 용
#
# crontab 예 (매일 09:00):
#   0 9 * * * /Users/simsimi/개인프로젝트/L1/scripts/run_l3_daily.sh >> /Users/simsimi/개인프로젝트/L1/outputs/l3_cron.log 2>&1
#
# macOS launchd: ~/Library/LaunchAgents/com.l1.l3bulk.plist 에서 이 스크립트 호출

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# venv 있으면 사용
if [[ -f "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

export PYTHONUNBUFFERED=1

echo "=== L3 daily run $(date -Iseconds) ==="

"$PY" scripts/generate_l3_expanded.py --resume \
  --max-calls-8b 10000 \
  --max-calls-gemini 500 \
  --max-calls-scout 800

echo "=== done $(date -Iseconds) ==="
