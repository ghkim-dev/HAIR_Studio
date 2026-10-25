"""헤어 용어 이해(그라운딩) 계층 — 속성 기반.

구조 (닫힌 속성 어휘 + 열린 용어집):
  knowledge/attributes.json      닫힌 속성 어휘: 길이·가르마·앞머리·컬·옆머리·볼륨… 각 값의 한국어/영어 묘사
  knowledge/corpus/male.json     남성 용어집 (공개 자료 조사, 출처 포함, 용어 → 속성 조합)
  knowledge/corpus/female.json   여성 용어집
  knowledge/hair_terms.json      직접 고친/추가한 용어 (용어집보다 우선)
  knowledge/learned_terms.json   용어집에 없어서 LLM이 속성 어휘 안에서 분해한 초안 (검토 대상)
  knowledge/images/<용어>/        참고 사진 (있으면 VLM이 정의 보강 + 검증 때 비교)

처리:
  1) 검색  : 별칭 매칭 (성별 지정 시 해당 용어집 우선)                     ← RAVEL: 지식에서 속성 검색
  2) 분해  : 모르는 용어는 LLM이 '속성 어휘 안에서만' 값을 골라 분해 + 신뢰도  ← 닫힌 어휘라 검증·수정 가능
  3) 시각  : 참고 사진이 있으면 VLM이 속성 값을 사진 기준으로 고침          ← ImageRAG: 참조 이미지로 보완
  4) 렌더  : 속성 → 영어 시각 묘사(이미지 모델용) + 검증 질문(VLM용) 자동 생성
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import prompts as P
from .utils import extract_json, log

ROOT = Path(__file__).resolve().parent.parent / "knowledge"
ATTR_FILE = ROOT / "attributes.json"
CORPUS_DIR = ROOT / "corpus"
CUSTOM_FILE = ROOT / "hair_terms.json"
LEARNED_FILE = ROOT / "learned_terms.json"
IMAGES_DIR = ROOT / "images"
GALLERY_DIR = ROOT / "gallery"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
CATEGORY_KO = {"cut": "컷", "perm": "펌", "treatment": "펌", "bangs": "앞머리", "part": "앞머리",
               "styling": "스타일링", "updo": "묶음"}


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_·]", "", str(s)).lower()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log(f"    (읽기 실패 {path.name}: {e})")
        return {}


# ---------------------------------------------------------------- 속성 어휘

@lru_cache(maxsize=1)
def vocab() -> dict:
    return _read_json(ATTR_FILE).get("attributes", {})


def clean_attrs(attrs: dict | None) -> dict:
    """어휘에 없는 키·값은 버린다 (LLM 분해 결과 검증용)."""
    v = vocab()
    return {k: val for k, val in (attrs or {}).items() if k in v and val in v[k]["values"]}


def attrs_en(attrs: dict) -> list[str]:
    v = vocab()
    out = []
    for k, val in attrs.items():
        d = v.get(k, {}).get("values", {}).get(val)
        if not d:
            continue
        en = d["en"]
        if k == "curl_size" and val != "none":
            en = f"{d['en']} curls"
        elif k == "curl_zone":
            en = f"curls {d['en']}"
        elif k == "bangs_length":
            en = f"bangs {d['en']}"
        out.append(en)
    return out


def attrs_ko(attrs: dict) -> list[str]:
    v = vocab()
    return [f"{v[k]['ko']}: {v[k]['values'][val]['ko']}" for k, val in attrs.items()
            if k in v and val in v[k]["values"]]


def render(entry: dict) -> dict:
    """속성 → 시각 묘사·검증 질문을 채운 완성 항목."""
    e = dict(entry)
    a = clean_attrs(e.get("attrs"))
    e["attrs"] = a
    e["attrs_en"] = attrs_en(a)
    e["attrs_ko"] = attrs_ko(a)
    if not e.get("visual_en"):
        e["visual_en"] = "; ".join(e["attrs_en"])
    checks = list(e.get("checks") or [])
    # 속성 하나하나를 검증 질문으로 (중요 속성 위주, 최대 6개)
    priority = ["length", "parting", "bangs", "texture", "sides", "forehead", "layers", "ends", "volume",
                "curl_zone", "updo", "back", "bangs_length", "silhouette", "top", "finish", "curl_size"]
    for k in [k for k in priority if k in a][:6]:
        checks.append(f"Does the RESULT hair show this: {attrs_en({k: a[k]})[0]}?")
    e["checks"] = checks
    e.setdefault("must_not", [])
    return e


# ---------------------------------------------------------------- 용어집 로드

@lru_cache(maxsize=1)
def _load_all() -> tuple:
    """(gender, term) → entry. 우선순위: 직접 추가 > 용어집 > 학습 초안."""
    entries: dict[tuple, dict] = {}
    for name, e in _read_json(LEARNED_FILE).get("terms", {}).items():
        entries[("learned", name)] = dict(e, term=name, source="learned")
    for f in sorted(CORPUS_DIR.glob("*.json")) if CORPUS_DIR.is_dir() else []:
        data = _read_json(f)
        file_gender = data.get("gender", "unisex")
        for e in data.get("terms", []):
            entries[(file_gender, e["term"])] = dict(e, source="corpus", corpus_gender=file_gender)
    for name, e in _read_json(CUSTOM_FILE).get("terms", {}).items():
        entries[("custom", name)] = dict(e, term=name, source="custom")
    return tuple(entries.items())


def reload() -> None:
    _load_all.cache_clear()
    vocab.cache_clear()


def entries(gender: str | None = None) -> dict:
    """성별에 맞는 용어 사전 {term: entry}.
    우선순위(뒤가 이김): 학습 초안 < 다른 성별 용어집 < 지정 성별 용어집 < 직접 추가(hair_terms.json)."""
    items = list(_load_all())
    out: dict = {}
    rank = lambda e: {"learned": 0, "corpus": 1, "custom": 3}.get(e.get("source"), 1) + (  # noqa: E731
        1 if e.get("source") == "corpus" and gender and e.get("corpus_gender") == gender else 0)
    for (_, name), e in sorted(items, key=lambda kv: rank(kv[1])):
        # 성별을 지정하면 다른 성별 용어집은 제외 ("C컬로"가 남성 요청에서 여성 C컬펌으로 잡히는 것 방지)
        if gender in ("male", "female") and e.get("source") == "corpus" and e.get("corpus_gender") != gender:
            continue
        out[name] = e
    return out


def item_id(e: dict) -> str:
    """갤러리·썸네일 파일명으로 쓰는 id (윈도우 파일명에 못 쓰는 문자 제거)."""
    raw = f"{e.get('corpus_gender', e.get('gender', 'unisex'))}_{e['term']}"
    return re.sub(r'[\\/:*?"<>|]', "-", raw).replace(" ", "_")


def find_by_id(style_id: str) -> dict | None:
    for (_, _), e in _load_all():
        if item_id(e) == style_id or e["term"] == style_id:
            return e
    return None


def match(request: str, terms: dict) -> list[str]:
    """요청 문장에서 용어 찾기 (긴 별칭 우선, 겹치는 구간은 한 번만)."""
    text = _norm(request)
    cands = []
    for name, e in terms.items():
        for alias in [name] + list(e.get("aliases", [])):
            a = _norm(alias)
            if len(a) >= 2:
                cands.append((len(a), 0 if a == _norm(name) else 1, a, name))
    found, used = [], [False] * len(text)
    for _, _, a, name in sorted(cands, key=lambda c: (-c[0], c[1], c[2])):
        start = text.find(a)
        while start != -1:
            span = range(start, start + len(a))
            if not any(used[i] for i in span):
                for i in span:
                    used[i] = True
                if name not in found:
                    found.append(name)
            start = text.find(a, start + 1)
    return found


def ref_images(term: str) -> list[Path]:
    d = IMAGES_DIR / term
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)[:3] if d.is_dir() else []


def gallery() -> list[dict]:
    """박람회 화면용 스타일 목록."""
    out, seen = [], set()
    for (_, _), e in _load_all():
        if e.get("source") == "learned":
            continue
        e = render(e)
        iid = item_id(e)
        if iid in seen:
            continue
        seen.add(iid)
        thumb = GALLERY_DIR / f"{iid}.png"
        out.append({"id": iid, "term": e["term"], "gender": e.get("corpus_gender", e.get("gender", "unisex")),
                    "category": CATEGORY_KO.get(e.get("category", ""), "스타일링"),
                    "description_ko": e.get("description_ko", ""), "popularity": e.get("popularity", "medium"),
                    "attrs_ko": e["attrs_ko"], "thumb": str(thumb) if thumb.exists() else None})
    return out


# ---------------------------------------------------------------- LLM 연동

def _vocab_prompt() -> str:
    v = vocab()
    return "\n".join(f"- {k} ({d['ko']}): " + ", ".join(f"{vk}={vd['ko']}" for vk, vd in d["values"].items())
                     for k, d in v.items())


def decompose(hub, term: str, context: str = "", gender: str | None = None) -> dict:
    """용어집에 없는 용어를 닫힌 속성 어휘 안에서 분해."""
    sys_prompt = P.TERM_DECOMPOSE.format(vocab=_vocab_prompt())
    text = hub.chat([{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": f"TERM: {term}\nGENDER: {gender or 'unknown'}\n"
                                                 f"USER SENTENCE: {context or term}"}], temperature=0.1)
    raw = extract_json(text)
    e = {"term": term, "aliases": raw.get("aliases", []), "gender": gender or raw.get("gender", "unisex"),
         "category": raw.get("category", "cut"), "attrs": clean_attrs(raw.get("attrs")),
         "must_not": raw.get("must_not", []), "description_ko": raw.get("description_ko", ""),
         "confidence": raw.get("confidence", "low"), "known": raw.get("known", True),
         "verified": False, "dropped_attrs": {k: v for k, v in (raw.get("attrs") or {}).items()
                                              if k not in clean_attrs(raw.get("attrs"))}}
    return e


def ground_from_images(hub, term: str, entry: dict) -> dict:
    """참고 사진을 VLM이 보고 속성 값을 고침 (사진이 텍스트 정의보다 우선)."""
    imgs = ref_images(term)
    if not imgs:
        return entry
    text = hub.vision(f"TERM: {term}\nCURRENT ATTRIBUTES: {json.dumps(entry.get('attrs', {}), ensure_ascii=False)}",
                      imgs, system=P.TERM_FROM_IMAGES.format(vocab=_vocab_prompt()))
    upd = extract_json(text)
    merged = dict(entry)
    merged["attrs"] = {**entry.get("attrs", {}), **clean_attrs(upd.get("attrs"))}
    if upd.get("must_not"):
        merged["must_not"] = list(dict.fromkeys(list(entry.get("must_not", [])) + upd["must_not"]))
    merged["visual_en"] = ""            # 속성에서 다시 렌더
    merged["image_grounded"] = [p.name for p in imgs]
    return merged


def find_unknown(hub, request: str, known: list[str]) -> list[str]:
    text = hub.chat([{"role": "system", "content": P.TERM_EXTRACT},
                     {"role": "user", "content": f"REQUEST: {request}\nALREADY KNOWN: {', '.join(known) or '-'}"}],
                    temperature=0.0)
    try:
        data = extract_json(text)
    except ValueError:
        return []
    items = data.get("unknown_terms", []) if isinstance(data, dict) else data
    kn = {_norm(k) for k in known}
    return [t for t in (str(x).strip() for x in items) if t and _norm(t) not in kn][:4]


def save_learned(entry: dict) -> None:
    data = _read_json(LEARNED_FILE) or {
        "_about": "용어집에 없어 LLM이 속성 어휘 안에서 분해한 초안. 확인 후 hair_terms.json으로 옮기면 확정됩니다.",
        "terms": {}}
    data.setdefault("terms", {})[entry["term"]] = {k: v for k, v in entry.items()
                                                  if k not in ("term", "source", "refs", "attrs_en", "attrs_ko")}
    ROOT.mkdir(parents=True, exist_ok=True)
    LEARNED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    reload()


def ground(hub, request: str, learn: bool = True, gender: str | None = None,
           style_id: str | None = None) -> list[dict]:
    """요청 → 용어별 속성 정의 목록."""
    terms = entries(gender)
    picked: list[dict] = []
    if style_id:
        e = find_by_id(style_id)
        if e:
            picked.append(e)
    names = [n for n in match(request, terms) if not any(p["term"] == n for p in picked)]
    picked += [terms[n] for n in names]
    out = []
    for e in picked:
        e = dict(e)
        if ref_images(e["term"]):
            e = ground_from_images(hub, e["term"], e)
        out.append(render(e))
    if learn:
        known = [e["term"] for e in out]
        for t in find_unknown(hub, request, known):
            try:
                e = decompose(hub, t, request, gender)
            except ValueError:
                continue
            if not e["attrs"]:
                log(f"    ('{t}'은(는) 속성으로 분해하지 못해 건너뜀)")
                continue
            if ref_images(t):
                e = ground_from_images(hub, t, e)
            save_learned(e)
            out.append(render(dict(e, source="decomposed")))
    for e in out:
        e["refs"] = [str(p) for p in ref_images(e["term"])]
    return out


def as_context(grounded: list[dict]) -> str:
    """계획·설계 에이전트에 넣을 '권위 있는 용어 지식' 블록."""
    if not grounded:
        return "(no salon terms detected)"
    return "\n".join(json.dumps({
        "term": e["term"], "category": e.get("category"), "means_visually": e.get("visual_en"),
        "attributes": e.get("attrs_en"), "must_not": e.get("must_not"),
        "confidence": e.get("confidence", "high"),
    }, ensure_ascii=False) for e in grounded)
