from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import ENTITY_INDEX, KEYWORD_OUTPUT_DIR
from .es import get_es
from .search import pick_entity_display, pick_ko_keyword


def entity_to_row(src: dict[str, Any], locale: str = "ko") -> dict[str, Any]:
    return {
        "qid": src.get("qid", ""),
        "keyword_ko": pick_ko_keyword(src),
        "keyword_en": (src.get("en_title") or "").strip(),
        "display": pick_entity_display(src, locale),
        "popularity": src.get("popularity", 0.0),
        "lang_count": src.get("lang_count", 0),
        "is_sensitive": bool(src.get("new_is_sensitive", False)),
        "category_ids": src.get("category_ids") or [],
        "desc_ko": (src.get("desc_ko") or "").strip(),
    }


def fetch_overview() -> dict[str, Any]:
    from .search import build_overview_aggs

    es = get_es()
    res = es.options(request_timeout=60).search(index=ENTITY_INDEX, body=build_overview_aggs())
    aggs = res["aggregations"]
    top_hits = aggs["top_popular"]["hits"]["hits"]

    return {
        "index": ENTITY_INDEX,
        "total_entities": int(aggs["total"]["value"]),
        "sensitive_count": int(aggs["sensitive"]["doc_count"]),
        "with_ko_title": int(aggs["with_ko_title"]["doc_count"]),
        "with_category": int(aggs["with_category"]["doc_count"]),
        "popularity": aggs["popularity_stats"],
        "lang_count": aggs["lang_count_stats"],
        "top_popular": [entity_to_row(h["_source"]) for h in top_hits],
    }


def fetch_top_keywords(
    *,
    limit: int = 50,
    safe_mode: bool = True,
    min_popularity: float = 0.0,
    has_category: bool | None = None,
) -> list[dict[str, Any]]:
    from .search import build_top_keywords_body

    es = get_es()
    body = build_top_keywords_body(
        limit=limit,
        safe_mode=safe_mode,
        min_popularity=min_popularity,
        has_category=has_category,
    )
    res = es.options(request_timeout=60).search(index=ENTITY_INDEX, body=body)
    return [entity_to_row(h["_source"]) for h in res["hits"]["hits"]]


def analyze_query(q: str, *, limit: int = 30, safe_mode: bool = True) -> dict[str, Any]:
    from .search import build_entity_suggest_body

    es = get_es()
    body = build_entity_suggest_body(q=q, limit=limit, safe_mode=safe_mode)
    res = es.options(request_timeout=30).search(index=ENTITY_INDEX, body=body)
    items = []
    for hit in res["hits"]["hits"]:
        row = entity_to_row(hit["_source"])
        row["score"] = hit.get("_score", 0.0)
        items.append(row)

    pop_values = [float(i["popularity"] or 0) for i in items]
    with_cat = sum(1 for i in items if i["category_ids"])

    return {
        "query": q,
        "result_count": len(items),
        "avg_popularity": sum(pop_values) / len(pop_values) if pop_values else 0.0,
        "with_category_count": with_cat,
        "keywords": items,
    }


def scroll_entities(
    *,
    batch_size: int = 500,
    safe_mode: bool = True,
    max_docs: int | None = None,
) -> Iterator[dict[str, Any]]:
    es = get_es()
    query: dict[str, Any] = {"match_all": {}}
    if safe_mode:
        query = {"bool": {"filter": [{"term": {"new_is_sensitive": False}}]}}

    res = es.search(
        index=ENTITY_INDEX,
        scroll="2m",
        size=batch_size,
        query=query,
        _source=[
            "qid",
            "ko_title",
            "en_title",
            "name_primary",
            "popularity",
            "lang_count",
            "category_ids",
            "new_is_sensitive",
            "desc_ko",
        ],
    )
    scroll_id = res["_scroll_id"]
    hits = res["hits"]["hits"]
    yielded = 0

    try:
        while hits:
            for hit in hits:
                yield entity_to_row(hit["_source"])
                yielded += 1
                if max_docs and yielded >= max_docs:
                    return
            res = es.scroll(scroll_id=scroll_id, scroll="2m")
            scroll_id = res["_scroll_id"]
            hits = res["hits"]["hits"]
    finally:
        if scroll_id:
            es.clear_scroll(scroll_id=scroll_id)


def export_keywords_csv(
    *,
    output_path: Path | None = None,
    safe_mode: bool = True,
    max_docs: int | None = None,
    min_popularity: float = 0.0,
) -> Path:
    KEYWORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = KEYWORD_OUTPUT_DIR / f"entities_{ts}.csv"

    fields = [
        "qid",
        "keyword_ko",
        "keyword_en",
        "popularity",
        "lang_count",
        "is_sensitive",
        "category_ids",
        "desc_ko",
    ]

    count = 0
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in scroll_entities(safe_mode=safe_mode, max_docs=max_docs):
            if float(row.get("popularity") or 0) < min_popularity:
                continue
            writer.writerow(
                {
                    **{k: row.get(k, "") for k in fields if k != "category_ids"},
                    "category_ids": "|".join(row.get("category_ids") or []),
                }
            )
            count += 1

    meta_path = output_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "index": ENTITY_INDEX,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "row_count": count,
                "safe_mode": safe_mode,
                "min_popularity": min_popularity,
                "max_docs": max_docs,
                "csv": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path
