# HWPX 상담양식 자동화 프로젝트 — 분석 자료 (Claude Code 전달용)

작성일: 2026-09-01
분석 대상 파일: `상담양식.hwpx` (빈 서식), `상담내역_샘플.hwpx` (작성 완료본)
문서 종류: 활동지원급여 수요조사카드 (총 2쪽, 서식번호 [25-028, 별지 제23-1호])

---

## 1. 프로젝트 목표 (단계적 접근)

- **최종 목표**: 상담사가 상담 녹음(mp3 등)을 업로드하면 LLM(Claude/Gemini)이 음성을
  분석해 `상담양식.hwpx`에 자동으로 값을 채운 결과물을 생성.
- **현재 단계 (1단계, 지금 구현할 것)**: 개인정보 문제로 녹음 파일을 아직 확보할 수
  없으므로, **화면에서 텍스트/폼으로 정보를 입력하면 → LLM이 이를 구조화 → HWPX 파일을
  생성**하는 파이프라인부터 구현. 오디오 STT 부분만 나중에 입력 소스로 갈아 끼우면 되도록
  설계.
- **배포 환경**: Streamlit Cloud (Linux 기반). 개발은 외부 IDE + Claude Code.

---

## 2. 기술적 결론: 가능함, 단 체크박스 처리가 핵심 난이도

### 2.1 HWPX 포맷 자체
HWPX는 ZIP + XML(OWPML/OPC) 구조이며, 한컴오피스 설치 없이 순수 파이썬으로 열고 수정하고
저장할 수 있습니다. 따라서 Streamlit Cloud(Linux, 한컴오피스 미설치)에 배포하는 데 문제가
없습니다. 사용 라이브러리는 **`python-hwpx`** (PyPI, 현재 확인된 버전 6.3.0, Apache-2.0
라이선스). 실제로 설치해서 두 파일 모두 정상적으로 열리는 것을 확인했습니다.

```bash
pip install python-hwpx
```

### 2.2 문서 구조 (실측 결과)
`상담양식.hwpx`는 표(테이블) 2개로 구성:

| 표 | 크기 | 내용 |
|---|---|---|
| 표 0 (1쪽) | 17행 × 10열 | 이용자명, 생년월일, 연락처, 장애유형, 지원등급, 주소, 건물형태, 이용공간, 주거환경, 애완동물, 건강상태, 의사소통, 흡연여부, 특이사항 |
| 표 1 (2쪽) | 27행 × 21열 | 가구여건, 가구구성원 사회활동, 자녀수, 비상연락, 수급자의 사회활동(직장/학교), 여가활동, 장애인 활동지원 욕구조사, 야간서비스, 활동지원인력 요건, 다른 복지서비스, 특이사항, 면담일자 |

표는 병합 셀(span)이 매우 많습니다. `python-hwpx`의 `get_cell_map()`은 병합 영역 전체에
동일한 anchor를 반환하므로 **anchor 기준 dedup 처리가 필수**입니다 (안 하면 같은 셀을
여러 번 순회하게 됨).

### 2.3 핵심 난제: 체크박스는 "진짜 체크박스"가 아니다

`doc.fields.check_boxes` (구버전 API명: `list_check_boxes()`)로 조회하면 **0개**가
나옵니다. 즉 이 문서의 □/■ 는 폼 필드(Form Field) 개체가 아니라 **셀 텍스트 안에 박힌
일반 유니코드 문자**입니다.

실제 비교 예시 (빈 양식 vs 작성된 샘플):

```
빈 양식: □단독주택   □아파트   □연립·다세대주택  □기타(               )
작성본: □단독주택   □아파트   □연립·다세대주택  ■기타( 원룸  )

빈 양식: □ 비흡연자   □ 흡연자
작성본: ■ 비흡연자   □ 흡연자
```

→ **"값을 채운다" = "셀의 원본 문자열에서 특정 □를 ■로 바꾸고, 괄호 안 공백에 텍스트를
끼워 넣어 새 문자열을 만들어 `cell.set_text()`로 덮어쓰는 것"** 입니다. 라벨 기반 자동
채우기(`fill_by_path` 류)만으로는 이 부분을 처리할 수 없고, 셀별 문자열 조작 로직이
필요합니다.

이 문제는 이미 해결했습니다 — `hwpx_toolkit.py`의 `toggle_checkboxes()` /
`fill_paren_blank()` 함수가 실제 원본 템플릿 문자열로 테스트를 마쳤고, 아래처럼 정확히
샘플과 동일한 패턴을 재현합니다.

```python
toggle_checkboxes('□단독주택   □아파트   □연립·다세대주택  □기타(               )', ['기타'])
# -> '□단독주택   □아파트   □연립·다세대주택  ■기타(               )'

fill_paren_blank(위결과, '기타', '빌라')
# -> '□단독주택   □아파트   □연립·다세대주택  ■기타( 빌라 )'
```

`hwpx_toolkit.py`를 실제 원본 `상담양식.hwpx`에 대해 실행해서 이용자명/생년월일/연락처/
장애유형/지원등급/건물형태(체크+빈칸)/흡연여부(체크)/특이사항 필드가 정확히 채워지고
저장까지 되는 것을 확인했습니다.

---

## 3. 전체 아키텍처 (1단계 기준)

