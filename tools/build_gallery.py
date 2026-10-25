"""박람회 스타일 갤러리 썸네일을 Hub의 텍스트→이미지 모델(Qwen-Image)로 미리 만들어 둔다.

  python tools/build_gallery.py                    # 전체 (이미 있는 건 건너뜀)
  python tools/build_gallery.py --gender female --limit 20
  python tools/build_gallery.py --only 허쉬컷,애즈펌 --force

용어집의 속성 정의 → 가상의 모델 사진. 썸네일 자체가 "용어를 시각 속성으로 이해했다"는 시연이 된다.
결과: knowledge/gallery/<id>.png (가로세로 384px)
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from pipeline import Hub, HubError, lexicon  # noqa: E402
from pipeline.utils import log  # noqa: E402

PERSON = {
    "male": "a Korean man in his twenties",
    "female": "a Korean woman in her twenties",
    "unisex": "a Korean person in their twenties",
}


def prompt_for(item: dict, entry: dict) -> str:
    e = lexicon.render(entry)
    avoid = ", ".join(e.get("must_not", [])[:3])
    return (f"Hair salon catalog photo of {PERSON.get(item['gender'], PERSON['unisex'])}, front view, "
            f"head and shoulders, plain light warm-gray studio background, soft even lighting, photorealistic. "
            f"Hairstyle: {e['visual_en']} Details: {'; '.join(e['attrs_en'][:6])}. "
            f"Natural realistic hair, plain black t-shirt, no text, no watermark."
            + (f" Avoid: {avoid}." if avoid else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gender", choices=["male", "female"])
    ap.add_argument("--only", help="쉼표로 구분한 용어")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="이미 있는 썸네일도 다시 생성")
    ap.add_argument("--workers", type=int, default=None,
                    help="동시 생성 수 (기본: 키 동시 한도 OASH_MAX_CONCURRENCY)")
    a = ap.parse_args()

    hub = Hub()
    from pipeline.hub import MAX_CONCURRENCY
    a.workers = a.workers or MAX_CONCURRENCY
    if not hub.models().image:
        sys.exit("텍스트→이미지 모델이 없습니다 (.env의 OASH_IMAGE_MODEL 지정)")
    items = lexicon.gallery()
    if a.gender:
        items = [i for i in items if i["gender"] == a.gender]
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        items = [i for i in items if i["term"] in want]
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order.get(i["popularity"], 1))
    out_dir = lexicon.GALLERY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [i for i in items if a.force or not (out_dir / f"{i['id']}.png").exists()]
    if a.limit:
        todo = todo[: a.limit]
    log(f"썸네일 {len(todo)}개 생성 (모델 {hub.models().image}) → {out_dir}")

    def one(item):
        entry = lexicon.find_by_id(item["id"])
        tmp = out_dir / f"{item['id']}.full.png"
        hub.generate_image(prompt_for(item, entry), tmp, size="1024x1024")
        from PIL import Image
        im = Image.open(tmp).convert("RGB")
        im.thumbnail((384, 384))
        im.save(out_dir / f"{item['id']}.png")
        tmp.unlink(missing_ok=True)
        return item["term"]

    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, i): i for i in todo}
        for f in as_completed(futs):
            i = futs[f]
            try:
                f.result()
                done += 1
                log(f"  ✔ {i['term']} ({i['gender']})  {done}/{len(todo)}")
            except HubError as e:
                log(f"  ✘ {i['term']}: {e}")
    log("완료. 박람회 서버를 다시 열면 갤러리에 사진이 보입니다.")


if __name__ == "__main__":
    main()
