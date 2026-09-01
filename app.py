"""
app.py

강서구 활동지원급여 수요조사카드(counsel.hwpx) 자동 입력 Streamlit 앱.

1단계(현재): 녹음 파일 대신 상담 내용을 텍스트로 입력하면, Gemini API가 이를
분석해 서식 필드에 맞는 값으로 구조화하고, 그 값으로 counsel.hwpx를 채운
결과 파일을 생성/다운로드한다.

2단계(추후): "상담 내용 입력" 부분을 오디오 업로드 + STT 결과로 교체하면 되고,
그 이후(LLM 구조화 -> HWPX 채우기) 로직은 그대로 재사용한다.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from field_map import FIELDS
from hwpx_engine import generate_hwpx_bytes
from llm_client import DEFAULT_MODEL, extract_fields, test_api_key

TEMPLATE_PATH = "counsel.hwpx"

st.set_page_config(page_title="강서구 활동지원 상담 자동입력", layout="wide")

if "extracted" not in st.session_state:
    st.session_state["extracted"] = {}
if "form_version" not in st.session_state:
    st.session_state["form_version"] = 0
if "gemini_api_key" not in st.session_state:
    try:
        st.session_state["gemini_api_key"] = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        st.session_state["gemini_api_key"] = ""

st.title("활동지원급여 수요조사카드 자동입력 — 1단계 (텍스트 입력 테스트)")
st.caption(
    "지금은 상담 녹음 파일 대신 상담 내용을 텍스트로 직접 입력해 파이프라인을 검증합니다. "
    "이 단계가 검증되면 '상담 내용 입력'란만 오디오 업로드 → STT 결과로 교체할 예정이며, "
    "이후 분석/문서 생성 로직은 그대로 재사용됩니다."
)

# ---------------------------------------------------------------------------
# 사이드바: LLM API 관리
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Gemini API 설정")
    api_key = st.text_input(
        "Gemini API Key",
        value=st.session_state["gemini_api_key"],
        type="password",
        help=(
            "Google AI Studio에서 발급받은 API 키를 입력하세요. "
            "Streamlit Cloud에 배포할 때는 코드에 직접 넣지 말고 "
            "st.secrets['GEMINI_API_KEY']로 등록하는 것을 권장합니다."
        ),
    )
    st.session_state["gemini_api_key"] = api_key
    model = st.text_input(
        "모델명",
        value=DEFAULT_MODEL,
        help="예: gemini-2.5-flash (빠르고 저렴), gemini-2.5-pro (고품질)",
    )

    if st.button("API 키 테스트", disabled=not api_key):
        with st.spinner("확인 중..."):
            ok, msg = test_api_key(api_key, model)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    st.divider()
    st.caption(
        "⚠️ 이 앱은 생년월일·연락처·주소 등 민감한 개인정보를 다룹니다. "
        "공개 배포 시 접근 제한(비밀번호 등)과 세션 종료 후 업로드/생성 파일 "
        "미보관 정책을 반드시 적용하세요."
    )

# ---------------------------------------------------------------------------
# 1) 상담 내용 입력 + AI 분석
# ---------------------------------------------------------------------------
st.subheader("1. 상담 내용 입력")

example = (
    "이용자 이름은 김철수이고 생년월일은 90년생입니다. 연락처는 010-1234-5678이고요, "
    "지체장애이시고 활동지원 등급은 14구간(가형)이에요. 강서구 화곡동 빌라 3층에 "
    "혼자 살고 계시고 엘리베이터는 없어요. 비흡연자시고 애완동물은 안 키우세요."
)
transcript = st.text_area(
    "상담 대화 내용 또는 메모를 입력하세요 (나중에는 이 부분이 음성 STT 결과로 대체됩니다)",
    height=220,
    placeholder=example,
    key="transcript_input",
)

col1, col2 = st.columns([1, 1])
with col1:
    analyze_clicked = st.button(
        "AI로 분석하기", type="primary", disabled=not (api_key and transcript.strip())
    )
with col2:
    reset_clicked = st.button("입력값 초기화")

if reset_clicked:
    st.session_state["extracted"] = {}
    st.session_state["form_version"] += 1

if analyze_clicked:
    with st.spinner("Gemini로 상담 내용을 분석하는 중..."):
        try:
            result = extract_fields(transcript, api_key, model)
            st.session_state["extracted"] = {k: v for k, v in result.items() if v}
            st.session_state["form_version"] += 1
            st.success("분석 완료. 아래에서 값을 확인하고 필요하면 수정한 뒤 문서를 생성하세요.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"분석 중 오류가 발생했습니다: {exc}")

st.divider()

# ---------------------------------------------------------------------------
# 2) 추출된 값 확인 및 수정
# ---------------------------------------------------------------------------
st.subheader("2. 값 확인 및 수정")
st.caption("AI가 채운 값이 정확한지 확인하고, 필요하면 직접 고치세요. 비워두면 서식 원본이 유지됩니다.")

extracted = st.session_state["extracted"]
version = st.session_state["form_version"]


def _key(field_id: str) -> str:
    return f"in_v{version}_{field_id}"


def render_fields(table_idx: int) -> dict:
    values: dict = {}
    for f in FIELDS:
        if f["table"] != table_idx:
            continue
        fid = f["id"]
        kind = f["kind"]

        if kind == "text":
            values[fid] = st.text_input(f["label"], value=extracted.get(fid) or "", key=_key(fid))

        elif kind == "custom":
            for sub in f["subfields"]:
                sid = sub["id"]
                values[sid] = st.text_input(sub["label"], value=extracted.get(sid) or "", key=_key(sid))

        elif kind in ("checkbox", "checkbox_multiline"):
            default_list = extracted.get(fid) or []
            if isinstance(default_list, str):
                default_list = [default_list]
            default_list = [o for o in default_list if o in f["options"]]
            values[fid] = st.multiselect(f["label"], f["options"], default=default_list, key=_key(fid))
            for blank in f.get("blanks", []):
                bid = blank["id"]
                values[bid] = st.text_input(blank["label"], value=extracted.get(bid) or "", key=_key(bid))

        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            opts = [""] + f["options"]
            default_val = extracted.get(fid) or ""
            idx = opts.index(default_val) if default_val in opts else 0
            sel = st.selectbox(f["label"], opts, index=idx, key=_key(fid))
            values[fid] = sel or None

    return values


tab1, tab2 = st.tabs(["1쪽", "2쪽"])
with tab1:
    values_p1 = render_fields(0)
with tab2:
    values_p2 = render_fields(1)

all_values = {**values_p1, **values_p2}

st.divider()

# ---------------------------------------------------------------------------
# 3) HWPX 생성 및 다운로드
# ---------------------------------------------------------------------------
st.subheader("3. HWPX 파일 생성")

if not Path(TEMPLATE_PATH).exists():
    st.error(f"템플릿 파일을 찾을 수 없습니다: {TEMPLATE_PATH}")
else:
    if st.button("HWPX 생성", type="primary"):
        with st.spinner("문서를 생성하는 중..."):
            try:
                st.session_state["generated_bytes"] = generate_hwpx_bytes(TEMPLATE_PATH, all_values)
                st.success("생성 완료. 아래 버튼으로 다운로드하세요.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"문서 생성 중 오류가 발생했습니다: {exc}")

    if st.session_state.get("generated_bytes"):
        st.download_button(
            "결과 HWPX 다운로드",
            data=st.session_state["generated_bytes"],
            file_name="상담결과.hwpx",
            mime="application/octet-stream",
        )
