#!/usr/bin/env python3
"""
네이버 블로그 글 스크래핑 (본문 + 글 사이 이미지 삽입).

Usage:
  python3 scripts/scrape_naver_blog.py "https://blog.naver.com/chummilmil99/224295273021"
  python3 scripts/scrape_naver_blog.py URL1 URL2 --output data/scraped
  python3 scripts/scrape_naver_blog.py --urls-file urls.txt
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "scraped"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

META_CONTENT_RE = re.compile(
    r'<meta\s+property="([^"]+)"\s+content="([^"]*)"',
    re.I,
)
COMPONENT_MARKER_RE = re.compile(r'se-component se-(\w+)')


@dataclass
class ContentBlock:
    kind: str
    content: str = ""
    url: str = ""
    width: int | None = None
    height: int | None = None
    caption: str = ""
    context_before: str = ""
    image_index: int = 0
    local_file: str = ""


@dataclass
class BlogPost:
    url: str
    blog_id: str
    log_no: str
    title: str
    author: str = ""
    published_at: str = ""
    blocks: list[ContentBlock] = field(default_factory=list)
    thumbnail_url: str = ""


def parse_blog_url(url: str) -> tuple[str, str]:
    """네이버 블로그 URL에서 blogId, logNo 추출."""
    parsed = urllib.parse.urlparse(url.strip())
    query = urllib.parse.parse_qs(parsed.query)

    if "blogId" in query and "logNo" in query:
        return query["blogId"][0], query["logNo"][0]

    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 2 and path_parts[-1].isdigit():
        return path_parts[-2], path_parts[-1]

    raise ValueError(f"네이버 블로그 URL 형식을 인식하지 못했습니다: {url}")


def mobile_post_url(blog_id: str, log_no: str) -> str:
    return (
        "https://m.blog.naver.com/PostView.naver"
        f"?blogId={blog_id}&logNo={log_no}"
    )


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_inline(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def extract_meta(page: str) -> dict[str, str]:
    return {
        prop: html.unescape(value)
        for prop, value in META_CONTENT_RE.findall(page)
    }


def extract_title(page: str) -> str:
    meta = extract_meta(page)
    if meta.get("og:title"):
        return meta["og:title"]
    match = re.search(r"<title>([^<]+)</title>", page, re.I)
    if not match:
        return ""
    title = clean_inline(match.group(1))
    return re.sub(r"\s*:\s*네이버\s*블로그\s*$", "", title).strip()


def extract_author(page: str) -> str:
    meta = extract_meta(page)
    if meta.get("naverblog:nickname"):
        return meta["naverblog:nickname"]
    match = re.search(r'class="blog_author[^"]*"[^>]*>([^<]+)<', page, re.I)
    return clean_inline(match.group(1)) if match else ""


def extract_date(page: str) -> str:
    match = re.search(r'class="blog_date"[^>]*>\s*([^<]+)', page, re.I)
    if not match:
        return ""
    raw = clean_inline(match.group(1))
    parts = re.match(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", raw)
    if not parts:
        return raw
    y, m, d = parts.groups()
    return f"{y}-{int(m):02d}-{int(d):02d}"


def extract_thumbnail(page: str) -> str:
    meta = extract_meta(page)
    return meta.get("og:image", "")


def extract_main_content(page: str) -> str:
    start = page.find("se-main-container")
    if start < 0:
        start = page.find('class="__se_component_area"')
    if start < 0:
        return page
    end = page.find("post_footer", start)
    return page[start : end if end > start else len(page)]


def parse_image_block(chunk: str) -> ContentBlock:
    linkdata_match = re.search(r"data-linkdata='({.*?})'", chunk, re.S)
    url = ""
    width: int | None = None
    height: int | None = None

    if linkdata_match:
        raw = linkdata_match.group(1).replace("&quot;", '"')
        try:
            data = json.loads(raw)
            url = data.get("src", "")
            width = int(data["originalWidth"]) if data.get("originalWidth") else None
            height = int(data["originalHeight"]) if data.get("originalHeight") else None
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    if not url:
        for pattern in (
            r'data-lazy-src="([^"]+)"',
            r'"src"\s*:\s*"([^"]+mblogthumb[^"]+)"',
            r'src="(https://mblogthumb[^"]+)"',
        ):
            match = re.search(pattern, chunk)
            if match:
                url = match.group(1).split("?")[0]
                break

    caption_match = re.search(r'class="se-caption[^"]*"[^>]*>(.*?)</', chunk, re.S)
    caption = clean_inline(caption_match.group(1)) if caption_match else ""

    alt_match = re.search(r'\balt="([^"]*)"', chunk)
    alt = clean_inline(alt_match.group(1)) if alt_match else ""

    return ContentBlock(
        kind="image",
        url=url,
        width=width,
        height=height,
        caption=caption or alt,
    )


def parse_text_block(chunk: str, *, title: str = "") -> ContentBlock | None:
    lines: list[str] = []
    seen: set[str] = set()
    for para in re.findall(r"se-text-paragraph[^>]*>(.*?)</", chunk, re.S | re.I):
        text = clean_inline(para)
        if len(text) < 1 or text == title or text in seen:
            continue
        seen.add(text)
        lines.append(text)
    if not lines:
        return None
    return ContentBlock(kind="text", content="\n\n".join(lines))


def parse_quotation_block(chunk: str) -> ContentBlock | None:
    parts = re.findall(
        r"se-quote(?:-|_)(?:paragraph|content)[^>]*>(.*?)</",
        chunk,
        re.S | re.I,
    )
    if not parts:
        parts = re.findall(r"se-text-paragraph[^>]*>(.*?)</", chunk, re.S | re.I)
    lines = [clean_inline(part) for part in parts if clean_inline(part)]
    if not lines:
        return None
    return ContentBlock(kind="quotation", content="\n\n".join(lines))


def extract_blocks(page: str, *, title: str = "") -> list[ContentBlock]:
    content = extract_main_content(page)
    markers = [(m.start(), m.group(1)) for m in COMPONENT_MARKER_RE.finditer(content)]
    blocks: list[ContentBlock] = []

    for i, (pos, kind) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(content)
        chunk = content[pos:end]

        if kind == "text":
            block = parse_text_block(chunk, title=title)
        elif kind == "image":
            block = parse_image_block(chunk)
        elif kind == "quotation":
            block = parse_quotation_block(chunk)
        else:
            block = None

        if block:
            blocks.append(block)

    image_no = 0
    last_text = ""
    for block in blocks:
        if block.kind == "text":
            last_text = block.content
        elif block.kind == "quotation":
            last_text = block.content
        elif block.kind == "image":
            image_no += 1
            block.image_index = image_no
            block.context_before = tail_context(last_text)

    return blocks


def tail_context(text: str, *, max_lines: int = 3) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return " / ".join(lines[-max_lines:])


def image_count(blocks: list[ContentBlock]) -> int:
    return sum(1 for block in blocks if block.kind == "image")


def scrape_post(url: str) -> BlogPost:
    blog_id, log_no = parse_blog_url(url)
    page = fetch_html(mobile_post_url(blog_id, log_no))
    title = extract_title(page)
    return BlogPost(
        url=url.strip(),
        blog_id=blog_id,
        log_no=log_no,
        title=title,
        author=extract_author(page),
        published_at=extract_date(page),
        blocks=extract_blocks(page, title=title),
        thumbnail_url=extract_thumbnail(page),
    )


def safe_dirname(blog_id: str, log_no: str) -> str:
    return f"{blog_id}_{log_no}"


def download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://blog.naver.com/"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())


def image_extension(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if ".png" in path:
        return ".png"
    if ".gif" in path:
        return ".gif"
    if ".webp" in path:
        return ".webp"
    return ".jpg"


def download_images(post: BlogPost, images_dir: Path, *, image_width: int = 800) -> None:
    for block in post.blocks:
        if block.kind != "image" or not block.url:
            continue
        filename = f"{block.image_index:02d}{image_extension(block.url)}"
        dest = images_dir / filename
        download_file(f"{block.url}?type=w{image_width}", dest)
        block.local_file = f"images/{filename}"
        time.sleep(0.15)


def render_body(post: BlogPost) -> str:
    lines = [f"# {post.title}", ""]
    if post.author or post.published_at:
        meta_bits = [b for b in (post.author, post.published_at) if b]
        lines.append(f"> {' · '.join(meta_bits)}")
        lines.append("")
    lines.append(f"원문: {post.url}")
    lines.append("")

    for block in post.blocks:
        if block.kind == "text":
            lines.append(block.content)
            lines.append("")
        elif block.kind == "quotation":
            lines.append(f"> {block.content.replace(chr(10), chr(10) + '> ')}")
            lines.append("")
        elif block.kind == "image":
            if block.local_file:
                alt = block.caption or f"이미지 {block.image_index}"
                lines.append(f"![{alt}]({block.local_file})")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def save_post(post: BlogPost, output_dir: Path, *, image_width: int = 800) -> Path:
    post_dir = output_dir / safe_dirname(post.blog_id, post.log_no)
    images_dir = post_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    download_images(post, images_dir, image_width=image_width)
    (post_dir / "body.md").write_text(render_body(post), encoding="utf-8")

    meta = {
        "url": post.url,
        "blog_id": post.blog_id,
        "log_no": post.log_no,
        "title": post.title,
        "author": post.author,
        "published_at": post.published_at,
        "thumbnail_url": post.thumbnail_url,
        "image_count": image_count(post.blocks),
        "block_count": len(post.blocks),
        "blocks": [asdict(block) for block in post.blocks],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    (post_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return post_dir


def append_index(index_path: Path, post: BlogPost, post_dir: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    header = "blog_id,log_no,title,author,published_at,image_count,path,url\n"
    row = (
        f"{post.blog_id},{post.log_no},"
        f"\"{post.title.replace('\"', '\"\"')}\","
        f"\"{post.author.replace('\"', '\"\"')}\","
        f"{post.published_at},{image_count(post.blocks)},"
        f"{post_dir.relative_to(index_path.parent)},"
        f"{post.url}\n"
    )
    if not index_path.exists():
        index_path.write_text(header + row, encoding="utf-8")
    else:
        with index_path.open("a", encoding="utf-8") as f:
            f.write(row)


def load_urls_from_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 블로그 글 스크래핑")
    parser.add_argument("urls", nargs="*", help="블로그 글 URL")
    parser.add_argument("--urls-file", type=Path, help="URL 목록 파일 (한 줄에 하나)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="저장 디렉터리")
    parser.add_argument("--image-width", type=int, default=800, help="다운로드 이미지 너비")
    parser.add_argument("--delay", type=float, default=1.0, help="글 간 대기(초)")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.urls_file:
        urls.extend(load_urls_from_file(args.urls_file))

    if not urls:
        parser.error("URL을 하나 이상 지정하거나 --urls-file 을 사용하세요.")

    args.output.mkdir(parents=True, exist_ok=True)
    index_path = args.output / "index.csv"
    ok, fail = 0, 0

    for i, url in enumerate(urls):
        if i > 0 and args.delay:
            time.sleep(args.delay)
        try:
            print(f"[{i + 1}/{len(urls)}] 스크래핑: {url}")
            post = scrape_post(url)
            post_dir = save_post(post, args.output, image_width=args.image_width)
            append_index(index_path, post, post_dir)
            print(
                f"  → {post_dir.name} | 제목: {post.title[:40]}... "
                f"| 이미지 {image_count(post.blocks)}장"
            )
            ok += 1
        except (ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"  ✗ 실패: {exc}", file=sys.stderr)
            fail += 1

    print(f"\n완료: 성공 {ok}건, 실패 {fail}건 → {args.output}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
