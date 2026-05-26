from typing import Any


def pick_entity_display(src: dict[str, Any], locale: str = "ko") -> str:
    if locale == "ko":
        v = (src.get("ko_title") or "").strip()
        if v:
            return v
    if locale == "en":
        v = (src.get("en_title") or "").strip()
        if v:
            return v

    titles = src.get("titles") or {}
    labels = src.get("labels") or {}
    if isinstance(titles, dict) and locale in titles:
        return str(titles[locale])
    if isinstance(labels, dict) and locale in labels:
        return str(labels[locale])

    for key in ("ko_title", "en_title"):
        v = (src.get(key) or "").strip()
        if v:
            return v
    return str(src.get("qid") or "")


def pick_ko_keyword(src: dict[str, Any]) -> str:
    ko = (src.get("ko_title") or "").strip()
    if ko:
        return ko
    primary = src.get("name_primary") or []
    if isinstance(primary, list):
        for item in primary:
            text = str(item).strip()
            if text and any("\uac00" <= ch <= "\ud7a3" for ch in text):
                return text
    return pick_entity_display(src, "ko")


def build_entity_suggest_body(
    q: str,
    limit: int,
    safe_mode: bool,
    locale: str = "ko",
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []
    if safe_mode:
        filters.append({"term": {"new_is_sensitive": False}})

    fields = [
        "name_primary^6",
        "name_primary._2gram^3",
        "name_primary._3gram^3",
        "name_alias^2",
        "name_alias._2gram^1.5",
        "name_alias._3gram^1.5",
        "name_all^1",
        "ko_title^4",
        "en_title^2",
    ]

    includes = [
        "qid",
        "en_title",
        "ko_title",
        "name_primary",
        "new_is_sensitive",
        "popularity",
        "category_ids",
        "lang_count",
        "desc_ko",
        "desc_en",
    ]

    return {
        "size": limit,
        "_source": {"includes": includes},
        "query": {
            "function_score": {
                "query": {
                    "bool": {
                        "filter": filters,
                        "must": [
                            {
                                "multi_match": {
                                    "query": q,
                                    "type": "bool_prefix",
                                    "fields": fields,
                                }
                            }
                        ],
                    }
                },
                "field_value_factor": {
                    "field": "popularity",
                    "modifier": "log1p",
                    "missing": 0.0,
                },
                "boost_mode": "sum",
                "score_mode": "sum",
            }
        },
    }


def build_top_keywords_body(
    *,
    limit: int = 50,
    safe_mode: bool = True,
    min_popularity: float = 0.0,
    has_category: bool | None = None,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"exists": {"field": "ko_title"}}]
    if safe_mode:
        filters.append({"term": {"new_is_sensitive": False}})
    if min_popularity > 0:
        filters.append({"range": {"popularity": {"gte": min_popularity}}})
    if has_category is True:
        filters.append({"exists": {"field": "category_ids"}})
    elif has_category is False:
        filters.append(
            {
                "bool": {
                    "should": [
                        {"bool": {"must_not": {"exists": {"field": "category_ids"}}}},
                        {"script": {"script": "doc['category_ids'].size() == 0"}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    return {
        "size": limit,
        "_source": {
            "includes": [
                "qid",
                "ko_title",
                "en_title",
                "popularity",
                "category_ids",
                "lang_count",
                "new_is_sensitive",
            ]
        },
        "query": {"bool": {"filter": filters}},
        "sort": [{"popularity": {"order": "desc"}}, {"lang_count": {"order": "desc"}}],
    }


def build_overview_aggs() -> dict[str, Any]:
    return {
        "size": 0,
        "aggs": {
            "total": {"value_count": {"field": "qid"}},
            "sensitive": {"filter": {"term": {"new_is_sensitive": True}}},
            "with_ko_title": {"filter": {"exists": {"field": "ko_title"}}},
            "with_category": {
                "filter": {
                    "bool": {
                        "must": [{"exists": {"field": "category_ids"}}],
                        "must_not": [{"term": {"category_ids": ""}}],
                    }
                }
            },
            "popularity_stats": {"stats": {"field": "popularity"}},
            "lang_count_stats": {"stats": {"field": "lang_count"}},
            "top_popular": {
                "top_hits": {
                    "size": 10,
                    "_source": ["qid", "ko_title", "en_title", "popularity"],
                    "sort": [{"popularity": {"order": "desc"}}],
                }
            },
        },
    }
