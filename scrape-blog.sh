#!/usr/bin/env bash
# 네이버 블로그 스크래핑 (본문 + 글 사이 이미지 삽입)
#
# 사용법:
#   ./scrape-blog.sh "https://blog.naver.com/아이디/글번호"
#   ./scrape-blog.sh URL1 URL2 URL3
#   ./scrape-blog.sh              # data/scraped/urls.txt 목록 일괄 수집
#   ./scrape-blog.sh --file my-urls.txt

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="python3"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

URLS_FILE="$ROOT/data/scraped/urls.txt"
OUTPUT="$ROOT/data/scraped"

usage() {
  cat <<'EOF'
네이버 블로그 글 수집

  ./scrape-blog.sh "블로그_URL"
  ./scrape-blog.sh URL1 URL2 ...
  ./scrape-blog.sh                 urls.txt 일괄 수집
  ./scrape-blog.sh --file 파일.txt  지정 파일 일괄 수집

저장 위치: data/scraped/
URL 목록 파일: data/scraped/urls.txt (한 줄에 URL 하나, # 은 주석)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

mkdir -p "$OUTPUT"

if [[ $# -eq 0 ]]; then
  if [[ ! -f "$URLS_FILE" ]]; then
    cat > "$URLS_FILE" <<'EOF'
# 한 줄에 URL 하나. # 으로 시작하는 줄은 무시됩니다.
# https://blog.naver.com/chummilmil99/224295273021
EOF
    echo "URL 목록 파일을 만들었습니다: data/scraped/urls.txt"
    echo "URL을 넣은 뒤 다시 ./scrape-blog.sh 를 실행하세요."
    exit 0
  fi
  exec "$PYTHON" "$ROOT/scripts/scrape_naver_blog.py" \
    --urls-file "$URLS_FILE" \
    --output "$OUTPUT"
fi

if [[ "${1:-}" == "--file" || "${1:-}" == "-f" ]]; then
  if [[ $# -lt 2 ]]; then
    echo "오류: --file 뒤에 URL 목록 파일을 지정하세요." >&2
    exit 1
  fi
  exec "$PYTHON" "$ROOT/scripts/scrape_naver_blog.py" \
    --urls-file "$2" \
    --output "$OUTPUT"
fi

exec "$PYTHON" "$ROOT/scripts/scrape_naver_blog.py" \
  --output "$OUTPUT" \
  "$@"
