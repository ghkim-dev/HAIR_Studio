"""용어 인식 평가 세트 (Hub 없이 실행).

  python tests/eval_terms.py

사람이 실제로 쓸 법한 요청 문장 → 인식돼야 할 용어. 용어집을 고치거나 늘린 뒤 돌려서 정확도가 떨어지지 않는지 확인.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import lexicon  # noqa: E402

CASES = [
    # (성별, 요청 문장, 인식돼야 할 용어)
    ("male", "애즈펌으로, 앞머리는 C컬로 살짝 넘기고 옆은 다운펌", {"애즈펌", "다운펌"}),
    ("male", "6:4 가르마펌 해주세요", {"가르마펌"}),
    ("male", "투블럭에 댄디컷", {"투블럭", "댄디컷"}),
    ("male", "올백으로 넘겨 주세요", {"슬릭백"}),
    ("male", "리젠트컷 느낌으로 이마 까고", {"리젠트컷"}),
    ("male", "스킨페이드 크롭컷", {"스킨페이드", "크롭컷"}),
    ("male", "쉼표머리 하고 싶어요", {"쉼표머리"}),
    ("male", "가일컷으로 해주세요", {"가일컷"}),
    ("male", "아이비리그컷", {"아이비리그컷"}),
    ("male", "히피펌 해볼래", {"히피펌"}),
    ("male", "시스루 댄디컷으로", {"시스루 댄디컷"}),
    ("male", "울프컷", {"울프컷"}),
    ("male", "멀렛 스타일", {"멀렛"}),
    ("male", "버즈컷으로 짧게", {"버즈컷"}),
    ("male", "포마드 스타일로 깔끔하게", {"포마드"}),
    ("female", "허쉬컷에 커튼뱅", {"허쉬컷", "커튼뱅"}),
    ("female", "중단발 C컬펌", {"중단발", "C컬펌"}),
    ("female", "빌드펌 해주세요", {"빌드펌"}),
    ("female", "엘리자벳펌으로 웨이브", {"엘리자벳펌"}),
    ("female", "히메컷", {"히메컷"}),
    ("female", "레이어드컷에 시스루뱅", {"레이어드컷", "시스루뱅"}),
    ("female", "단발 보브컷", {"보브컷"}),
    ("female", "숏컷 픽시컷", {"숏컷", "픽시컷"}),
    ("female", "포니테일로 묶은 모습", {"포니테일"}),
    ("female", "반묶음 해주세요", {"반묶음"}),
    ("female", "매직스트레이트로 쫙 펴기", {"매직스트레이트"}),
    ("female", "물결펌", {"물결펌"}),
    ("female", "똥머리", {"똥머리"}),
    ("female", "처피뱅", {"처피뱅"}),
    ("female", "태슬컷", {"태슬컷"}),
    ("female", "울프컷", {"울프컷"}),
    ("female", "허쉬펌", {"허쉬펌"}),
    ("female", "볼륨매직", {"볼륨매직"}),
    ("female", "버터플라이컷", {"버터플라이컷"}),
    ("female", "땋은머리", {"땋은머리"}),
    (None, "가운데 가르마로", {"5:5 가르마", "5:5가르마"}),
]


def main() -> None:
    ok = 0
    misses = []
    for g, req, want in CASES:
        got = set(lexicon.match(req, lexicon.entries(g)))
        # 같은 이름 다른 표기(5:5 가르마 / 5:5가르마)는 하나만 맞아도 정답
        hit = want <= got if len(want) > 1 and not want & {"5:5 가르마", "5:5가르마"} else bool(want & got)
        hit = hit and not (got - want - {"5:5 가르마", "5:5가르마", "단발"})   # 엉뚱한 용어가 끼면 오답
        ok += hit
        if not hit:
            misses.append((g, req, sorted(want), sorted(got)))
    print(f"용어 인식 정확도: {ok}/{len(CASES)} = {ok / len(CASES):.0%}")
    for m in misses:
        print("  ✗", m)


if __name__ == "__main__":
    main()
