"""파이프라인 단계별 에이전트. 각 에이전트는 Hub의 LLM/VLM 한 번 호출 + JSON 파싱."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompts as P
from . import lexicon
from .hub import Hub
from .utils import extract_json, log


def _ask_json(hub: Hub, system: str, user: str, images: list[Path] | None = None,
              temperature: float = 0.3, retries: int = 2):
    """JSON을 돌려받을 때까지 최대 retries번 재시도 (형식 오류 피드백 포함)."""
    last_err = ""
    for _ in range(retries + 1):
        extra = f"\n\n이전 응답이 올바른 JSON이 아니었습니다({last_err}). JSON만 출력하세요." if last_err else ""
        if images:
            text = hub.vision(user + extra, images, system=system, temperature=temperature)
        else:
            text = hub.chat([{"role": "system", "content": system},
                             {"role": "user", "content": user + extra}], temperature=temperature)
        try:
            return extract_json(text)
        except ValueError as e:
            last_err = str(e)[:120]
    raise RuntimeError(f"에이전트가 JSON을 반환하지 못했습니다: {last_err}")


def observe(hub: Hub, photo: Path) -> dict:
    """Stage 0 — 입력 사진에서 보존해야 할 정체성 단서를 뽑는다."""
    return _ask_json(hub, P.OBSERVE, "Describe this photo.", images=[photo])


def plan(hub: Hub, request: str, observation: dict | None, want: str,
         terms: list[dict] | None = None) -> dict:
    """Stage 1 — 요구사항을 원자적 하위 편집 + 보존 목록으로 분해 (X-Planner).
    용어는 Stage 0.5(lexicon)에서 이미 시각 속성으로 풀린 것을 근거로 쓴다."""
    terms = terms or []
    user = (f"USER REQUEST (Korean ok):\n{request}\n\n"
            f"TERM KNOWLEDGE (authoritative, one JSON per line):\n{lexicon.as_context(terms)}\n\n"
            f"REQUESTED OUTPUT: {want}\n\n"
            f"PHOTO OBSERVATION:\n{json.dumps(observation, ensure_ascii=False, indent=1) if observation else 'no input photo'}")
    spec = _ask_json(hub, P.PLAN, user)
    spec["output"] = want if want in ("image", "video") else spec.get("output", "image")
    spec.setdefault("sub_edits", [])
    spec.setdefault("preserve", [])
    # 이후 단계(체크리스트·설계·검증)가 같은 용어 정의를 보도록 계획에 붙여 둔다
    spec["term_knowledge"] = [{"term": t["term"], "means_visually": t.get("visual_en"),
                               "must_not": t.get("must_not", [])} for t in terms]
    return spec


def checklist(hub: Hub, spec: dict, terms: list[dict] | None = None) -> list[dict]:
    """Stage 2 — 검증용 yes/no 질문 생성 (TIFA).
    용어 사전의 검증 질문과 '하면 안 되는 모양'을 그대로 넣어, 검증도 용어를 이해한 채로 하게 한다."""
    data = _ask_json(hub, P.CHECKLIST, json.dumps(spec, ensure_ascii=False, indent=1))
    qs = data.get("questions", data if isinstance(data, list) else [])
    for i, q in enumerate(qs, 1):
        q["id"] = f"q{i}"
        q.setdefault("kind", "edit")
        q["weight"] = float(q.get("weight", 1.0))
    for t in terms or []:
        for text in t.get("checks", []):
            qs.append({"id": "", "kind": "edit", "ref": t["term"], "question": text, "weight": 1.5})
        bad = [b for b in t.get("must_not", []) if "ID photo" not in b]
        if bad:
            qs.append({"id": "", "kind": "quality", "ref": t["term"], "weight": 1.5,
                       "question": f"Is the hair free of all of these wrong shapes: {'; '.join(bad)}?"})
    # 머리 편집이면 자연스러움 기본 검사를 항상 추가 (VLM이 관대하게 넘기는 실패 유형 방지)
    if any(str(e.get("region", "")).startswith("hair") or "hair" in str(e.get("type", ""))
           for e in spec.get("sub_edits", [])):
        for text, w in P.STANDARD_HAIR_CHECKS:
            qs.append({"id": "", "kind": "quality", "ref": "standard", "question": text, "weight": w})
    for i, q in enumerate(qs, 1):
        q["id"] = f"q{i}"
    return qs


def design(hub: Hub, spec: dict, memory: list[dict], n: int, mode: str) -> list[dict]:
    """Stage 3 — 메모리(이전 라운드 반성)를 반영해 후보 프롬프트 n개 작성 (Idea2Img)."""
    kind = {"edit": "instruction-based image editing", "generate": "text-to-image",
            "video": "image-to-video / text-to-video"}[mode]
    system = P.DESIGN.format(model_kind=kind, n=n, model_hint=P.DESIGN_HINT[mode])
    mem = [{"round": m["round"], "prompt": m["prompt"], "score": m["score"],
            "failed": m["failed"], "reflection": m["reflection"]} for m in memory]
    user = (f"PLAN:\n{json.dumps(spec, ensure_ascii=False, indent=1)}\n\n"
            f"MEMORY (previous rounds, oldest first):\n{json.dumps(mem, ensure_ascii=False, indent=1) if mem else '[] (first round)'}")
    data = _ask_json(hub, system, user, temperature=0.8)
    items = data.get("prompts", data if isinstance(data, list) else [])
    out = [it if isinstance(it, dict) else {"prompt": str(it), "rationale": ""} for it in items]
    return [o for o in out if o.get("prompt")][:n] or [{"prompt": spec.get("intent", ""), "rationale": "fallback"}]


def verify(hub: Hub, questions: list[dict], result_images: list[Path], original: Path | None,
           request: str = "", spec: dict | None = None, refs: list[Path] | None = None) -> dict:
    """Stage 4 — VLM이 근거를 먼저 쓰고 체크리스트에 답 + 전체 자연스러움/요청 일치 평가 (TIFA 확장).

    점수 = 0.5 × 체크리스트 가중 통과율 + 0.25 × 자연스러움 + 0.25 × 요청 일치
    자연스러움이나 요청 일치가 2 이하면 점수 상한 0.5 → 그 라운드에서 끝나지 않고 반성 루프로 넘어감.
    """
    spec = spec or {}
    qtext = "\n".join(f'{q["id"]} [{q["kind"]}] {q["question"]}' for q in questions)
    target = " | ".join(f'{t["term"]} = {t.get("means_visually")}' for t in spec.get("term_knowledge", [])) \
        or " ".join(str(e.get("instruction", "")) for e in spec.get("sub_edits", []))
    refs = list(refs or [])[:2]
    images = ([original] if original else []) + refs + result_images
    ref_note = (f"Images 2..{1 + len(refs)} are REFERENCE photos of the target hairstyle chosen by the user "
                "(other people) — compare the RESULT's hair shape to them, not the faces.") if refs else ""
    system = P.VERIFY.format(questions=qtext, request=request or spec.get("intent", ""),
                             target=target or "-", ref_note=ref_note)
    data = _ask_json(hub, system, "Judge the RESULT.", images=images, temperature=0.0)

    answers, evidence = {}, {}
    for k, v in (data.get("answers") or {}).items():
        if isinstance(v, dict):
            answers[k] = str(v.get("answer", "no")).strip().lower()
            evidence[k] = v.get("evidence", "")
        else:
            answers[k] = str(v).strip().lower()
    ok = lambda q: answers.get(q["id"], "no").startswith("y")  # noqa: E731
    total = sum(q["weight"] for q in questions) or 1.0
    check = sum(q["weight"] for q in questions if ok(q)) / total
    failed = [dict(q, evidence=evidence.get(q["id"], "")) for q in questions if not ok(q)]

    def rate(key):
        try:
            return max(1, min(5, int(float(data.get(key, 1)))))
        except (TypeError, ValueError):
            return 1
    nat, match = rate("naturalness"), rate("request_match")
    score = 0.5 * check + 0.25 * (nat - 1) / 4 + 0.25 * (match - 1) / 4
    # 정체성(preserve, weight≥2) 실패는 하드 페널티: 아무리 편집이 잘돼도 다른 사람이면 실패
    identity_fail = any(q["kind"] == "preserve" and q["weight"] >= 2 for q in failed)
    if identity_fail:
        score *= 0.6
    if nat <= 2 or match <= 2:
        score = min(score, 0.5)
    return {"score": round(score, 3), "checklist": round(check, 3), "naturalness": nat, "request_match": match,
            "answers": answers, "failed": failed, "identity_fail": identity_fail, "notes": data.get("notes", "")}


def reflect(hub: Hub, best: dict) -> dict:
    """Stage 5 — 실패 원인 검증 → 수정 경로 라우팅 → 다음 라운드용 반성문 (GenMAC + Reflect-DiT)."""
    user = json.dumps({
        "prompt": best["prompt"], "score": best["score"],
        "naturalness_1to5": best.get("naturalness"), "request_match_1to5": best.get("request_match"),
        "failed_questions": [f'{q["question"]} (seen: {q.get("evidence", "")})' for q in best["failed"]],
        "judge_notes": best["notes"],
    }, ensure_ascii=False, indent=1)
    try:
        return _ask_json(hub, P.REFLECT, user)
    except RuntimeError as e:
        log(f"    (반성 단계 실패, 실패 질문으로 대체: {e})")
        return {"failures": [q["question"] for q in best["failed"]], "route": "strengthen_edit",
                "reflection": "Fix: " + "; ".join(q["question"] for q in best["failed"]),
                "summary_ko": "반성 단계 응답 실패 — 실패 항목을 그대로 다음 라운드에 반영"}


# ================================================================ 빠른 모드 (용어집 기반, LLM 호출 0회)
# 동시 1건 키처럼 호출 수가 귀할 때: 계획·체크리스트·설계·반성을 용어집 속성에서 규칙으로 만든다.
# Hub 호출은 후보마다 이미지 편집 1회 + 비전 검증 1회만 남는다.

PRESERVE_DEFAULT = ["the same person's face and identity", "facial expression", "glasses if any",
                    "clothing", "background", "framing and camera angle", "lighting", "skin tone"]


def plan_rule(request: str, terms: list[dict], want: str = "image") -> dict:
    sub_edits = []
    for i, t in enumerate(terms, 1):
        sub_edits.append({"id": f"e{i}", "type": "hair_style", "region": "hair",
                          "instruction": f"{t.get('visual_en', '')} ({'; '.join(t.get('attrs_en', [])[:6])})"})
    return {"intent": f"Change only the hairstyle: {request}", "output": want, "sub_edits": sub_edits,
            "preserve": PRESERVE_DEFAULT, "style": "photorealistic photo", "video": None,
            "assumptions": ["용어집 정의를 그대로 사용 (빠른 모드)"],
            "term_knowledge": [{"term": t["term"], "means_visually": t.get("visual_en"),
                                "must_not": t.get("must_not", [])} for t in terms]}


def checklist_rule(terms: list[dict]) -> list[dict]:
    qs = [{"kind": "preserve", "ref": "face", "weight": 2.0,
           "question": "Is it clearly the same person as in the original (same face, eyes, nose, mouth)?"},
          {"kind": "preserve", "ref": "rest", "weight": 1.0,
           "question": "Are the glasses (if any), clothing, background and framing unchanged from the original?"}]
    for t in terms:
        for text in t.get("checks", [])[:5]:
            qs.append({"kind": "edit", "ref": t["term"], "question": text, "weight": 1.5})
        bad = list(t.get("must_not", []))[:5]
        if bad:
            qs.append({"kind": "quality", "ref": t["term"], "weight": 1.5,
                       "question": f"Is the hair free of all of these wrong shapes: {'; '.join(bad)}?"})
    for text, w in P.STANDARD_HAIR_CHECKS:
        qs.append({"kind": "quality", "ref": "standard", "question": text, "weight": w})
    for i, q in enumerate(qs, 1):
        q["id"] = f"q{i}"
    return qs


SIDE_NEGATIVES = "shaved sides, undercut, fade, visible scalp on the sides"
BASE_NEGATIVES = ("different person, changed face, changed glasses, changed clothes, changed background, "
                  "big spiral curls, one-sided clump of hair, wig-like hair, blurry, distorted")


def default_negative(terms: list[dict]) -> str:
    return design_rule(terms, [], 1)[0]["negative"]


def design_rule(terms: list[dict], memory: list[dict], n: int, request: str = "") -> list[dict]:
    """속성 → 편집 프롬프트 (짧은 긍정문) + 금지 모양은 negative_prompt로 분리.
    이미지 모델은 본문에 적힌 단어를 그리는 경향이 있어서 'shaved sides' 같은 금지어를 본문에 쓰지 않는다."""
    look = " ".join(t.get("visual_en", "") for t in terms).strip()
    details = "; ".join(a for t in terms for a in t.get("attrs_en", [])[:6])
    keep = "Keep the same face, expression, glasses, clothes, background and framing. Photorealistic."
    wants_short_sides = any(t.get("attrs", {}).get("sides") in ("skin_fade", "clipped") or
                            t.get("attrs", {}).get("length") == "buzz" for t in terms)
    neg = [re.sub(r"\s*\([^)]*\)", "", b) for t in terms for b in t.get("must_not", [])]   # 괄호 속 한국어 설명 제거
    neg += [BASE_NEGATIVES] + ([] if wants_short_sides else [SIDE_NEGATIVES])
    if memory and memory[-1].get("negative_extra"):
        neg.append(memory[-1]["negative_extra"])
    negative = ", ".join(dict.fromkeys(x.strip() for x in ", ".join(neg).split(",") if x.strip()))

    extra = request.split("추가 요청:", 1)[1].strip() if "추가 요청:" in request else ""
    tail = f" Also: {extra}." if extra else ""
    if memory and memory[-1].get("reflection"):
        tail += " " + memory[-1]["reflection"]
    variants = [
        ("기본: 용어집 묘사 그대로", f"Change only the hairstyle to this: {look} {keep}{tail}"),
        ("절제: 자연스럽고 은은하게", f"Restyle only the hair into a subtle, natural, real-salon version of this look: "
                                   f"{look} {keep}{tail}"),
        ("강조: 핵심 속성을 또렷하게", f"Change only the hair so that: {details}. {keep}{tail}"),
    ]
    return [{"prompt": p, "rationale": r, "negative": negative} for r, p in variants[:max(1, n)]]


def reflect_rule(best: dict) -> dict:
    """검증 실패 항목 → 다음 라운드 지시 (LLM 없이).
    요청한 특징이 약하면 본문에 긍정문으로, 잘못된 모양이 나왔으면 negative_prompt에 추가."""
    failed = best.get("failed", [])
    if not failed:
        return {"route": "fix_quality", "reflection": "Make the hair look natural and realistic.",
                "negative_extra": "", "summary_ko": "자연스러움을 높이도록 다시 시도"}
    preserve = [q for q in failed if q["kind"] == "preserve"]
    wrong = [q for q in failed if "free of all of these wrong shapes" in q["question"] or q["ref"] == "standard"]
    weak = [q for q in failed if q["question"].startswith("Does the RESULT hair show this:")]
    want = "; ".join(q["question"].split(":", 1)[1].rstrip("?").strip() for q in weak[:3])
    neg = "; ".join(q["question"].split(":", 1)[1].rstrip("?").strip() for q in wrong
                    if "free of all" in q["question"])
    route = "protect_identity" if preserve else ("tone_down" if wrong else "strengthen_edit")
    reflection = (f"Make sure the hair clearly shows: {want}." if want else "") + \
                 (" Keep the person exactly the same as the original." if preserve else "")
    return {"route": route, "reflection": reflection.strip(), "negative_extra": neg.replace(";", ","),
            "summary_ko": {"protect_identity": "얼굴·옷이 바뀌어 원본 유지를 강화",
                           "tone_down": "잘못된 모양이 나와 금지 조건(negative)을 강화",
                           "strengthen_edit": "요청한 특징이 약해서 더 또렷하게"}[route]}
