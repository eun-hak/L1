#!/usr/bin/env python3
"""
Wave 기반 L2 확장 — Scout(생성) + Gemini 3.1 flash-lite(검증) 하이브리드.

Usage:
  python scripts/expand_taxonomy_l2.py --wave 1
  python scripts/expand_taxonomy_l2.py --wave 1 --dry-run
  python scripts/expand_taxonomy_l2.py --wave 1 --l1 09
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_client import MODEL_FAST as GEMINI_MODEL, chat as gemini_chat, parse_json_array  # noqa: E402
from groq_client import MODEL_FAST as SCOUT_MODEL, chat as scout_chat, slugify_en  # noqa: E402

PLAN_CSV = ROOT / "data2" / "seed" / "l2_expansion_plan.csv"
L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
BRIEF = ROOT / "data2" / "brief.txt"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
OUT_CSV = ROOT / "data2" / "topics_l2_expanded.csv"
OUT_JSON = ROOT / "data2" / "topics_l2_expanded.json"
MANIFEST = ROOT / "data2" / "manifest_l2_expansion.json"
REPORT = ROOT / "data2" / "reports" / "l2_expansion_report.json"

TAXONOMY_SYSTEM = (
    "너는 한국어 네이버 블로그 SEO 카테고리 기획자야. "
    "검색 수요가 있는 구체적이고 실용적인 카테고리를 설계한다. "
    "반드시 유효한 JSON 배열만 출력한다. 설명·주석·마크다운 코드블록 금지."
)

SLEEP_SEC = 1.5


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def norm_name(name: str) -> str:
    return re.sub(r"\s+", "", name.strip().lower())


def is_similar(a: str, b: str) -> bool:
    na, nb = norm_name(a), norm_name(b)
    if na == nb:
        return True
    if len(na) >= 4 and (na in nb or nb in na):
        return True
    return False


def name_exists(name: str, existing: list[str]) -> bool:
    return any(is_similar(name, e) for e in existing)


def scout_generate_l2_batch(
    *,
    l1_name: str,
    l1_theme: str,
    l1_description: str,
    hints: list[dict[str, str]],
    avoid_names: list[str],
    brief: str,
) -> list[dict[str, str]]:
    hint_lines = "\n".join(
        f"- {h['name_ko_hint']} (축: {h['expansion_axis']}, 타입: {h['l2_type']})"
        for h in hints
    )
    avoid = "\n".join(f"- {n}" for n in avoid_names[:80]) or "- (없음)"
    prompt = f"""L1 "{l1_name}" 아래 L2 중분류를 설계해.

[L1 설명] {l1_description}
[L1 테마] {l1_theme}

[기획 힌트]
{brief[:1200]}

[반드시 포함할 L2 이름 — name_ko는 힌트와 동일하게, description만 작성]
{hint_lines}

[이미 사용 중 — 절대 중복·유사 금지]
{avoid}

[규칙]
- name_ko: 힌트 이름과 정확히 일치
- slug: 영문 kebab-case, 검색 친화적 (예: jeju-food, gangnam-food)
- description: 한국어 1문장, 이 L2에서 다룰 글 유형
- "종합", "기타", "전체", "정보" 금지
- JSON 배열만 출력, 힌트 개수와 동일하게

[
  {{"name_ko": "제주 맛집", "slug": "jeju-food", "description": "제주 지역 맛집 추천 및 식당 정보"}}
]
"""
    raw = scout_chat(prompt, system=TAXONOMY_SYSTEM, model=SCOUT_MODEL, temperature=0.7)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    hint_map = {h["name_ko_hint"]: h for h in hints}
    used_slugs: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name:
            continue
        if name not in hint_map:
            for hint_name in hint_map:
                if is_similar(name, hint_name):
                    name = hint_name
                    break
            else:
                continue
        slug = slugify_en(str(item.get("slug", "")).strip()) or slugify_en(name)
        base = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        out.append({
            "name_ko": name,
            "slug": slug,
            "description": str(item.get("description", "")).strip() or f"{name} 관련 실용 정보 글",
            "l2_type": hint_map[name]["l2_type"],
            "expansion_axis": hint_map[name]["expansion_axis"],
            "target_l3": hint_map[name]["target_l3"],
        })
    return out


def gemini_validate_l2_batch(
    *,
    l1_name: str,
    candidates: list[dict[str, str]],
    existing_names: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    cand_json = json.dumps(candidates, ensure_ascii=False, indent=2)
    exist_json = json.dumps(existing_names[:60], ensure_ascii=False)
    prompt = f"""L1 "{l1_name}" L2 후보를 검증해.

[기존 L2 이름]
{exist_json}

