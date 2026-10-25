"""박람회 웹 서비스 테스트.

  python tests/test_webapp.py            # API + SSE 흐름 테스트 (가짜 파이프라인)
  python tests/test_webapp.py --shots    # + Playwright로 화면별 스크린샷 (/tmp/claude-0/expo_shots)

실제 Hub나 GPU 없이 돌아간다. pipeline.run을 계약(on_event 이벤트)대로 이벤트를 내보내는 가짜로 바꾼다.
"""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OASH_API_KEY", "test")
os.environ["EXPO_OUTPUT_DIR"] = str(Path(tempfile.mkdtemp(prefix="expo_")))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import pipeline  # noqa: E402
from webapp import server  # noqa: E402

SAMPLE = ROOT / "sample.jpg"
SHOTS = Path("/tmp/claude-0/expo_shots")


def sample_data_url() -> str:
    if SAMPLE.exists():
        raw = SAMPLE.read_bytes()
    else:
        buf = io.BytesIO()
        Image.new("RGB", (413, 531), (220, 220, 220)).save(buf, "JPEG")
        raw = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def fake_run(delay: float = 0.05):
    """계약대로 이벤트를 내보내고 결과 PNG를 쓰는 가짜 run()."""
    def run(cfg, hub=None):
        emit = getattr(cfg, "on_event", None) or (lambda e: None)
        d = Path(cfg.out_root) / "run"
        base = Image.open(cfg.photo).convert("RGB")
        def cand(r, i, tint):
            p = d / f"round{r}" / f"cand{i}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            im = base.copy()
            ImageDraw.Draw(im, "RGBA").rectangle([0, 0, im.width, int(im.height * .38)], fill=tint)
            im.save(p)
            return str(p)
        steps = [
            {"stage": "observe", "message": "사진에서 얼굴·안경·옷 특징을 읽는 중"},
            {"stage": "terms", "message": "미용 용어를 모양으로 바꾸는 중",
             "terms": [{"term": ((pipeline.lexicon.find_by_id(cfg.style_id) or {}).get("term") if cfg.style_id else None) or "애즈펌", "source": "corpus",
                        "visual_en": "bangs fall down onto the forehead with the ends curving gently inward in a small soft C-curl",
                        "attrs_ko": ["앞머리: 일자로 내림", "컬: C컬(작게)", "이마: 살짝 보임", "옆머리: 다운펌으로 눌림"]},
                       {"term": "다운펌", "source": "corpus", "visual_en": "side hair lies smooth and flat",
                        "attrs_ko": ["옆머리: 다운펌으로 눌림"]}]},
            {"stage": "plan", "message": "하위 편집 2개, 보존 항목 8개로 계획했어요"},
            {"stage": "checklist", "message": "검증 질문 14개를 만들었어요"},
            {"stage": "round", "round": 1, "message": "라운드 1 시작"},
        ]
        for e in steps:
            emit(e); time.sleep(delay)
        f1 = cand(1, 1, (40, 30, 20, 90)); emit({"stage": "generate", "round": 1, "cand": 1, "file": f1, "message": "후보 1 생성"}); time.sleep(delay)
        emit({"stage": "verify", "round": 1, "cand": 1, "file": f1, "score": 0.46, "naturalness": 2, "request_match": 3,
              "notes": "한쪽에 컬이 몰려 있어요", "message": "후보 1 검증"}); time.sleep(delay)
        f2 = cand(1, 2, (60, 40, 20, 70)); emit({"stage": "generate", "round": 1, "cand": 2, "file": f2, "message": "후보 2 생성"}); time.sleep(delay)
        emit({"stage": "verify", "round": 1, "cand": 2, "file": f2, "score": 0.58, "naturalness": 3, "request_match": 3,
              "notes": "옆머리가 조금 떠 있어요", "message": "후보 2 검증"}); time.sleep(delay)
        emit({"stage": "reflect", "route": "tone_down", "summary_ko": "컬 크기를 줄이고 좌우 볼륨을 맞추도록 수정", "message": "반성"}); time.sleep(delay)
        emit({"stage": "round", "round": 2, "message": "라운드 2 시작"}); time.sleep(delay)
        f3 = cand(2, 1, (30, 30, 30, 60)); emit({"stage": "generate", "round": 2, "cand": 1, "file": f3, "message": "후보 1 생성"}); time.sleep(delay)
        emit({"stage": "verify", "round": 2, "cand": 1, "file": f3, "score": 0.86, "naturalness": 4, "request_match": 5,
              "notes": "요청한 모양에 가깝고 자연스러워요", "message": "후보 1 검증"}); time.sleep(delay)
        final = {"file": f3, "score": 0.86, "prompt": "Change only the hairstyle ..."}
        emit({"stage": "done", "message": "완성!", "final": final, "run_dir": str(d)})
        return {"final": final}
    return run


def patch(delay: float = 0.05) -> None:
    pipeline.run = fake_run(delay)
    server.RunConfig = lambda **kw: SimpleNamespace(**kw)        # on_event 등 새 필드를 그대로 받도록
    server._supported = lambda name: True
    server.get_hub = lambda: SimpleNamespace(models=lambda: SimpleNamespace(
        text="qwen3-next-80b", vision="qwen3-vl-32b", image_edit="Qwen-Image-Edit"))


