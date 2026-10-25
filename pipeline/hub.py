"""Open AI Service Hub 클라이언트 (포털 Playground가 실제로 쓰는 규격 기준).

- 채팅/비전:   POST {base}/chat/completions            (OpenAI 호환, 비전은 content에 image_url)
- 이미지 생성: POST {base}/images/generations          JSON {model, prompt, size, steps, response_format:"b64_json"}
- 이미지 편집: POST {base}/images/generations          위와 동일 + reference: 원본 사진 data URL
- 영상 생성:   POST {base}/videos/generations          JSON {model, prompt, size, length(프레임), steps, seed,
                                                          negative_prompt, image(I2V일 때 첫 프레임 data URL)}
               → {job_id} → GET {base}/jobs/{job_id} 폴링 (QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED)
               → artifact_url 다운로드 (상대 경로면 게이트웨이 루트 기준)
- 모델 목록:   GET {base}/models (공개)
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .utils import image_to_data_url, log


class HubError(RuntimeError):
    pass


# 키별 동시 요청 한도 (개발키 기본 1건). 프로세스 전체가 이 슬롯을 공유한다.
MAX_CONCURRENCY = max(1, int(os.getenv("OASH_MAX_CONCURRENCY", "1")))
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)
_RETRIES = int(os.getenv("OASH_MAX_RETRIES", "6"))


def _retry_after(r: requests.Response) -> int:
    try:
        v = int(float(r.headers.get("Retry-After", "0") or 0))
        if v:
            return v
        d = r.json().get("detail", {})
        return int(float(d.get("retry_after", 0))) if isinstance(d, dict) else 0
    except Exception:
        return 0


def _limit_kind(r: requests.Response) -> str:
    try:
        d = r.json().get("detail", {})
        return {"concurrent_limit": "동시 요청 한도", "rate_limit": "분당 한도"}.get(d.get("error"), d.get("error", ""))
    except Exception:
        return "GPU 준비 중" if r.status_code == 503 else ""


# 역할별 자동 선택 키워드 (앞쪽일수록 우선)
TEXT_PREFS = ("qwen3", "qwen2.5-72b", "exaone", "gpt-oss", "llama", "gemma", "deepseek", "qwen", "gpt", "mistral")
# 범용 VLM 우선. OCR·문서 전용 VLM(paddleocr-vl 등)은 얼굴·머리 판단에 부적합하므로 제외
VISION_KW = ("qwen3-vl", "qwen2.5-vl", "qwen-vl", "internvl", "gemma-3", "llava", "pixtral",
             "minicpm-v", "glm-4v", "vision", "-vl", "vl")
SPECIALIST_KW = ("ocr", "coder", "code", "math", "guard", "embed", "rerank")
IMAGE_KW = ("qwen-image", "flux", "sdxl", "sd3", "stable-diffusion", "diffusion", "kolors", "hidream", "image")
EDIT_KW = ("edit", "kontext", "inpaint")
VIDEO_KW = ("video", "wan", "hunyuan", "cogvideo", "ltx", "mochi", "t2v", "i2v")
NON_CHAT_KW = ("embed", "bge", "rerank", "whisper", "tts", "stt") + IMAGE_KW + VIDEO_KW

# 영상 모델 규격 (포털 카탈로그의 video_spec 값). 모르는 모델은 DEFAULT_VIDEO_SPEC 사용
VIDEO_SPECS = {
    "wan2.2-i2v": {"fps": 16, "max_seconds": 4, "frame_align": 4, "sizes": ["832x480", "640x480"]},
    "wan2.2-t2v": {"fps": 16, "max_seconds": 10, "frame_align": 4, "sizes": ["832x480", "640x480", "480x832", "512x512"]},
    "wan2.1-t2v": {"fps": 16, "max_seconds": 10, "frame_align": 4, "sizes": ["832x480", "640x480", "480x832", "512x512"]},
    "ltx-2": {"fps": 24, "max_seconds": 10, "frame_align": 8, "sizes": ["1280x704", "1216x704", "768x512"]},
}
DEFAULT_VIDEO_SPEC = {"fps": 16, "max_seconds": 4, "frame_align": 4, "sizes": ["832x480"]}
NEGATIVE_VIDEO = "blurry, low quality, distorted face, different person, static, watermark, text"


@dataclass
class Models:
    text: str = ""
    vision: str = ""
    image: str = ""
    image_edit: str = ""
    video_t2v: str = ""
    video_i2v: str = ""
    available: list[str] = field(default_factory=list)

    @property
    def video(self) -> str:
        return self.video_i2v or self.video_t2v


class Hub:
    def __init__(self) -> None:
        base = (os.getenv("OASH_BASE_URL") or "https://open.hasa.re.kr/v1").strip().strip('"\'').rstrip("/")
        # 실수로 엔드포인트까지 적은 경우(…/v1/models 등) 기본 주소만 남김
        for tail in ("/models", "/chat/completions", "/images/generations", "/images/edits",
                     "/videos/generations", "/videos"):
            if base.endswith(tail):
                base = base[: -len(tail)]
        self.base = base
        u = urlsplit(base)
        self.root = f"{u.scheme}://{u.netloc}"          # artifact_url(/files/…) 해석용
        self.key = os.getenv("OASH_API_KEY", "").strip().strip('"\'')
        self.timeout = int(os.getenv("OASH_TIMEOUT", "600"))
        self.steps = int(os.getenv("OASH_IMAGE_STEPS", "20"))
        if not self.key:
            raise HubError("OASH_API_KEY가 없습니다. .env에 개발키를 넣어 주세요.")
        self._models: Models | None = None

    # ------------------------------------------------------------ 저수준
    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def _check(self, r: requests.Response, what: str) -> dict:
        if r.status_code >= 400:
            if "invalid_api_key" in r.text:
                raise HubError("개발키가 유효하지 않거나 만료/폐기되었습니다. 포털(account/dev-keys)에서 키 상태를 "
                               "확인하고 .env의 OASH_API_KEY를 고친 뒤 실행하세요. (반복 실패 시 일시 차단됩니다)")
            hint = {401: " → API Key 없음/무효", 403: " → 이 키로 해당 모델 사용 불가",
                    404: " → 등록되지 않은 모델이거나 경로 오류", 429: " → 요청 한도 초과, 잠시 후 다시",
                    503: " → GPU 백엔드 준비 중, 잠시 후 다시"}.get(r.status_code, "")
            raise HubError(f"{what} 실패 (HTTP {r.status_code}): {r.text[:500]}{hint}")
        try:
            return r.json()
        except ValueError as e:
            raise HubError(f"{what} 응답이 JSON이 아닙니다: {r.text[:300]}") from e

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        """모든 Hub 호출은 여기로: 프로세스 전체에서 동시 요청 수를 키 한도(OASH_MAX_CONCURRENCY) 이하로 유지.
        429(동시·분당 한도)·503(GPU 준비 중)은 서버가 알려준 시간만큼 기다렸다 재시도."""
        for i in range(_RETRIES + 1):
            try:
                with _SLOTS:
                    r = requests.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                if i == _RETRIES:
                    raise HubError(f"Hub 연결이 계속 끊깁니다 ({type(e).__name__}). 네트워크나 Hub 게이트웨이 상태를 "
                                   f"확인하세요: {str(e)[:200]}") from e
                wait = min(5 * (i + 1), 30)
                log(f"    (연결 끊김: {type(e).__name__}) {wait}초 후 재시도… [{i + 1}/{_RETRIES}]")
                time.sleep(wait)
                continue
            if r.status_code not in (429, 503) or i == _RETRIES:
                return r
            wait = _retry_after(r) or min(5 * (i + 1), 30)
            log(f"    ({r.status_code} {_limit_kind(r)}) {wait}초 후 재시도… [{i + 1}/{_RETRIES}]")
            time.sleep(wait)
        return r

    def _post(self, path: str, body: dict, what: str, timeout: int | None = None) -> dict:
        r = self._req("POST", f"{self.base}{path}", headers=self._h(), json=body, timeout=timeout or self.timeout)
        return self._check(r, what)

    # ------------------------------------------------------------ 모델
    def list_models(self) -> list[str]:
        r = self._req("GET", f"{self.base}/models", headers=self._h(), timeout=30)
        return [m.get("id", "") for m in self._check(r, "모델 목록").get("data", [])]

    def models(self) -> Models:
        """키로 쓸 수 있는 모델 중에서 역할별 모델을 고른다. .env 값이 있으면 그것을 우선."""
        if self._models:
            return self._models
        avail = self.list_models()
        low = {m: m.lower() for m in avail}

        def pick(kws, exclude=(), require=()):
            for kw in kws:
                for m, l in low.items():
                    if kw in l and not any(x in l for x in exclude) and all(x in l for x in require):
                        return m
            return ""

        vision = pick(VISION_KW, exclude=IMAGE_KW + VIDEO_KW + SPECIALIST_KW)
        chat_like = [m for m, l in low.items()
                     if not any(k in l for k in NON_CHAT_KW) and not any(k in l for k in VISION_KW)]
        general = [m for m in chat_like if not any(k in low[m] for k in SPECIALIST_KW)]
        text = ""
        for pool in (general, chat_like):          # 범용 모델 먼저, 없으면 coder 등 전문 모델
            for kw in TEXT_PREFS:
                text = next((m for m in pool if kw in low[m]), "")
                if text:
                    break
            if text or pool:
                text = text or pool[0]
                break
        vision = vision or text
        image = pick(IMAGE_KW, exclude=EDIT_KW + VIDEO_KW)
        image_edit = pick(EDIT_KW, exclude=VIDEO_KW)
        video_i2v = pick(("i2v", "image-to-video"))
        video_t2v = pick(("t2v", "ltx", "video", "wan", "hunyuan", "cogvideo", "mochi"), exclude=("i2v",))
        env_video = os.getenv("OASH_VIDEO_MODEL", "")
        if env_video:
            if "i2v" in env_video.lower():
                video_i2v = env_video
            else:
                video_t2v = env_video

        self._models = Models(
            text=os.getenv("OASH_TEXT_MODEL") or text,
            vision=os.getenv("OASH_VISION_MODEL") or vision,
            image=os.getenv("OASH_IMAGE_MODEL") or image,
            image_edit=os.getenv("OASH_IMAGE_EDIT_MODEL") or image_edit,
            video_t2v=video_t2v, video_i2v=video_i2v, available=avail,
        )
        return self._models

    # ------------------------------------------------------------ 채팅 / 비전
    def chat(self, messages: list[dict], model: str | None = None, temperature: float = 0.4,
             max_tokens: int = 2048) -> str:
        model = model or self.models().text
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        data = self._post("/chat/completions", body, f"채팅({model})")
        return (data["choices"][0]["message"].get("content") or "").strip()

    def vision(self, prompt: str, images: list[Path], system: str | None = None,
               temperature: float = 0.2, max_tokens: int = 2048) -> str:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in images:
            content.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        return self.chat(msgs, model=self.models().vision, temperature=temperature, max_tokens=max_tokens)

    # ------------------------------------------------------------ 이미지
    @staticmethod
    def _auto_size(photo: Path | None) -> str:
        """세로 사진(증명사진)은 세로 비율로, 그 외는 정사각."""
        if photo:
            try:
                from PIL import Image
                w, h = Image.open(photo).size
                if h > w * 1.1:
                    return "768x1024"
                if w > h * 1.1:
                    return "1024x768"
            except Exception:
                pass
        return "1024x1024"

    def generate_image(self, prompt: str, out: Path, size: str | None = None) -> Path:
        m = self.models().image
        if not m:
            raise HubError("텍스트→이미지 모델이 없습니다 (.env의 OASH_IMAGE_MODEL 지정)")
        body = {"model": m, "prompt": prompt, "size": size or "1024x1024", "steps": self.steps,
                "response_format": "b64_json", "n": 1}
        return self._save_image(self._post("/images/generations", body, "이미지 생성"), out)

    _neg_ok = True   # 서버가 negative_prompt를 거부하면 False로 바꾸고 다시는 보내지 않음

    def edit_image(self, photo: Path, prompt: str, out: Path, size: str | None = None,
                   negative: str | None = None) -> Path:
        m = self.models().image_edit
        if not m:
            raise HubError("이미지 편집 모델이 없습니다 (.env의 OASH_IMAGE_EDIT_MODEL 지정)")
        sizes = [size] if size else [self._auto_size(photo), "1024x1024"]
        ref = image_to_data_url(photo, max_side=1024)
        last: HubError | None = None
        for s in dict.fromkeys(sizes):              # 세로 크기를 거부하면 정사각으로 한 번 더
            body = {"model": m, "prompt": prompt, "reference": ref, "size": s, "steps": self.steps,
                    "response_format": "b64_json", "n": 1}
            if negative and Hub._neg_ok:
                body["negative_prompt"] = negative
            try:
                return self._save_image(self._post("/images/generations", body, f"이미지 편집({s})"), out)
            except HubError as e:
                last = e
                if "negative_prompt" in body and ("HTTP 400" in str(e) or "HTTP 422" in str(e)):
                    Hub._neg_ok = False
                    log("    (Hub가 negative_prompt를 받지 않아 빼고 다시 요청)")
                    body.pop("negative_prompt")
                    try:
                        return self._save_image(self._post("/images/generations", body, f"이미지 편집({s})"), out)
                    except HubError as e2:
                        last = e2
                if "(HTTP 4" not in str(e) or "HTTP 401" in str(e) or "HTTP 403" in str(e):
                    raise
        raise last  # type: ignore[misc]

    def _save_image(self, data: dict, out: Path) -> Path:
        items = data.get("data") or []
        if not items:
            raise HubError(f"응답에 이미지가 없습니다: {json.dumps(data, ensure_ascii=False)[:400]}")
        item = items[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            out.write_bytes(self._download(item["url"]))
        else:
            raise HubError("이미지 응답에 b64_json/url이 없습니다.")
        return out

    def _download(self, url: str) -> bytes:
        if not url.startswith(("http://", "https://")):
            url = self.root + (url if url.startswith("/") else "/" + url)
        r = self._req("GET", url, headers={"Authorization": f"Bearer {self.key}"}, timeout=self.timeout)
        if r.status_code >= 400:
            raise HubError(f"다운로드 실패 (HTTP {r.status_code}): {url}")
        return r.content

    # ------------------------------------------------------------ 영상
    @staticmethod
    def _frames(seconds: float, spec: dict) -> int:
        s = max(0.5, min(float(seconds), spec["max_seconds"]))
        a = spec["frame_align"]
        return max(a + 1, round((s * spec["fps"] - 1) / a) * a + 1)

    def generate_video(self, prompt: str, out: Path, photo: Path | None = None,
                       seconds: float | None = None, size: str | None = None,
                       interval: int = 5, max_wait: int = 3600) -> Path:
        m = self.models()
        model = m.video_i2v if photo and m.video_i2v else m.video_t2v
        if not model:
            raise HubError("영상 모델이 없습니다 (.env의 OASH_VIDEO_MODEL 지정)")
        if photo and model != m.video_i2v:
            log("    (주의) 사진→영상(I2V) 모델이 없어 텍스트→영상으로 만듭니다. 얼굴이 달라질 수 있어요.")
        spec = VIDEO_SPECS.get(model.lower(), DEFAULT_VIDEO_SPEC)
        seconds = min(seconds or spec["max_seconds"], spec["max_seconds"])
        body = {"model": model, "prompt": prompt, "size": size or spec["sizes"][0],
                "length": self._frames(seconds, spec), "steps": 20, "seed": -1,
                "negative_prompt": NEGATIVE_VIDEO}
        if photo and model == m.video_i2v:
            body["image"] = image_to_data_url(photo, max_side=1024)
        job = self._post("/videos/generations", body, f"영상 작업 생성({model})", timeout=120)
        job_id = job.get("job_id") or job.get("id")
        if not job_id:
            raise HubError(f"영상 작업 ID 없음: {json.dumps(job, ensure_ascii=False)[:400]}")
        log(f"    영상 작업 {job_id} ({model}, {seconds:g}초, {body['size']}) — 수 분 걸릴 수 있어요")
        start, last = time.time(), ""
        while True:
            r = self._req("GET", f"{self.base}/jobs/{job_id}", headers=self._h(), timeout=60)
            job = self._check(r, "영상 상태 조회")
            status = str(job.get("status", "")).upper()
            line = f"{status} {job.get('progress', 0)}%"
            if line != last:
                log(f"      {line}  [{int(time.time() - start)}초]")
                last = line
            if status in ("COMPLETED", "SUCCEEDED", "DONE"):
                break
            if status in ("FAILED", "CANCELLED", "CANCELED", "ERROR"):
                raise HubError(f"영상 생성 실패: {job.get('error') or status}")
            if time.time() - start > max_wait:
                raise HubError(f"영상 대기 시간 초과 (작업 ID {job_id})")
            time.sleep(interval)
        art = job.get("artifact_url") or job.get("url")
        if not art:
            raise HubError(f"완료됐지만 artifact_url이 없습니다: {json.dumps(job, ensure_ascii=False)[:300]}")
        ext = Path(urlsplit(art).path).suffix or ".mp4"
        out = out.with_suffix(ext)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self._download(art))
        return out