[신규 후보]
{cand_json}

각 후보에 대해:
- 기존과 의미 중복이면 reject
- name_ko가 모호하거나 L1 범위 밖이면 reject
- 통과하면 description을 1문장으로 다듬기

JSON 배열만 출력:
[
  {{
    "name_ko": "...",
    "slug": "...",
    "description": "...",
    "l2_type": "...",
    "expansion_axis": "...",
    "target_l3": "...",
    "verdict": "accept|reject",
    "reject_reason": ""
  }}
]
"""
    raw = gemini_chat(prompt, system=TAXONOMY_SYSTEM, model=GEMINI_MODEL, temperature=0.3)
    items = parse_json_array(raw, raise_on_fail=False)
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name:
            continue
        row = {
            "name_ko": name,
            "slug": str(item.get("slug", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "l2_type": str(item.get("l2_type", "")).strip(),
            "expansion_axis": str(item.get("expansion_axis", "")).strip(),
            "target_l3": str(item.get("target_l3", "")).strip(),
        }
        if str(item.get("verdict", "")).strip() == "accept":
            if not row["slug"]:
                row["slug"] = slugify_en(name)
            accepted.append(row)
        else:
            row["reject_reason"] = str(item.get("reject_reason", "")).strip()
            rejected.append(row)
    if not accepted and candidates:
        for c in candidates:
            if not name_exists(c["name_ko"], existing_names):
                accepted.append(c)
    return accepted, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description="L2 Wave 확장 (Scout + Gemini)")
    parser.add_argument("--wave", type=int, default=1)
    parser.add_argument("--l1", type=str, default=None, help="특정 L1만 (예: 09)")
    parser.add_argument("--plan", type=Path, default=PLAN_CSV)
    parser.add_argument("--max-scout", type=int, default=30, help="Scout API 호출 상한")
    parser.add_argument("--max-gemini", type=int, default=10, help="Gemini API 호출 상한")
    parser.add_argument("--batch", type=int, default=12, help="Scout 배치 크기")
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    plan_rows = load_csv(args.plan)
    l1_rows = {r["l1_code"]: r for r in load_csv(L1_CSV)}
    existing_l2 = load_csv(L2_CSV) if L2_CSV.exists() else []
    brief = BRIEF.read_text(encoding="utf-8").strip() if BRIEF.exists() else ""

    pending = [
        r for r in plan_rows
        if r.get("wave") == str(args.wave)
        and r.get("action") == "add"
        and r.get("status") == "pending"
        and (not args.l1 or r.get("l1_code") == args.l1)
    ]
    if not pending:
        raise SystemExit(f"wave={args.wave} pending add 항목이 없습니다.")

    by_l1: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in pending:
        by_l1[row["l1_code"]].append(row)

    scout_calls = 0
    gemini_calls = 0
    all_new: list[dict[str, str]] = []
    rejected_log: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()

    print(f"Wave {args.wave}: {len(pending)}개 L2 확장 (L1 {len(by_l1)}개)")
    print(f"한도: Scout ≤{args.max_scout}, Gemini ≤{args.max_gemini}")

    for l1_code in sorted(by_l1):
        hints = by_l1[l1_code]
        l1 = l1_rows.get(l1_code)
        if not l1:
            print(f"  ⚠ L1 {l1_code} 없음, skip")
            continue
        l1_name = l1["name_ko"]
        existing_names = [r["name_ko"] for r in existing_l2 if r["l1_code"] == l1_code]
        global_avoid = [r["name_ko"] for r in existing_l2]

        print(f"\n[{l1_code}] {l1_name} — {len(hints)}개", flush=True)

        if args.dry_run:
            for h in hints:
                print(f"  · {h['name_ko_hint']} ({h['l2_type']})")
            continue

        l1_candidates: list[dict[str, str]] = []
        for i in range(0, len(hints), args.batch):
            if scout_calls >= args.max_scout:
                print("  ⚠ Scout 한도 도달")
                break
            batch_hints = hints[i : i + args.batch]
            chunk = scout_generate_l2_batch(
                l1_name=l1_name,
                l1_theme=l1.get("theme", ""),
                l1_description=l1.get("description", ""),
                hints=batch_hints,
                avoid_names=global_avoid + [c["name_ko"] for c in l1_candidates],
                brief=brief,
            )
            scout_calls += 1
            for c in chunk:
                if not name_exists(c["name_ko"], global_avoid + [x["name_ko"] for x in l1_candidates]):
                    l1_candidates.append(c)
                    print(f"  scout · {c['name_ko']} ({c['slug']})")
            time.sleep(args.sleep)

        missing = [
            h for h in hints
            if not any(is_similar(h["name_ko_hint"], c["name_ko"]) for c in l1_candidates)
        ]
        for h in missing:
            slug = slugify_en(h["name_ko_hint"])
            l1_candidates.append({
                "name_ko": h["name_ko_hint"],
                "slug": slug,
                "description": f"{h['name_ko_hint']} 관련 실용 정보 및 검색형 글",
                "l2_type": h["l2_type"],
                "expansion_axis": h["expansion_axis"],
                "target_l3": h["target_l3"],
            })
            print(f"  fallback · {h['name_ko_hint']} ({slug})")

        if gemini_calls < args.max_gemini and l1_candidates:
            accepted, rejected = gemini_validate_l2_batch(
                l1_name=l1_name,
                candidates=l1_candidates,
                existing_names=existing_names,
            )
            gemini_calls += 1
            rejected_log.extend(rejected)
            l1_candidates = accepted
            print(f"  gemini 검증: accept {len(accepted)}, reject {len(rejected)}")
            time.sleep(args.sleep)
        else:
            print("  gemini skip (한도 또는 후보 없음)")

        max_sort = max(
            (int(r["sort_order"]) for r in existing_l2 if r["l1_code"] == l1_code),
            default=0,
        )
        used_slugs = {r["l2_slug"] for r in existing_l2 if r["l1_code"] == l1_code}
        for c in l1_candidates:
            slug = c["slug"] or slugify_en(c["name_ko"])
            base = slug
            n = 2
            while slug in used_slugs:
                slug = f"{base}-{n}"
                n += 1
            used_slugs.add(slug)
            max_sort += 1
            row = {
                "l2_id": f"{l1_code}-{slug}",
                "l1_code": l1_code,
                "l2_slug": slug,
                "name_ko": c["name_ko"],
                "description": c["description"],
                "sort_order": str(max_sort),
                "l2_type": c.get("l2_type", ""),
                "expansion_axis": c.get("expansion_axis", ""),
                "target_l3": c.get("target_l3", ""),
                "wave": str(args.wave),
                "model_scout": SCOUT_MODEL,
                "model_gemini": GEMINI_MODEL,
                "generated_at": now,
            }
            all_new.append(row)
            existing_l2.append(row)
            global_avoid.append(c["name_ko"])

    if args.dry_run:
        print("\ndry-run 완료")
        return

    if not all_new:
        raise SystemExit("생성된 L2가 없습니다.")

    fieldnames = [
        "l2_id", "l1_code", "l2_slug", "name_ko", "description", "sort_order",
        "l2_type", "expansion_axis", "target_l3", "wave",
        "model_scout", "model_gemini", "generated_at",
    ]
    write_csv(OUT_CSV, all_new, fieldnames)

    merged = []
    base_fields = [
        "l2_id", "l1_code", "l2_slug", "name_ko", "description",
        "sort_order", "model", "generated_at",
    ]
    for r in load_csv(L2_CSV):
        merged.append({k: r.get(k, "") for k in base_fields})
    for r in all_new:
        merged.append({
            "l2_id": r["l2_id"],
            "l1_code": r["l1_code"],
            "l2_slug": r["l2_slug"],
            "name_ko": r["name_ko"],
            "description": r["description"],
            "sort_order": r["sort_order"],
            "model": f"scout+gemini",
            "generated_at": r["generated_at"],
        })
    write_csv(L2_CSV, merged, base_fields)
    OUT_JSON.write_text(json.dumps(all_new, ensure_ascii=False, indent=2), encoding="utf-8")

    updated_plan = []
    new_names = {norm_name(r["name_ko"]) for r in all_new}
    for row in plan_rows:
        r = dict(row)
        if (
            r.get("wave") == str(args.wave)
            and r.get("action") == "add"
            and r.get("status") == "pending"
            and norm_name(r.get("name_ko_hint", "")) in new_names
        ):
            r["status"] = "done"
        updated_plan.append(r)
    write_csv(args.plan, updated_plan, list(plan_rows[0].keys()))

    report = {
        "generated_at": now,
        "wave": args.wave,
        "scout_calls": scout_calls,
        "gemini_calls": gemini_calls,
        "new_l2_count": len(all_new),
        "rejected": rejected_log,
        "l1_breakdown": {
            code: sum(1 for r in all_new if r["l1_code"] == code)
            for code in sorted({r["l1_code"] for r in all_new})
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n완료: +{len(all_new)} L2")
    print(f"API: Scout {scout_calls}회, Gemini {gemini_calls}회")
    print(f"→ {OUT_CSV}")
    print(f"→ {L2_CSV} (merged)")
    print(f"→ {REPORT}")


if __name__ == "__main__":
    main()