def test_api_flow() -> None:
    patch()
    with TestClient(server.app) as c:
        assert c.get("/").status_code == 200
        g = c.get("/api/gallery").json()
        assert g["items"], "갤러리가 비어 있음"
        cfg = c.get("/api/config").json()
        assert cfg["models"]["image_edit"] == "Qwen-Image-Edit"
        # 동의 없으면 거절
        assert c.post("/api/jobs", json={"photo": sample_data_url(), "style_id": "애즈펌"}).status_code == 400
        r = c.post("/api/jobs", json={"photo": sample_data_url(), "consent": True, "style_id": "애즈펌", "gender": "male"})
        assert r.status_code == 200, r.text
        jid = r.json()["job_id"]
        stages = []
        with c.stream("GET", f"/api/jobs/{jid}/events") as s:
            for line in s.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                stages.append(ev.get("stage") or ev.get("type"))
                if ev.get("type") == "end":
                    assert ev["status"] == "done", ev
                    break
        for need in ("queued", "terms", "plan", "generate", "verify", "reflect", "done", "end"):
            assert need in stages, (need, stages)
        snap = c.get(f"/api/jobs/{jid}").json()
        assert snap["status"] == "done" and snap["final"]["url"].startswith(f"/api/jobs/{jid}/image/")
        assert c.get(snap["final"]["url"]).status_code == 200
        assert c.get(f"/api/jobs/{jid}/image/input.jpg").status_code == 200
        assert c.get(f"/api/jobs/{jid}/image/../../.env").status_code == 404       # 경로 탈출 차단
        assert c.get(f"/api/jobs/{jid}/qr.png").headers["content-type"] == "image/png"
        assert c.get(f"/r/{jid}").status_code == 200
        # EXIF 제거·크기 제한 확인
        im = Image.open(server.EXPO_DIR / jid / "input.jpg")
        assert max(im.size) <= 1024 and not im.getexif()
        assert c.post(f"/api/jobs/{jid}/delete").json()["deleted"]
        assert not (server.EXPO_DIR / jid).exists()
        assert c.get(f"/r/{jid}").status_code == 404
    print("API/SSE 흐름 OK:", " → ".join(dict.fromkeys(stages)))


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def fake_camera_y4m() -> str | None:
    """sample.jpg로 가짜 웹캠 영상(y4m)을 만든다 (ffmpeg 있을 때)."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not SAMPLE.exists():
        return None
    out = Path(tempfile.mkdtemp()) / "cam.y4m"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-loop", "1", "-i", str(SAMPLE), "-t", "2",
                    "-vf", "scale=960:720:force_original_aspect_ratio=decrease,pad=960:720:(ow-iw)/2:(oh-ih)/2:white,format=yuv420p",
                    "-r", "15", str(out)], check=True)
    return str(out)


def screenshots() -> None:
    import uvicorn
    from playwright.sync_api import sync_playwright
    patch(delay=0.9)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)
    SHOTS.mkdir(parents=True, exist_ok=True)
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    cam = fake_camera_y4m()
    if cam:
        args.append(f"--use-file-for-fake-video-capture={cam}")
    with sync_playwright() as p:
        b = p.chromium.launch(args=args)
        for vw, vh, tag in ((1920, 1080, ""), (820, 1180, "_tablet")):
            ctx = b.new_context(viewport={"width": vw, "height": vh}, permissions=["camera"])
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{port}/")
            pg.wait_for_timeout(500); pg.screenshot(path=str(SHOTS / f"1_attract{tag}.png"))
            pg.click("text=시작하기"); pg.wait_for_timeout(300); pg.screenshot(path=str(SHOTS / f"2_consent{tag}.png"))
            pg.click(".consent"); pg.click("#consent-next"); pg.wait_for_timeout(1500)
            pg.screenshot(path=str(SHOTS / f"3_camera{tag}.png"))
            pg.click("#btn-capture"); pg.wait_for_timeout(3600)
            pg.screenshot(path=str(SHOTS / f"4_captured{tag}.png"))
            pg.click("#btn-use"); pg.wait_for_timeout(800)
            pg.click(".seg button[data-gender='male']"); pg.wait_for_timeout(500)
            pg.click("#grid .tile >> nth=1"); pg.wait_for_timeout(300)
            pg.screenshot(path=str(SHOTS / f"5_style{tag}.png"))
            pg.click("#btn-make"); pg.wait_for_timeout(6500)
            pg.screenshot(path=str(SHOTS / f"6_progress{tag}.png"))
            pg.wait_for_selector("#s-result.active", timeout=30000); pg.wait_for_timeout(700)
            pg.mouse.move(vw * 0.3, vh * 0.5)
            pg.screenshot(path=str(SHOTS / f"7_result{tag}.png"))
            ctx.close()
        b.close()
    srv.should_exit = True
    print("스크린샷:", SHOTS)


if __name__ == "__main__":
    test_api_flow()
    if "--shots" in sys.argv:
        screenshots()
