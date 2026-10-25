from __future__ import annotations

import base64
import io
import json
import re
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def image_to_data_url(path: Path, max_side: int = 1024) -> str:
    """VLM 입력용 data URL. Pillow가 있으면 긴 변을 줄여 전송량을 낮춘다."""
    raw = Path(path).read_bytes()
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        raw, mime = buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def extract_json(text: str):
    """LLM 응답에서 JSON을 꺼낸다 (```json 블록, 앞뒤 설명문, <think> 블록 허용)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.S)
    if m:
        text = m.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        s, e = text.find(opener), text.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"JSON 파싱 실패: {text[:300]}")


def video_frames(video: Path, out_dir: Path, n: int = 3) -> list[Path]:
    """영상 검증용으로 처음·중간·끝 프레임을 추출 (opencv 필요, 없으면 빈 목록)."""
    try:
        import cv2
    except ImportError:
        return []
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    frames = []
    if total <= 0:
        return frames
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        idx = int((total - 1) * i / max(n - 1, 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            p = out_dir / f"{video.stem}_f{i}.jpg"
            cv2.imwrite(str(p), frame)
            frames.append(p)
    cap.release()
    return frames


def korean_font(size: int):
    from PIL import ImageFont
    for fp in ("C:/Windows/Fonts/malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc",
               "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


if sys.platform == "win32":  # 윈도우 콘솔 한글 출력
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
