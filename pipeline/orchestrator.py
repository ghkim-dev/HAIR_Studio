"""요구사항 → 관찰 → 용어 이해 → 계획 → 체크리스트 → (설계 → 생성 → 검증 → 선택 → 반성)×라운드 루프.

on_event 콜백으로 단계별 진행 상황을 내보낸다 (박람회 화면이 실시간으로 보여줌).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import agents, lexicon
from .hub import Hub, HubError
from .report import write_report
from .utils import log, video_frames


@dataclass
class RunConfig:
    request: str
    photo: Path | None = None
    output: str = "image"          # image | video
    rounds: int = 3                # 최대 반복 라운드
    candidates: int = 2            # 라운드당 후보 수 (best-of-N, 동시에 생성)
    threshold: float = 0.9         # 이 점수 이상이면 조기 종료
    size: str | None = None
    seconds: int | None = None
    plan_only: bool = False        # 이해 단계만 실행하고 생성은 하지 않음
    learn_terms: bool = True       # 용어집에 없는 용어를 속성 어휘로 분해해 learned_terms.json에 저장
    gender: str | None = None      # male | female | None — 같은 이름 용어의 남녀 정의 선택
    style_id: str | None = None    # 갤러리에서 고른 스타일 id (용어집 항목)
    fast: bool = False             # 빠른 모드: 용어집 규칙으로 계획·체크리스트·설계·반성 (Hub 호출 = 후보당 편집+검증)
    out_root: Path = Path("output")
    name: str = "run"
    on_event: Callable[[dict], None] | None = None


SOURCE_KO = {"corpus": "용어집", "custom": "직접 추가", "learned": "학습 초안", "decomposed": "새로 분해"}


def run(cfg: RunConfig, hub: Hub | None = None) -> dict:
    def emit(stage: str, message: str, **extra) -> None:
        if cfg.on_event:
            try:
                cfg.on_event({"type": "stage", "stage": stage, "message": message, **extra})
            except Exception as e:  # 화면 쪽 오류가 생성을 멈추지 않게
                log(f"    (이벤트 전송 실패: {e})")

    try:
        return _run(cfg, hub or Hub(), emit)
    except Exception as e:
        emit("error", f"오류: {e}")
        raise


def _run(cfg: RunConfig, hub: Hub, emit) -> dict:
    m = hub.models()
    run_dir = cfg.out_root / f"{datetime.now():%Y%m%d_%H%M%S}_{cfg.name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    mode = "video" if cfg.output == "video" else ("edit" if cfg.photo else "generate")

    log(f"\n=== {cfg.name} | 출력={cfg.output} | 저장={run_dir}")
    log(f"    모델: text={m.text} vision={m.vision} "
        f"{'video='+m.video if mode == 'video' else ('edit='+m.image_edit if mode == 'edit' else 'image='+m.image)}")

    state: dict = {"config": {k: str(v) for k, v in cfg.__dict__.items() if k != "on_event"},
                   "models": {k: v for k, v in m.__dict__.items() if k != "available"},
                   "started": datetime.now().isoformat(timespec="seconds"), "rounds": []}

    def save():
        (run_dir / "run.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str),
                                          encoding="utf-8")
        write_report(state, run_dir)

    # 빠른 모드 가능 여부: 용어집에서 용어가 잡히면 LLM 없이 진행
    fast = False
    if cfg.fast:
        pre = lexicon.ground(hub, cfg.request, learn=False, gender=cfg.gender, style_id=cfg.style_id)
        fast = bool(pre)
        log("    ⚡ 빠른 모드: 용어집 규칙 사용 (Hub 호출 = 후보당 편집 1 + 검증 1)" if fast
            else "    (용어집에서 용어를 못 찾아 전체 모드로 진행)")
    state["fast"] = fast

    # Stage 0 — 관찰
    if cfg.photo and not fast:
        log("[0] 관찰: 입력 사진의 정체성 단서 추출")
        emit("observe", "사진에서 얼굴·안경·옷처럼 지켜야 할 특징을 찾는 중")
        state["observation"] = agents.observe(hub, cfg.photo)

    # Stage 0.5 — 용어 이해
    log("[0.5] 용어 이해: 헤어 용어 → 시각 속성")
    emit("terms", "헤어 용어를 눈에 보이는 속성으로 풀이하는 중")
    terms = pre if fast else lexicon.ground(hub, cfg.request, learn=cfg.learn_terms, gender=cfg.gender,
                                            style_id=cfg.style_id)
    state["terms"] = terms
    for t in terms:
        tag = SOURCE_KO.get(t.get("source"), t.get("source"))
        img = f" + 참고사진 {len(t['refs'])}장" if t.get("refs") else ""
        conf = f", 신뢰도 {t['confidence']}" if t.get("confidence") and t.get("confidence") != "high" else ""
        log(f"    · {t['term']} [{tag}{img}{conf}] " + " / ".join(t.get("attrs_ko", [])[:5]))
    if not terms:
        log("    (인식된 헤어 용어 없음 — 문장 그대로 해석)")
    emit("terms", f"용어 {len(terms)}개 이해 완료" if terms else "인식된 용어 없음 — 문장 그대로 해석",
         terms=[{"term": t["term"], "source": t.get("source"), "visual_en": t.get("visual_en"),
                 "attrs_ko": t.get("attrs_ko", []), "confidence": t.get("confidence", "high")} for t in terms])
    refs = [Path(r) for t in terms for r in t.get("refs", [])]

    # Stage 1 — 계획
    log("[1] 계획: 요구사항 → 하위 편집 / 보존 목록")
    emit("plan", "바꿀 것과 지킬 것을 나누는 중")
    spec = (agents.plan_rule(cfg.request, terms, cfg.output) if fast
            else agents.plan(hub, cfg.request, state.get("observation"), cfg.output, terms))
    state["spec"] = spec
    for e in spec["sub_edits"]:
        log(f"    · {e.get('id')} [{e.get('type')}] {e.get('instruction')}")
    for a in spec.get("assumptions", []) or []:
        log(f"    (가정) {a}")
    emit("plan", f"편집 {len(spec['sub_edits'])}개 · 보존 {len(spec['preserve'])}개",
         sub_edits=[e.get("instruction") for e in spec["sub_edits"]], preserve=spec["preserve"])

    # Stage 2 — 체크리스트
    log("[2] 체크리스트: 검증 질문 생성")
    questions = agents.checklist_rule(terms) if fast else agents.checklist(hub, spec, terms)
    state["checklist"] = questions
    log(f"    질문 {len(questions)}개")
    emit("checklist", f"검증 질문 {len(questions)}개 준비", questions=[q["question"] for q in questions])
    save()

    if cfg.plan_only:
        log("[3] 설계(미리보기): 첫 라운드 프롬프트")
        state["preview_prompts"] = (agents.design_rule(terms, [], cfg.candidates, cfg.request) if fast
                                    else agents.design(hub, spec, [], cfg.candidates, mode))
        for p in state["preview_prompts"]:
            log(f"    → {p['prompt']}")
        save()
        emit("done", "미리보기 완료", final=None, run_dir=str(run_dir))
        log(f"완료(plan-only): {run_dir / 'report.html'}")
        return state

    memory: list[dict] = []
    best_overall: dict | None = None
    for r in range(1, cfg.rounds + 1):
        log(f"[라운드 {r}/{cfg.rounds}]")
        emit("round", f"{r}라운드 시작", round=r, rounds=cfg.rounds)
        rdir = run_dir / f"round{r}"
        emit("design", "후보 프롬프트 작성 중" + (" (이전 라운드 반성 반영)" if memory else ""), round=r)
        designs = (agents.design_rule(terms, memory, cfg.candidates, cfg.request) if fast and mode == "edit"
                   else agents.design(hub, spec, memory, cfg.candidates, mode))

        def one(i_d):
            i, d = i_d
            t0 = time.time()
            emit("generate", f"후보 {i} 생성 중", round=r, cand=i)
            try:
                out = gen(hub, mode, d["prompt"], cfg, rdir / f"cand{i}", d.get("negative") or (agents.default_negative(terms) if terms else None))
            except HubError as e:
                log(f"  후보 {i} 생성 실패: {e}")
                emit("generate", f"후보 {i} 생성 실패", round=r, cand=i, error=str(e))
                return {"cand": i, "prompt": d["prompt"], "rationale": d.get("rationale"), "error": str(e),
                        "score": 0.0, "failed": [], "notes": "", "file": None}
            emit("generate", f"후보 {i} 생성 완료", round=r, cand=i, file=str(out))
            emit("verify", f"후보 {i} 검증 중", round=r, cand=i)
            judge = [out] if mode != "video" else (video_frames(out, rdir / "frames") or [])
            if judge:
                try:
                    v = agents.verify(hub, questions, judge, cfg.photo, request=cfg.request, spec=spec, refs=refs)
                except Exception as e:   # 검증 호출 실패 → 이 후보만 '미검증'으로 두고 계속
                    log(f"  후보 {i} 검증 실패: {e}")
                    v = {"score": 0.3, "answers": {}, "failed": [], "identity_fail": False,
                         "notes": f"검증 실패(네트워크 등) — 점수 미확정: {str(e)[:120]}"}
            else:
                v = {"score": 0.5, "answers": {}, "failed": [], "identity_fail": False,
                     "notes": "영상 프레임 추출 불가(opencv 미설치 또는 영상 읽기 실패) — 검증 생략"}
            extra = (f" [체크 {v['checklist']:.2f} · 자연스러움 {v['naturalness']}/5 · 요청일치 {v['request_match']}/5]"
                     if "naturalness" in v else "")
            log(f"  후보 {i}: {d.get('rationale', '')}\n    점수 {v['score']:.2f}{extra}  "
                f"({time.time() - t0:.0f}s)  {v['notes']}")
            emit("verify", f"후보 {i} 점수 {v['score']:.2f}", round=r, cand=i, file=str(out), score=v["score"],
                 naturalness=v.get("naturalness"), request_match=v.get("request_match"), notes=v.get("notes"),
                 failed=[q["question"] for q in v.get("failed", [])][:4])
            return {"cand": i, "prompt": d["prompt"], "rationale": d.get("rationale"), "file": str(out), **v}

        with ThreadPoolExecutor(max_workers=max(1, len(designs))) as ex:   # 후보는 동시에 생성·검증
            cands = sorted(ex.map(one, enumerate(designs, 1)), key=lambda c: c["cand"])

        ok = [c for c in cands if c.get("file")]
        if not ok:
            state["rounds"].append({"round": r, "candidates": cands, "best": None})
            save()
            if r == 1:
                log("  모든 후보 생성 실패 — 엔드포인트/모델 설정을 확인하세요.")
                raise HubError("모든 후보 생성에 실패했습니다: " + (cands[0].get("error") or "")[:300])
            continue
        best = max(ok, key=lambda c: c["score"])                 # 선택 (Idea2Img draft selection)
        if not best_overall or best["score"] > best_overall["score"]:
            best_overall = best
        rec = {"round": r, "candidates": cands, "best": best["file"], "best_score": best["score"]}

        if best["score"] >= cfg.threshold or r == cfg.rounds:
            rec["reflection"] = None
            state["rounds"].append(rec)
            save()
            if best["score"] >= cfg.threshold:
                log(f"  기준 점수({cfg.threshold}) 달성 → 종료")
            break
        emit("reflect", "부족한 점을 분석해 다음 라운드를 준비하는 중", round=r)
        refl = agents.reflect_rule(best) if fast else agents.reflect(hub, best)   # 반성·라우팅 (GenMAC / Reflect-DiT)
        log(f"  반성[{refl.get('route')}]: {refl.get('summary_ko', '')}")
        emit("reflect", refl.get("summary_ko", ""), round=r, route=refl.get("route"),
             summary_ko=refl.get("summary_ko", ""))
        rec["reflection"] = refl
        memory.append({"round": r, "prompt": best["prompt"], "score": best["score"],
                       "failed": [q["question"] for q in best["failed"]],
                       "reflection": refl.get("reflection", "") if fast else f"[{refl.get('route')}] {refl.get('reflection', '')}",
                       "negative_extra": refl.get("negative_extra", "")})
        state["rounds"].append(rec)
        save()

    state["final"] = best_overall and {"file": best_overall["file"], "score": best_overall["score"],
                                       "prompt": best_overall["prompt"],
                                       "naturalness": best_overall.get("naturalness"),
                                       "request_match": best_overall.get("request_match")}
    state["finished"] = datetime.now().isoformat(timespec="seconds")
    save()
    if best_overall:
        log(f"최종: {best_overall['file']} (점수 {best_overall['score']:.2f})")
    log(f"리포트: {run_dir / 'report.html'}")
    emit("done", "완료", final=state["final"], run_dir=str(run_dir))
    return state


def gen(hub: Hub, mode: str, prompt: str, cfg: RunConfig, stem: Path, negative: str | None = None) -> Path:
    if mode == "edit":
        return hub.edit_image(cfg.photo, prompt, stem.with_suffix(".png"), cfg.size, negative=negative)
    if mode == "generate":
        return hub.generate_image(prompt, stem.with_suffix(".png"), cfg.size)
    # 영상: 사진이 있으면 첫 프레임으로 사용
    return hub.generate_video(prompt, stem.with_suffix(".mp4"), photo=cfg.photo,
                              seconds=cfg.seconds, size=cfg.size)
