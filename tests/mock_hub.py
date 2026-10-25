"""오프라인 테스트용 가짜 Hub 서버 (OpenAI 호환 응답 흉내).

  python tests/mock_hub.py            # http://127.0.0.1:8765/v1
  set OASH_BASE_URL=http://127.0.0.1:8765/v1 && set OASH_API_KEY=test && python hair_studio.py run ...

실제 Hub 없이 파이프라인 흐름(계획→체크리스트→설계→생성→검증→반성)이 끝까지 도는지 확인할 때 씁니다.
"""

import base64
import io
import itertools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_round = itertools.count()
# 실제 Hub처럼 동시 요청 한도를 흉내 (MOCK_CONCURRENCY, 기본 1). 넘으면 429 concurrent_limit
import os, threading, time
LIMIT = int(os.getenv("MOCK_CONCURRENCY", "1"))
INFLIGHT = [0]
LOCK = threading.Lock()
STATS = {"429": 0, "ok": 0, "reset": 0}
RESET_EVERY = int(os.getenv("MOCK_RESET_EVERY", "0"))   # N번째 요청마다 연결을 끊어 ConnectionReset 흉내
_count = itertools.count(1)


def _png(color=(120, 160, 200)) -> bytes:
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (256, 320), color).save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:  # 1x1 png
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def chat_reply(system: str) -> dict:
    if "ALREADY KNOWN list" in system:
        return {"unknown_terms": ["테스트펌"]}
    if "CLOSED attribute vocabulary" in system:
        return {"known": False, "aliases": [], "gender": "unisex", "category": "perm",
                "attrs": {"texture": "s_wave", "curl_size": "medium", "length": "ear", "bogus_key": "x"},
                "must_not": ["test wrong"], "description_ko": "모의 분해", "confidence": "low"}
    if "Stage 0" in system:
        return {"subject": "young Korean man ID photo", "identity_anchors": ["oval face", "single eyelids"],
                "current_hair": "thick black hair with full bangs", "accessories": ["round metal glasses"],
                "clothing": "black jacket, black tee", "background": "white", "framing_lighting": "frontal, soft",
                "photo_type": "ID photo"}
    if "Stage 1" in system:
        return {"intent": "Change hairstyle to 6:4 side-part perm", "output": "image",
                "sub_edits": [{"id": "e1", "type": "hair_style", "region": "hair",
                               "instruction": "6:4 side part with soft S-waves, forehead one-third visible"},
                              {"id": "e2", "type": "hair_style", "region": "hair",
                               "instruction": "sides pressed flat (down perm)"}],
                "preserve": ["face identity", "round glasses", "black jacket", "white background"],
                "style": "photorealistic ID photo", "video": None, "assumptions": ["가르마 방향은 왼쪽으로 가정"]}
    if "Stage 2" in system:
        return {"questions": [
            {"id": "q1", "kind": "edit", "ref": "e1", "question": "Is the hair parted about 6:4?", "weight": 1.5},
            {"id": "q2", "kind": "edit", "ref": "e2", "question": "Are the sides flat?", "weight": 1.0},
            {"id": "q3", "kind": "preserve", "ref": "face", "question": "Is it clearly the same person?", "weight": 2.0},
            {"id": "q4", "kind": "quality", "ref": "-", "question": "Is the hairline natural?", "weight": 1.0}]}
    if "Stage 3" in system:
        return {"prompts": [{"prompt": "Change only the hairstyle to a 6:4 side-part perm ... keep face identical.",
                             "rationale": "기본 지시"},
                            {"prompt": "Restyle hair: soft S-wave 6:4 parting, forehead partly visible ... keep glasses.",
                             "rationale": "볼륨 강조"}]}
    if "Stage 4" in system:
        n = next(_round)
        good = n >= 2  # 첫 라운드는 일부 실패, 이후 통과 → 반성 루프 확인
        ans = {f"q{i}": {"evidence": "ok", "answer": "yes"} for i in range(1, 8)}
        if not good:
            ans["q2"] = {"evidence": "sides puffy", "answer": "no"}
        return {"answers": ans, "naturalness": 5 if good else 2, "request_match": 5 if good else 3,
                "notes": "옆머리가 아직 떠 있음" if not good else "요구사항 충족"}
    if "Stage 5" in system:
        return {"failures": ["sides not flat"], "route": "strengthen_edit",
                "reflection": "Explicitly say the side hair is pressed flat against the head above the ears.",
                "summary_ko": "옆머리 다운펌 표현을 강화"}
    return {"ok": True}


class H(BaseHTTPRequestHandler):
    jobs: dict = {}

    def _send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/__stats"):
            return self._send(STATS)
        if self.path.endswith("/models"):
            return self._send({"data": [{"id": x} for x in
                                        ("qwen3-coder", "qwen3-next-80b", "paddleocr-vl", "qwen3-vl-32b", "bge-m3", "Qwen-Image",
                                         "Qwen-Image-Edit", "Wan2.2-T2V", "wan2.2-i2v", "LTX-2")]})
        if "/jobs/" in self.path:
            jid = self.path.split("/jobs/")[1]
            return self._send({"job_id": jid, "status": "COMPLETED", "progress": 100,
                               "artifact_url": f"/files/{jid}.webm"})
        if self.path.startswith("/files/"):
            return self._send(b"\x1aE\xdf\xa3fakewebm", ctype="video/webm")
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if RESET_EVERY and next(_count) % RESET_EVERY == 0:
            STATS["reset"] += 1
            import socket as _s
            self.connection.setsockopt(_s.SOL_SOCKET, _s.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            self.connection.close()
            return
        with LOCK:
            if INFLIGHT[0] >= LIMIT:
                STATS["429"] += 1
                return self._send({"detail": {"error": "concurrent_limit", "retry_after": 1}}, 429)
            INFLIGHT[0] += 1
        try:
            if "/images/" in self.path:
                time.sleep(0.3)          # 생성 시간 흉내 → 동시 요청이 겹치게
            STATS["ok"] += 1
            return self._post(raw)
        finally:
            with LOCK:
                INFLIGHT[0] -= 1

    def _post(self, raw):
        if self.path.endswith("/chat/completions"):
            req = json.loads(raw)
            system = next((m["content"] for m in req["messages"] if m["role"] == "system"), "")
            content = "```json\n" + json.dumps(chat_reply(system), ensure_ascii=False) + "\n```"
            return self._send({"choices": [{"message": {"role": "assistant", "content": content}}]})
        if self.path.endswith("/images/generations"):
            req = json.loads(raw)
            STATS["last_negative"] = req.get("negative_prompt", "")
            STATS["last_prompt"] = req.get("prompt", "")
            assert req.get("response_format") == "b64_json"
            return self._send({"data": [{"b64_json": base64.b64encode(_png((200, 120, 120) if req.get("reference") else (120, 160, 200))).decode()}]})
        if self.path.endswith("/videos/generations"):
            req = json.loads(raw)
            assert "length" in req
            return self._send({"job_id": "job_1" + ("_i2v" if req.get("image") else "_t2v"), "status": "QUEUED"})
        self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock hub: http://127.0.0.1:{port}/v1")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
