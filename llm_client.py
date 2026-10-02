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
from typing import Any

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.5-flash-lite"


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
) -> dict[str, Any]:
    """입력 텍스트(transcript)를 Gemini로 분석해 필드 id -> 값 dict를 반환한다.
    system_prompt/fields는 업무(서식)별로 forms.FormDefinition에서 가져온다."""
    client = genai.Client(api_key=api_key)
    schema = build_schema(fields)

    response = client.models.generate_content(
        model=model,
        contents=f"{system_prompt}\n\n[입력 내용]\n{transcript}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
        ),
    )

    text = response.text
    if not text:
        raise ValueError("Gemini 응답이 비어 있습니다.")
    return json.loads(text)


def test_api_key(api_key: str, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """API 키가 유효한지 가벼운 호출로 확인한다. (성공여부, 메시지) 반환."""
    try:
        client = genai.Client(api_key=api_key)
        client.models.generate_content(model=model, contents="ping")
        return True, "API 키가 정상적으로 확인되었습니다."
    except Exception as exc:  # noqa: BLE001 - UI에 원인 그대로 보여주기 위함
        return False, str(exc)
