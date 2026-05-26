from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.analyze import (
    analyze_query,
    export_keywords_csv,
    fetch_overview,
    fetch_top_keywords,
)
from app.config import ENTITY_INDEX, KEYWORD_OUTPUT_DIR
from app.es import get_es
from app.search import build_entity_suggest_body, pick_entity_display, pick_ko_keyword

app = FastAPI(title="L1 ES Keyword API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExportRequest(BaseModel):
    safe_mode: bool = True
    max_docs: int | None = Field(default=5000, ge=1, le=250000)
    min_popularity: float = Field(default=0.0, ge=0.0)


@app.get("/es")
def es_health() -> dict[str, Any]:
    es = get_es()
    info = es.info()
    count = es.count(index=ENTITY_INDEX)["count"]
    return {
        "cluster_name": info.get("cluster_name"),
        "version": info.get("version", {}).get("number"),
        "entity_index": ENTITY_INDEX,
        "entity_count": count,
    }


@app.get("/v1/suggest")
def suggest(
    q: str = Query(..., min_length=1),
    safe_mode: bool = Query(True),
    locale: str = Query("ko"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    q = q.strip()
    es = get_es()
    body = build_entity_suggest_body(q=q, limit=limit, safe_mode=safe_mode, locale=locale)
    res = es.options(request_timeout=30).search(index=ENTITY_INDEX, body=body)

    items = []
    for hit in res["hits"]["hits"]:
        src = hit["_source"]
        items.append(
            {
                "qid": src.get("qid"),
                "keyword_ko": pick_ko_keyword(src),
                "display": pick_entity_display(src, locale),
                "popularity": src.get("popularity", 0.0),
                "is_sensitive": bool(src.get("new_is_sensitive", False)),
                "category_ids": src.get("category_ids") or [],
                "score": hit.get("_score", 0.0),
            }
        )

    return {"query": q, "count": len(items), "items": items}


@app.get("/v1/entity/{qid}")
def get_entity(qid: str, locale: str = Query("ko")) -> dict[str, Any]:
    es = get_es()
    try:
        doc = es.options(request_timeout=30).get(index=ENTITY_INDEX, id=qid)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"entity not found: {qid}") from exc

    src = doc["_source"]
    return {
        "qid": qid,
        "keyword_ko": pick_ko_keyword(src),
        "display": pick_entity_display(src, locale),
        "en_title": src.get("en_title"),
        "ko_title": src.get("ko_title"),
        "popularity": src.get("popularity"),
        "lang_count": src.get("lang_count"),
        "is_sensitive": bool(src.get("new_is_sensitive", False)),
        "category_ids": src.get("category_ids") or [],
        "desc_ko": src.get("desc_ko"),
        "desc_en": src.get("desc_en"),
        "name_primary": src.get("name_primary") or [],
    }


@app.get("/v1/analysis/overview")
def analysis_overview() -> dict[str, Any]:
    return fetch_overview()


@app.get("/v1/analysis/top")
def analysis_top(
    limit: int = Query(50, ge=1, le=500),
    safe_mode: bool = Query(True),
    min_popularity: float = Query(0.0, ge=0.0),
    has_category: bool | None = Query(None),
) -> dict[str, Any]:
    items = fetch_top_keywords(
        limit=limit,
        safe_mode=safe_mode,
        min_popularity=min_popularity,
        has_category=has_category,
    )
    return {"count": len(items), "items": items}


@app.get("/v1/analysis/query")
def analysis_by_query(
    q: str = Query(..., min_length=1),
    limit: int = Query(30, ge=1, le=100),
    safe_mode: bool = Query(True),
) -> dict[str, Any]:
    return analyze_query(q, limit=limit, safe_mode=safe_mode)


@app.post("/v1/analysis/export")
def analysis_export(req: ExportRequest) -> dict[str, Any]:
    path = export_keywords_csv(
        safe_mode=req.safe_mode,
        max_docs=req.max_docs,
        min_popularity=req.min_popularity,
    )
    return {
        "ok": True,
        "csv": str(path),
        "meta": str(path.with_suffix(".meta.json")),
        "output_dir": str(KEYWORD_OUTPUT_DIR),
    }


@app.get("/v1/analysis/exports")
def list_exports() -> dict[str, Any]:
    KEYWORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(KEYWORD_OUTPUT_DIR.glob("entities_*.csv"), reverse=True)
    return {
        "output_dir": str(KEYWORD_OUTPUT_DIR),
        "files": [{"name": f.name, "path": str(f), "size": f.stat().st_size} for f in files[:20]],
    }