```
[Streamlit 폼 입력]
   - 정형 필드(이름, 생년월일, 연락처 등): st.text_input 등으로 직접 입력
   - 비정형 메모(예: "3층인데 엘베 없어서 힘들어하심"): st.text_area 자유 입력
        │
        ▼
[LLM 구조화 추출 단계] (Claude 또는 Gemini API)
   - 프롬프트: field_schema.json 기반으로 만든 "필드 정의 + 체크박스 옵션 목록"을
     시스템 프롬프트/스키마로 제공
   - 출력: JSON (Structured Output / Tool use) — 예:
     {"housing_type": ["기타"], "housing_type_etc": "빌라", "smoking": "비흡연자", ...}
        │
        ▼
[HWPX 채우기 단계] (hwpx_toolkit.py 확장)
   - field_schema.json의 anchor를 이용해 셀 위치 조회
   - 텍스트 필드는 set_text() 직접 대입
   - 체크박스 필드는 toggle_checkboxes() + fill_paren_blank()/fill_named_blank()로 문자열 조합 후 set_text()
        │
        ▼
[다운로드] doc.save_to_path() → st.download_button()으로 제공
```

**나중에 오디오 STT를 붙일 때**: "Streamlit 폼 입력" 부분만 "오디오 업로드 → STT 텍스트"로
교체하고, 그 이후 LLM 구조화 추출 단계부터는 로직을 그대로 재사용 가능하도록 설계되어
있습니다. 즉 지금 단계에서 프롬프트/스키마/채우기 로직을 잘 만들어두는 것이 그대로 다음
단계의 자산이 됩니다.

---

## 4. 함께 전달하는 파일

| 파일 | 내용 |
|---|---|
| `hwpx_form_analysis.md` | 이 문서 |
| `field_schema.json` | 표 0/1의 모든 셀을 anchor/span/원본텍스트/필드유형(`checkbox_group`, `checkbox_single`, `text_blank`, `label_or_static`)으로 분류한 스키마. LLM 프롬프트 설계 및 채우기 로직 확장의 기준 자료 |
| `form_dump.json` | 빈 양식(`상담양식.hwpx`)의 모든 셀 원본 텍스트 raw dump |
| `sample_dump.json` | 작성 완료 샘플(`상담내역_샘플.hwpx`)의 모든 셀 원본 텍스트 raw dump — 어떤 값이 어떤 형태로 채워지는지 실제 예시 대조용 |
| `hwpx_toolkit.py` | 검증된 실행 가능 코드: 문서 덤프 함수, 체크박스 토글 엔진, 괄호 빈칸 채우기, 대표 필드 채우기 예시. `python hwpx_toolkit.py 상담양식.hwpx 출력.hwpx` 로 바로 실행해서 결과 확인 가능 |

`field_schema.json`은 105개 셀이 분류되어 있으며, Claude Code에서 이 파일을 순회하며
`checkbox_group`/`checkbox_single` 타입은 `options_preview` 배열을 참고해 LLM 출력
스키마(예: JSON Schema의 enum)를 자동 생성하는 식으로 활용하는 것을 권장합니다.
`sample_dump.json`은 실제로 어떤 문구/형식으로 값이 들어가는지(날짜 형식, 전화번호
마스킹 표기 등) LLM few-shot 예시로 쓰기에도 유용합니다.

---

## 5. 준비 체크리스트

1. **API 키**: Anthropic API 키(Claude), Google API 키(Gemini) — Streamlit Cloud의
   Secrets(`st.secrets`)에 등록. 코드에 하드코딩 금지.
2. **requirements.txt**: 최소한 아래 포함
   ```
   streamlit
   python-hwpx
   anthropic
   google-generativeai
   ```
3. **개인정보 처리**: 이 폼 자체에 생년월일/연락처/주소 등 민감정보가 들어갑니다.
   Streamlit Cloud 공개 URL 접근 제한(비밀번호 보호 등), 세션 종료 시 업로드/생성 파일
   미보관 등을 배포 전에 점검할 것.
4. **python-hwpx 버전 확인**: 이 문서의 API 예시는 6.3.0 기준입니다. 라이브러리가
   활발히 업데이트되는 편이라 Claude Code 작업 시작 시
   `pip show python-hwpx`로 버전을 확인하고, API가 다르면
   `python3 -c "from hwpx import HwpxDocument; d=HwpxDocument.open('상담양식.hwpx'); print(dir(d))"`
   로 실제 메서드를 재확인할 것.
5. **2쪽 표(27×21)의 나머지 필드**: 이번 분석에서 1쪽 표는 대표 필드 위주로 채우기
   로직까지 검증했고, 2쪽 표는 구조 파악(라벨/체크박스 위치)까지 완료했습니다. 실제 채우기
   함수 확장은 `field_schema.json`을 기준으로 동일 패턴 반복 작업이라 Claude Code로
   충분히 진행 가능합니다.

---

## 6. python-hwpx 핵심 API 요약 (실측 확인)

```python
from hwpx import HwpxDocument

doc = HwpxDocument.open("상담양식.hwpx")

tables = doc.tables.all              # list[HwpxOxmlTable], 프로퍼티(호출 X)
t0 = tables[0]
print(t0.row_count, t0.column_count) # 17 10

cell_map = t0.get_cell_map()         # list[list[HwpxTableGridPosition]]
# 각 원소: pos.anchor(=(row,col) 튜플), pos.span(=(rowspan,colspan)), pos.cell

cell = t0.cell(3, 1)                 # 특정 셀 직접 접근
print(cell.text)                     # 읽기
cell.set_text("홍길동")               # 쓰기 — 서식 유지, 문서 재조립 없이 반영

doc.save_to_path("결과.hwpx")
```

주의: `doc.fields.check_boxes`, `doc.tables.fill_by_path` 등은 존재하지만 이번 문서의
"텍스트에 박힌 □" 패턴에는 맞지 않아 사용하지 않았습니다. 버전업 시 API 위치가 이동될 수
있으므로(예: `list_check_boxes()` → `fields.check_boxes`), 실제 설치 버전에서
`dir(doc)`으로 재확인 후 작업하시길 권장합니다.
