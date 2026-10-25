"""박람회 부스용 AI 헤어 스타일 체험 서버.

    python -m webapp.server            # http://0.0.0.0:8000 (EXPO_PORT로 변경)

관람객 사진 → (용어 이해 → 계획 → 체크리스트 → 생성 → 검증 → 반성) 파이프라인 → 결과/QR.
진행 상황은 Server-Sent Events(/api/jobs/{id}/events)로 화면에 실시간 전달한다.
사진은 output/expo/<job_id>/ 에만 저장되고 EXPO_RETENTION_MIN 분 뒤 자동 삭제된다.
"""

from __future__ import annotations

import asyncio
import base64
import html
import dataclasses
import io
import json
import os
import shutil
import sys
import threading
import time
import uuid
from typing import Optional
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import pipeline  # noqa: E402
from pipeline import RunConfig  # noqa: E402

# ---------------------------------------------------------------- 설정
EXPO_PORT = int(os.getenv("EXPO_PORT", "8000"))
EXPO_WORKERS = int(os.getenv("EXPO_WORKERS", "2"))
EXPO_ROUNDS = int(os.getenv("EXPO_ROUNDS", "2"))
EXPO_CANDIDATES = int(os.getenv("EXPO_CANDIDATES", "2"))
EXPO_THRESHOLD = float(os.getenv("EXPO_THRESHOLD", "0.8"))
EXPO_RETENTION_MIN = float(os.getenv("EXPO_RETENTION_MIN", "30"))
EXPO_PUBLIC_URL = os.getenv("EXPO_PUBLIC_URL", "").rstrip("/")
EXPO_IDLE_SEC = int(os.getenv("EXPO_IDLE_SEC", "90"))
EXPO_DIR = ROOT / os.getenv("EXPO_OUTPUT_DIR", "output/expo")
# 빠른 모드: auto면 키 동시 한도가 1일 때 켬 (갤러리 스타일은 LLM 없이 편집+검증만)
_fast_env = os.getenv("EXPO_FAST", "auto").lower()
EXPO_FAST = (int(os.getenv("OASH_MAX_CONCURRENCY", "1")) <= 1) if _fast_env == "auto" else _fast_env in ("1", "true", "yes")
STATIC_DIR = Path(__file__).resolve().parent / "static"
KNOWLEDGE = ROOT / "knowledge"
GALLERY_DIR = KNOWLEDGE / "gallery"

PRIVACY_TEXT = (
    "촬영한 사진은 이 체험의 헤어스타일 합성에만 사용됩니다. "
    f"사진과 결과는 이 부스의 서버에만 저장되며 {int(EXPO_RETENTION_MIN)}분 뒤 자동으로 삭제됩니다. "
    "결과 화면의 ‘내 사진 삭제’ 버튼으로 즉시 삭제할 수도 있습니다. "
    "AI 모델 호출은 경북IT융합산업기술원 AI클라우드센터의 Open AI Service Hub 안에서만 이루어집니다."
)

CATEGORIES = ["컷", "펌", "앞머리", "스타일링", "묶음"]


# ---------------------------------------------------------------- 작업(Job)
@dataclass
class Job:
    id: str
    dir: Path
    request: str
    gender: Optional[str]
    style_id: Optional[str]
    label: Optional[str] = None
    created: float = field(default_factory=time.time)
    status: str = "queued"          # queued | running | done | error | deleted
    events: list = field(default_factory=list)
    final: Optional[dict] = None
    terms: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, ev: dict) -> None:
        ev = dict(ev)
        ev.setdefault("type", "stage")
        ev["t"] = round(time.time() - self.created, 1)
        # 파일 경로 → 브라우저용 URL
        for key in ("file",):
            if ev.get(key):
                ev["url"] = self.url_for(ev[key])
        if isinstance(ev.get("final"), dict) and ev["final"].get("file"):
            ev["final"] = dict(ev["final"], url=self.url_for(ev["final"]["file"]))
        with self.lock:
            ev["seq"] = len(self.events)
            self.events.append(ev)

    def url_for(self, path: str) -> Optional[str]:
        try:
            rel = Path(path).resolve().relative_to(self.dir.resolve())
        except (ValueError, OSError):
            return None
        return f"/api/jobs/{self.id}/image/{rel.as_posix()}"

    def snapshot(self) -> dict:
        return {"id": self.id, "status": self.status, "request": self.request, "gender": self.gender,
                "style_id": self.style_id, "label": self.label, "final": self.final, "terms": self.terms,
                "input_url": f"/api/jobs/{self.id}/image/input.jpg",
                "events": list(self.events), "queue_position": queue_position(self.id)}


