"""
document_store.py

업무(business_id)별로 "문서함"에 들어갈 임시 저장 문서를 JSON 파일로 보관한다.
app_flet.py의 load_config()/save_config()와 동일하게 홈 디렉터리를 우선 사용하고,
쓰기 권한이 없는 환경에서는 프로젝트 로컬 폴더로 폴백한다.

문서 1건 = 파일 1개: ~/.gangseo_counsel_docs/<business_id>/<doc_id>.json
doc_id = "{YYYYMMDD}-{seq:03d}" (해당 업무 폴더 안에서 오늘 날짜로 시작하는 파일 개수를
세어 다음 순번을 매긴다. 단일 사용자 데스크톱 앱이라 동시 쓰기 충돌을 고려하지 않는다).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

HOME_ROOT = Path.home() / ".gangseo_counsel_docs"
LOCAL_ROOT = Path(".app_docs")


def _roots_for(business_id: str) -> tuple[Path, Path]:
    return HOME_ROOT / business_id, LOCAL_ROOT / business_id


def _writable_root(business_id: str) -> Path:
    """쓰기 가능한 루트 폴더를 찾아 생성하고 반환한다(홈 우선, 로컬 폴백)."""
    home_dir, local_dir = _roots_for(business_id)
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        probe = home_dir / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return home_dir
    except Exception:
        local_dir.mkdir(parents=True, exist_ok=True)
        return local_dir


def _existing_dirs(business_id: str) -> list[Path]:
    home_dir, local_dir = _roots_for(business_id)
    return [d for d in (home_dir, local_dir) if d.exists()]


def _next_doc_id(business_id: str) -> str:
    date_prefix = datetime.now().strftime("%Y%m%d")
    count = 0
    for d in _existing_dirs(business_id):
        count += len(list(d.glob(f"{date_prefix}-*.json")))
    return f"{date_prefix}-{count + 1:03d}"


def list_documents(business_id: str) -> list[dict[str, Any]]:
    """저장된 문서 요약 목록을 최신순으로 반환한다 (doc_id, key, created_at, updated_at)."""
    seen: dict[str, dict[str, Any]] = {}
    for d in _existing_dirs(business_id):
        for path in d.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            doc_id = data.get("doc_id") or path.stem
            seen[doc_id] = {
                "doc_id": doc_id,
                "key": data.get("key") or "",
                "created_at": data.get("created_at") or "",
                "updated_at": data.get("updated_at") or data.get("created_at") or "",
            }
    return sorted(seen.values(), key=lambda d: d["doc_id"], reverse=True)


def load_document(business_id: str, doc_id: str) -> dict[str, Any] | None:
    for d in _existing_dirs(business_id):
        path = d / f"{doc_id}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def save_document(
    business_id: str,
    doc_id: str | None,
    key: str,
    values: dict[str, Any],
    transcript: str = "",
) -> str:
    """문서를 저장한다. doc_id가 None이면 새 문서를 만들고, 있으면 같은 파일을 덮어쓴다.
    최종적으로 저장된 doc_id를 반환한다."""
    now = datetime.now().isoformat(timespec="seconds")
    is_new = doc_id is None
    if is_new:
        doc_id = _next_doc_id(business_id)

    existing = None if is_new else load_document(business_id, doc_id)
    created_at = (existing or {}).get("created_at") or now

    data = {
        "doc_id": doc_id,
        "business_id": business_id,
        "created_at": created_at,
        "updated_at": now,
        "key": key or "",
        "values": values,
        "transcript": transcript or "",
    }

    root = _writable_root(business_id)
    with open(root / f"{doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return doc_id


def delete_document(business_id: str, doc_id: str) -> bool:
    deleted = False
    for d in _existing_dirs(business_id):
        path = d / f"{doc_id}.json"
        if path.exists():
            path.unlink()
            deleted = True
    return deleted
