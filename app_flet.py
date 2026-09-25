"""
app_flet.py

강서구 활동지원급여 수요조사카드(counsel.hwpx) 자동 입력 Flet(데스크톱) 앱.
Google Stitch AI의 "Civic Trust Desktop" 디자인 시스템을 적용한 프리미엄 UI 버전.

주요 UI/UX:
  - Midnight Slate(#0F172A) 다크 사이드바 네비게이션
  - Soft Slate(#F8FAFC) 캔버스 및 Pure White(#FFFFFF) 카드 섹션 분할
  - 1쪽/2쪽 필드를 논리적 그룹(인적사항, 주거/환경, 건강/소통, 급여/서비스 등)으로 카드화
  - Gemini API 키 및 설정 영구 보관 (~/.gangseo_counsel_config.json)
  - 폰트 크기(13/15/17/19px) 실시간 변경 및 영구 보관
  - 상담 입력 → AI 추출 요약 → 값 확인/수정 → 즉시 HWPX 생성/저장
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import flet as ft

from field_map import FIELDS
from hwpx_engine import generate_hwpx_bytes
from llm_client import DEFAULT_MODEL, extract_fields, test_api_key


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


TEMPLATE_PATH = get_resource_path("counsel.hwpx")
EMPTY_CHOICE = "(선택 안 함)"

CONFIG_PATH = Path.home() / ".gangseo_counsel_config.json"
LOCAL_CONFIG_PATH = Path(".app_config.json")

EXAMPLE = (
    "이용자 이름은 김철수이고 생년월일은 90년생입니다. 연락처는 010-1234-5678이고요, "
    "지체장애이시고 활동지원 등급은 14구간(가형)이에요. 강서구 화곡동 빌라 3층에 "
    "혼자 살고 계시고 엘리베이터는 없어요. 비흡연자시고 애완동물은 안 키우세요."
)

_MULTILINE_HINTS = ("특이사항", "여가활동", "사회활동", "직장", "학교", "자녀", "복지서비스", "필요 사유")


def _is_multiline(label: str) -> bool:
    return any(h in label for h in _MULTILINE_HINTS)


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
# 위젯 레지스트리 및 폰트 크기 동적 조절
# ---------------------------------------------------------------------------
def build_widgets(font_size: int = 15) -> dict[str, Any]:
    """값 id -> Flet 컨트롤 매핑 생성"""
    widgets: dict[str, Any] = {}
    label_style = ft.TextStyle(size=font_size)

    for f in FIELDS:
        kind = f["kind"]
        if kind == "text":
            widgets[f["id"]] = ft.TextField(
                label=f["label"],
                multiline=_is_multiline(f["label"]),
                min_lines=3 if _is_multiline(f["label"]) else 1,
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


def set_widgets_from_extracted(widgets: dict[str, Any], extracted: dict[str, Any]) -> None:
    """AI 추출값(또는 {})을 위젯에 반영한다."""
    for f in FIELDS:
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


def collect_values(widgets: dict[str, Any]) -> dict[str, Any]:
    """위젯 상태에서 hwpx_engine.apply_fields용 flat dict를 만든다."""
    values: dict[str, Any] = {}
    for f in FIELDS:
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
def controls_for_fields(widgets: dict[str, Any], field_ids: list[str]) -> list[ft.Control]:
    """특정 필드 ID 목록에 대한 입력 컨트롤들을 순서대로 반환한다."""
    id_set = set(field_ids)
    controls: list[ft.Control] = []
    for f in FIELDS:
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


def widgets_for_table(widgets: dict[str, Any], table_idx: int) -> list[ft.Control]:
    """특정 표(0=1쪽, 1=2쪽)를 Stitch 스타일의 3대 섹션 카드로 분할 생성한다."""
    if table_idx == 0:
        t0_ids = [f["id"] for f in FIELDS if f["table"] == 0]
        card1 = create_section_card(
            "기본 인적사항",
            "성명, 생년월일, 연락처, 장애유형, 판정등급 및 주소 정보",
            controls_for_fields(widgets, t0_ids[:7]),
            ft.Icons.PERSON_OUTLINE,
        )
        card2 = create_section_card(
            "주거 및 교통환경",
            "건물형태, 방 개수, 층수/승강기, 대중교통 및 반려동물 유무",
            controls_for_fields(widgets, t0_ids[7:19]),
            ft.Icons.HOME_WORK_OUTLINED,
        )
        card3 = create_section_card(
            "신체상태 및 소통특성",
            "신체 제약사항, 인공호흡기, 의사소통 수준, 공간인지, 흡연 및 건강 특이사항",
            controls_for_fields(widgets, t0_ids[19:]),
            ft.Icons.HEALTH_AND_SAFETY_OUTLINED,
        )
        return [card1, card2, card3]
    else:
        t1_ids = [f["id"] for f in FIELDS if f["table"] == 1]
        card1 = create_section_card(
            "가구 및 사회활동",
            "동거 가구구성원, 자녀정보, 비상연락처, 직장/학교 출퇴근 및 여가활동",
            controls_for_fields(widgets, t1_ids[:13]),
            ft.Icons.PEOPLE_OUTLINE,
        )
        card2 = create_section_card(
            "지원급여 및 희망 서비스",
            "판정 인정시간, 신체/가사/사회활동 서비스 시간 및 야간급여 희망 사유",
            controls_for_fields(widgets, t1_ids[13:27]),
            ft.Icons.ACCESS_TIME_ROUNDED,
        )
        card3 = create_section_card(
            "활동지원사 조건 및 상담 총평",
            "희망 지원사 성별/연령/흡연여부 매칭조건 및 종합 면담 총평",
            controls_for_fields(widgets, t1_ids[27:]),
            ft.Icons.FACT_CHECK_OUTLINED,
        )
        return [card1, card2, card3]


# ---------------------------------------------------------------------------
# Flet Application Main (Stitch Theme)
# ---------------------------------------------------------------------------
def main(page: ft.Page) -> None:
    page.title = "강서구 활동지원 상담 기록 → HWPX 서식 자동 입력"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#F8FAFC"  # Stitch Canvas Slate

    # 1. 설정 로드
    config = load_config()
    saved_key = config.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
    saved_model = config.get("model", DEFAULT_MODEL)
    saved_font_size = int(config.get("font_size", 15))

    state: dict[str, Any] = {
        "extracted": {},
        "generated_bytes": None,
        "api_key": saved_key,
        "model": saved_model,
        "font_size": saved_font_size,
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

    # 3. 위젯 셋 생성
    widgets = build_widgets(font_size=state["font_size"])

    # 4. 파일 저장 픽커
    file_picker = ft.FilePicker()

    async def do_save_file(data: bytes) -> None:
        saved_path = await file_picker.save_file(
            dialog_title="결과 HWPX 저장",
            file_name="상담결과.hwpx",
            src_bytes=data,
        )
        if saved_path:
            review_status_text.value = f"저장 완료: {saved_path}"
            review_status_text.color = "#059669"
            snack(f"HWPX 파일이 성공적으로 저장되었습니다:\n{saved_path}")
        else:
            review_status_text.value = "저장이 취소되었습니다."
            review_status_text.color = "#64748B"
        page.update()

    # -----------------------------------------------------------------------
    # 상태 알림 뱃지 갱신 헬퍼
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

            input_key_badge.content = ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color="#10B981", size=16),
                    ft.Text("Gemini API 연동 준비 완료", color="#065F46", size=12, weight=ft.FontWeight.W_500),
                ],
                spacing=6,
            )
            input_key_badge.bgcolor = "#ECFDF5"
            input_key_badge.border = ft.Border.all(1, "#A7F3D0")

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

            input_key_badge.content = ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="#F59E0B", size=16),
                    ft.Text("API Key 미설정 — [설정] 메뉴에서 API 키를 등록해주세요.", color="#92400E", size=12),
                    ft.TextButton("설정으로 이동", on_click=lambda _: switch_nav(2)),
                ],
                spacing=6,
            )
            input_key_badge.bgcolor = "#FFFBEB"
            input_key_badge.border = ft.Border.all(1, "#FDE68A")

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
    input_key_badge = ft.Container(padding=ft.Padding(left=12, top=6, right=12, bottom=6), border_radius=8)

    # -----------------------------------------------------------------------
    # 1. 상담내역 입력 화면 컨트롤
    # -----------------------------------------------------------------------
    transcript_field = ft.TextField(
        label="상담 대화 내용 또는 메모 입력",
        multiline=True,
        min_lines=10,
        max_lines=16,
        hint_text=EXAMPLE,
        text_size=state["font_size"],
    )
    input_status_text = ft.Text("", size=13)
    result_summary_box = ft.Container(visible=False)

    btn_analyze = ft.FilledButton(
        "AI로 분석하기",
        icon=ft.Icons.AUTO_AWESOME,
        style=ft.ButtonStyle(
            bgcolor="#2563EB",
            color=ft.Colors.WHITE,
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
    )
    btn_reset = ft.OutlinedButton(
        "입력값 초기화",
        icon=ft.Icons.REFRESH,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    analyzing_progress_bar = ft.ProgressBar(
        color="#2563EB",
        bgcolor="#DBEAFE",
        visible=False,
    )

    analyzing_banner = ft.Container(
        content=ft.Row(
            [
                ft.ProgressRing(width=26, height=26, color="#2563EB", stroke_width=3),
                ft.Column(
                    [
                        ft.Text(
                            "Gemini AI가 상담 대화 내용을 분석하고 있습니다...",
                            weight=ft.FontWeight.BOLD,
                            size=14,
                            color="#1E3A8A",
                        ),
                        ft.Text(
                            "인적사항, 주거환경, 건강·의사소통, 지원급여 등 65개 서식 항목을 추출 중입니다. 잠시만 기다려주세요.",
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
        state["extracted"] = {}
        set_widgets_from_extracted(widgets, {})
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
            input_status_text.value = "상담 내용을 먼저 입력해주세요."
            input_status_text.color = "#DC2626"
            snack("상담 내용을 먼저 입력해주세요.")
            page.update()
            return

        # 동작 중임을 사용자가 즉시 인지하도록 버튼에 스피너(뱅글뱅글) 표시 및 배너 노출
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

        # UI 업데이트가 플러터 클라이언트에 즉각 전송되어 스피너가 회전할 수 있도록 이벤트 루프 양보
        await asyncio.sleep(0.05)

        try:
            model = state.get("model") or DEFAULT_MODEL
            # 백그라운드 스레드에서 Gemini API 실행 (UI 프리징 완전 방지!)
            result = await asyncio.to_thread(extract_fields, transcript, key, model)
            extracted = {k: v for k, v in result.items() if v}
            state["extracted"] = extracted
            set_widgets_from_extracted(widgets, extracted)

            count = len(extracted)
            input_status_text.value = f"분석 완료! 총 {count}개의 항목이 성공적으로 추출되었습니다."
            input_status_text.color = "#059669"

            # Stitch 스타일의 AI 결과 요약 콜아웃
            result_summary_box.content = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color="#2563EB", size=24),
                        ft.Column(
                            [
                                ft.Text(f"추출 완료된 서식 항목: 총 {count}개", weight=ft.FontWeight.BOLD, size=14, color="#0F172A"),
                                ft.Text("2번 메뉴로 이동하여 추출된 값을 검토하고 HWPX 문서를 즉시 생성하세요.", size=12, color="#475569"),
                            ],
                            expand=True,
                            spacing=2,
                        ),
                        ft.FilledButton(
                            "값 확인 및 HWPX 생성으로 이동 →",
                            icon=ft.Icons.ARROW_FORWARD,
                            style=ft.ButtonStyle(
                                bgcolor="#2563EB",
                                color=ft.Colors.WHITE,
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                            on_click=lambda _: switch_nav(1),
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
            snack(f"AI 분석 완료 ({count}개 항목 추출). 값 확인 화면으로 이동할 수 있습니다.")
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
                            ft.Text("1. 상담내역 입력 및 AI 분석", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
                            ft.Text(
                                "상담 대화 내용이나 메모를 입력하면 Gemini AI가 강서구 수요조사카드 서식의 각 항목을 자동으로 추출합니다.",
                                size=13,
                                color="#475569",
                            ),
                        ],
                        spacing=2,
                    ),
                    input_key_badge,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            transcript_field,
                            ft.Row(
                                [
                                    btn_analyze,
                                    btn_reset,
                                ],
                                spacing=10,
                            ),
                            analyzing_progress_bar,
                            analyzing_banner,
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
            input_status_text,
            result_summary_box,
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # -----------------------------------------------------------------------
    # 2. 값 확인 및 HWPX 생성 화면 컨트롤 (Stitch Section Cards)
    # -----------------------------------------------------------------------
    review_status_text = ft.Text("", size=13)
    review_busy = ft.ProgressRing(visible=False, width=20, height=20, color="#2563EB")

    def on_generate(_: ft.ControlEvent) -> None:
        if not Path(TEMPLATE_PATH).exists():
            review_status_text.value = f"템플릿 서식 파일({TEMPLATE_PATH})을 찾을 수 없습니다."
            review_status_text.color = "#DC2626"
            page.update()
            return
        review_busy.visible = True
        review_status_text.value = "HWPX 문서를 생성하고 있습니다..."
        review_status_text.color = "#2563EB"
        page.update()
        try:
            values = collect_values(widgets)
            data = generate_hwpx_bytes(TEMPLATE_PATH, values)
            state["generated_bytes"] = data
            review_status_text.value = "HWPX 문서가 생성되었습니다. 저장할 위치를 선택하세요."
            review_status_text.color = "#059669"
            page.update()
            page.run_task(do_save_file, data)
        except Exception as exc:
            review_status_text.value = f"문서 생성 중 오류가 발생했습니다: {exc}"
            review_status_text.color = "#DC2626"
            page.update()
        finally:
            review_busy.visible = False
            page.update()

    tabs_control = ft.Tabs(
        length=2,
        content=ft.Column(
            [
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="1쪽 (기본정보 / 생활환경 / 의사소통)"),
                        ft.Tab(label="2쪽 (사회활동 / 욕구 / 총평)"),
                    ],
                    indicator_color="#2563EB",
                    label_color="#2563EB",
                    unselected_label_color="#64748B",
                ),
                ft.TabBarView(
                    height=650,
                    controls=[
                        ft.Column(
                            [
                                ft.Container(height=10),
                                *widgets_for_table(widgets, 0),
                                ft.Container(height=24),
                            ],
                            spacing=0,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        ft.Column(
                            [
                                ft.Container(height=10),
                                *widgets_for_table(widgets, 1),
                                ft.Container(height=24),
                            ],
                            spacing=0,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ],
                ),
            ]
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

    view_review = ft.Column(
        controls=[
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text("2. 값 확인 및 HWPX 생성", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
                                    review_status_pill,
                                ],
                                spacing=10,
                            ),
                            ft.Text(
                                "AI가 채운 값이 정확한지 확인하고 필요 시 수정하세요. 비워둔 항목은 서식 원본이 그대로 유지됩니다.",
                                size=13,
                                color="#475569",
                            ),
                        ],
                        expand=True,
                        spacing=2,
                    ),
                    ft.FilledButton(
                        "HWPX 파일 생성 및 저장",
                        icon=ft.Icons.SAVE,
                        style=ft.ButtonStyle(
                            bgcolor="#2563EB",
                            color=ft.Colors.WHITE,
                            shape=ft.RoundedRectangleBorder(radius=8),
                        ),
                        on_click=on_generate,
                    ),
                    review_busy,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            review_status_text,
            tabs_control,
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text("검토가 완료되면 오른쪽 버튼을 눌러 HWPX 문서를 저장하세요.", size=12, color="#64748B"),
                        ft.FilledButton(
                            "HWPX 파일 생성 및 저장",
                            icon=ft.Icons.SAVE,
                            style=ft.ButtonStyle(
                                bgcolor="#2563EB",
                                color=ft.Colors.WHITE,
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                            on_click=on_generate,
                        ),
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
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # -----------------------------------------------------------------------
    # 3. 설정 화면 컨트롤
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
        font_size = state["font_size"]

        state["api_key"] = key
        state["model"] = model

        saved = save_config({
            "api_key": key,
            "model": model,
            "font_size": font_size,
        })

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

        # 위젯 및 입력 필드 글자 크기 갱신
        apply_font_size(widgets, new_size)
        transcript_field.text_size = new_size
        api_key_field.text_size = new_size
        model_field.text_size = new_size

        # 설정 파일에도 즉시 저장
        save_config({
            "api_key": state["api_key"],
            "model": state["model"],
            "font_size": new_size,
        })
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
            ft.Text("3. 설정", size=22, weight=ft.FontWeight.BOLD, color="#0F172A"),
            ft.Text("Gemini AI API 키 및 화면 폰트 크기 등 앱 동작 환경을 설정합니다.", size=13, color="#475569"),
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
                                            bgcolor="#2563EB",
                                            color=ft.Colors.WHITE,
                                            shape=ft.RoundedRectangleBorder(radius=8),
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
                            ft.Text("상담 내용 입력창 및 서식 필드 항목들의 글자 크기를 조절합니다.", size=12, color="#64748B"),
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
    # 좌측 NavigationRail 및 화면 전환 로직 (Midnight Slate Theme)
    # -----------------------------------------------------------------------
    def switch_nav(index: int) -> None:
        nav_rail.selected_index = index
        view_input.visible = (index == 0)
        view_review.visible = (index == 1)
        view_settings.visible = (index == 2)
        page.update()

    def on_nav_change(e: ft.ControlEvent) -> None:
        idx = int(e.control.selected_index)
        switch_nav(idx)

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        extended=True,
        min_extended_width=230,
        bgcolor="#0F172A",
        indicator_color="#2563EB",
        unselected_label_text_style=ft.TextStyle(color="#94A3B8", size=13),
        selected_label_text_style=ft.TextStyle(color=ft.Colors.WHITE, size=13, weight=ft.FontWeight.BOLD),
        leading=ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, color=ft.Colors.WHITE, size=20),
                        bgcolor="#2563EB",
                        border_radius=8,
                        padding=8,
                    ),
                    ft.Column(
                        [
                            ft.Text("강서구 활동지원", weight=ft.FontWeight.BOLD, size=15, color=ft.Colors.WHITE),
                            ft.Text("수요조사카드 자동화", size=11, color="#94A3B8"),
                        ],
                        spacing=1,
                    ),
                ],
                spacing=10,
            ),
            padding=ft.Padding(left=16, top=20, right=16, bottom=20),
        ),
        trailing=rail_footer,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.EDIT_NOTE,
                selected_icon=ft.Icons.EDIT_NOTE_SHARP,
                label="상담내역 입력",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.CHECKLIST,
                selected_icon=ft.Icons.CHECKLIST_RTL,
                label="값 확인 및 HWPX 생성",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label="설정",
            ),
        ],
        on_change=on_nav_change,
    )

    # 초기 화면 뷰 가시성 설정
    view_input.visible = True
    view_review.visible = False
    view_settings.visible = False

    update_key_badges()

    # 전체 화면 레이아웃 조립
    page.add(
        ft.Row(
            [
                nav_rail,
                ft.VerticalDivider(width=1, color="#E2E8F0"),
                ft.Container(
                    content=ft.Stack(
                        [
                            view_input,
                            view_review,
                            view_settings,
                        ],
                        expand=True,
                    ),
                    expand=True,
                    padding=20,
                    bgcolor="#F8FAFC",
                ),
            ],
            expand=True,
        )
    )


if __name__ == "__main__":
    ft.run(main)
