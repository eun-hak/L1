"""블로그 답변용 문단 풀 — 템플릿 모드에서 글자 수·다양성 확보."""

from __future__ import annotations

import random
from typing import Callable

# (제목, 본문 생성 함수) — focus, question_text, l1_name, mega 받음
SectionBuilder = Callable[[str, str, str, str], str]


def _pick(pool: list[str], seed: int) -> str:
    return pool[seed % len(pool)]


def intro_para(focus: str, _q: str, _l1: str, mega: str, seed: int) -> str:
    pools = {
        "라이프": [
            f"혼자 사는 집, 출퇴근, 주말 일정이 겹치면 {focus}을(를) 미루기 쉽습니다. "
            f"다만 짧게라도 기록해 두면 감정 정리와 다음 행동 선택에 도움이 됩니다. "
            f"이 글에서는 현실적인 시간·도구·루틴 기준으로 정리했습니다.",
            f"{focus}에 관심 있지만 어디서부터 손대야 할지 막막한 분을 위해, "
            f"처음 1주일과 1개월 시점에 무엇을 보면 좋은지 단계별로 풀어 씁니다.",
        ],
        "머니": [
            f"{focus}은(는) 숫자와 조건이 조금만 달라도 결과가 크게 바뀝니다. "
            f"본인 상황(소득·가구·기간)에 맞는 선택지를 먼저 좁히는 것이 중요합니다.",
            f"주변 후기만 보고 결정하면 나중에 비용·시간을 더 쓰는 경우가 많습니다. "
            f"{focus}을(를) 비교할 때 꼭 확인할 항목을 표처럼 정리했습니다.",
        ],
        "푸드": [
            f"메뉴·재료·조리법에 따라 {focus}의 만족도와 비용이 달라집니다. "
            f"집에서 할지, 외식·배달할지에 따라 준비물도 크게 다릅니다.",
            f"{focus}을(를) 처음 시도할 때는 '맛 + 시간 + 가격' 세 가지를 동시에 맞추기 어렵습니다. "
            f"우선순위를 정한 뒤 한 가지씩 맞추는 방식이 부담이 적습니다.",
        ],
        "default": [
            f"{focus}을(를) 검색했다면, 아마 지금 당장 필요한 상황이거나 앞으로의 선택을 앞두고 있을 가능성이 큽니다. "
            f"이 글은 입문자도 따라 할 수 있게 핵심만 길지 않게 나눴습니다.",
            f"정보가 많을수록 오히려 헷갈리기 쉬운 주제입니다. "
            f"{focus}에 대해 자주 묻는 질문 순서로 정리하고, 마지막에 체크리스트를 붙였습니다.",
        ],
    }
    pool = pools.get(mega, pools["default"])
    return _pick(pool, seed)


def core_section(focus: str, q: str, _l1: str, mega: str, seed: int) -> str:
    titles = [
        f"{focus}, 지금 상황에서 확인할 것",
        f"{focus} — 실전에서 먼저 하는 순서",
        f"{focus} 핵심 포인트",
    ]
    bodies = {
        "라이프": [
            "첫째, 시간 블록을 고정합니다. '매일 30분'보다 '주 3회 10분'이 지속되기 쉽습니다. "
            "둘째, 기록 도구를 하나만 씁니다. 앱·노트·보이스 메모 중 부담 없는 것 하나로 통일하세요. "
            "셋째, 완벽한 문장보다 키워드 3개만 적어도 충분합니다. "
            "넷째, 주말에 지난 기록을 훑으며 다음 주 한 가지 행동만 정합니다.",
            "감정이 올라온 날은 사실만 적고, 해석은 다음 날 적는 방식도 좋습니다. "
            "반복되는 패턴(야근, 잠, 식사)이 보이면 그때부터 루틴을 조정하면 됩니다.",
        ],
        "머니": [
            "수입·지출·기한을 한 장에 적습니다. 숨은 비용(수수료·자동이체·구독)을 빠뜨리기 쉽습니다. "
            "비교할 때는 '총 비용'과 '조건(기간·해지·위약)'을 같이 봅니다. "
            "본인에게 맞지 않는 상품은 조기에 정리하는 편이 이자·기회비용을 줄입니다.",
        ],
        "푸드": [
            "재료는 가능한 한 손질된 것을 쓰면 시간이 줄어듭니다. "
            "양념·소스는 미리 소분해 두면 평일 저녁에 유리합니다. "
            "칼로리·알레르기가 걱정되면 메뉴를 바꾸기보다 분량·토핑을 조절하는 쪽이 현실적입니다.",
        ],
        "default": [
            "준비 단계에서 목표를 한 문장으로 적습니다. 예: '이번 주 안에 ○○까지 결정한다'. "
            "중간 점검 날짜를 캘린더에 넣어 두면 미루지 않기 쉽습니다. "
            "막히면 전문가·커뮤니티·공식 안내 중 하나만 선택해 확인하세요. "
            "여러 곳을 동시에 보면 오히려 결정이 늦어질 수 있습니다.",
        ],
    }
    pool = bodies.get(mega, bodies["default"])
    title = _pick(titles, seed)
    body = _pick(pool, seed + 1)
    return f"## {title}\n\n{body}"


