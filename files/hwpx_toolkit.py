"""
hwpx_toolkit.py

python-hwpx (v6.3.0 기준, PyPI: python-hwpx) 를 이용해
"상담양식.hwpx" 같은 고정 서식 HWPX 문서에 값을 채워 넣기 위한 헬퍼 모듈.

핵심 배경 (분석 결과 요약):
- 이 양식의 체크박스는 실제 체크박스 개체(Form Field)가 아니라,
  셀 텍스트 안에 박힌 일반 유니코드 문자 '□' (미체크) / '■' (체크됨) 이다.
  (doc.fields.check_boxes 로 조회하면 0개가 나옴 -> 진짜 체크박스 아님)
- 따라서 값을 채운다는 것은 "라벨을 찾아 텍스트를 넣는" 것이 아니라,
  "셀의 원본 템플릿 문자열에서 특정 □를 ■로 바꾸고, 괄호/공백 빈칸에 문자열을
  끼워 넣어 새 문자열을 만든 뒤 cell.set_text()로 덮어쓰는" 작업이다.
- 표는 병합 셀(span)이 많다. get_cell_map()은 병합된 영역 전체에 동일한 anchor를
  반환하므로, 중복 방문을 막기 위해 anchor 기준으로 dedup 해야 한다.

이 파일은:
  1) 문서를 열어 표/셀 구조를 딕셔너리로 덤프하는 함수
  2) 체크박스 템플릿 문자열을 토글하는 함수
  3) 괄호/빈칸에 자유 텍스트를 채우는 함수
  4) 위 세 가지를 조합해 실제 저장까지 해보는 실행 예시
를 포함한다. 실제 업로드된 원본 파일 구조를 기준으로 테스트를 마쳤다.

설치:
    pip install python-hwpx --break-system-packages   # 서버(리눅스) 환경
    pip install python-hwpx                            # 일반 환경

python-hwpx 6.3.0 API 메모 (2026-09 기준 확인):
- HwpxDocument.open(path) / doc.save_to_path(path)
- doc.tables.all              -> list[HwpxOxmlTable]  (list, 메서드 아님. 주의: 예전 문서엔 tables()로 나오는 예시도 있으나
                                  현재 버전은 프로퍼티/네임스페이스 형태이므로 실제 설치된 버전에서 dir(doc)로 재확인 권장)
- table.get_cell_map()        -> list[list[HwpxTableGridPosition]]  (각 원소: anchor, span, cell)
- table.cell(row, col)        -> HwpxOxmlTableCell
- cell.text                   -> str (읽기)
- cell.set_text(text)         -> 셀 전체 텍스트 교체 (서식 유지, 문서 재조립 없이 반영)
- doc.tables.fill_by_path(...) / doc.fill_by_path(...) 같은 라벨 기반 자동 채우기 API도
  존재하나, 체크박스처럼 "부분 문자열 치환"이 필요한 셀에는 맞지 않아 이 모듈에서는
  cell.set_text()로 직접 문자열을 만들어 넣는 방식을 택했다.
- 버전에 따라 API 위치가 이동/deprecate 될 수 있으므로(예: list_check_boxes ->
  fields.check_boxes), Claude Code로 작업 시작 시 실제 설치된 버전에서
  `python3 -c "from hwpx import HwpxDocument; d=HwpxDocument.open('...'); print(dir(d))"`
  로 먼저 API를 재확인할 것을 권장.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any

from hwpx import HwpxDocument


# ---------------------------------------------------------------------------
# 1) 문서 구조 덤프
# ---------------------------------------------------------------------------

def dump_tables(path: str) -> list[dict[str, Any]]:
    """HWPX 문서의 모든 표를 병합 셀 dedup 처리해서 anchor/span/text로 덤프한다."""
    doc = HwpxDocument.open(path)
    out = []
    for ti, t in enumerate(doc.tables.all):
        seen = set()
        cells = []
        for row in t.get_cell_map():
            for pos in row:
                if pos.anchor in seen:
                    continue
                seen.add(pos.anchor)
                cells.append({
                    "anchor": list(pos.anchor),
                    "span": list(pos.span),
                    "text": pos.cell.text,
                })
        out.append({
            "table_index": ti,
            "rows": t.row_count,
            "cols": t.column_count,
            "cells": cells,
        })
    doc.close() if hasattr(doc, "close") else None
    return out


# ---------------------------------------------------------------------------
# 2) 체크박스 토글 / 빈칸 채우기 엔진
# ---------------------------------------------------------------------------

def toggle_checkboxes(template: str, selected_labels: list[str]) -> str:
    """
    template 문자열 안의 각 '□옵션라벨' 조각을 찾아,
    selected_labels 중 하나라도 해당 옵션 라벨에 부분 포함되면 □ -> ■ 로 바꾼다.

    예:
        toggle_checkboxes('□ 유   □ 무', ['유'])
        -> '■ 유   □ 무'
    """
    parts = re.split(r'(□)', template)
    result = []
    i = 0
    while i < len(parts):
        if parts[i] == '□':
            label_chunk = parts[i + 1] if i + 1 < len(parts) else ''
            checked = any(sel.strip() and sel.strip() in label_chunk for sel in selected_labels)
            result.append('■' if checked else '□')
        else:
            result.append(parts[i])
        i += 1
    return ''.join(result)


def fill_paren_blank(template: str, label_before: str, value: str) -> str:
    """
    'label_before(공백 또는 내용)' 패턴의 괄호 안을 value로 교체한다.
    예: fill_paren_blank('기타(               )', '기타', '원룸')
        -> '기타( 원룸 )'
    같은 라벨이 여러 번 나오면 첫 번째만 교체하므로, 필요시 count 인자를 조정할 것.
    """
    pattern = re.escape(label_before) + r'\([^)]*\)'
    return re.sub(pattern, f'{label_before}( {value} )', template, count=1)


def fill_named_blank(template: str, before_marker: str, value: str, blank_char: str = ' ') -> str:
    """
    '층수' 셀의 '(    층)' 처럼 괄호가 아닌 공백/밑줄 빈칸을 채울 때 사용.
    before_marker 뒤에 오는 연속 공백/밑줄 구간을 value로 치환한다.
    예: fill_named_blank('(    층)', '(', '3') -> '(3층)' 유사 처리가 필요하면
    실제 셀별로 정규식을 커스터마이즈해야 한다 (형식이 셀마다 다름).
    이 함수는 참고용 뼈대이며, field_schema.json의 raw_text를 보고
    셀별 맞춤 정규식을 작성하는 것을 권장한다.
    """
    pattern = re.escape(before_marker) + r'[\s_]+'
    return re.sub(pattern, before_marker + value, template, count=1)


# ---------------------------------------------------------------------------
# 3) 필드 값 적용 (예시: 1쪽 표의 대표 필드 몇 가지)
# ---------------------------------------------------------------------------

@dataclass
class FieldValue:
    """LLM 구조화 출력이 채워줄 값의 표준 형태 (Claude Code에서 이 모양의 JSON을
    LLM에게 요청하도록 프롬프트/스키마를 설계하면 된다)."""
    field_id: str
    value: Any  # 텍스트 필드면 str, 체크박스 그룹이면 list[str] (선택된 라벨들)


def apply_known_fields(doc: HwpxDocument, values: dict[str, FieldValue]) -> None:
    """
    실제 채우기 로직의 뼈대. 여기서는 검증된 셀 몇 개만 예시로 구현했다.
    나머지 필드는 field_schema.json 을 참고해 동일한 패턴으로 확장하면 된다.

    values 딕셔너리 키는 field_id (예: "user_name", "housing_type" 등).
    실제 서비스에서는 field_schema.json 을 로드해서 anchor를 찾아
    자동으로 순회하는 방식으로 일반화하는 것을 권장한다.
    """
    t0 = doc.tables.all[0]

    if "user_name" in values:
        t0.cell(3, 1).set_text(str(values["user_name"].value))

    if "birth_date" in values:
        t0.cell(3, 5).set_text(str(values["birth_date"].value))

    if "phone" in values:
        t0.cell(3, 9).set_text(str(values["phone"].value))

    if "disability_type" in values:
        t0.cell(4, 1).set_text(str(values["disability_type"].value))

    if "support_grade" in values:
        t0.cell(4, 7).set_text(str(values["support_grade"].value))

    if "housing_type" in values:
        # 원본 템플릿: '□단독주택   □아파트   □연립·다세대주택  □기타(               )'
        cell = t0.cell(7, 4)
        raw = cell.text
        selected = values["housing_type"].value  # list[str], 예: ['기타']
        new_text = toggle_checkboxes(raw, selected)
        if "기타" in selected and "housing_type_etc" in values:
            new_text = fill_paren_blank(new_text, "기타", str(values["housing_type_etc"].value))
        cell.set_text(new_text)

    if "smoking" in values:
        cell = t0.cell(14, 4)
        raw = cell.text
        cell.set_text(toggle_checkboxes(raw, [values["smoking"].value]))

    if "notes" in values:
        t0.cell(16, 4).set_text(str(values["notes"].value))


# ---------------------------------------------------------------------------
# 4) 실행 예시 (검증됨: 실제 업로드된 상담양식.hwpx 구조 기준으로 테스트)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("사용법: python hwpx_toolkit.py <원본양식.hwpx> <출력파일.hwpx>")
        sys.exit(1)

    src, dst = sys.argv[1], sys.argv[2]

    doc = HwpxDocument.open(src)

    sample_values = {
        "user_name": FieldValue("user_name", "홍길동"),
        "birth_date": FieldValue("birth_date", "80****"),
        "phone": FieldValue("phone", "010-1234-****"),
        "disability_type": FieldValue("disability_type", "지체장애"),
        "support_grade": FieldValue("support_grade", "13구간 (나형)"),
        "housing_type": FieldValue("housing_type", ["기타"]),
        "housing_type_etc": FieldValue("housing_type_etc", "빌라"),
        "smoking": FieldValue("smoking", "비흡연자"),
        "notes": FieldValue("notes", "고혈압 관리 중, 특이사항 없음"),
    }

    apply_known_fields(doc, sample_values)
    doc.save_to_path(dst)
    print(f"저장 완료: {dst}")
