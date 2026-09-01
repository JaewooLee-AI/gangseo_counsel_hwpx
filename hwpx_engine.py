"""
hwpx_engine.py

field_map.py 의 FIELDS 정의를 이용해 LLM이 추출한 값(flat dict)을 counsel.hwpx
템플릿에 실제로 채워 넣는 엔진.

배경(files/hwpx_form_analysis.md 참고):
- 이 서식의 체크박스는 진짜 폼 필드가 아니라 셀 텍스트에 박힌 유니코드 문자
  '□'(미체크) / '■'(체크됨) 이다. 따라서 "채운다" = 셀의 원본 문자열에서 특정
  □를 ■로 바꾸고, 괄호/빈칸에 텍스트를 끼워 넣어 새 문자열을 만들어
  cell.set_text()로 덮어쓰는 작업이다.
- 한 셀 안에 같은 라벨("상"/"중"/"하" 등)이 여러 번 반복되는 경우가 있어,
  단순 전체-셀 토글은 오작동한다. 이를 위해 prefix로 범위를 좁히는
  toggle_scoped / toggle_scoped_until, 줄 단위로만 토글하는 toggle_multiline을
  따로 둔다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from hwpx import HwpxDocument

from field_map import FIELDS

# ---------------------------------------------------------------------------
# 레이아웃 캐시 보존 텍스트 교체
# ---------------------------------------------------------------------------
#
# python-hwpx의 cell.set_text() / paragraph.text 세터는 호출될 때마다 무조건
# 문단의 <hp:linesegarray>(한글이 마지막으로 계산해 둔 줄 배치 캐시)를 지운다.
# 캐시가 없는 문단을 한글에서 열면 그 문단만 다시 레이아웃을 계산하는데, 이
# 서식(counsel.hwpx)의 표는 noAdjust="1"(행 높이 자동 확장 안 됨) + 여러 줄
# 체크박스 항목이 칸 높이에 딱 맞게 촘촘히 배치되어 있어서, 재계산된 줄 높이가
# 원본 캐시보다 아주 조금만 커져도 표 전체가 밀려 다음 페이지로 넘친다. 실제로
# 상담 값을 채운 뒤 저장한 파일이 원본 2쪽짜리 서식인데도 3쪽으로 생성되는
# 문제가 있었는데, 원인이 바로 이것이었다(가운데 생기는 여백투성이 3번째 페이지).
#
# 체크박스 토글(□ -> ■)처럼 글자 수가 그대로인 교체는 실제 렌더링 폭이 바뀌지
# 않으므로 캐시를 지울 필요가 없다. 그런 경우엔 <hp:t> 텍스트 노드만 직접
# 덮어써서 <hp:linesegarray>를 그대로 보존한다. 줄 수가 달라지거나 어느 한
# 줄이라도 글자 수가 달라지면(빈칸에 실제 값을 채워 넣는 경우 등) 안전하게
# 원래의 cell.set_text(split_paragraphs=True)로 폴백해 한글이 다시 계산하게
# 둔다.


def _paragraph_single_text_node(paragraph):
    """paragraph가 '런 1개 + 텍스트 노드 1개'로만 이루어진 단순한 구조면 그
    텍스트 엘리먼트를 반환하고, tab/여러 런 등 복잡한 구조면 None을 반환한다."""
    runs = paragraph.runs
    if len(runs) != 1:
        return None
    children = list(runs[0].element)
    if len(children) != 1:
        return None
    (only_child,) = children
    if only_child.tag.rsplit("}", 1)[-1] != "t":
        return None
    return only_child


def set_cell_text_preserving_layout(cell: Any, new_text: str) -> None:
    paragraphs = cell.paragraphs
    new_lines = (new_text or "").split("\n")
    if len(new_lines) != len(paragraphs):
        cell.set_text(new_text, split_paragraphs=True)
        return

    text_nodes = []
    for paragraph, new_line in zip(paragraphs, new_lines):
        old_line = paragraph.text or ""
        if len(old_line) != len(new_line):
            cell.set_text(new_text, split_paragraphs=True)
            return
        node = _paragraph_single_text_node(paragraph)
        if node is None:
            cell.set_text(new_text, split_paragraphs=True)
            return
        text_nodes.append((node, new_line))

    for node, new_line in text_nodes:
        if node.text != new_line:
            node.text = new_line

# ---------------------------------------------------------------------------
# 문서 구조 덤프 (디버그/검증용)
# ---------------------------------------------------------------------------


def dump_tables(path: str) -> list[dict[str, Any]]:
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
                cells.append({"anchor": list(pos.anchor), "span": list(pos.span), "text": pos.cell.text})
        out.append({"table_index": ti, "rows": t.row_count, "cols": t.column_count, "cells": cells})
    return out


# ---------------------------------------------------------------------------
# 체크박스 토글 / 빈칸 채우기 원시 함수
# ---------------------------------------------------------------------------


def _chunk_matches(chunk: str, selected_labels: list[str]) -> bool:
    """□ 뒤에 오는 텍스트 조각(chunk)이 선택된 옵션 중 하나로 '시작'하는지 검사.
    부분 문자열 포함(in)이 아니라 접두 일치(startswith)를 쓰는 이유: 이 서식에는
    "가능"/"불가능", "흡연자"/"비흡연자"처럼 한 옵션이 다른 옵션의 부분 문자열인
    경우가 많아, 포함 검사만 쓰면 "흡연자"를 선택했는데 "비흡연자"까지 함께
    체크되는 등의 오탐이 생긴다. 각 옵션 라벨은 항상 자기 자신의 □ 바로 뒤에서
    시작하므로 접두 일치로 충분하다."""
    stripped = chunk.strip()
    return any(sel.strip() and stripped.startswith(sel.strip()) for sel in selected_labels)


def toggle_checkboxes(template: str, selected_labels: list[str]) -> str:
    """template 전체를 대상으로, 각 '□옵션라벨' 조각의 옵션라벨이 selected_labels
    중 하나로 시작하면 □ -> ■ 로 바꾼다. (옵션 라벨이 셀 안에서 서로 충돌하지
    않을 때만 사용할 것.)"""
    parts = re.split(r"(□)", template)
    result = []
    i = 0
    while i < len(parts):
        if parts[i] == "□":
            label_chunk = parts[i + 1] if i + 1 < len(parts) else ""
            checked = _chunk_matches(label_chunk, selected_labels)
            result.append("■" if checked else "□")
        else:
            result.append(parts[i])
        i += 1
    return "".join(result)


def toggle_multiline(template: str, options: list[str], selected_labels: list[str]) -> str:
    """줄 단위로 분리해, 각 줄의 '첫 번째' □ 만 그 줄의 텍스트가 selected_labels
    중 하나로 시작하는지에 따라 토글한다. 같은 셀 안에 여러 독립적인 항목이
    줄바꿈으로 구분된 경우(신체 불편사항, 애완동물 유무 등)에 사용. 줄 안의
    두 번째 이후 □(중첩된 하위 옵션)는 건드리지 않는다."""
    lines = template.split("\n")
    new_lines = []
    for line in lines:
        idx = line.find("□")
        if idx == -1:
            new_lines.append(line)
            continue
        rest = line[idx + 1 :]
        next_idx = rest.find("□")
        chunk = rest if next_idx == -1 else rest[:next_idx]
        checked = _chunk_matches(chunk, selected_labels)
        new_lines.append(line[:idx] + ("■" if checked else "□") + line[idx + 1 :])
    return "\n".join(new_lines)


def toggle_scoped(template: str, prefix: str, options: list[str], selected: str) -> str:
    """prefix 뒤에 나오는 첫 '(...)' 괄호 안에서만 토글한다 (예: '언어 표현 능력
    (□ 상 □ 중 □ 하)'). 같은 옵션 라벨이 셀 안에서 여러 번 반복될 때 사용."""
    if not selected:
        return template
    pattern = re.escape(prefix) + r"\s*\(([^)]*)\)"
    m = re.search(pattern, template)
    if not m:
        return template
    inner = toggle_checkboxes(m.group(1), [selected])
    return template[: m.start(1)] + inner + template[m.end(1) :]


def toggle_scoped_until(template: str, prefix: str, end_chars: str, options: list[str], selected: str) -> str:
    """prefix 뒤부터 end_chars 중 하나가 나오기 전까지의 구간에서만 토글한다.
    괄호로 자체 완결되지 않고 상위 괄호에 얹혀 있는 하위 옵션(애완동물 공격성
    등)에 사용."""
    if not selected:
        return template
    idx = template.find(prefix)
    if idx == -1:
        return template
    start = idx + len(prefix)
    end = len(template)
    for i in range(start, len(template)):
        if template[i] in end_chars:
            end = i
            break
    inner = toggle_checkboxes(template[start:end], [selected])
    return template[:start] + inner + template[end:]


def fill_paren_blank(template: str, label_before: str, value: str) -> str:
    """'label_before(내용)' 패턴에서 괄호 안을 value로 교체한다 (라벨과 괄호 사이
    공백 유무는 원본 그대로 유지). 같은 라벨이 여러 번 나오면 첫 번째만 교체."""
    pattern = re.escape(label_before) + r"(\s*)\(([^)]*)\)"

    def repl(m: re.Match) -> str:
        return f"{label_before}{m.group(1)}( {value} )"

    return re.sub(pattern, repl, template, count=1)


# ---------------------------------------------------------------------------
# custom 필드 핸들러
# ---------------------------------------------------------------------------


def _h_living_rooms(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("living_rooms")
    if not v:
        return None
    return re.sub(r"[□■]방\s*\d+\s*개", f"■방 {v} 개", text, count=1)


def _h_floor_number(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("floor_number")
    if not v:
        return None
    return re.sub(r"\(\s*층\)", f"({v}층)", text, count=1)


def _h_transport_bus_interval(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("transport_bus_interval")
    if not v:
        return None
    return re.sub(r"배차간격\s*분", f"배차간격 {v}분", text, count=1)


def _h_transport_walk_minutes(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("transport_walk_minutes")
    if not v:
        return None
    return re.sub(r"도보 이동 시간\s*분", f"도보 이동 시간 {v}분", text, count=1)


def _h_pet_type(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("pet_type")
    if not v:
        return None
    return re.sub(r"※종류:\s*", f"※종류: {v}  ", text, count=1)


def _h_household_size(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("household_size")
    if not v:
        return None
    return re.sub(r"^\s*명", f"  {v}명", text, count=1)


def _h_emergency_contact(text: str, values: dict[str, Any]) -> str | None:
    phone = values.get("emergency_phone")
    relation = values.get("emergency_relation")
    if not phone and not relation:
        return None
    if phone:
        text = re.sub(r"(휴대번호:)\s*(?=수급자와의 관계:)", rf"\1 {phone}   ", text, count=1)
    if relation:
        text = re.sub(r"(수급자와의 관계:)\s*$", rf"\1  {relation}", text, count=1)
    return text


def _h_night_monthly_hours(text: str, values: dict[str, Any]) -> str | None:
    v = values.get("night_monthly_hours")
    if not v:
        return None
    return re.sub(r"^\s*시간/일", f"  {v}시간/일", text, count=1)


def _h_interview_date(text: str, values: dict[str, Any]) -> str | None:
    month = values.get("interview_month")
    day = values.get("interview_day")
    name = values.get("interviewer_name")
    if not (month or day or name):
        return None

    def repl(m: re.Match) -> str:
        return f"면담 일자: 2026. {month or ''} . {day or ''} .           면담원 성명 : {name or ''}"

    return re.sub(r"^면담 일자:[^\n]*", repl, text, count=1)


CUSTOM_HANDLERS = {
    "living_rooms": _h_living_rooms,
    "floor_number": _h_floor_number,
    "transport_bus_interval": _h_transport_bus_interval,
    "transport_walk_minutes": _h_transport_walk_minutes,
    "pet_type": _h_pet_type,
    "household_size": _h_household_size,
    "emergency_contact": _h_emergency_contact,
    "night_monthly_hours": _h_night_monthly_hours,
    "interview_date": _h_interview_date,
}


# ---------------------------------------------------------------------------
# 폭이 좁은 고정폭 칸 보정
# ---------------------------------------------------------------------------
#
# "sido"(시·도) 칸은 폭이 약 16mm로 설계돼 있는데, 이 서식이 강서구 전용이라
# 실제 값은 항상 "서울특별시"(5자)가 들어간다. 이 값이 칸 너비를 넘어서면
# 2줄로 줄바꿈되는데, noAdjust="1" 고정 행 높이 표에서는 그 줄바꿈이 뒤 내용
# 전체를 다음 페이지로 밀어내 문서가 2쪽짜리에서 3쪽으로 늘어난다(실측: 이
# 칸 하나만 채워도 재현되고, 장평을 좁히면 사라진다). 장평(ratio)을 줄여
# 한 줄에 들어가게 한다. 값 -> 장평(%).
NARROW_FIELD_RATIO: dict[str, int] = {
    "sido": 70,
}


def _ensure_narrow_run_style(doc: HwpxDocument, base_char_pr_id: str, ratio: int) -> str:
    base = doc.styles.char_properties.get(str(base_char_pr_id))
    bold = base is not None and "bold" in base.child_attributes
    color = base.attributes.get("textColor") if base is not None else None
    return doc.styles.ensure_run(base_char_pr_id=base_char_pr_id, ratio=ratio, bold=bold, color=color)


def _apply_narrow_field_ratios(doc: HwpxDocument, values: dict[str, Any]) -> None:
    tables = doc.tables.all
    for f in FIELDS:
        ratio = NARROW_FIELD_RATIO.get(f["id"])
        if ratio is None or not values.get(f["id"]):
            continue
        row, col = f["anchor"]
        cell = tables[f["table"]].cell(row, col)
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                new_id = _ensure_narrow_run_style(doc, run.char_pr_id_ref or "0", ratio)
                run.char_pr_id_ref = new_id


# ---------------------------------------------------------------------------
# 메인 적용 로직
# ---------------------------------------------------------------------------


def apply_fields(doc: HwpxDocument, values: dict[str, Any]) -> None:
    """values(필드 id -> 추출값)를 FIELDS 정의에 따라 doc에 적용한다.

    주의: python-hwpx의 cell.set_text()는 셀의 기존 문단 수만큼 빈 줄을
    누적시키는 특성이 있다(같은 셀에 set_text를 여러 번 호출하면 호출할
    때마다 빈 문단이 늘어남). 여러 필드가 같은 셀(anchor)을 공유하는 경우가
    많으므로(예: 방 개수 + 이용공간, 신체활동/가사활동/사회활동 지원 등),
    셀별로 최종 텍스트를 메모리에서 모두 조합한 뒤 셀당 set_text()를 딱
    한 번만 호출한다. FIELDS 안에서 같은 anchor를 참조하는 필드들은 정의된
    순서대로 누적 적용되므로, field_map.py에서 그 순서를 바꾸지 말 것."""
    tables = doc.tables.all
    text_cache: dict[tuple[int, tuple[int, int]], str] = {}
    touched: set[tuple[int, tuple[int, int]]] = set()

    def get_text(table_idx: int, anchor: tuple[int, int]) -> str:
        key = (table_idx, anchor)
        if key not in text_cache:
            row, col = anchor
            text_cache[key] = tables[table_idx].cell(row, col).text
        return text_cache[key]

    def put_text(table_idx: int, anchor: tuple[int, int], new_text: str) -> None:
        key = (table_idx, anchor)
        text_cache[key] = new_text
        touched.add(key)

    for f in FIELDS:
        table_idx = f["table"]
        anchor = f["anchor"]
        kind = f["kind"]
        text = get_text(table_idx, anchor)

        if kind == "text":
            val = values.get(f["id"])
            if val:
                put_text(table_idx, anchor, str(val))

        elif kind == "checkbox":
            selected = values.get(f["id"]) or []
            if isinstance(selected, str):
                selected = [selected]
            changed = False
            if selected:
                text = toggle_checkboxes(text, selected)
                changed = True
            for blank in f.get("blanks", []):
                if blank["trigger"] in selected:
                    bval = values.get(blank["id"])
                    if bval:
                        text = fill_paren_blank(text, blank["trigger"], str(bval))
                        changed = True
            if changed:
                put_text(table_idx, anchor, text)

        elif kind == "checkbox_multiline":
            selected = values.get(f["id"]) or []
            if isinstance(selected, str):
                selected = [selected]
            if selected:
                put_text(table_idx, anchor, toggle_multiline(text, f["options"], selected))

        elif kind == "checkbox_scoped":
            selected = values.get(f["id"])
            if selected:
                put_text(table_idx, anchor, toggle_scoped(text, f["prefix"], f["options"], selected))

        elif kind == "checkbox_scoped_until":
            selected = values.get(f["id"])
            if selected:
                put_text(
                    table_idx,
                    anchor,
                    toggle_scoped_until(text, f["prefix"], f["end_chars"], f["options"], selected),
                )

        elif kind == "custom":
            handler = CUSTOM_HANDLERS.get(f["id"])
            if handler:
                new_text = handler(text, values)
                if new_text is not None:
                    put_text(table_idx, anchor, new_text)

    for table_idx, anchor in touched:
        row, col = anchor
        # set_cell_text_preserving_layout: 글자 수가 그대로인 교체(체크박스
        # 토글 등)는 <hp:linesegarray> 캐시를 보존해 페이지가 밀리는 것을
        # 막고, 그 외의 경우엔 기존처럼 cell.set_text(split_paragraphs=True)로
        # 폴백한다. split_paragraphs=True가 필요한 이유: 이 서식의 다중 줄
        # 셀은 내부적으로 줄마다 별도 <hp:p> 문단으로 구성되어 있는데, 기본
        # 옵션(split_paragraphs=False)으로 set_text를 쓰면 전체 텍스트를 첫
        # 문단에만 넣고 나머지 문단은 빈 채로 남겨, 호출할 때마다 셀 끝에
        # 빈 줄이 누적된다.
        set_cell_text_preserving_layout(tables[table_idx].cell(row, col), text_cache[(table_idx, anchor)])

    _apply_narrow_field_ratios(doc, values)


def generate_hwpx_bytes(template_path: str, values: dict[str, Any]) -> bytes:
    """template_path를 열어 values를 채운 뒤 결과 hwpx 파일의 바이트를 반환한다
    (Streamlit download_button 등에서 사용)."""
    doc = HwpxDocument.open(template_path)
    apply_fields(doc, values)
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "output.hwpx"
        doc.save_to_path(str(out_path))
        return out_path.read_bytes()
