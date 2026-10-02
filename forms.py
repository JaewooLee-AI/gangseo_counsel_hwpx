"""
forms.py

앱이 지원하는 "업무"(서식) 목록을 등록하는 레지스트리. 업무마다 템플릿 hwpx 파일,
FIELDS 정의(field_map.py류), 체크박스 토글 등을 처리하는 CUSTOM_HANDLERS, 문서함에서
문서를 구분하는 키 필드, AI 추출용 system prompt/예시 입력을 하나로 묶는다.

새 업무를 추가하려면: 1) <업무>_field_map.py에 FIELDS/CUSTOM_HANDLERS 정의, 2) 서식
hwpx 파일을 프로젝트 루트에 두기, 3) 아래 FORMS에 FormDefinition 한 개 추가 — 나머지
(app_flet.py의 문서함/입력/값확인 화면, document_store.py 저장)는 FIELDS를 그대로
순회하는 기존 공용 로직을 재사용하므로 추가 수정이 필요 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import field_map
import hwpx_engine
import meeting_field_map
from llm_client import STYLE_NARRATIVE, WRITING_STYLES


@dataclass
class FormDefinition:
    id: str
    name: str
    short_label: str
    template_filename: str
    fields: list[dict[str, Any]]
    custom_handlers: dict[str, Callable[..., Any]]
    key_field_id: str
    system_prompt: str
    example_transcript: str
    result_filename: str
    # table_idx -> [(title, subtitle, icon_key, field_ids), ...] — app_flet.py가 이 정의를
    # 그대로 섹션 카드로 렌더링한다(icon_key는 app_flet.py의 _ICONS 매핑 키).
    review_sections: dict[int, list[tuple[str, str, str, list[str]]]] = field(default_factory=dict)
    # table_idx -> 값 확인 화면의 탭 라벨
    page_labels: dict[int, str] = field(default_factory=dict)
    narrow_field_ratio: dict[str, int] = field(default_factory=dict)


COUNSEL_SYSTEM_PROMPT = """당신은 장애인 활동지원급여 수요조사(상담) 내용을 분석해 정해진 서식에
들어갈 값을 추출하는 보조원입니다.

아래 [입력 내용]에는 상담사와 이용자(또는 보호자) 사이의 대화 내용, 혹은 상담사가
직접 정리한 메모가 텍스트로 주어집니다. 이 내용에서 실제로 언급되거나 명확히
추론 가능한 정보만 추출하세요.

규칙:
1. 입력 내용에 명시적으로 언급되지 않은 항목은 절대 추측하지 말고 생략(null 또는
   빈 값)하세요. 없는 정보를 지어내면 안 됩니다.
2. 체크박스 성격의 필드는 주어진 옵션 목록 중에서만 골라야 합니다. 목록에 없는
   표현이 언급되면 가장 가까운 옵션을 고르거나, 애매하면 생략하세요.
3. 개인정보(전화번호, 생년월일, 주소 등)는 입력 내용에 나온 그대로 옮기세요.
   마스킹(예: 010-1234-****)이 되어 있으면 그대로 두고, 실제 값이 나오면 실제
   값을 쓰세요.
4. 숫자만 요구하는 필드(층수, 방 개수, 시간 등)는 숫자만 넣으세요.
5. 각 필드의 설명(description)을 참고해 어떤 정보를 채워야 하는지 판단하세요.
"""

COUNSEL_EXAMPLE = (
    "이용자 이름은 김철수이고 생년월일은 90년생입니다. 연락처는 010-1234-5678이고요, "
    "지체장애이시고 활동지원 등급은 14구간(가형)이에요. 강서구 화곡동 빌라 3층에 "
    "혼자 살고 계시고 엘리베이터는 없어요. 비흡연자시고 애완동물은 안 키우세요."
)

MEETING_SYSTEM_PROMPT = """당신은 장애인 활동지원기관의 "중점사례 회의록" 작성을 돕는
보조원입니다.

아래 [입력 내용]에는 중점사례 회의에서 오간 대화 내용, 혹은 담당자가 직접 정리한
메모(사례 선정 사유, 회의 중 발언 내용, 해결 방안, 결과보고 등)가 텍스트로 주어집니다.
이 내용에서 실제로 언급되거나 명확히 추론 가능한 정보만 추출하세요.

규칙:
1. 입력 내용에 명시적으로 언급되지 않은 항목은 절대 추측하지 말고 생략(null 또는
   빈 값)하세요. 없는 정보를 지어내면 안 됩니다.
2. 회의 일시(연/월/일, 시작~종료 시각)는 숫자만 분리해서 넣으세요(요일은 자동으로
   계산되므로 추출하지 않습니다).
3. 회의 내용(meeting_content)은 "발언자 이름 + 발언 요지" 형태로, 실제 대화 흐름을
   요약하지 말고 언급된 발언을 순서대로 정리하세요.