JOBS: dict[str, Job] = {}
QUEUE: list[str] = []
QUEUE_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=EXPO_WORKERS, thread_name_prefix="expo")
_HUB = None
_HUB_LOCK = threading.Lock()


def queue_position(job_id: str) -> int:
    with QUEUE_LOCK:
        return QUEUE.index(job_id) + 1 if job_id in QUEUE else 0


def get_hub():
    """Hub는 모델 목록 조회 결과를 캐시하므로 하나를 공유한다."""
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = pipeline.Hub()
        return _HUB


def _supported(name: str) -> bool:
    return name in {f.name for f in dataclasses.fields(RunConfig)}


def run_job(job: Job) -> None:
    with QUEUE_LOCK:
        if job.id in QUEUE:
            QUEUE.remove(job.id)
    if job.status == "deleted":
        return
    job.status = "running"
    job.emit({"stage": "start", "message": "AI 파이프라인을 시작합니다"})

    def on_event(ev: dict) -> None:
        if job.status == "deleted":
            return
        if ev.get("stage") == "terms" and ev.get("terms"):
            job.terms = ev["terms"]
        job.emit(ev)

    kwargs = dict(request=job.request, photo=job.dir / "input.jpg", output="image",
                  rounds=EXPO_ROUNDS, candidates=EXPO_CANDIDATES, threshold=EXPO_THRESHOLD,
                  out_root=job.dir, name="run")
    for k, v in (("on_event", on_event), ("gender", job.gender), ("style_id", job.style_id),
                 ("fast", EXPO_FAST)):
        if _supported(k):
            kwargs[k] = v
    try:
        state = pipeline.run(RunConfig(**kwargs), get_hub())
        final = (state or {}).get("final")
        if final and final.get("file"):
            job.final = dict(final, url=job.url_for(final["file"]))
            job.status = "done"
            if not any(e.get("stage") == "done" for e in job.events):
                job.emit({"stage": "done", "message": "완성!", "final": final})
        else:
            job.status = "error"
            job.emit({"stage": "error", "message": "결과 이미지를 만들지 못했습니다. 다시 시도해 주세요."})
    except Exception as e:  # noqa: BLE001 — 부스에서는 어떤 오류든 화면에 보여주고 다음 관람객을 받는다
        job.status = "error"
        job.emit({"stage": "error", "message": f"오류가 발생했습니다: {str(e)[:300]}"})


def retention_loop() -> None:
    while True:
        cutoff = time.time() - EXPO_RETENTION_MIN * 60
        for jid, job in list(JOBS.items()):
            if job.created < cutoff and job.status != "running":
                delete_job(job)
                JOBS.pop(jid, None)
        if EXPO_DIR.exists():  # 서버 재시작 전 남은 폴더도 정리
            for d in EXPO_DIR.iterdir():
                if d.is_dir() and d.name not in JOBS and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
        time.sleep(60)


def delete_job(job: Job) -> None:
    job.status = "deleted"
    with QUEUE_LOCK:
        if job.id in QUEUE:
            QUEUE.remove(job.id)
    shutil.rmtree(job.dir, ignore_errors=True)


# ---------------------------------------------------------------- 갤러리
def _fallback_gallery() -> list[dict]:
    path = KNOWLEDGE / "hair_terms.json"
    try:
        terms = json.loads(path.read_text(encoding="utf-8")).get("terms", {})
    except (OSError, ValueError):
        return []
    out = []
    for name, e in terms.items():
        kind = e.get("kind", "style")
        cat = ("앞머리" if kind == "part" else "펌" if (kind == "treatment" or "펌" in name)
               else "컷" if (kind == "cut" or "컷" in name) else "스타일링")
        attrs = [f"{k}: {v}" for k, v in list((e.get("attributes") or {}).items())[:3]]
        out.append({"id": name, "term": name, "gender": e.get("gender", "unisex"), "category": cat,
                    "description_ko": e.get("notes_ko") or e.get("visual_en", ""), "popularity": "medium",
                    "attrs_ko": attrs, "thumb": None})
    return out


