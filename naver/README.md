# naver/ — 이슈 뉴스 → SEO 블로그 자동화

**연예 / TV·예능 / 사회** 뉴스를 수집·분석·글 생성까지 자동화합니다.  
발행(네이버 블로그 업로드)은 Phase 3에서 `NAVER_BLOG_ID` 등록 후 진행.

> L1 루트의 `scripts/`, `data/scraped/` 등과 **분리**되어 있습니다.  
> 이 폴더(`naver/`)만 보면 됩니다.

---

## RSS / 키워드가 뭔가요?

| 용어 | 쉬운 설명 |
|------|-----------|
| **RSS** | 뉴스 사이트·Google News가 제공하는 **“새 기사 목록 URL”**. API 키 없이 자동 수집 가능. |
| **keywords_watch** | `config/news_feeds.yaml`의 **우선순위 단어**. 제목·본문에 있으면 점수↑ (예: `나는솔로`, `드라마`). |
| **네이버 검색 API** | (선택) `.env`에 `NAVER_CLIENT_ID` 넣으면 네이버 뉴스도 추가 수집. **없어도 Google News RSS만으로 동작.** |

---

## 뉴스 소스 (기본 설정)

`config/news_feeds.yaml`에 이미 넣어 둠:

| 카테고리 | 소스 |
|----------|------|
| 연예 | Google News RSS `연예` |
| TV·예능 | Google News RSS `TV 예능 OR 예능 프로 OR 방송` |
| 사회 | Google News RSS `사회` |
| (선택) | 네이버 뉴스 검색 API — 카테고리별 10건 |

원하면 `config/news_feeds.yaml`에서 URL·키워드만 수정하면 됩니다.

---

## 디렉터리

```
naver/
├── config/           # 수집·SEO·발행 설정
├── data/
│   ├── news/raw/     # 수집 기사 JSON
│   ├── news/issues/  # 이슈 분석 JSON
│   └── publish/
│       ├── drafts/   # body.md + meta.json + images/
│       └── inbox/    # (나중에) 직접 넣은 이미지
├── scripts/
│   ├── ingest_news.py
│   ├── analyze_issue.py
│   ├── generate_issue_post.py
│   └── pipeline.py
└── templates/
    └── issue_post_system.txt
```

---

## 설치

```bash
cd /path/to/L1
.venv/bin/pip install -r naver/requirements.txt
```

`.env` — L1 루트 또는 `naver/.env`:

```bash
GROQ_API_KEY=...          # 필수 (분석·글 생성)
GEMINI_API_KEY=...        # Phase 2 이미지용
NAVER_BLOG_ID=...         # Phase 3 발행용 (나중에)
NAVER_CLIENT_ID=...       # 선택 (네이버 뉴스 추가)
NAVER_CLIENT_SECRET=...
```

---

## 사용법

```bash
# 상태 확인
python3 naver/scripts/pipeline.py status

# 1단계: 뉴스 수집
python3 naver/scripts/pipeline.py ingest

# 2단계: 핵심 이슈 분석 (Groq)
python3 naver/scripts/pipeline.py analyze

# 3단계: SEO 블로그 초안 (Groq)
python3 naver/scripts/pipeline.py generate

# 한 번에 (테스트: 각 단계 2건)
python3 naver/scripts/pipeline.py run --limit 2
```

결과: `naver/data/publish/drafts/{issue_id}/body.md`

---

## 파이프라인 흐름

```
[RSS/API 수집] → raw/*.json
      ↓ Groq
[이슈 분석]    → issues/*.json
      ↓ Groq + SEO 템플릿
[글 생성]      → drafts/*/body.md
      ↓ (Phase 2) 이미지
      ↓ (Phase 3) 네이버 발행
```

---

## Phase 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| P0 | 뉴스 수집 | ✅ |
| P1 | 이슈 분석 + SEO 글 생성 | ✅ |
| P2 | 이미지 (inbox / og / Gemini) | 예정 |
| P3 | 네이버 블로그 자동 발행 | 예정 (`NAVER_BLOG_ID`) |

---

## 설정 수정

- **카테고리·RSS URL**: `config/news_feeds.yaml` → `feeds`
- **우선순위 키워드**: 같은 파일 → `keywords_watch`
- **글 분량·이미지 개수**: `config/seo_style.yaml`
- **발행 옵션**: `config/publish.yaml`
