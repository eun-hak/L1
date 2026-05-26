"""naver/ 파이프라인 공통 경로."""

from __future__ import annotations

import os
import sys
from pathlib import Path

NAVER_ROOT = Path(__file__).resolve().parents[1]
L1_ROOT = NAVER_ROOT.parent

# L1 groq_client 재사용
sys.path.insert(0, str(L1_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv(L1_ROOT / ".env")
load_dotenv(L1_ROOT / ".env.local")
load_dotenv(NAVER_ROOT / ".env")
load_dotenv(NAVER_ROOT / ".env.local")

CONFIG_DIR = NAVER_ROOT / "config"
DATA_DIR = NAVER_ROOT / "data"
NEWS_RAW_DIR = DATA_DIR / "news" / "raw"
NEWS_ISSUES_DIR = DATA_DIR / "news" / "issues"
DRAFTS_DIR = DATA_DIR / "publish" / "drafts"
INBOX_DIR = DATA_DIR / "publish" / "inbox"
TEMPLATES_DIR = NAVER_ROOT / "templates"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for path in (
        NEWS_RAW_DIR,
        NEWS_ISSUES_DIR,
        DRAFTS_DIR,
        INBOX_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def load_yaml(name: str) -> dict:
    import yaml

    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def naver_api_configured() -> bool:
    return bool(os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"))