def gallery_items() -> list[dict]:
    items = None
    try:
        from pipeline import lexicon
        if hasattr(lexicon, "gallery"):
            items = lexicon.gallery()
    except Exception:  # noqa: BLE001
        items = None
    items = items or _fallback_gallery()
    for it in items:
        thumb = GALLERY_DIR / f"{it['id']}.png"
        it["thumb_url"] = f"/gallery/{it['id']}.png" if thumb.exists() else None
        it.setdefault("category", "스타일링")
        it.setdefault("gender", "unisex")
        it.setdefault("attrs_ko", [])
    return items


# ---------------------------------------------------------------- 앱
@asynccontextmanager
async def lifespan(_app):
    EXPO_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=retention_loop, daemon=True).start()
    yield


app = FastAPI(title="AI 헤어 스타일 체험", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/gallery/{name}")
def gallery_thumb(name: str):
    p = (GALLERY_DIR / name).resolve()
    if GALLERY_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/gallery")
def api_gallery(gender: Optional[str] = None):
    items = gallery_items()
    if gender in ("male", "female"):
        items = [i for i in items if i.get("gender") in (gender, "unisex")]
    items = [i for i in items if i.get("popularity") != "hidden"]      # 시연에서 뺄 스타일
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: (order.get(i.get("popularity"), 1), i["term"]))
    return {"items": items, "categories": CATEGORIES}


_MODELS_CACHE: dict = {}


@app.get("/api/config")
def api_config():
    if not _MODELS_CACHE:
        try:
            m = get_hub().models()
            _MODELS_CACHE.update({k: getattr(m, k, "") for k in ("text", "vision", "image_edit")})
        except Exception:  # noqa: BLE001
            pass
    return {"rounds": EXPO_ROUNDS, "candidates": EXPO_CANDIDATES, "threshold": EXPO_THRESHOLD,
            "retention_min": EXPO_RETENTION_MIN, "fast": EXPO_FAST, "idle_sec": EXPO_IDLE_SEC, "workers": EXPO_WORKERS,
            "privacy": PRIVACY_TEXT, "models": dict(_MODELS_CACHE),
            "infra": "H100 GPU · 경북 AI클라우드센터 Open AI Service Hub"}


class JobIn(BaseModel):
    photo: str
    consent: bool = False
    gender: Optional[str] = None
    style_id: Optional[str] = None
    request: Optional[str] = None


def _save_photo(data_url: str, out: Path) -> None:
    from PIL import Image, ImageOps
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1] if "," in data_url else data_url)
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im).convert("RGB")   # 회전 반영 후 EXIF는 저장하지 않음
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"사진을 읽을 수 없습니다: {e}") from e
    im.thumbnail((1024, 1024))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=92)


@app.post("/api/jobs")
def create_job(body: JobIn):
    if not body.consent:
        raise HTTPException(400, "개인정보 이용 동의가 필요합니다.")
    style = (body.style_id or "").strip() or None
    extra = (body.request or "").strip()
    if not style and not extra:
        raise HTTPException(400, "스타일을 고르거나 원하는 스타일을 적어 주세요.")
    keep = "얼굴, 안경, 옷, 배경은 그대로"
    gender = body.gender if body.gender in ("male", "female") else None
    label = style
    if style:
        item = next((i for i in gallery_items() if i["id"] == style), None)
        if item:
            label = item["term"]
            if not gender and item.get("gender") in ("male", "female"):
                gender = item["gender"]
        request = f"{label} 스타일로 바꿔줘. {keep}." + (f" 추가 요청: {extra}" if extra else "")
    else:
        request = f"{extra}. {keep}."
    jid = uuid.uuid4().hex[:10]
    job = Job(id=jid, dir=EXPO_DIR / jid, request=request, gender=gender, style_id=style, label=label)
    _save_photo(body.photo, job.dir / "input.jpg")
    JOBS[jid] = job
    with QUEUE_LOCK:
        QUEUE.append(jid)
    job.emit({"stage": "queued", "message": "대기열에 등록되었습니다", "queue_position": queue_position(jid)})
    EXECUTOR.submit(run_job, job)
    return {"job_id": jid}


def _job(jid: str) -> Job:
    job = JOBS.get(jid)
    if not job or job.status == "deleted":
        raise HTTPException(404, "결과가 없거나 삭제되었습니다.")
    return job


