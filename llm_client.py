"""
llm_client.py

field_map.py의 FIELDS 정의로부터 JSON Schema를 자동 생성하고, Gemini API
(google-genai SDK)를 호출해 상담 텍스트를 구조화된 값(dict)으로 추출한다.

- 나중에 다른 LLM(Claude 등)으로 바꾸거나 STT 오디오 입력으로 바꿀 때도
  build_schema() / SYSTEM_PROMPT는 그대로 재사용 가능하도록 설계했다.
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from field_map import FIELDS

DEFAULT_MODEL = "gemini-3.5-flash-lite"

SYSTEM_PROMPT = """당신은 장애인 활동지원급여 수요조사(상담) 내용을 분석해 정해진 서식에
들어갈 값을 추출하는 보조원입니다.

아래 [상담 내용]에는 상담사와 이용자(또는 보호자) 사이의 대화 내용, 혹은 상담사가
직접 정리한 메모가 텍스트로 주어집니다. 이 내용에서 실제로 언급되거나 명확히
추론 가능한 정보만 추출하세요.

규칙:
1. 상담 내용에 명시적으로 언급되지 않은 항목은 절대 추측하지 말고 생략(null 또는
   빈 값)하세요. 없는 정보를 지어내면 안 됩니다.
2. 체크박스 성격의 필드는 주어진 옵션 목록 중에서만 골라야 합니다. 목록에 없는
   표현이 언급되면 가장 가까운 옵션을 고르거나, 애매하면 생략하세요.
3. 개인정보(전화번호, 생년월일, 주소 등)는 상담 내용에 나온 그대로 옮기세요.
   마스킹(예: 010-1234-****)이 되어 있으면 그대로 두고, 실제 값이 나오면 실제
   값을 쓰세요.
4. 숫자만 요구하는 필드(층수, 방 개수, 시간 등)는 숫자만 넣으세요.
5. 각 필드의 설명(description)을 참고해 어떤 정보를 채워야 하는지 판단하세요.
"""


def _checkbox_property(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "enum": field["options"]},
        "description": field["label"],
    }


def build_schema() -> dict[str, Any]:
    """FIELDS 정의로부터 Gemini structured output용 JSON Schema(dict)를 만든다."""
    properties: dict[str, Any] = {}

    for f in FIELDS:
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
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """상담 텍스트(transcript)를 Gemini로 분석해 필드 id -> 값 dict를 반환한다."""
    client = genai.Client(api_key=api_key)
    schema = build_schema()

    response = client.models.generate_content(
        model=model,
        contents=f"{SYSTEM_PROMPT}\n\n[상담 내용]\n{transcript}",
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
