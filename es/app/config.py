from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if isinstance(value, str):
        return value.strip()
    return default


ES_URL = _get_env("ES_URL", "http://localhost:9200")
ES_USER = _get_env("ES_USER", "")
ES_PASSWD = _get_env("ES_PASSWD", "")
ES_VERIFY_CERTS = os.getenv("ES_VERIFY_CERTS", "true").lower() in ("1", "true", "yes", "y")
ES_CA_CERTS = _get_env("ES_CA_CERTS", "")

ENTITY_INDEX = "simsimi_entities_v1"
CATEGORY_INDEX = "simsimi_categories_v1"

KEYWORD_OUTPUT_DIR = ROOT / "keyword" / "es"