@app.get("/api/jobs/{jid}")
def job_snapshot(jid: str):
    return _job(jid).snapshot()


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, request: Request):
    job = _job(jid)

    async def stream():
        idx, last_pos, last_ping = 0, None, time.time()
        while True:
            if await request.is_disconnected():
                return
            with job.lock:
                new = job.events[idx:]
            for ev in new:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            idx += len(new)
            if job.status == "queued":
                pos = queue_position(jid)
                if pos != last_pos:
                    last_pos = pos
                    yield f"data: {json.dumps({'type': 'queue', 'queue_position': pos}, ensure_ascii=False)}\n\n"
            if job.status in ("done", "error", "deleted") and idx >= len(job.events):
                yield f"data: {json.dumps({'type': 'end', 'status': job.status}, ensure_ascii=False)}\n\n"
                return
            if time.time() - last_ping > 15:
                last_ping = time.time()
                yield ": ping\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{jid}/image/{name:path}")
def job_image(jid: str, name: str):
    job = _job(jid)
    p = (job.dir / name).resolve()
    if job.dir.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


def _public_base(request: Request) -> str:
    return EXPO_PUBLIC_URL or str(request.base_url).rstrip("/")


@app.get("/api/jobs/{jid}/qr.png")
def job_qr(jid: str, request: Request):
    _job(jid)
    import qrcode
    img = qrcode.make(f"{_public_base(request)}/r/{jid}", box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{jid}/delete")
def job_delete(jid: str):
    job = JOBS.get(jid)
    if job:
        delete_job(job)
    return {"deleted": True}


@app.get("/r/{jid}", response_class=HTMLResponse)
def result_page(jid: str):
    job = JOBS.get(jid)
    if not job or job.status != "done" or not job.final:
        return HTMLResponse(_mobile_page("결과를 찾을 수 없어요",
                                         "<p>결과가 아직 준비되지 않았거나, 개인정보 보호를 위해 삭제되었습니다.</p>"),
                            status_code=404)
    before, after = f"/api/jobs/{jid}/image/input.jpg", job.final["url"]
    terms = "".join(f"<span class='chip'>{t.get('term', '')}</span>" for t in job.terms)
    body = f"""
      <p class="sub">{html.escape(job.label or '요청하신 스타일')} · AI가 만든 가상 헤어스타일</p>
      <div class="pair"><figure><img src="{before}" alt="원본"><figcaption>원본</figcaption></figure>
      <figure><img src="{after}" alt="결과"><figcaption>AI 스타일</figcaption></figure></div>
      <div class="chips">{terms}</div>
      <a class="btn" href="{after}" download="ai-hair-{jid}.png">결과 이미지 저장</a>
      <p class="note">이 페이지와 사진은 {int(EXPO_RETENTION_MIN)}분 뒤 자동 삭제됩니다. 필요하면 지금 저장해 주세요.</p>
      <form method="post" action="/api/jobs/{jid}/delete" onsubmit="setTimeout(()=>location.reload(),300)">
        <button class="btn ghost" type="submit">지금 삭제하기</button></form>"""
    return HTMLResponse(_mobile_page("AI 헤어 스타일 체험 결과", body))


def _mobile_page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{--bg:#f6f5f1;--fg:#16181a;--mut:#5d6166;--line:#dcdad3;--acc:#0f6b5c;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121414;--fg:#eceae4;--mut:#a3a7aa;--line:#2c3030;--acc:#5cc3ad;--card:#1b1e1e}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 "Pretendard","Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;padding:20px 16px 40px;max-width:640px;margin:auto}}
h1{{font-size:22px;margin:4px 0}} .sub,.note{{color:var(--mut)}} .note{{font-size:13px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}}
figure{{margin:0}} img{{width:100%;border-radius:12px;display:block;background:var(--card)}}
figcaption{{text-align:center;font-size:13px;color:var(--mut);margin-top:4px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}}
.chip{{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:13px}}
.btn{{display:block;width:100%;box-sizing:border-box;text-align:center;padding:14px;border-radius:12px;background:var(--acc);color:#fff;
text-decoration:none;font-weight:700;font-size:16px;border:0;margin:10px 0;cursor:pointer}}
.btn.ghost{{background:transparent;color:var(--mut);border:1px solid var(--line)}}
</style></head><body><h1>{title}</h1>{body}</body></html>"""


def main() -> None:
    import uvicorn
    print(f"AI 헤어 스타일 체험 서버: http://localhost:{EXPO_PORT}  (작업자 {EXPO_WORKERS}명, "
          f"라운드 {EXPO_ROUNDS} × 후보 {EXPO_CANDIDATES}, 보관 {int(EXPO_RETENTION_MIN)}분)")
    uvicorn.run(app, host=os.getenv("EXPO_HOST", "0.0.0.0"), port=EXPO_PORT, log_level="warning")


if __name__ == "__main__":
    main()
