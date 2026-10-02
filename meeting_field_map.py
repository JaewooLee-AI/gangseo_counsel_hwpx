"""
meeting_field_map.py

meeting.hwpx (중점사례 회의록) 서식의 채울 수 있는 필드 정의.
files/meeting_form_dump.json, files/meeting_sample_dump.json 분석 결과를 기반으로
작성됨 (hwpx_engine.dump_tables()로 직접 덤프해서 확인).

field_map.py(counsel.hwpx)와 동일한 FIELDS 리스트 포맷을 따른다. 이 서식은 체크박스가
전혀 없고(표 2개, 14x7 / 7x4) 전부 텍스트 셀이라 counsel.hwpx보다 훨씬 단순하다. 유일한
예외는 일시 셀("년 월 일 요일 시 분 ~ 시 분")로, 요일까지 포함돼 있어 custom 핸들러에서
날짜로부터 요일을 자동 계산해 조립한다(hwpx_engine.CUSTOM_HANDLERS의 interview_date 패턴).

table/anchor는 files/meeting_form_dump.json 기준 (표 인덱스, (row, col)) 이다.
"""

from __future__ import annotations

from typing import Any

FIELDS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # 표 0: 중점사례 회의록
    # ------------------------------------------------------------------
    {
        "id": "meeting_datetime",
        "label": "회의 일시(년/월/일 및 시작~종료 시각)",
        "kind": "custom",
        "table": 0,
        "anchor": (2, 1),
        "subfields": [
            {"id": "meeting_year", "label": "회의 연도(4자리, 예: 2026)", "value_type": "string"},
            {"id": "meeting_month", "label": "회의 월(숫자)", "value_type": "string"},
            {"id": "meeting_day", "label": "회의 일(숫자)", "value_type": "string"},
            {"id": "meeting_start_hour", "label": "시작 시(숫자)", "value_type": "string"},
            {"id": "meeting_start_min", "label": "시작 분(숫자)", "value_type": "string"},
            {"id": "meeting_end_hour", "label": "종료 시(숫자)", "value_type": "string"},
            {"id": "meeting_end_min", "label": "종료 분(숫자)", "value_type": "string"},
        ],
    },
    {"id": "location", "label": "장소", "kind": "text", "table": 0, "anchor": (3, 1)},
    {"id": "author", "label": "작성자(회의록)", "kind": "text", "table": 0, "anchor": (3, 6)},
    {"id": "attendees", "label": "참석자", "kind": "text", "table": 0, "anchor": (4, 1)},
    {"id": "subject_name", "label": "사례 대상자 성명", "kind": "text", "table": 0, "anchor": (6, 2)},
    {"id": "disability_type", "label": "장애유형", "kind": "text", "table": 0, "anchor": (6, 5)},
    {
        "id": "current_situation",
        "label": "현재상황(사례 선정 사유 중 현재상황)",
        "kind": "text",
        "table": 0,
        "anchor": (8, 2),
        "multiline": True,
    },
    {
        "id": "needs",
        "label": "욕구사항(사례 선정 사유 중 주요욕구)",
        "kind": "text",
        "table": 0,
        "anchor": (9, 2),
        "multiline": True,
    },
    {
        "id": "meeting_content",
        "label": "회의 내용(발언자별 논의 내용)",
        "kind": "text",
        "table": 0,
        "anchor": (11, 1),
        "multiline": True,
    },
    {
        "id": "solution_plan",
        "label": "해결 방안(서비스 계획 및 자원연계 여부)",
        "kind": "text",
        "table": 0,
        "anchor": (13, 1),
        "multiline": True,
    },

    # ------------------------------------------------------------------
    # 표 1: 중점사례 결과보고
    # ------------------------------------------------------------------
    {"id": "report_date", "label": "결과보고 작성일", "kind": "text", "table": 1, "anchor": (2, 1)},
    {"id": "report_author", "label": "결과보고 작성자", "kind": "text", "table": 1, "anchor": (2, 3)},
    {"id": "report_subject", "label": "결과보고 대상", "kind": "text", "table": 1, "anchor": (3, 1)},
    {
        "id": "report_reason",
        "label": "선정사유",
        "kind": "text",
        "table": 1,
        "anchor": (4, 1),
        "multiline": True,
    },
    {
        "id": "report_intervention",
        "label": "개입 내용",
        "kind": "text",
        "table": 1,
        "anchor": (5, 1),
        "multiline": True,
    },
    {
        "id": "report_result",
        "label": "결과보고 내용",
        "kind": "text",
        "table": 1,
        "anchor": (6, 1),
        "multiline": True,
    },
]


_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _h_meeting_datetime(text: str, values: dict[str, Any]) -> str | None:
    year = (values.get("meeting_year") or "").strip()
    month = (values.get("meeting_month") or "").strip()
    day = (values.get("meeting_day") or "").strip()
    start_hour = (values.get("meeting_start_hour") or "").strip()
    start_min = (values.get("meeting_start_min") or "").strip()
    end_hour = (values.get("meeting_end_hour") or "").strip()
    end_min = (values.get("meeting_end_min") or "").strip()

    if not any([year, month, day, start_hour, start_min, end_hour, end_min]):
        return None

    weekday = ""
    if year and month and day:
        try:
            import datetime

            weekday = _WEEKDAY_KO[datetime.date(int(year), int(month), int(day)).weekday()] + "요일"
        except (ValueError, TypeError):
            weekday = ""

    return (
        f"  {year}년 {month}월 {day}일  {weekday}    "
        f"{start_hour}시 {start_min}분 ~ {end_hour}시 {end_min}분  "
    )


CUSTOM_HANDLERS = {
    "meeting_datetime": _h_meeting_datetime,
}


def get_field(field_id: str) -> dict[str, Any] | None:
    for f in FIELDS:
        if f["id"] == field_id:
            return f
    return None
