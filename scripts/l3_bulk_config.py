"""L3 대량 생성 공통 설정."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs"
DATA2_DIR = ROOT / "data2"

CANDIDATES_CSV = OUT_DIR / "l2_candidates_gte100_depth1.csv"
TOPICS_L2_CSV = DATA2_DIR / "topics_l2.csv"

HITIER_CSV = OUT_DIR / "l3_expanded_hitier.csv"
LONGTAIL_CSV = OUT_DIR / "l3_expanded_longtail.csv"
CHECKPOINT_FILE = OUT_DIR / "l3_bulk_checkpoint.json"
RETRY_QUEUE_FILE = OUT_DIR / "l3_bulk_retry_queue.jsonl"
DAILY_BUDGET_FILE = OUT_DIR / "l3_daily_budget.json"
RUN_LOG_FILE = OUT_DIR / "l3_bulk_run.log"

HITIER_THRESHOLD = 1000
YMYL_L1_CODES = frozenset({"02", "03"})

L1_NAMES = {
    "01": "엔터·미디어",
    "02": "금융·재테크",
    "03": "건강·다이어트",
    "04": "패션",
    "05": "스포츠",
    "06": "자동차",
    "07": "IT·테크",
    "08": "뷰티",
    "09": "맛집·카페",
    "10": "요리·푸드",
    "11": "여행",
    "12": "문화·예술",
    "13": "반려동물",
    "14": "아웃도어",
    "15": "홈·리빙",
    "16": "육아",
    "17": "커리어",
    "18": "게임",
}

L3_FIELDNAMES = [
    "l3_id", "l2_id", "l1_code", "l3_slug",
    "focus_keyword", "title_ko",
    "search_intent", "topic_angle",
    "description", "model", "generated_at",
    "monthly_total", "tier", "meta_source",
]

# retry_stage → (model_key, batch_size)
RETRY_PLAN_YMYL = {
    1: ("gemini", 5),
    2: ("gemini", 1),
    3: ("scout", 5),
}
RETRY_PLAN_DEFAULT = {
    1: ("8b", 5),
    2: ("8b", 1),
    3: ("scout", 5),
}

DEFAULT_DAILY_LIMITS = {
    "8b": 10_000,
    "gemini": 500,
    "scout": 800,
}
