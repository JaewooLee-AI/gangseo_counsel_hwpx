"""
llm_client.py

주어진 FIELDS 정의(field_map.FIELDS / meeting_field_map.FIELDS 등)로부터 JSON Schema를
자동 생성하고, Gemini API(google-genai SDK)를 호출해 입력 텍스트를 구조화된 값(dict)으로
추출한다. 업무(서식)마다 FIELDS와 system_prompt가 다르므로 둘 다 호출 측(forms.py의
FormDefinition)에서 넘겨받는다.

- 나중에 다른 LLM(Claude 등)으로 바꾸거나 STT 오디오 입력으로 바꿀 때도
  build_schema() 구조는 그대로 재사용 가능하도록 설계했다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.5-flash-lite"

# 서술형/개조식처럼 사용자가 항목별로 고르는 작성 방식. 필드 정의에 "style_selectable": True가
# 있는 항목만 대상이며, extract_fields()의 field_styles(필드 id -> 아래 키)로 넘겨받는다.
STYLE_NARRATIVE = "narrative"
STYLE_BULLET = "bullet"
WRITING_STYLES: dict[str, dict[str, str]] = {
    STYLE_NARRATIVE: {
        "label": "서술형",
        "rule": (
            "번호나 기호 없이 완결된 문장들을 자연스럽게 이어 쓴 문단으로 작성하세요. "
            "문장 끝은 서식 관례에 맞게 '~함', '~임', '~하였음' 형태로 맺으세요. "
            "발언자 구분이 필요한 항목은 발언자마다 '이름' 한 줄, 줄바꿈(\\n), 그 발언을 서술한 "
            "문단 순서로 쓰고 발언자 사이도 줄바꿈(\\n)으로 구분하세요. "
            "예: \"홍길동\\n지원사 일정 변경으로 갈등이 시작되었다고 설명함.\\n김철수\\n복약 관리가 "
            "시급하다고 판단하여 방문간호 연계를 제안함.\""
        ),
    },
    STYLE_BULLET: {
        "label": "개조식",
        "rule": (
            "'1. ', '2. '처럼 번호로 시작하는 짧은 항목을 나열하고, 항목 사이는 반드시 "
            "줄바꿈 문자(\\n)로 구분해 한 줄에 한 항목만 쓰세요. 한 항목에는 한 가지 사실이나 "
            "조치만 담고, 끝은 '~함', '~임', '~예정' 같은 명사형으로 간결하게 맺으세요. "
            "예: \"1. 활동지원사 교체 진행함\\n2. 방문간호 연계 신청함\\n3. 2주 후 모니터링 예정\". "
            "발언자 구분이 필요한 항목은 '1. 발언자 이름' 줄 아래에 '- '로 시작하는 하위 항목을 "
            "한 줄씩 나열하세요. 예: \"1. 홍길동\\n- 일정 변경으로 갈등 발생함\\n2. 김철수\\n- 방문간호 연계 제안함\"."
        ),
    },
}

_NUMBERED_ITEM = re.compile(r"\s+(?=(\d{1,2})\.\s)")


def _split_bullet_lines(text: str) -> str:
    """개조식으로 요청했는데 모델이 줄바꿈 없이 '1. … 2. … 3. …'처럼 한 줄로 이어 쓴 경우,
    번호가 1부터 차례로 이어지는 지점에서만 줄을 나눈다(날짜 '2026. 9. 30.' 같은 숫자는
    순번이 맞지 않으므로 건드리지 않는다). 이미 줄바꿈이 있으면 그대로 둔다."""
    if not isinstance(text, str) or "\n" in text or not text.lstrip().startswith("1."):
        return text
    parts: list[str] = []
    start = 0
    expected = 2
    for m in _NUMBERED_ITEM.finditer(text):
        if int(m.group(1)) != expected:
            continue
        parts.append(text[start : m.start()])
        start = m.end()
        expected += 1
    parts.append(text[start:])
    return "\n".join(p.strip() for p in parts)


def build_style_instructions(fields: list[dict[str, Any]], field_styles: dict[str, str] | None) -> str:
    """항목별 작성 방식 지시문을 만든다. 지정된 항목이 없으면 빈 문자열."""
    if not field_styles:
        return ""
    lines = []
    for f in fields:
        style = field_styles.get(f["id"])
        if not f.get("style_selectable") or style not in WRITING_STYLES:
            continue
        lines.append(f"- {f['label']} ({f['id']}): {WRITING_STYLES[style]['label']}")
    if not lines:
        return ""
    rules = "\n".join(f"- {s['label']}: {s['rule']}" for s in WRITING_STYLES.values())
    return (
        "[항목별 작성 방식]\n"
        "아래 항목은 지정된 작성 방식으로 값을 작성하세요. 작성 방식은 문장 형태만 정하며, "
        "입력 내용에 없는 정보를 덧붙이거나 내용을 빼지 마세요.\n"
        + "\n".join(lines)
        + "\n\n작성 방식 규칙:\n"
        + rules
    )


def _checkbox_property(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "enum": field["options"]},
        "description": field["label"],
    }


def build_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """fields 정의로부터 Gemini structured output용 JSON Schema(dict)를 만든다."""
    properties: dict[str, Any] = {}

    for f in fields:
        kind = f["kind"]

        if kind == "text":
            properties[f["id"]] = {"type": "string", "description": f["label"]}

        elif kind in ("checkbox", "checkbox_multiline"):
            properties[f["id"]] = _checkbox_property(f)
            for blank in f.get("blanks", []):
                properties[blank["id"]] = {"type": "string", "description": blank["label"]}

        elif kind in ("checkbox_scoped", "checkbox_scoped_until"):
            properties[f["id"]] = {
                "type": "string",
                "enum": f["options"],
                "description": f["label"],
            }

        elif kind == "custom":
            for sub in f["subfields"]:
                properties[sub["id"]] = {"type": "string", "description": sub["label"]}

    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def extract_fields(
    transcript: str,
    api_key: str,
    system_prompt: str,
    fields: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    field_styles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """입력 텍스트(transcript)를 Gemini로 분석해 필드 id -> 값 dict를 반환한다.
    system_prompt/fields는 업무(서식)별로 forms.FormDefinition에서 가져온다.
    field_styles(필드 id -> STYLE_NARRATIVE/STYLE_BULLET)를 넘기면 해당 항목을 그 작성
    방식으로 쓰도록 지시한다."""
    client = genai.Client(api_key=api_key)
    schema = build_schema(fields)
    style_instructions = build_style_instructions(fields, field_styles)
    prompt = system_prompt
    if style_instructions:
        prompt = f"{prompt}\n\n{style_instructions}"

    response = client.models.generate_content(
        model=model,
        contents=f"{prompt}\n\n[입력 내용]\n{transcript}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
        ),
    )

    text = response.text
    if not text:
        raise ValueError("Gemini 응답이 비어 있습니다.")
    result = json.loads(text)
    for fid, style in (field_styles or {}).items():
        value = result.get(fid)
        if not isinstance(value, str):
            continue
        # 지시문의 예시("…\\n…")를 따라 하다 실제 줄바꿈 대신 '\n' 두 글자를 쓰는 경우가 있다.
        value = value.replace("\\n", "\n")
        if style == STYLE_BULLET:
            value = _split_bullet_lines(value)
        result[fid] = value
    return result


def test_api_key(api_key: str, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """API 키가 유효한지 가벼운 호출로 확인한다. (성공여부, 메시지) 반환."""
    try:
        client = genai.Client(api_key=api_key)
        client.models.generate_content(model=model, contents="ping")
        return True, "API 키가 정상적으로 확인되었습니다."
    except Exception as exc:  # noqa: BLE001 - UI에 원인 그대로 보여주기 위함
        return False, str(exc)