def tips_section(focus: str, _q: str, _l1: str, _mega: str, seed: int) -> str:
    tips = [
        f"- {focus}을(를) 시작할 때는 '최소 버전'부터: 시간·비용·난이도 중 하나만 낮춥니다.",
        "- 비슷한 경험담 2~3개만 참고하고, 나머지는 직접 짧게 실험해 봅니다.",
        "- 일주일마다 한 줄 메모: 잘 된 점 / 아쉬운 점 / 다음에 바꿀 점.",
        "- 몸 상태(수면·컨디션)가 나쁜 날은 목표를 절반으로 줄입니다.",
        "- 완료 기준을 명확히: '○○ 파일 정리', '○○ 메뉴 1번 시도'처럼 측정 가능하게.",
        "- 주변에 공유할 필요는 없습니다. 본인에게 유용한 형태가 최우선입니다.",
    ]
    chosen = []
    for i in range(5):
        chosen.append(tips[(seed + i * 3) % len(tips)])
    return "## 실전 꿀팁\n\n" + "\n".join(chosen)


def caution_section(focus: str, _q: str, _l1: str, mega: str, seed: int) -> str:
    lines = {
        "머니": [
            "과장된 수익·혜택 광고는 조건·기간을 반드시 확인하세요.",
            "계약·자동이체는 해지 방법·위약금을 먼저 읽는 것이 안전합니다.",
        ],
        "바디": [
            "통증·어지럼·호흡 곤란이 있으면 무리하지 말고 전문가와 상담하세요.",
            "검색 정보만으로 진단·치료를 대체하기 어렵습니다.",
        ],
        "default": [
            f"{focus} 관련 정보는 시기·지역·개인 조건에 따라 달라질 수 있습니다.",
            "한 번에 크게 바꾸기보다 작게 시도한 뒤 몸·지갑·일정 반응을 보고 조절하세요.",
        ],
    }
    pool = lines.get(mega, lines["default"])
    body = " ".join(_pick(pool, seed + i) for i in range(min(3, len(pool))))
    return f"## 주의할 점\n\n{body}"


def faq_section(focus: str, q: str, _l1: str, _mega: str, seed: int) -> str:
    faqs = [
        (f"Q. {focus}은(를) 완전 초보도 할 수 있나요?", 
         "A. 네. 처음에는 '짧게·자주·단순하게'만 지키면 됩니다. 완벽함보다 기록·시도 자체가 목표입니다."),
        (f"Q. 시간이 거의 없을 때는?", 
         "A. 5~10분 블록으로 쪼개거나, 주말에 한 번만 몰아서 정리하는 방식을 택하세요."),
        (f"Q. 비용이 부담될 때는?", 
         "A. 무료·저비용 옵션부터 비교하고, 효과를 본 뒤에만 비용을 늘리는 순서가 안전합니다."),
        (f"Q. {q} — 핵심만 다시 정리하면?", 
         f"A. {focus}의 목표를 한 줄로 적고, 이번 주에 할 행동 1가지만 정한 뒤 실행·회고하면 됩니다."),
    ]
    a, b = faqs[seed % len(faqs)], faqs[(seed + 2) % len(faqs)]
    return f"## 자주 묻는 질문\n\n{a[0]}\n{a[1]}\n\n{b[0]}\n{b[1]}"


def steps_section(focus: str, _q: str, _l1: str, _mega: str, seed: int) -> str:
    steps = [
        f"1. **목표 정하기:** {focus}에서 이번 주에 달성할 결과를 한 문장으로 적습니다.",
        "2. **준비물·조건 확인:** 시간, 비용, 장소, 필요 앱·서류를 체크합니다.",
        "3. **최소 실행:** 완벽하지 않아도 10~20분 안에 끝나는 버전으로 한 번 시도합니다.",
        "4. **기록:** 잘 된 점·막힌 점을 각각 한 줄씩 남깁니다.",
        "5. **조정:** 다음 주에 바꿀 것은 1가지만 고릅니다.",
        "6. **재검토:** 2주 뒤 같은 방식으로 반복할지, 중단·변경할지 결정합니다.",
    ]
    ordered = [steps[(seed + i) % len(steps)] for i in range(6)]
    return "## 순서대로 따라 하기\n\n" + "\n".join(ordered)


