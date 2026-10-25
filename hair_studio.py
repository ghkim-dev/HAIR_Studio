"""
Hair Studio — 요구사항을 이해해서 이미지·영상을 만드는 Open AI Service Hub 파이프라인

  python hair_studio.py models
  python hair_studio.py run  --photo 증명사진.jpg --request "가르마펌으로 바꿔줘, 안경은 그대로"
  python hair_studio.py run  --photo 증명사진.jpg --request "..." --plan-only     # 이해 단계만 확인
  python hair_studio.py run  --photo 결과.png --output video --request "고개를 좌우로 돌려 머리를 보여줘"
  python hair_studio.py hair --photo 증명사진.jpg                                  # 추천 4개 스타일 일괄

파이프라인 구조와 근거 논문은 PIPELINE.md 참고.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from pipeline import Hub, HubError, RunConfig, run  # noqa: E402
from pipeline import lexicon  # noqa: E402
from pipeline.utils import korean_font, log  # noqa: E402

# 앞서 추천한 4개 스타일 — 사람이 말하듯 쓴 요구사항 그대로 파이프라인에 넣는다.
PRESETS = {
    "01_가르마펌": "6:4 가르마펌으로 바꿔줘. 이마가 3분의 1 정도 보이게, 옆머리는 다운펌으로 눌러서 깔끔하게.",
    "02_애즈펌": "애즈펌으로 바꿔줘. 앞머리를 C컬로 한쪽으로 넘겨서 이마가 살짝 보이게, 너무 꼬불거리지 않게 자연스럽게.",
    "03_댄디컷": "지금 길이는 비슷하게 두고 숱을 쳐서 윗머리가 덜 부해 보이게 정리한 댄디컷. 앞머리 끝은 눈썹 위로 깔끔하게.",
    "04_투블럭리프컷": "옆은 짧게 친 투블럭에 윗머리는 리프컷처럼 자연스럽게 옆으로 흐르게. 단정하고 성숙한 인상으로.",
}
KEEP = " 얼굴, 안경, 옷, 배경, 구도는 원본 증명사진과 똑같이 유지하고 머리만 바꿔줘."


def cmd_models(_a) -> None:
    hub = Hub()
    m = hub.models()
    log(f"키로 사용 가능한 모델 {len(m.available)}개:")
    for x in m.available:
        log(f"  {x}")
    log("\n자동 선택 결과 (.env의 OASH_*_MODEL로 덮어쓸 수 있음)")
    for role in ("text", "vision", "image", "image_edit", "video_i2v", "video_t2v"):
        log(f"  {role:<10} {getattr(m, role) or '(없음)'}")
    # /models는 키 없이도 열리므로, 짧은 채팅 1회로 키가 실제로 유효한지 확인
    log("\n개발키 확인 중…")
    try:
        hub.chat([{"role": "user", "content": "ping"}], max_tokens=1)
        log("  개발키 정상 ✔")
    except HubError as e:
        log(f"  ✘ {e}")


def cmd_terms(a) -> None:
    g = getattr(a, "gender", None)
    terms = lexicon.entries(g)
    if a.action == "list":
        items = lexicon.gallery()
        if g:
            items = [i for i in items if i["gender"] in (g, "unisex")]
        for cat in ("컷", "펌", "앞머리", "스타일링", "묶음"):
            rows = [i for i in items if i["category"] == cat]
            if not rows:
                continue
            log(f"[{cat}] {len(rows)}개")
            for gen in ("male", "female", "unisex"):
                names = [i["term"] for i in rows if i["gender"] == gen]
                if names:
                    log(f"  {'남성' if gen == 'male' else '여성' if gen == 'female' else '공용'}: " + ", ".join(names))
        learned = [k for k, v in terms.items() if v.get("source") == "learned"]
        if learned:
            log(f"[학습 초안·미검토] " + ", ".join(learned))
        log(f"\n총 {len(items)}개 · 용어집: {lexicon.CORPUS_DIR} · 직접 추가: {lexicon.CUSTOM_FILE}")
        return
    if not a.term:
        sys.exit("용어를 적어 주세요. 예: python hair_studio.py terms show 애즈펌")
    hits = lexicon.match(a.term, terms)
    if a.action == "show":
        if not hits:
            sys.exit(f"'{a.term}'은(는) 용어집에 없습니다. `terms learn {a.term}` 으로 속성 분해를 해 볼 수 있어요.")
        for name in hits:
            e = lexicon.render(terms[name])
            log(f"■ {name}  ({e.get('corpus_gender', e.get('gender', ''))} · {e.get('category')} · "
                f"출처 {e.get('source')} · 신뢰도 {e.get('confidence', '-')})")
            log(f"  {e.get('description_ko', '')}")
            for x in e["attrs_ko"]:
                log(f"   - {x}")
            log(f"  이미지 모델에 전달: {e['visual_en']}")
            log(f"  하면 안 되는 모양: {', '.join(e.get('must_not', []))}")
            if e.get("aliases"):
                log(f"  별칭: {', '.join(e['aliases'])}")
            for s in e.get("sources", []) or []:
                log(f"  출처: {s}")
        return
    hub = Hub()
    name = hits[0] if hits else a.term
    if a.action == "learn":
        e = lexicon.decompose(hub, name, a.term, g)
    else:  # photos
        if not lexicon.ref_images(name):
            sys.exit(f"참고 사진이 없습니다. {lexicon.IMAGES_DIR / name} 폴더에 jpg/png를 1~3장 넣어 주세요.")
        e = terms.get(name) or lexicon.decompose(hub, name, a.term, g)
    if lexicon.ref_images(name):
        e = lexicon.ground_from_images(hub, name, e)
    e["term"] = name
    lexicon.save_learned(e)
    r = lexicon.render(e)
    log(f"■ {name} (신뢰도 {e.get('confidence', '-')}, 모르는 용어로 판단: {not e.get('known', True)})")
    for x in r["attrs_ko"]:
        log(f"   - {x}")
    log(f"  이미지 모델에 전달: {r['visual_en']}")
    log(f"\n저장됨 → {lexicon.LEARNED_FILE}  (맞으면 hair_terms.json의 terms로 옮겨 확정하세요)")


def _cfg(a, request: str, name: str) -> RunConfig:
    return RunConfig(request=request, photo=Path(a.photo) if a.photo else None, output=a.output,
                     rounds=a.rounds, candidates=a.candidates, threshold=a.threshold, size=a.size,
                     seconds=getattr(a, "seconds", None), plan_only=a.plan_only,
                     out_root=Path(a.out), name=name, learn_terms=not a.no_learn,
                     gender=getattr(a, "gender", None), fast=getattr(a, "fast", False))


def cmd_run(a) -> None:
    if a.photo and not Path(a.photo).exists():
        sys.exit(f"사진을 찾을 수 없습니다: {a.photo}")
    request = a.request or Path(a.request_file).read_text(encoding="utf-8")
    run(_cfg(a, request, a.name))


def cmd_hair(a) -> None:
    if not Path(a.photo).exists():
        sys.exit(f"사진을 찾을 수 없습니다: {a.photo}")
    a.output = "image"
    keys = list(PRESETS) if a.styles == "all" else [k for k in PRESETS if any(s in k for s in a.styles.split(","))]
    hub = Hub()
    finals = []
    for k in keys:
        st = run(_cfg(a, PRESETS[k] + KEEP, k), hub)
        if st.get("final"):
            finals.append((k, Path(st["final"]["file"]), st["final"]["score"]))
    if finals:
        sheet(Path(a.photo), finals, Path(a.out) / "비교_헤어스타일.png")


def sheet(original: Path, items: list, out: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    h = 512
    tiles = [("원본", original, None)] + items
    ims = []
    for label, p, score in tiles:
        im = Image.open(p).convert("RGB")
        ims.append((label if score is None else f"{label}  ({score:.2f})", im.resize((int(im.width * h / im.height), h))))
    W = sum(i.width for _, i in ims) + 12 * (len(ims) + 1)
    s = Image.new("RGB", (W, h + 56), "white")
    d = ImageDraw.Draw(s)
    font = korean_font(22)
    x = 12
    for label, im in ims:
        s.paste(im, (x, 12))
        d.text((x + 4, h + 20), label, fill="black", font=font)
        x += im.width + 12
    out.parent.mkdir(parents=True, exist_ok=True)
    s.save(out)
    log(f"\n비교 이미지: {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="요구사항 이해 기반 이미지·영상 생성 (Open AI Service Hub)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="키로 쓸 수 있는 모델과 역할별 자동 선택 결과").set_defaults(func=cmd_models)

    def common(sp):
        sp.add_argument("--rounds", type=int, default=3, help="최대 반복 라운드 (기본 3)")
        sp.add_argument("--candidates", type=int, default=2, help="라운드당 후보 수 (기본 2)")
        sp.add_argument("--threshold", type=float, default=0.9, help="조기 종료 점수 (0~1, 기본 0.9)")
        sp.add_argument("--size", help="예: 1024x1024 (모델이 지원할 때)")
        sp.add_argument("--plan-only", action="store_true", help="생성 없이 이해 단계(계획·체크리스트·프롬프트)만")
        sp.add_argument("--out", default="output", help="결과 폴더 (기본 output)")
        sp.add_argument("--no-learn", action="store_true", help="용어집에 없는 용어를 분해·학습하지 않음")
        sp.add_argument("--gender", choices=["male", "female"], help="같은 이름 용어의 남녀 정의 중 선택")
        sp.add_argument("--fast", action="store_true",
                        help="빠른 모드: 용어집 규칙으로 진행, Hub 호출은 후보당 편집 1 + 검증 1 (동시 1건 키용)")

    r = sub.add_parser("run", help="자유 요구사항으로 실행")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--request", help="원하는 것 (한국어 그대로)")
    g.add_argument("--request-file", help="요구사항이 적힌 텍스트 파일")
    r.add_argument("--photo", help="입력 사진 (있으면 편집, 영상이면 첫 프레임)")
    r.add_argument("--output", choices=["image", "video"], default="image")
    r.add_argument("--seconds", type=int, help="영상 길이(초)")
    r.add_argument("--name", default="run", help="결과 폴더 이름")
    common(r)
    r.set_defaults(func=cmd_run)

    tm = sub.add_parser("terms", help="미용 용어 사전 보기·학습·참고사진 반영")
    tm.add_argument("action", choices=["list", "show", "learn", "photos"],
                    help="list: 전체 / show: 정의 보기 / learn: 모르는 용어를 속성으로 분해 / photos: 참고사진으로 보강")
    tm.add_argument("term", nargs="?", help="용어 (예: 애즈펌)")
    tm.add_argument("--gender", choices=["male", "female"])
    tm.set_defaults(func=cmd_terms)

    h = sub.add_parser("hair", help="추천 4개 헤어스타일 일괄 실행 + 비교 이미지")
    h.add_argument("--photo", required=True)
    h.add_argument("--styles", default="all", help="예: 가르마펌,애즈펌 (기본 all)")
    common(h)
    h.set_defaults(func=cmd_hair)

    a = p.parse_args()
    try:
        a.func(a)
    except HubError as e:
        sys.exit(f"[오류] {e}")
    except KeyboardInterrupt:
        sys.exit("\n중단됨")


if __name__ == "__main__":
    main()
