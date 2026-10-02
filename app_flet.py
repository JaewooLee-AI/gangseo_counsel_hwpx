"""
app_flet.py

강서나눔돌봄센터 활동지원 서식 자동화 (수요조사카드 · 중점사례 회의록) Flet 데스크톱 앱.
Google Stitch AI의 "Civic Trust Desktop" 디자인 시스템을 적용한 프리미엄 UI 버전.

주요 UI/UX:
  - Midnight Slate(#0F172A) 커스텀 사이드바 네비게이션 (서식별 직접 분리 구성)
  - Soft Slate(#F8FAFC) 캔버스 및 Pure White(#FFFFFF) 카드 섹션 분할
  - 좌측 사이드바에서 [수요조사카드] / [중점사례 회의록] 업무를 직관적으로 직접 구분 선택
  - 서식별 입력(AI 분석) → 서식 확인 및 HWPX 생성 → 문서함(저장/불러오기) 단계별 메뉴 제공
  - Gemini API 키 및 설정 영구 보관 (~/.gangseo_counsel_config.json)
  - 문서함 저장 파일: ~/.gangseo_counsel_docs/<업무>/<생성일자-순번>.json (document_store.py)
  - 폰트 크기(13/15/17/19px) 실시간 변경 및 영구 보관
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import flet as ft

import document_store
import forms
from hwpx_engine import generate_hwpx_bytes
from llm_client import (
    DEFAULT_MODEL,
    STYLE_BULLET,
    STYLE_NARRATIVE,
    WRITING_STYLES,
    extract_fields,
    test_api_key,
)


def get_resource_path(relative_path: str) -> str:
    """PyInstaller 번들 환경(sys._MEIPASS) 및 로컬 환경에서 리소스 경로를 안전하게 반환합니다."""
    if hasattr(sys, "_MEIPASS"):
        meipass_path = Path(sys._MEIPASS) / relative_path
        if meipass_path.exists():
            return str(meipass_path)
    base_dir = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    local_path = base_dir / relative_path
    if local_path.exists():
        return str(local_path)
    cwd_path = Path(relative_path)
    if cwd_path.exists():
        return str(cwd_path.resolve())
    return relative_path


EMPTY_CHOICE = "(선택 안 함)"

CONFIG_PATH = Path.home() / ".gangseo_counsel_config.json"
LOCAL_CONFIG_PATH = Path(".app_config.json")

_MULTILINE_HINTS = ("특이사항", "여가활동", "사회활동", "직장", "학교", "자녀", "복지서비스", "필요 사유")


def _is_multiline(f: dict[str, Any]) -> bool:
    """필드 dict에 명시적 "multiline" 플래그가 있으면 그것을, 없으면 라벨 키워드
    휴리스틱을 사용한다(counsel.hwpx 쪽 기존 필드들은 플래그가 없어 휴리스틱으로 판정됨)."""
    if f.get("multiline"):
        return True
    return any(h in f["label"] for h in _MULTILINE_HINTS)


# ---------------------------------------------------------------------------
# 설정 입출력 함수 (영구 보관)
# ---------------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    """저장된 설정을 로드한다. 홈 디렉터리 우선, 프로젝트 로컬 폴백."""
    for path in (CONFIG_PATH, LOCAL_CONFIG_PATH):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def save_config(cfg: dict[str, Any]) -> bool:
    """설정을 파일에 저장한다."""
    success = False
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        success = True
    except Exception:
        try:
            with open(LOCAL_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            success = True
        except Exception:
            pass
    return success


# ---------------------------------------------------------------------------
# 위젯 레지스트리 및 폰트 크기 동적 조절 (업무별 fields 파라미터화)
# ---------------------------------------------------------------------------
def build_widgets(fields: list[dict[str, Any]], font_size: int = 15) -> dict[str, Any]:
    """값 id -> Flet 컨트롤 매핑 생성"""
    widgets: dict[str, Any] = {}
    label_style = ft.TextStyle(size=font_size)

    for f in fields:
        kind = f["kind"]
        if kind == "text":
            widgets[f["id"]] = ft.TextField(
                label=f["label"],
                multiline=_is_multiline(f),
                min_lines=3 if _is_multiline(f) else 1,
                text_size=font_size,
                label_style=label_style,
                margin=ft.Margin(left=0, top=6, right=0, bottom=2),
            )
        elif kind == "custom":
            for sub in f["subfields"]:
                widgets[sub["id"]] = ft.TextField(
                    label=sub["label"],
                    text_size=font_size,
                    label_style=label_style,
                    margin=ft.Margin(left=0, top=6, right=0, bottom=2),
                )
        elif kind in ("checkbox", "checkbox_multiline"):
            entry: dict[str, Any] = {
                "checks": {
                    opt: ft.Checkbox(label=opt, value=False, label_style=label_style)
                    for opt in f["options"]
                },
                "blanks": {},
            }
            for blank in f.get("blanks", []):
                entry["blanks"][blank["id"]] = ft.TextField(
                    label=blank["label"],
                    text_size=font_size,
                    label_style=label_style,
                    margin=ft.Margin(left=0, top=6, right=0, bottom=2),
                )
            widgets[f["id"]] = entry
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            widgets[f["id"]] = ft.Dropdown(
                label=f["label"],
                options=[ft.DropdownOption(EMPTY_CHOICE)] + [ft.DropdownOption(o) for o in f["options"]],
                value=EMPTY_CHOICE,
                text_size=font_size,
                label_style=label_style,
                margin=ft.Margin(left=0, top=6, right=0, bottom=2),
            )
    return widgets


def apply_font_size(widgets: dict[str, Any], font_size: int) -> None:
    """기존 생성된 위젯들의 글자 크기를 동적으로 갱신한다."""
    label_style = ft.TextStyle(size=font_size)
    for ctrl in widgets.values():
        if isinstance(ctrl, ft.TextField):
            ctrl.text_size = font_size
            ctrl.label_style = label_style
        elif isinstance(ctrl, ft.Dropdown):
            ctrl.text_size = font_size
            ctrl.label_style = label_style
        elif isinstance(ctrl, dict):
            for box in ctrl.get("checks", {}).values():
                box.label_style = label_style
            for blank in ctrl.get("blanks", {}).values():
                blank.text_size = font_size
                blank.label_style = label_style


def set_widgets_from_extracted(
    widgets: dict[str, Any], fields: list[dict[str, Any]], extracted: dict[str, Any]
) -> None:
    """AI 추출값(또는 {})을 위젯에 반영한다."""
    for f in fields:
        kind = f["kind"]
        fid = f["id"]
        if kind == "text":
            widgets[fid].value = extracted.get(fid) or ""
        elif kind == "custom":
            for sub in f["subfields"]:
                widgets[sub["id"]].value = extracted.get(sub["id"]) or ""
        elif kind in ("checkbox", "checkbox_multiline"):
            selected = extracted.get(fid) or []
            if isinstance(selected, str):
                selected = [selected]
            selected = [o for o in selected if o in f["options"]]
            for opt, box in widgets[fid]["checks"].items():
                box.value = opt in selected
            for blank in f.get("blanks", []):
                widgets[fid]["blanks"][blank["id"]].value = extracted.get(blank["id"]) or ""
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            val = extracted.get(fid) or ""
            widgets[fid].value = val if val in f["options"] else EMPTY_CHOICE


def collect_values(widgets: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """위젯 상태에서 hwpx_engine.apply_fields용 flat dict를 만든다."""
    values: dict[str, Any] = {}
    for f in fields:
        kind = f["kind"]
        fid = f["id"]
        if kind == "text":
            values[fid] = widgets[fid].value or ""
        elif kind == "custom":
            for sub in f["subfields"]:
                values[sub["id"]] = widgets[sub["id"]].value or ""
        elif kind in ("checkbox", "checkbox_multiline"):
            values[fid] = [opt for opt, box in widgets[fid]["checks"].items() if box.value]
            for blank in f.get("blanks", []):
                values[blank["id"]] = widgets[fid]["blanks"][blank["id"]].value or ""
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            sel = widgets[fid].value
            values[fid] = sel if sel in f["options"] else None
    return values


# ---------------------------------------------------------------------------
# Stitch 스타일 그룹 카드 빌더
# ---------------------------------------------------------------------------
def controls_for_fields(
    widgets: dict[str, Any], fields: list[dict[str, Any]], field_ids: list[str]
) -> list[ft.Control]:
    """특정 필드 ID 목록에 대한 입력 컨트롤들을 순서대로 반환한다."""
    id_set = set(field_ids)
    controls: list[ft.Control] = []
    for f in fields:
        fid = f["id"]
        if fid not in id_set:
            continue
        kind = f["kind"]
        if kind == "text":
            controls.append(widgets[fid])
        elif kind == "custom":
            controls.append(ft.Text(f["label"], weight=ft.FontWeight.W_600, size=13, color="#1E293B"))
            for sub in f["subfields"]:
                controls.append(widgets[sub["id"]])
        elif kind in ("checkbox", "checkbox_multiline"):
            boxes = list(widgets[fid]["checks"].values())
            blanks = list(widgets[fid]["blanks"].values())
            controls.append(
                ft.Column(
                    [
                        ft.Text(f["label"], weight=ft.FontWeight.W_600, size=13, color="#1E293B"),
                        *boxes,
                        *blanks,
                    ],
                    spacing=3,
                )
            )
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            controls.append(widgets[fid])
    return controls


def create_section_card(title: str, subtitle: str, controls: list[ft.Control], icon: str) -> ft.Card:
    """Stitch Civic Trust 디자인 시스템의 섹션 카드 컴포넌트"""
    return ft.Card(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(icon, color="#2563EB", size=18),
                                bgcolor="#EFF6FF",
                                border_radius=6,
                                padding=6,
                            ),
                            ft.Column(
                                [
                                    ft.Text(title, weight=ft.FontWeight.BOLD, size=15, color="#0F172A"),
                                    ft.Text(subtitle, size=12, color="#64748B") if subtitle else ft.Container(),
                                ],
                                spacing=1,
                            ),
                        ],
                        spacing=10,
                    ),
                    ft.Divider(height=1, color="#F1F5F9"),
                    ft.Container(height=2),
                    *controls,
                ],
                spacing=10,
            ),
            padding=20,
            bgcolor="#FFFFFF",
            border=ft.Border.all(1, "#E2E8F0"),
            border_radius=12,
        ),
        elevation=0,
        margin=ft.Margin(left=0, top=0, right=0, bottom=14),
    )


_ICONS = {
    "person": ft.Icons.PERSON_OUTLINE,
    "home": ft.Icons.HOME_WORK_OUTLINED,
    "health": ft.Icons.HEALTH_AND_SAFETY_OUTLINED,
    "people": ft.Icons.PEOPLE_OUTLINE,
    "time": ft.Icons.ACCESS_TIME_ROUNDED,
    "check": ft.Icons.FACT_CHECK_OUTLINED,
    "note": ft.Icons.EDIT_NOTE,
}


def widgets_for_table(
    widgets: dict[str, Any],
    fields: list[dict[str, Any]],
    review_sections: dict[int, list[tuple[str, str, str, list[str]]]],
    table_idx: int,
) -> list[ft.Control]:
    """forms.FormDefinition.review_sections 정의대로 특정 표를 섹션 카드들로 렌더링한다."""
    sections = review_sections.get(table_idx, [])
    return [
        create_section_card(
            title, subtitle, controls_for_fields(widgets, fields, ids), _ICONS.get(icon, ft.Icons.ARTICLE)
        )
        for title, subtitle, icon, ids in sections
    ]


def _format_dt(iso_text: str) -> str:
    if not iso_text:
        return ""
    return iso_text.replace("T", " ")[:16]


# ---------------------------------------------------------------------------
# Flet Application Main (Stitch Theme)
# ---------------------------------------------------------------------------
def main(page: ft.Page) -> None:
    page.title = "강서나눔돌봄센터 - 활동지원 서식 자동화 (수요조사카드 · 중점사례 회의록)"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#F8FAFC"  # Stitch Canvas Slate

    # 1. 설정 로드
    config = load_config()
    saved_key = config.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
    saved_model = config.get("model", DEFAULT_MODEL)
    saved_font_size = int(config.get("font_size", 15))

    state: dict[str, Any] = {
        "api_key": saved_key,
        "model": saved_model,
        "font_size": saved_font_size,
        "current_route": "counsel_input",
        "doc_id": {bid: None for bid in forms.FORM_ORDER},
    }
    # 항목별 작성 방식(서술형/개조식). style_prefs는 사용자가 마지막으로 고른 값(설정 파일에
    # 저장되어 새 문서의 기본값이 됨), field_styles는 지금 작성 중인 문서에 적용되는 값이다.
    saved_styles = config.get("field_styles") or {}
    state["style_prefs"] = {
        bid: forms.resolve_field_styles(form, saved_styles.get(bid)) for bid, form in forms.FORMS.items()
    }
    state["field_styles"] = {bid: dict(styles) for bid, styles in state["style_prefs"].items()}

    def config_snapshot() -> dict[str, Any]:
        return {
            "api_key": state["api_key"],
            "model": state["model"],
            "font_size": state["font_size"],
            "field_styles": state["style_prefs"],
        }

    # 2. 알림용 SnackBar (Flet 1.0 공식 show_dialog 방식)
    def snack(msg: str) -> None:
        try:
            page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(msg, color=ft.Colors.WHITE, size=13),
                    bgcolor="#0F172A",
                    behavior=ft.SnackBarBehavior.FLOATING,
                    show_close_icon=True,
                    duration=3500,
                )
            )
        except Exception:
            pass

    # 3. 업무별 위젯 셋 생성
    widgets: dict[str, dict[str, Any]] = {
        bid: build_widgets(form.fields, font_size=state["font_size"]) for bid, form in forms.FORMS.items()
    }

    # 4. 파일 저장 픽커 (업무 공용, 저장 시점에 파일명만 다르게 지정)
    file_picker = ft.FilePicker()

    async def do_save_file(data: bytes, file_name: str, status_text: ft.Text) -> None:
        saved_path = await file_picker.save_file(
            dialog_title="결과 HWPX 저장",
            file_name=file_name,
            src_bytes=data,
        )
        if saved_path:
            status_text.value = f"저장 완료: {saved_path}"
            status_text.color = "#059669"
            snack(f"HWPX 파일이 성공적으로 저장되었습니다:\n{saved_path}")
        else:
            status_text.value = "저장이 취소되었습니다."
            status_text.color = "#64748B"
        page.update()

    # -----------------------------------------------------------------------
    # 상태 알림 뱃지 갱신 헬퍼 (API 키는 업무 공용)
    # -----------------------------------------------------------------------
    def update_key_badges() -> None:
        has_key = bool((state["api_key"] or "").strip())
        if has_key:
            settings_key_badge.content = ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color="#10B981", size=18),
                    ft.Text("저장된 API Key가 정상적으로 설정되어 있습니다.", color="#065F46", size=13, weight=ft.FontWeight.W_500),
                ],
                spacing=8,
            )
            settings_key_badge.bgcolor = "#ECFDF5"
            settings_key_badge.border = ft.Border.all(1, "#A7F3D0")

            for badge in input_key_badges.values():
                badge.content = ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color="#10B981", size=16),
                        ft.Text("Gemini API 연동 준비 완료", color="#065F46", size=12, weight=ft.FontWeight.W_500),
                    ],
                    spacing=6,
                )
                badge.bgcolor = "#ECFDF5"
                badge.border = ft.Border.all(1, "#A7F3D0")

            rail_status_icon.name = ft.Icons.CHECK_CIRCLE
            rail_status_icon.color = "#10B981"
            rail_status_label.value = f"연동 완료 ({state['model']})"
        else:
            settings_key_badge.content = ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="#F59E0B", size=18),
                    ft.Text("저장된 API Key가 없습니다. Google AI Studio에서 키를 발급받아 입력 후 저장해주세요.", color="#92400E", size=13),
                ],
                spacing=8,
            )
            settings_key_badge.bgcolor = "#FFFBEB"
            settings_key_badge.border = ft.Border.all(1, "#FDE68A")

            for badge in input_key_badges.values():
                badge.content = ft.Row(
                    [
                        ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="#F59E0B", size=16),
                        ft.Text("API Key 미설정 — [설정] 메뉴에서 API 키를 등록해주세요.", color="#92400E", size=12),
                        ft.TextButton("설정으로 이동", on_click=lambda _: navigate_to("settings")),
                    ],
                    spacing=6,
                )
                badge.bgcolor = "#FFFBEB"
                badge.border = ft.Border.all(1, "#FDE68A")

            rail_status_icon.name = ft.Icons.WARNING_AMBER_ROUNDED
            rail_status_icon.color = "#F59E0B"
            rail_status_label.value = "API 키 미설정"

    # -----------------------------------------------------------------------
    # 사이드바 하단 상태 카드
    # -----------------------------------------------------------------------
    rail_status_icon = ft.Icon(ft.Icons.INFO, size=14)
    rail_status_label = ft.Text("", size=11, color="#CBD5E1")
    rail_footer = ft.Container(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            rail_status_icon,
                            ft.Text("Gemini AI 상태", weight=ft.FontWeight.BOLD, size=11, color=ft.Colors.WHITE),
                        ],
                        spacing=6,
                    ),
                    rail_status_label,
                ],
                spacing=4,
            ),
            bgcolor="#1E293B",
            border=ft.Border.all(1, "#334155"),
            border_radius=8,
            padding=10,
        ),
        padding=ft.Padding(left=12, top=0, right=12, bottom=16),
    )

    settings_key_badge = ft.Container(padding=12, border_radius=8)
    input_key_badges: dict[str, ft.Container] = {
        bid: ft.Container(padding=ft.Padding(left=12, top=6, right=12, bottom=6), border_radius=8)
        for bid in forms.FORM_ORDER
    }

    # -----------------------------------------------------------------------
    # 업무별 화면(문서함 / 입력 / 값확인) 팩토리
    # -----------------------------------------------------------------------
    transcript_fields: dict[str, ft.TextField] = {}
    refresh_funcs: dict[str, Any] = {}

    def build_business_views(bid: str, form: forms.FormDefinition):
        fields = form.fields
        w = widgets[bid]
        template_path = get_resource_path(form.template_filename)

        is_counsel = (bid == "counsel")
        badge_text = "활동지원 수요조사카드" if is_counsel else "중점사례 회의록"
        badge_color = "#2563EB" if is_counsel else "#7C3AED"
        badge_bg = "#EFF6FF" if is_counsel else "#F5F3FF"
        badge_border = "#BFDBFE" if is_counsel else "#DDD6FE"

        def make_category_badge() -> ft.Container:
            return ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.ASSIGNMENT_OUTLINED if is_counsel else ft.Icons.GROUPS_OUTLINED,
                            size=13,
                            color=badge_color,
                        ),
                        ft.Text(badge_text, size=11, weight=ft.FontWeight.BOLD, color=badge_color),
                    ],
                    spacing=5,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=badge_bg,
                border=ft.Border.all(1, badge_border),
                border_radius=6,
                padding=ft.Padding(left=8, top=3, right=8, bottom=3),
            )

        # ---------------- 문서함 화면 ----------------
        docs_column = ft.Column(spacing=8)

        def refresh_docs() -> None:
            docs = document_store.list_documents(bid)
            if not docs:
                docs_column.controls = [
                    ft.Container(
                        content=ft.Text(
                            "아직 저장된 문서가 없습니다. [+ 새 문서]를 눌러 작성을 시작하세요.",
                            size=13,
                            color="#64748B",
                        ),
                        padding=20,
                    )
                ]
            else:
                docs_column.controls = [doc_row(d) for d in docs]
            page.update()

        def doc_row(d: dict[str, Any]) -> ft.Container:
            def on_open(_: ft.ControlEvent) -> None:
                loaded = document_store.load_document(bid, d["doc_id"])
                if not loaded:
                    snack("문서를 불러오지 못했습니다.")
                    return
                state["doc_id"][bid] = d["doc_id"]
                set_widgets_from_extracted(w, fields, loaded.get("values") or {})
                # 작성 방식이 저장되지 않은 이전 문서는 현재 기본값(마지막 선택값)을 쓴다.
                state["field_styles"][bid] = forms.resolve_field_styles(
                    form, loaded.get("field_styles") or state["style_prefs"][bid]
                )
                sync_style_buttons()
                transcript_fields[bid].value = loaded.get("transcript") or ""
                snack(f"문서를 불러왔습니다: {d['key'] or d['doc_id']}")
                navigate_to(f"{bid}_review")

            def on_delete(_: ft.ControlEvent) -> None:
                document_store.delete_document(bid, d["doc_id"])
                if state["doc_id"][bid] == d["doc_id"]:
                    state["doc_id"][bid] = None
                snack(f"문서를 삭제했습니다: {d['key'] or d['doc_id']}")
                refresh_docs()

            return ft.Container(
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(d["key"] or "(이름 미입력)", weight=ft.FontWeight.W_600, size=14, color="#0F172A"),
                                ft.Text(
                                    f"문서번호 {d['doc_id']}  ·  수정 {_format_dt(d['updated_at'])}",
                                    size=12,
                                    color="#64748B",
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.TextButton("열기", icon=ft.Icons.FOLDER_OPEN, on_click=on_open),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color="#DC2626", on_click=on_delete, tooltip="삭제"),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.Padding(left=16, top=10, right=10, bottom=10),
                bgcolor="#FFFFFF",
                border=ft.Border.all(1, "#E2E8F0"),
                border_radius=10,
            )

        def on_new_doc(_: ft.ControlEvent) -> None:
            state["doc_id"][bid] = None
            set_widgets_from_extracted(w, fields, {})
            transcript_fields[bid].value = ""
            state["field_styles"][bid] = dict(state["style_prefs"][bid])
            sync_style_buttons()
            input_status_text.value = ""
            result_summary_box.visible = False
            snack(f"{form.name} 새 문서를 시작합니다.")
            navigate_to(f"{bid}_input")

        view_docs = ft.Column(
            controls=[
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Row([make_category_badge()], spacing=6),
                                ft.Text(f"{form.name} — 문서함", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
                                ft.Text("저장된 문서를 불러와 수정하거나, 새 문서를 작성하세요.", size=13, color="#475569"),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        ft.FilledButton(
                            "+ 새 문서",
                            icon=ft.Icons.ADD,
                            style=ft.ButtonStyle(
                                bgcolor="#2563EB", color=ft.Colors.WHITE, shape=ft.RoundedRectangleBorder(radius=8)
                            ),
                            on_click=on_new_doc,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                docs_column,
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        refresh_funcs[bid] = refresh_docs

        # ---------------- 입력(AI 분석) 화면 ----------------
        transcript_field = ft.TextField(
            label=f"{form.name} — 입력 내용 또는 메모",
            multiline=True,
            min_lines=10,
            max_lines=16,
            hint_text=form.example_transcript,
            text_size=state["font_size"],
        )
        transcript_fields[bid] = transcript_field

        # 항목별 작성 방식(서술형/개조식) 선택 — style_selectable 필드가 있는 업무만 표시
        style_fields = forms.style_selectable_fields(form)
        style_buttons: dict[str, ft.SegmentedButton] = {}

        def sync_style_buttons() -> None:
            for fid, btn in style_buttons.items():
                btn.selected = [state["field_styles"][bid][fid]]

        def on_style_change(e: ft.ControlEvent) -> None:
            selected = list(e.control.selected or [])
            if not selected:
                return
            fid = e.control.data
            state["field_styles"][bid][fid] = selected[0]
            state["style_prefs"][bid][fid] = selected[0]
            save_config(config_snapshot())

        for f in style_fields:
            style_buttons[f["id"]] = ft.SegmentedButton(
                selected=[state["field_styles"][bid][f["id"]]],
                allow_multiple_selection=False,
                data=f["id"],
                on_change=on_style_change,
                segments=[
                    ft.Segment(value=STYLE_NARRATIVE, label=ft.Text(WRITING_STYLES[STYLE_NARRATIVE]["label"])),
                    ft.Segment(value=STYLE_BULLET, label=ft.Text(WRITING_STYLES[STYLE_BULLET]["label"])),
                ],
            )

        style_panel_controls: list[ft.Control] = []
        if style_fields:
            style_panel_controls = [
                ft.Divider(height=1, color="#E2E8F0"),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.FORMAT_LIST_NUMBERED, size=18, color="#7C3AED"),
                        ft.Text("항목별 작성 방식", weight=ft.FontWeight.BOLD, size=14, color="#0F172A"),
                    ],
                    spacing=6,
                ),
                ft.Text(
                    "AI가 각 항목을 서술형(문단) 또는 개조식(번호 목록)으로 작성합니다. "
                    "선택은 다음 작성 때도 유지되고, 문서를 저장하면 문서별로 함께 저장됩니다.",
                    size=12,
                    color="#64748B",
                ),
                ft.ResponsiveRow(
                    [
                        ft.Row(
                            [
                                ft.Text(f["label"].split("(")[0].strip(), size=13, color="#334155", expand=True),
                                style_buttons[f["id"]],
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            col={"xs": 12, "md": 6},
                        )
                        for f in style_fields
                    ],
                    spacing=24,
                    run_spacing=8,
                ),
            ]

        input_status_text = ft.Text("", size=13)
        result_summary_box = ft.Container(visible=False)

        btn_analyze = ft.FilledButton(
            "AI로 분석하기",
            icon=ft.Icons.AUTO_AWESOME,
            style=ft.ButtonStyle(bgcolor="#2563EB", color=ft.Colors.WHITE, shape=ft.RoundedRectangleBorder(radius=8)),
        )
        btn_reset = ft.OutlinedButton(
            "입력값 초기화", icon=ft.Icons.REFRESH, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))
        )
        analyzing_progress_bar = ft.ProgressBar(color="#2563EB", bgcolor="#DBEAFE", visible=False)
        analyzing_banner = ft.Container(
            content=ft.Row(
                [
                    ft.ProgressRing(width=26, height=26, color="#2563EB", stroke_width=3),
                    ft.Column(
                        [
                            ft.Text(
                                "Gemini AI가 입력 내용을 분석하고 있습니다...",
                                weight=ft.FontWeight.BOLD,
                                size=14,
                                color="#1E3A8A",
                            ),
                            ft.Text(
                                f"{form.name} 서식의 각 항목을 추출 중입니다. 잠시만 기다려주세요.",
                                size=12,
                                color="#2563EB",
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#EFF6FF",
            border=ft.Border.all(1, "#BFDBFE"),
            border_radius=10,
            padding=16,
            visible=False,
        )

        def on_reset_input(_: ft.ControlEvent) -> None:
            transcript_field.value = ""
            set_widgets_from_extracted(w, fields, {})
            input_status_text.value = "입력값이 초기화되었습니다."
            input_status_text.color = "#64748B"
            result_summary_box.visible = False
            snack("입력창과 추출 데이터가 초기화되었습니다.")
            page.update()

        async def on_analyze(_: ft.ControlEvent) -> None:
            key = (state["api_key"] or "").strip()
            transcript = (transcript_field.value or "").strip()
            if not key:
                input_status_text.value = "API 키가 설정되지 않았습니다. [설정] 메뉴에서 먼저 등록해주세요."
                input_status_text.color = "#DC2626"
                snack("API 키가 설정되지 않았습니다. [설정] 메뉴에서 등록해주세요.")
                page.update()
                return
            if not transcript:
                input_status_text.value = "내용을 먼저 입력해주세요."
                input_status_text.color = "#DC2626"
                snack("내용을 먼저 입력해주세요.")
                page.update()
                return

            btn_analyze.disabled = True
            btn_analyze.icon = None
            btn_analyze.content = ft.Row(
                [
                    ft.ProgressRing(width=16, height=16, stroke_width=2.5, color=ft.Colors.WHITE),
                    ft.Text("AI 분석 진행 중...", color=ft.Colors.WHITE, size=14, weight=ft.FontWeight.W_600),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            )
            btn_reset.disabled = True
            analyzing_banner.visible = True
            analyzing_progress_bar.visible = True
            input_status_text.value = ""
            result_summary_box.visible = False
            page.update()

            await asyncio.sleep(0.05)

            try:
                model = state.get("model") or DEFAULT_MODEL
                result = await asyncio.to_thread(
                    extract_fields,
                    transcript,
                    key,
                    form.system_prompt,
                    fields,
                    model,
                    field_styles=dict(state["field_styles"][bid]),
                )
                extracted = {k: v for k, v in result.items() if v}
                set_widgets_from_extracted(w, fields, extracted)

                count = len(extracted)
                input_status_text.value = f"분석 완료! 총 {count}개의 항목이 성공적으로 추출되었습니다."
                input_status_text.color = "#059669"

                result_summary_box.content = ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.CHECK_CIRCLE, color="#2563EB", size=24),
                            ft.Column(
                                [
                                    ft.Text(f"추출 완료된 서식 항목: 총 {count}개", weight=ft.FontWeight.BOLD, size=14, color="#0F172A"),
                                    ft.Text("값 확인 메뉴로 이동하여 추출된 값을 검토하고 저장/HWPX 생성을 진행하세요.", size=12, color="#475569"),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.FilledButton(
                                "서식 확인 및 HWPX 생성으로 이동 →",
                                icon=ft.Icons.ARROW_FORWARD,
                                style=ft.ButtonStyle(
                                    bgcolor="#2563EB", color=ft.Colors.WHITE, shape=ft.RoundedRectangleBorder(radius=8)
                                ),
                                on_click=lambda _: navigate_to(f"{bid}_review"),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    bgcolor="#EFF6FF",
                    border=ft.Border.all(1, "#BFDBFE"),
                    border_radius=12,
                    padding=16,
                )
                result_summary_box.visible = True
                snack(f"AI 분석 완료 ({count}개 항목 추출). 서식 확인 화면으로 이동할 수 있습니다.")
            except Exception as exc:
                input_status_text.value = f"분석 중 오류가 발생했습니다: {exc}"
                input_status_text.color = "#DC2626"
                result_summary_box.visible = False
                snack(f"분석 중 오류 발생: {exc}")
            finally:
                btn_analyze.disabled = False
                btn_analyze.icon = ft.Icons.AUTO_AWESOME
                btn_analyze.content = "AI로 분석하기"
                btn_reset.disabled = False
                analyzing_banner.visible = False
                analyzing_progress_bar.visible = False
                page.update()

        btn_analyze.on_click = on_analyze
        btn_reset.on_click = on_reset_input

        view_input = ft.Column(
            controls=[
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Row([make_category_badge()], spacing=6),
                                ft.Text(f"{form.name} — 입력 및 AI 분석", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
                                ft.Text(
                                    "내용을 입력하면 Gemini AI가 서식의 각 항목을 자동으로 추출합니다. "
                                    "AI 분석 없이 다음 화면에서 바로 값을 입력해도 됩니다.",
                                    size=13,
                                    color="#475569",
                                ),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        input_key_badges[bid],
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                transcript_field,
                                *style_panel_controls,
                                ft.Row([btn_analyze, btn_reset], spacing=10),
                                analyzing_progress_bar,
                                analyzing_banner,
                            ],
                            spacing=12,
                            # 입력창이 기본 폭으로 좁게 표시되지 않도록 카드 폭 전체로 늘린다.
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        padding=20,
                        bgcolor="#FFFFFF",
                        border=ft.Border.all(1, "#E2E8F0"),
                        border_radius=12,
                    ),
                    elevation=0,
                ),
                input_status_text,
                result_summary_box,
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # ---------------- 값 확인 및 HWPX 생성 화면 ----------------
        review_status_text = ft.Text("", size=13)
        review_busy = ft.ProgressRing(visible=False, width=20, height=20, color="#2563EB")

        def current_key_value() -> str:
            kf = w.get(form.key_field_id)
            if isinstance(kf, ft.TextField):
                return (kf.value or "").strip()
            return ""

        def on_save_draft(_: ft.ControlEvent) -> None:
            values = collect_values(w, fields)
            new_id = document_store.save_document(
                bid, state["doc_id"][bid], current_key_value(), values, transcript_field.value or "",
                field_styles=state["field_styles"][bid],
            )
            state["doc_id"][bid] = new_id
            refresh_docs()
            review_status_text.value = f"임시저장 완료 (문서번호 {new_id})"
            review_status_text.color = "#059669"
            snack(f"문서함에 저장되었습니다: {new_id}")
            page.update()

        def on_generate(_: ft.ControlEvent) -> None:
            if not Path(template_path).exists():
                review_status_text.value = f"템플릿 서식 파일({template_path})을 찾을 수 없습니다."
                review_status_text.color = "#DC2626"
                page.update()
                return
            review_busy.visible = True
            review_status_text.value = "HWPX 문서를 생성하고 있습니다..."
            review_status_text.color = "#2563EB"
            page.update()
            try:
                values = collect_values(w, fields)
                # 내보내기 직전에 문서함에도 자동 저장해 작업 내용이 유실되지 않게 한다.
                new_id = document_store.save_document(
                    bid, state["doc_id"][bid], current_key_value(), values, transcript_field.value or "",
                    field_styles=state["field_styles"][bid],
                )
                state["doc_id"][bid] = new_id
                refresh_docs()
                data = generate_hwpx_bytes(
                    template_path, values, fields, form.custom_handlers, form.narrow_field_ratio
                )
                review_status_text.value = f"HWPX 문서가 생성되었습니다(문서번호 {new_id}). 저장할 위치를 선택하세요."
                review_status_text.color = "#059669"
                page.update()
                page.run_task(do_save_file, data, form.result_filename, review_status_text)
            except Exception as exc:
                review_status_text.value = f"문서 생성 중 오류가 발생했습니다: {exc}"
                review_status_text.color = "#DC2626"
                page.update()
            finally:
                review_busy.visible = False
                page.update()

        table_indices = sorted(form.page_labels.keys())
        # 탭 영역은 남은 높이를 모두 채우고 각 탭 안에서만 스크롤한다. 고정 높이 +
        # 바깥 화면 스크롤 조합이면 마우스 휠이 안쪽 스크롤에만 먹혀 하단 버튼 바까지
        # 내려갈 수 없으므로, 바깥(view_review)은 스크롤하지 않고 버튼 바를 하단에 고정한다.
        tabs_control = ft.Tabs(
            length=len(table_indices),
            expand=True,
            content=ft.Column(
                [
                    ft.TabBar(
                        tabs=[ft.Tab(label=form.page_labels[idx]) for idx in table_indices],
                        indicator_color="#2563EB",
                        label_color="#2563EB",
                        unselected_label_color="#64748B",
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            ft.Column(
                                [
                                    ft.Container(height=10),
                                    *widgets_for_table(w, fields, form.review_sections, idx),
                                    ft.Container(height=24),
                                ],
                                spacing=0,
                                scroll=ft.ScrollMode.AUTO,
                            )
                            for idx in table_indices
                        ],
                    ),
                ],
                expand=True,
            ),
        )

        review_status_pill = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color="#059669", size=15),
                    ft.Text("서식 데이터 작성 중", size=12, color="#065F46", weight=ft.FontWeight.W_500),
                ],
                spacing=6,
            ),
            bgcolor="#ECFDF5",
            border=ft.Border.all(1, "#A7F3D0"),
            border_radius=999,
            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
        )

        action_buttons = ft.Row(
            [
                ft.OutlinedButton(
                    "임시저장",
                    icon=ft.Icons.SAVE_OUTLINED,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                    on_click=on_save_draft,
                ),
                ft.FilledButton(
                    "HWPX 파일 생성 및 저장",
                    icon=ft.Icons.SAVE,
                    style=ft.ButtonStyle(bgcolor="#2563EB", color=ft.Colors.WHITE, shape=ft.RoundedRectangleBorder(radius=8)),
                    on_click=on_generate,
                ),
                review_busy,
            ],
            spacing=10,
        )

        view_review = ft.Column(
            controls=[
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Row([make_category_badge()], spacing=6),
                                ft.Row(
                                    [
                                        ft.Text(f"{form.name} — 서식 확인 및 HWPX 생성", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
                                        review_status_pill,
                                    ],
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text(
                                    "AI가 채운 값(또는 직접 입력한 값)이 정확한지 확인하고 필요 시 수정하세요. "
                                    "비워둔 항목은 서식 원본이 그대로 유지됩니다.",
                                    size=13,
                                    color="#475569",
                                ),
                            ],
                            expand=True,
                            spacing=4,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                review_status_text,
                tabs_control,
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("검토가 완료되면 저장하거나 HWPX 문서로 내보내세요.", size=12, color="#64748B"),
                            action_buttons,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=ft.Padding(left=16, top=12, right=16, bottom=12),
                    bgcolor="#FFFFFF",
                    border=ft.Border.all(1, "#E2E8F0"),
                    border_radius=12,
                ),
            ],
            spacing=10,
            expand=True,
        )

        return view_docs, view_input, view_review

    per_business: dict[str, tuple[ft.Column, ft.Column, ft.Column]] = {
        bid: build_business_views(bid, form) for bid, form in forms.FORMS.items()
    }

    # -----------------------------------------------------------------------
    # 3. 설정 화면 컨트롤 (업무 공용)
    # -----------------------------------------------------------------------
    api_key_field = ft.TextField(
        label="Gemini API Key",
        password=True,
        can_reveal_password=True,
        value=state["api_key"],
        helper="Google AI Studio(aistudio.google.com)에서 발급받은 API 키를 입력하세요.",
        text_size=state["font_size"],
    )
    model_field = ft.TextField(
        label="Gemini 모델명",
        value=state["model"],
        helper="기본 권장 모델: gemini-2.5-flash (빠른 응답 속도 및 높은 정밀도)",
        text_size=state["font_size"],
    )
    api_test_result = ft.Text("", size=13)
    settings_busy = ft.ProgressRing(visible=False, width=18, height=18, color="#2563EB")

    def on_test_api(_: ft.ControlEvent) -> None:
        key = (api_key_field.value or "").strip()
        if not key:
            api_test_result.value = "API 키를 먼저 입력하세요."
            api_test_result.color = "#DC2626"
            page.update()
            return
        settings_busy.visible = True
        api_test_result.value = "Gemini API 연결 확인 중..."
        api_test_result.color = "#2563EB"
        page.update()

        model = (model_field.value or "").strip() or DEFAULT_MODEL
        ok, msg = test_api_key(key, model)
        settings_busy.visible = False
        api_test_result.value = msg
        api_test_result.color = "#059669" if ok else "#DC2626"
        page.update()

    def on_save_settings(_: ft.ControlEvent) -> None:
        key = (api_key_field.value or "").strip()
        model = (model_field.value or "").strip() or DEFAULT_MODEL

        state["api_key"] = key
        state["model"] = model

        saved = save_config(config_snapshot())

        update_key_badges()

        if saved:
            snack("설정 및 API 키가 성공적으로 저장되었습니다.")
        else:
            snack("설정 저장에 실패했습니다. 파일 권한을 확인해주세요.")
        page.update()

    def on_font_size_change(e: ft.ControlEvent) -> None:
        selected_set = e.control.selected
        if not selected_set:
            return
        new_size = int(list(selected_set)[0])
        state["font_size"] = new_size

        for w in widgets.values():
            apply_font_size(w, new_size)
        for tf in transcript_fields.values():
            tf.text_size = new_size
        api_key_field.text_size = new_size
        model_field.text_size = new_size

        save_config(config_snapshot())
        snack(f"글자 크기가 {new_size}px로 변경되었습니다.")
        page.update()

    font_segmented_button = ft.SegmentedButton(
        selected=[str(state["font_size"])],
        allow_multiple_selection=False,
        on_change=on_font_size_change,
        segments=[
            ft.Segment(value="13", label=ft.Text("작게 (13px)")),
            ft.Segment(value="15", label=ft.Text("보통 (15px)")),
            ft.Segment(value="17", label=ft.Text("크게 (17px)")),
            ft.Segment(value="19", label=ft.Text("아주 크게 (19px)")),
        ],
    )

    view_settings = ft.Column(
        controls=[
            ft.Row(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=13, color="#64748B"),
                                ft.Text("시스템 환경설정", size=11, weight=ft.FontWeight.BOLD, color="#475569"),
                            ],
                            spacing=5,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor="#F1F5F9",
                        border=ft.Border.all(1, "#CBD5E1"),
                        border_radius=6,
                        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
                    ),
                ],
                spacing=6,
            ),
            ft.Text("환경설정 및 API 키 관리", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
            ft.Text("Gemini AI API 키 및 화면 폰트 크기 등 앱 동작 환경을 설정합니다. (모든 서식 공통 적용)", size=13, color="#475569"),
            ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.KEY_ROUNDED, color="#2563EB", size=20),
                                    ft.Text("Gemini API 설정", weight=ft.FontWeight.BOLD, size=15, color="#0F172A"),
                                ],
                                spacing=8,
                            ),
                            settings_key_badge,
                            api_key_field,
                            model_field,
                            ft.Row(
                                [
                                    ft.FilledButton(
                                        "API 키 저장",
                                        icon=ft.Icons.SAVE,
                                        style=ft.ButtonStyle(
                                            bgcolor="#2563EB", color=ft.Colors.WHITE, shape=ft.RoundedRectangleBorder(radius=8)
                                        ),
                                        on_click=on_save_settings,
                                    ),
                                    ft.OutlinedButton(
                                        "API 키 테스트",
                                        icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                                        on_click=on_test_api,
                                    ),
                                    settings_busy,
                                ],
                                spacing=10,
                            ),
                            api_test_result,
                        ],
                        spacing=12,
                    ),
                    padding=20,
                    bgcolor="#FFFFFF",
                    border=ft.Border.all(1, "#E2E8F0"),
                    border_radius=12,
                ),
                elevation=0,
            ),
            ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.FORMAT_SIZE_ROUNDED, color="#2563EB", size=20),
                                    ft.Text("화면 폰트 크기 설정", weight=ft.FontWeight.BOLD, size=15, color="#0F172A"),
                                ],
                                spacing=8,
                            ),
                            ft.Text("입력창 및 서식 필드 항목들의 글자 크기를 조절합니다.", size=12, color="#64748B"),
                            font_segmented_button,
                        ],
                        spacing=12,
                    ),
                    padding=20,
                    bgcolor="#FFFFFF",
                    border=ft.Border.all(1, "#E2E8F0"),
                    border_radius=12,
                ),
                elevation=0,
            ),
            ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.SECURITY_ROUNDED, color="#0284C7", size=20),
                                    ft.Text("개인정보 보호 및 보안 안내", weight=ft.FontWeight.BOLD, size=14, color="#0F172A"),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                "• API 키는 사용자 로컬 PC의 홈 디렉터리(~/.gangseo_counsel_config.json)에만 안전하게 보관됩니다.\n"
                                "• 저장된 문서는 ~/.gangseo_counsel_docs/ 폴더에 업무별로 보관되며, 외부로 전송되지 않습니다.\n"
                                "• 본 프로그램은 생년월일, 연락처, 주소 등 민감한 개인정보를 처리하므로 공용 PC 사용 시 유의하시기 바랍니다.",
                                size=12,
                                color="#475569",
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=20,
                    bgcolor="#FFFFFF",
                    border=ft.Border.all(1, "#E2E8F0"),
                    border_radius=12,
                ),
                elevation=0,
            ),
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # -----------------------------------------------------------------------
    # 좌측 사이드바 및 통합 라우팅 시스템 (Stitch: Civic Trust Theme)
    # -----------------------------------------------------------------------
    nav_buttons: dict[str, ft.Container] = {}

    def create_nav_item(route_key: str, label: str, icon_name: str) -> ft.Container:
        icon_ctrl = ft.Icon(icon_name, size=17, color="#94A3B8")
        label_ctrl = ft.Text(label, size=13, color="#94A3B8")

        def handle_click(_: ft.ControlEvent) -> None:
            navigate_to(route_key)

        def handle_hover(e: ft.HoverEvent) -> None:
            if state["current_route"] != route_key:
                btn_container.bgcolor = "#1E293B" if e.data == "true" else None
                try:
                    btn_container.update()
                except Exception:
                    pass

        btn_container = ft.Container(
            content=ft.Row(
                [
                    icon_ctrl,
                    label_ctrl,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=16, top=8, right=10, bottom=8),
            border_radius=8,
            ink=True,
            on_click=handle_click,
            on_hover=handle_hover,
        )
        btn_container.data = {"icon": icon_ctrl, "label": label_ctrl}
        nav_buttons[route_key] = btn_container
        return btn_container

    def nav_major_header(title: str, icon_name: str, badge_color: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(icon_name, size=16, color=ft.Colors.WHITE),
                        bgcolor=badge_color,
                        border_radius=6,
                        padding=5,
                    ),
                    ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=6, top=14, right=6, bottom=6),
        )

    def update_nav_styles(active_route: str, update_controls: bool = True) -> None:
        for key, btn in nav_buttons.items():
            is_active = (key == active_route)
            icon_ctrl: ft.Icon = btn.data["icon"]
            label_ctrl: ft.Text = btn.data["label"]
            if is_active:
                btn.bgcolor = "#2563EB"
                icon_ctrl.color = ft.Colors.WHITE
                label_ctrl.color = ft.Colors.WHITE
                label_ctrl.weight = ft.FontWeight.W_600
            else:
                btn.bgcolor = None
                icon_ctrl.color = "#94A3B8"
                label_ctrl.color = "#94A3B8"
                label_ctrl.weight = ft.FontWeight.NORMAL
            if update_controls:
                try:
                    btn.update()
                except Exception:
                    pass

    views_by_route: dict[str, ft.Control] = {
        "counsel_input": per_business["counsel"][1],
        "counsel_review": per_business["counsel"][2],
        "counsel_docs": per_business["counsel"][0],
        "meeting_input": per_business["meeting"][1],
        "meeting_review": per_business["meeting"][2],
        "meeting_docs": per_business["meeting"][0],
        "settings": view_settings,
    }

    def navigate_to(route_key: str, update_page: bool = True) -> None:
        state["current_route"] = route_key
        for r_key, view in views_by_route.items():
            view.visible = (r_key == route_key)
        update_nav_styles(route_key, update_controls=update_page)
        if route_key == "counsel_docs":
            refresh_funcs["counsel"]()
        elif route_key == "meeting_docs":
            refresh_funcs["meeting"]()
        if update_page:
            try:
                page.update()
            except Exception:
                pass

    sidebar = ft.Container(
        width=270,
        bgcolor="#0F172A",
        content=ft.Column(
            [
                # App Branding Header
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, color=ft.Colors.WHITE, size=22),
                                bgcolor="#2563EB",
                                border_radius=8,
                                padding=8,
                            ),
                            ft.Column(
                                [
                                    ft.Text("강서나눔돌봄센터", weight=ft.FontWeight.BOLD, size=15, color=ft.Colors.WHITE),
                                    ft.Text("활동지원 서식 자동화", size=11, color="#94A3B8"),
                                ],
                                spacing=1,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding(left=16, top=20, right=16, bottom=16),
                ),
                ft.Divider(height=1, color="#1E293B"),
                # Navigation Menu Items (Scrollable if necessary)
                ft.Container(
                    content=ft.Column(
                        [
                            # 1. 수요조사카드 대메뉴 (15px Bold White + 아이콘 뱃지)
                            nav_major_header("수요조사카드", ft.Icons.ASSIGNMENT_ROUNDED, "#0284C7"),
                            create_nav_item("counsel_input", "상담 입력 (AI 분석)", ft.Icons.EDIT_NOTE),
                            create_nav_item("counsel_review", "서식 확인 및 HWPX", ft.Icons.CHECKLIST),
                            create_nav_item("counsel_docs", "수요조사 문서함", ft.Icons.FOLDER_OUTLINED),

                            # 섹션 구분선
                            ft.Container(
                                content=ft.Divider(height=1, color="#1E293B"),
                                padding=ft.Padding(left=6, top=10, right=6, bottom=4),
                            ),

                            # 2. 중점사례 회의록 대메뉴 (15px Bold White + 아이콘 뱃지)
                            nav_major_header("중점사례 회의록", ft.Icons.GROUPS_ROUNDED, "#7C3AED"),
                            create_nav_item("meeting_input", "회의 입력 (AI 분석)", ft.Icons.EDIT_NOTE),
                            create_nav_item("meeting_review", "서식 확인 및 HWPX", ft.Icons.CHECKLIST),
                            create_nav_item("meeting_docs", "회의록 문서함", ft.Icons.FOLDER_OUTLINED),

                            # 섹션 구분선
                            ft.Container(
                                content=ft.Divider(height=1, color="#1E293B"),
                                padding=ft.Padding(left=6, top=10, right=6, bottom=4),
                            ),

                            # 3. 시스템 설정 대메뉴 (15px Bold White + 아이콘 뱃지)
                            nav_major_header("시스템 설정", ft.Icons.SETTINGS_ROUNDED, "#475569"),
                            create_nav_item("settings", "환경설정 및 API 키", ft.Icons.TUNE_ROUNDED),
                        ],
                        spacing=3,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    expand=True,
                    padding=ft.Padding(left=10, top=10, right=10, bottom=10),
                ),
                # Pinned Footer (Gemini AI Status)
                rail_footer,
            ],
            spacing=0,
            expand=True,
        ),
    )

    # 초기 화면 활성화 설정 (컨트롤 마운트 전에는 update 호출을 생략)
    navigate_to("counsel_input", update_page=False)

    all_views: list[ft.Control] = list(views_by_route.values())

    page.add(
        ft.Row(
            [
                sidebar,
                ft.VerticalDivider(width=1, color="#E2E8F0"),
                ft.Container(
                    content=ft.Stack(all_views, expand=True),
                    expand=True,
                    padding=24,
                    bgcolor="#F8FAFC",
                ),
            ],
            expand=True,
            spacing=0,
        )
    )

    update_key_badges()
    page.update()


if __name__ == "__main__":
    ft.run(main)
