#!/usr/bin/env python3
"""
뉴스 RSS/API 수집 → data/news/raw/{id}.json

Usage:
  python3 naver/scripts/ingest_news.py
  python3 naver/scripts/ingest_news.py --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _paths import (  # noqa: E402
    NEWS_RAW_DIR,
    USER_AGENT,
    ensure_dirs,
    load_yaml,
    naver_api_configured,
)

INDEX_PATH = NEWS_RAW_DIR / "_index.json"


def url_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def load_index() -> dict[str, str]:
    if not INDEX_PATH.exists():
        return {}
    with INDEX_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def save_index(index: dict[str, str]) -> None:
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def score_article(title: str, body: str, keywords: list[str]) -> float:
    """RSS로 이미 카테고리 필터됨 → 기본 0.35, watch 키워드마다 +0.1."""
    text = f"{title} {body}".lower()
    score = 0.35
    for kw in keywords:
        if kw.lower() in text:
            score += 0.1
    return min(score, 1.0)


def extract_page_meta(html: str, page_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    og_image = ""
    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            og_image = tag["content"].strip()
            break

    body_parts: list[str] = []
    for sel in ("article", "#articleBodyContents", ".news_body", ".article_body"):
        node = soup.select_one(sel)
        if node:
            body_parts.append(node.get_text(" ", strip=True))
            break
    if not body_parts:
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) > 40:
                body_parts.append(t)
    body_text = " ".join(body_parts)
    if len(body_text) > 8000:
        body_text = body_text[:8000]
    return og_image, body_text


def parse_rss_feed(feed_cfg: dict, keywords: list[str], delay: float, timeout: int) -> list[dict]:
    parsed = feedparser.parse(feed_cfg["url"])
    items: list[dict] = []
    max_items = int(feed_cfg.get("max_items", 15))

    for entry in parsed.entries[:max_items]:
        link = entry.get("link", "").strip()
        if not link:
            continue
        title = strip_html(entry.get("title", ""))
        summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
        published = entry.get("published", "") or entry.get("updated", "")

        og_image = ""
        body_text = summary
        try:
            time.sleep(delay)
            html = fetch_url(link, timeout=timeout)
            page_og, page_body = extract_page_meta(html, link)
            og_image = page_og or og_image
            if len(page_body) > len(body_text):
                body_text = page_body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  [warn] 본문 fetch 실패: {link[:60]}… ({exc})")

        source = ""
        if hasattr(entry, "source") and entry.source:
            source = getattr(entry.source, "title", "") or ""

        items.append(
            {
                "url": link,
                "source": source or feed_cfg.get("name", "rss"),
                "title": title,
                "body_text": body_text,
                "og_image": og_image,
                "published_at": published,
                "category": feed_cfg.get("category", "general"),
                "feed_name": feed_cfg.get("name", ""),
                "score": score_article(title, body_text, keywords),
            }
        )
    return items


def parse_naver_news(feed_cfg: dict, keywords: list[str], delay: float) -> list[dict]:
    import os

    client_id = os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return []

    query = urllib.parse.quote(feed_cfg.get("query", "연예"))
    display = int(feed_cfg.get("display", 10))
    url = f"https://openapi.naver.com/v1/search/news.json?query={query}&display={display}&sort=date"

    req = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": USER_AGENT,
        },
    )
    time.sleep(delay)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    items: list[dict] = []
    for row in data.get("items", []):
        link = row.get("originallink") or row.get("link", "")
        title = strip_html(row.get("title", ""))
        summary = strip_html(row.get("description", ""))
        pub = row.get("pubDate", "")
        items.append(
            {
                "url": link,
                "source": "naver_news",
                "title": title,
                "body_text": summary,
                "og_image": "",
                "published_at": pub,
                "category": feed_cfg.get("category", "general"),
                "feed_name": feed_cfg.get("name", ""),
                "score": score_article(title, summary, keywords),
            }
        )
    return items


def save_raw(article: dict) -> str | None:
    uid = url_id(article["url"])
    path = NEWS_RAW_DIR / f"{uid}.json"
    if path.exists():
        return None

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": uid,
        "url": article["url"],
        "source": article["source"],
        "title": article["title"],
        "body_text": article["body_text"],
        "og_image": article.get("og_image", ""),
        "published_at": article.get("published_at", ""),
        "category": article.get("category", "general"),
        "feed_name": article.get("feed_name", ""),
        "score": article.get("score", 0.0),
        "fetched_at": now,
        "status": "raw",
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return uid


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스 수집")
    parser.add_argument("--limit", type=int, default=None, help="피드당 최대 신규 저장 (테스트용)")
    args = parser.parse_args()

    ensure_dirs()
    cfg = load_yaml("news_feeds.yaml")
    keywords = cfg.get("keywords_watch", [])
    delay = float(cfg.get("request_delay", 0.8))
    timeout = int(cfg.get("fetch_timeout", 15))

    index = load_index()
    saved = 0
    skipped = 0

    for feed in cfg.get("feeds", []):
        ftype = feed.get("type", "rss")
        if ftype == "naver_news":
            if not naver_api_configured():
                print(f"[skip] {feed['name']}: NAVER API 키 없음")
                continue
            if feed.get("enabled") is False:
                continue
            print(f"[naver] {feed['name']} …")
            articles = parse_naver_news(feed, keywords, delay)
        elif ftype == "rss":
            print(f"[rss] {feed['name']} …")
            articles = parse_rss_feed(feed, keywords, delay, timeout)
        else:
            print(f"[skip] unknown type: {ftype}")
            continue

        for article in articles:
            if args.limit is not None and saved >= args.limit:
                break
            uid = save_raw(article)
            if uid:
                index[article["url"]] = uid
                saved += 1
                print(f"  + {uid} | {article['title'][:50]}… (score={article['score']:.2f})")
            else:
                skipped += 1

    save_index(index)
    print(f"\n완료: 신규 {saved}건, 중복 스킵 {skipped}건 → {NEWS_RAW_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