4. 개인정보(이름, 연락처 등)는 입력 내용에 나온 그대로 옮기세요.
5. 각 필드의 설명(description)을 참고해 어떤 정보를 채워야 하는지 판단하세요.
"""

MEETING_EXAMPLE = (
    "2026년 3월 10일 월요일 오전 10시부터 11시까지 프로그램실에서 중점사례 회의를 "
    "진행했습니다. 참석자는 김사회, 이복지, 박지원입니다. 대상자는 최영희(지적장애 2급)이고, "
    "최근 활동지원사와의 갈등으로 서비스 중단 위기에 있어 긴급히 사례회의를 소집했습니다."
)


_counsel_t0_ids = [f["id"] for f in field_map.FIELDS if f["table"] == 0]
_counsel_t1_ids = [f["id"] for f in field_map.FIELDS if f["table"] == 1]

COUNSEL_REVIEW_SECTIONS: dict[int, list[tuple[str, str, str, list[str]]]] = {
    0: [
        ("기본 인적사항", "성명, 생년월일, 연락처, 장애유형, 판정등급 및 주소 정보", "person", _counsel_t0_ids[:7]),
        ("주거 및 교통환경", "건물형태, 방 개수, 층수/승강기, 대중교통 및 반려동물 유무", "home", _counsel_t0_ids[7:19]),
        ("신체상태 및 소통특성", "신체 제약사항, 인공호흡기, 의사소통 수준, 공간인지, 흡연 및 건강 특이사항", "health", _counsel_t0_ids[19:]),
    ],
    1: [
        ("가구 및 사회활동", "동거 가구구성원, 자녀정보, 비상연락처, 직장/학교 출퇴근 및 여가활동", "people", _counsel_t1_ids[:13]),
        ("지원급여 및 희망 서비스", "판정 인정시간, 신체/가사/사회활동 서비스 시간 및 야간급여 희망 사유", "time", _counsel_t1_ids[13:27]),
        ("활동지원사 조건 및 상담 총평", "희망 지원사 성별/연령/흡연여부 매칭조건 및 종합 면담 총평", "check", _counsel_t1_ids[27:]),
    ],
}
COUNSEL_PAGE_LABELS = {0: "1쪽 (기본정보 / 생활환경 / 의사소통)", 1: "2쪽 (사회활동 / 욕구 / 총평)"}

_meeting_t0_ids = [f["id"] for f in meeting_field_map.FIELDS if f["table"] == 0]
_meeting_t1_ids = [f["id"] for f in meeting_field_map.FIELDS if f["table"] == 1]

MEETING_REVIEW_SECTIONS: dict[int, list[tuple[str, str, str, list[str]]]] = {
    0: [
        ("회의 기본정보", "일시, 장소, 작성자, 참석자 및 사례 대상자 정보", "person", _meeting_t0_ids[:6]),
        ("사례 내용 및 회의 결과", "현재상황, 욕구사항, 회의 내용 및 해결 방안", "note", _meeting_t0_ids[6:]),
    ],
    1: [
        ("중점사례 결과보고", "작성일/작성자, 대상, 선정사유, 개입 및 결과보고 내용", "check", _meeting_t1_ids),
    ],
}
MEETING_PAGE_LABELS = {0: "회의록", 1: "결과보고"}


FORMS: dict[str, FormDefinition] = {
    "counsel": FormDefinition(
        id="counsel",
        name="활동지원급여 수요조사카드",
        short_label="수요조사카드",
        template_filename="counsel.hwpx",
        fields=field_map.FIELDS,
        custom_handlers=hwpx_engine.CUSTOM_HANDLERS,
        narrow_field_ratio=hwpx_engine.NARROW_FIELD_RATIO,
        key_field_id="user_name",
        system_prompt=COUNSEL_SYSTEM_PROMPT,
        example_transcript=COUNSEL_EXAMPLE,
        result_filename="상담결과.hwpx",
        review_sections=COUNSEL_REVIEW_SECTIONS,
        page_labels=COUNSEL_PAGE_LABELS,
    ),
    "meeting": FormDefinition(
        id="meeting",
        name="중점사례 회의록",
        short_label="중점사례 회의록",
        template_filename="meeting.hwpx",
        fields=meeting_field_map.FIELDS,
        custom_handlers=meeting_field_map.CUSTOM_HANDLERS,
        narrow_field_ratio={},
        key_field_id="subject_name",
        system_prompt=MEETING_SYSTEM_PROMPT,
        example_transcript=MEETING_EXAMPLE,
        result_filename="중점사례_회의록.hwpx",
        review_sections=MEETING_REVIEW_SECTIONS,
        page_labels=MEETING_PAGE_LABELS,
    ),
}

FORM_ORDER: list[str] = ["counsel", "meeting"]


def style_selectable_fields(form: FormDefinition) -> list[dict[str, Any]]:
    """사용자가 서술형/개조식을 고를 수 있는 항목(필드 정의의 "style_selectable") 목록."""
    return [f for f in form.fields if f.get("style_selectable")]


def resolve_field_styles(form: FormDefinition, saved: dict[str, Any] | None = None) -> dict[str, str]:
    """항목별 작성 방식(필드 id -> llm_client.WRITING_STYLES 키)을 만든다. 필드 정의의
    default_style을 기본으로 하고, saved(설정 파일/문서에 저장된 값) 중 유효한 값으로 덮어쓴다."""
    saved = saved or {}
    styles: dict[str, str] = {}
    for f in style_selectable_fields(form):
        value = saved.get(f["id"])
        styles[f["id"]] = value if value in WRITING_STYLES else f.get("default_style", STYLE_NARRATIVE)
    return styles
