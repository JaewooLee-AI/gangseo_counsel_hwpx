"""hwpx_engine.apply_fields()가 실제 counsel.hwpx에 대해 sample.hwpx와 유사한
결과를 만들어내는지 확인하는 수동 검증 스크립트. (pytest 아님, 그냥 실행해서
눈으로 diff 확인용)

실행: python3 test_engine.py
"""

from __future__ import annotations

import json

from hwpx import HwpxDocument

from hwpx_engine import apply_fields, dump_tables

# sample_dump.json (작성 완료 샘플)을 흉내 낸 입력값. 실제로는 LLM이 텍스트
# 상담내용에서 이 dict를 추출해준다.
VALUES = {
    "user_name": "김**",
    "birth_date": "70****",
    "phone": "010-2452-****",
    "disability_type": "지체장애",
    "support_grade": "14구간 (가형)",
    "address_registered": " 07775) 강서구 월정로***************",
    "housing_type": ["기타"],
    "housing_type_etc": "원룸",
    "living_rooms": "1",
    "living_space": ["거실", "화장실", "주방"],
    "floor_number": "3",
    "elevator": ["유"],
    "transport_main": ["지하철", "버스", "도보"],
    "transport_bus_interval": None,
    "transport_walk_minutes": None,
    "transport_car_parking": "가능",
    "pet_status": ["없음"],
    "mobility_status": [],
    "comm_understanding": "중",
    "comm_verbal": "상",
    "comm_alt_expression": "아니오",
    "comm_risk_awareness": "상",
    "comm_challenging_behavior": "하",
    "comm_sudden_behavior": "하",
    "comm_spatial_awareness": "상",
    "comm_knows_address": "예",
    "smoking": "비흡연자",
    "notes_health": "신장.신부전.당뇨.고혈압-이대목동병원 (순환기내과 신장내과 안과 진료)\n왼쪽무릎까지 절단 의족사용-홍익 병원 (정형외과 진료)",
    "household_size": "1",
    "household_composition": ["1인 가구"],
    "emergency_phone": "010-2452-****",
    "emergency_relation": "여동생- 김**",
    "physical_support": ["신체활동지원"],
    "physical_support_detail": "20일 1시간",
    "housework_support": ["가사활동지원"],
    "housework_support_detail": "20일 2시간",
    "social_support": ["사회활동지원"],
    "social_support_detail": "20일 1시간",
    "staff_start_time": "17:00",
    "staff_end_time": "22:00",
    "staff_gender": "여",
    "staff_age_group": "60대",
    "interview_month": "08",
    "interview_day": "19",
    "interviewer_name": "정**",
}

doc = HwpxDocument.open("counsel.hwpx")
apply_fields(doc, VALUES)
doc.save_to_path("_test_output.hwpx")
print("저장 완료: _test_output.hwpx")

dumped = dump_tables("_test_output.hwpx")
by_anchor = {}
for t in dumped:
    for c in t["cells"]:
        by_anchor[(t["table_index"], tuple(c["anchor"]))] = c["text"]

sample = json.load(open("files/sample_dump.json"))
sample_by_anchor = {}
for t in sample:
    for c in t["cells"]:
        sample_by_anchor[(t["table_index"], tuple(c["anchor"]))] = c["text"]

checks = [
    (0, (3, 1)), (0, (3, 5)), (0, (3, 9)), (0, (4, 1)), (0, (4, 7)),
    (0, (5, 4)), (0, (7, 4)), (0, (8, 4)), (0, (9, 4)), (0, (9, 8)),
    (0, (10, 4)), (0, (11, 4)), (0, (13, 4)), (0, (14, 4)), (0, (16, 4)),
    (1, (1, 4)), (1, (4, 4)), (1, (14, 1)), (1, (15, 12)), (1, (15, 19)),
    (1, (20, 11)), (1, (22, 11)), (1, (25, 0)),
]

print("\n=== anchor별 결과 vs 샘플 비교 ===")
for key in checks:
    got = by_anchor.get(key, "<MISSING>")
    exp = sample_by_anchor.get(key, "<NO SAMPLE>")
    match = "OK " if got.strip() == exp.strip() else "DIFF"
    print(f"\n[{match}] table{key[0]} anchor{key[1]}")
    print(f"  got: {got!r}")
    print(f"  exp: {exp!r}")