def closing_para(focus: str, _q: str, l1: str, _mega: str, seed: int) -> str:
    closings = [
        f"{focus}은(를) 오늘부터 '최소 단위'로 시작해 보세요. "
        f"일주일 뒤 기록을 열어보면 다음 행동이 훨씬 분명해집니다. "
        f"({l1} 관련 다른 주제도 천천히 확장해 가면 좋습니다.)",
        f"정리하면, {focus}의 핵심은 '나에게 맞는 빈도·도구·완료 기준'을 찾는 것입니다. "
        f"무리하지 않는 선에서 꾸준히 이어가는 쪽이 결과적으로 유리합니다.",
    ]
    return "## 마무리\n\n" + _pick(closings, seed)


def build_template_answer(
    *,
    question_text: str,
    focus_keyword: str,
    l1_name: str,
    mega: str,
    seed: int,
    min_chars: int = 1500,
    max_chars: int = 3000,
) -> str:
    """섹션 조합으로 목표 글자 수에 근접한 마크다운 본문."""
    rng = random.Random(seed)
    parts = [
        f"# {question_text}\n",
        f"**핵심 한 줄:** {focus_keyword} — 오늘 당장 적용할 순서와 주의점을 정리했습니다.\n",
        intro_para(focus_keyword, question_text, l1_name, mega, seed),
        "",
        core_section(focus_keyword, question_text, l1_name, mega, seed + 1),
        "",
        tips_section(focus_keyword, question_text, l1_name, mega, seed + 2),
        "",
        steps_section(focus_keyword, question_text, l1_name, mega, seed + 6),
        "",
        caution_section(focus_keyword, question_text, l1_name, mega, seed + 3),
        "",
        faq_section(focus_keyword, question_text, l1_name, mega, seed + 4),
        "",
        closing_para(focus_keyword, question_text, l1_name, mega, seed + 5),
        "",
        "---\n*이 글은 일반 정보 제공 목적이며, 의료·법률·투자 등 전문 영역은 전문가 상담을 권장합니다.*",
    ]
    body = "\n".join(parts)

    # 글자 수 부족 시 보조 문단 추가
    extra_pool = [
        f"\n\n## 더 알아두면 좋은 점\n\n"
        f"{focus_keyword}을(를) 지속하려면 '환경'을 먼저 바꾸는 편이 의지력에 덜 의존합니다. "
        f"도구를 눈에 보이게 두거나, 캘린더 알림을 주 3회만 설정하는 식으로 부담을 낮추세요. "
        f"작은 성공 경험이 쌓이면 기록·비교·조정이 자연스럽게 이어집니다. "
        f"주변 사람과 비교하기보다, 지난주의 나와 비교하는 기준이 스트레스를 줄입니다.",
        f"\n\n## 상황별로 이렇게 나눠 보세요\n\n"
        f"바쁠 때·여유 있을 때·컨디션이 나쁠 때 각각 할 수 있는 최소 행동을 미리 적어 두면, "
        f"{focus_keyword} 관련 결정을 미루지 않게 됩니다. "
        f"주 1회만이라도 '지난번과 무엇이 달랐는지' 한 줄 비교하면 개선 속도가 빨라집니다. "
        f"실패한 시도도 버리지 말고 '왜 안 됐는지'만 남기면 다음 선택에 반영됩니다.",
        f"\n\n## 7일·30일 체크리스트\n\n"
        f"**1주차:** 목표 한 줄 적기 → 도구·시간 1개 고정 → 3회 실행 → 짧은 회고.\n"
        f"**2~3주차:** 같은 방식 유지, 불편한 점 1가지만 수정.\n"
        f"**4주차:** {focus_keyword}에서 얻은 변화(시간·돈·감정)를 숫자 또는 키워드로 기록.\n"
        f"한 달 뒤에도 유지할지, 방식을 바꿀지 결정하면 됩니다.",
        f"\n\n## 검색할 때 헷갈리는 표현\n\n"
        f"비슷한 키워드가 많을수록 기준이 필요합니다. "
        f"본인에게 중요한 우선순위(가격·시간·품질·안전)를 먼저 정한 뒤, "
        f"후기·블로그·공식 안내를 그 기준으로만 거르세요. "
        f"{focus_keyword}은(는) 한 번에 완벽히 정리되기보다, "
        f"짧게 여러 번 시도하며 맞는 방식을 찾는 경우가 많습니다.",
    ]
    i = 0
    while len(body) < min_chars and i < 12:
        body += extra_pool[(seed + i) % len(extra_pool)]
        i += 1

    if len(body) > max_chars:
        body = body[: max_chars - 80].rsplit("\n", 1)[0] + "\n\n...(이하 생략)\n"

    return body
