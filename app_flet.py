"""
app_flet.py

강서구 활동지원급여 수요조사카드(counsel.hwpx) 자동 입력 Flet(데스크톱) 앱.
기존 Streamlit 앱(app.py)은 그대로 두고, 동일한 파이프라인을 Flet으로 구현했다:

  상담 텍스트 입력 → Gemini 구조화 추출 → 값 확인/수정 → HWPX 생성/저장

재사용 모듈: field_map.py, hwpx_engine.py, llm_client.py (Streamlit 앱과 공유)

실행:
  flet run app_flet.py
  또는
  python app_flet.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import flet as ft

from field_map import FIELDS
from hwpx_engine import generate_hwpx_bytes
from llm_client import DEFAULT_MODEL, extract_fields, test_api_key

TEMPLATE_PATH = "counsel.hwpx"
EMPTY_CHOICE = "(선택 안 함)"

EXAMPLE = (
    "이용자 이름은 김철수이고 생년월일은 90년생입니다. 연락처는 010-1234-5678이고요, "
    "지체장애이시고 활동지원 등급은 14구간(가형)이에요. 강서구 화곡동 빌라 3층에 "
    "혼자 살고 계시고 엘리베이터는 없어요. 비흡연자시고 애완동물은 안 키우세요."
)

# 라벨에 이 단어들이 들어가는 텍스트 필드는 여러 줄 입력으로 만든다.
_MULTILINE_HINTS = ("특이사항", "여가활동", "사회활동", "직장", "학교", "자녀", "복지서비스", "필요 사유")


def _is_multiline(label: str) -> bool:
    return any(h in label for h in _MULTILINE_HINTS)


# ---------------------------------------------------------------------------
# 위젯 레지스트리 (테스트 가능한 순수 로직: page 없이도 생성/조작 가능)
# ---------------------------------------------------------------------------
#
# widgets: 값 id -> 컨트롤 매핑
# - text / custom 하위필드 / checkbox blank : 값 id -> ft.TextField
# - checkbox / checkbox_multiline          : 필드 id -> {"checks": {옵션: ft.Checkbox}}
# - checkbox_scoped 계열                    : 필드 id -> ft.Dropdown


def build_widgets() -> dict[str, Any]:
    widgets: dict[str, Any] = {}
    for f in FIELDS:
        kind = f["kind"]
        if kind == "text":
            widgets[f["id"]] = ft.TextField(
                label=f["label"],
                multiline=_is_multiline(f["label"]),
                min_lines=3 if _is_multiline(f["label"]) else 1,
            )
        elif kind == "custom":
            for sub in f["subfields"]:
                widgets[sub["id"]] = ft.TextField(label=sub["label"])
        elif kind in ("checkbox", "checkbox_multiline"):
            entry: dict[str, Any] = {
                "checks": {opt: ft.Checkbox(label=opt, value=False) for opt in f["options"]},
                "blanks": {},
            }
            for blank in f.get("blanks", []):
                entry["blanks"][blank["id"]] = ft.TextField(label=blank["label"])
            widgets[f["id"]] = entry
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            widgets[f["id"]] = ft.Dropdown(
                label=f["label"],
                options=[ft.DropdownOption(EMPTY_CHOICE)] + [ft.DropdownOption(o) for o in f["options"]],
                value=EMPTY_CHOICE,
            )
    return widgets


def set_widgets_from_extracted(widgets: dict[str, Any], extracted: dict[str, Any]) -> None:
    """AI 추출값(또는 {})을 위젯에 반영한다. Streamlit의 form_version 리셋과 동등."""
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


def widgets_for_table(widgets: dict[str, Any], table_idx: int) -> list[ft.Control]:
    """특정 표(0=1쪽, 1=2쪽)에 속한 필드들의 입력 컨트롤을 순서대로 묶는다."""
    controls: list[ft.Control] = []
    for f in FIELDS:
        if f["table"] != table_idx:
            continue
        kind = f["kind"]
        fid = f["id"]
        if kind == "text":
            controls.append(widgets[fid])
        elif kind == "custom":
            controls.append(ft.Text(f["label"], weight=ft.FontWeight.BOLD, size=13))
            for sub in f["subfields"]:
                controls.append(widgets[sub["id"]])
        elif kind in ("checkbox", "checkbox_multiline"):
            boxes = list(widgets[fid]["checks"].values())
            blanks = list(widgets[fid]["blanks"].values())
            controls.append(
                ft.Column(
                    [ft.Text(f["label"], weight=ft.FontWeight.BOLD, size=13), *boxes, *blanks],
                    spacing=2,
                )
            )
        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            controls.append(widgets[fid])
    return controls


# ---------------------------------------------------------------------------
# Flet UI
# ---------------------------------------------------------------------------


def main(page: ft.Page) -> None:
    page.title = "강서구 활동지원 상담 자동입력"
    page.scroll = ft.ScrollMode.AUTO
    page.theme_mode = ft.ThemeMode.LIGHT

    state: dict[str, Any] = {"extracted": {}, "generated_bytes": None}

    api_key_field = ft.TextField(
        label="Gemini API Key",
        password=True,
        can_reveal_password=True,
        value=os.environ.get("GEMINI_API_KEY", ""),
        helper="Google AI Studio 발급 키. 환경변수 GEMINI_API_KEY로도 설정 가능",
    )
    model_field = ft.TextField(label="모델명", value=DEFAULT_MODEL, helper="예: gemini-2.5-flash, gemini-2.5-pro")
    api_test_result = ft.Text("", size=12)
    transcript_field = ft.TextField(
        label="상담 대화 내용 또는 메모 (나중에는 이 부분이 음성 STT 결과로 대체됩니다)",
        multiline=True,
        min_lines=8,
        max_lines=12,
        hint_text=EXAMPLE,
    )
    status_text = ft.Text("", size=13)
    busy = ft.ProgressRing(visible=False, width=20, height=20)

    widgets = build_widgets()

    snack_text = ft.Text("")
    snack_bar = ft.SnackBar(content=snack_text)
    page.overlay.append(snack_bar)

    def snack(msg: str) -> None:
        snack_text.value = msg
        snack_bar.open = True
        page.update()

    # -- 파일 저장 픽커 ------------------------------------------------------
    # 주의: flet 1.x의 FilePicker는 Service라서 생성만으로 현재 페이지에 자동
    # 등록된다. page.overlay에 넣으면 컨트롤로 전송되어 클라이언트에서
    # "Unknown control: FilePicker" 오류가 난다.
    file_picker = ft.FilePicker()

    async def do_save_file(data: bytes) -> None:
        saved_path = await file_picker.save_file(
            dialog_title="결과 HWPX 저장",
            file_name="상담결과.hwpx",
            src_bytes=data,
        )
        if saved_path:
            status_text.value = f"저장 완료: {saved_path}"
            snack("HWPX 파일이 저장되었습니다.")
        else:
            status_text.value = "저장이 취소되었습니다."
        page.update()

    # -- 이벤트 핸들러 --------------------------------------------------------
    def on_test_api(_: ft.ControlEvent) -> None:
        key = (api_key_field.value or "").strip()
        if not key:
            api_test_result.value = "API 키를 먼저 입력하세요."
            page.update()
            return
        busy.visible = True
        api_test_result.value = "확인 중..."
        page.update()
        ok, msg = test_api_key(key, (model_field.value or "").strip() or DEFAULT_MODEL)
        busy.visible = False
        api_test_result.value = msg
        api_test_result.color = (
            ft.Colors.GREEN_700 if ok else ft.Colors.RED_700
        )
        page.update()

    def on_analyze(_: ft.ControlEvent) -> None:
        key = (api_key_field.value or "").strip()
        transcript = (transcript_field.value or "").strip()
        if not (key and transcript):
            status_text.value = "API 키와 상담 내용을 모두 입력하세요."
            page.update()
            return
        busy.visible = True
        status_text.value = "Gemini로 상담 내용을 분석하는 중..."
        page.update()
        try:
            result = extract_fields(transcript, key, (model_field.value or "").strip() or DEFAULT_MODEL)
            state["extracted"] = {k: v for k, v in result.items() if v}
            set_widgets_from_extracted(widgets, state["extracted"])
            status_text.value = "분석 완료. 아래에서 값을 확인하고 필요하면 수정한 뒤 문서를 생성하세요."
        except Exception as exc:  # noqa: BLE001 - 원인을 화면에 그대로 표시
            status_text.value = f"분석 중 오류가 발생했습니다: {exc}"
        finally:
            busy.visible = False
            page.update()

    def on_reset(_: ft.ControlEvent) -> None:
        state["extracted"] = {}
        transcript_field.value = ""
        set_widgets_from_extracted(widgets, {})
        status_text.value = "입력값이 초기화되었습니다."
        page.update()

    def on_generate(_: ft.ControlEvent) -> None:
        if not Path(TEMPLATE_PATH).exists():
            status_text.value = f"템플릿 파일을 찾을 수 없습니다: {TEMPLATE_PATH}"
            page.update()
            return
        busy.visible = True
        status_text.value = "문서를 생성하는 중..."
        page.update()
        try:
            data = generate_hwpx_bytes(TEMPLATE_PATH, collect_values(widgets))
            state["generated_bytes"] = data
            status_text.value = "생성 완료. 저장 위치를 선택하세요."
            page.update()
            page.run_task(do_save_file, data)
        except Exception as exc:  # noqa: BLE001 - 원인을 화면에 그대로 표시
            status_text.value = f"문서 생성 중 오류가 발생했습니다: {exc}"
            page.update()
        finally:
            busy.visible = False
            page.update()

    # -- 레이아웃 --------------------------------------------------------------
    settings_card = ft.Card(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text("Gemini API 설정", weight=ft.FontWeight.BOLD, size=15),
                    api_key_field,
                    model_field,
                    ft.FilledButton("API 키 테스트", on_click=on_test_api),
                    api_test_result,
                    ft.Divider(),
                    ft.Text(
                        "⚠️ 생년월일·연락처·주소 등 민감한 개인정보를 다룹니다. "
                        "공개 배포 시 접근 제한과 세션 종료 후 파일 미보관 정책을 적용하세요.",
                        size=11,
                        color=ft.Colors.GREY_700,
                    ),
                ],
                spacing=8,
            ),
            padding=16,
        )
    )

    body = ft.Column(
        [
            ft.Text("활동지원급여 수요조사카드 자동입력 — Flet 버전", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(
                "상담 내용을 텍스트로 입력해 파이프라인을 검증하는 1단계 앱입니다. "
                "이 단계가 검증되면 '상담 내용 입력'란만 오디오 업로드 → STT 결과로 교체 예정이며, "
                "이후 분석/문서 생성 로직은 그대로 재사용됩니다.",
                size=12,
                color=ft.Colors.GREY_700,
            ),
            settings_card,
            ft.Divider(),
            ft.Text("1. 상담 내용 입력", weight=ft.FontWeight.BOLD, size=16),
            transcript_field,
            ft.Row(
                [
                    ft.FilledButton("AI로 분석하기", on_click=on_analyze),
                    ft.OutlinedButton("입력값 초기화", on_click=on_reset),
                    busy,
                ],
                spacing=10,
            ),
            ft.Divider(),
            ft.Text("2. 값 확인 및 수정", weight=ft.FontWeight.BOLD, size=16),
            ft.Text("AI가 채운 값이 정확한지 확인하고, 필요하면 직접 고치세요. 비워두면 서식 원본이 유지됩니다.", size=12),
            ft.Tabs(
                length=2,
                content=ft.Column(
                    [
                        ft.TabBar(tabs=[ft.Tab(label="1쪽"), ft.Tab(label="2쪽")]),
                        ft.TabBarView(
                            controls=[
                                ft.Column(widgets_for_table(widgets, 0), spacing=8, scroll=ft.ScrollMode.AUTO),
                                ft.Column(widgets_for_table(widgets, 1), spacing=8, scroll=ft.ScrollMode.AUTO),
                            ],
                        ),
                    ]
                ),
            ),
            ft.Divider(),
            ft.Text("3. HWPX 파일 생성", weight=ft.FontWeight.BOLD, size=16),
            ft.FilledButton("HWPX 생성 및 저장", on_click=on_generate),
            status_text,
        ],
        spacing=10,
        expand=True,
    )

    page.add(ft.Container(content=body, padding=16, expand=True))


if __name__ == "__main__":
    ft.run(main)
