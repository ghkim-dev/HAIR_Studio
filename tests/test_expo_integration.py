"""실제 파이프라인 + 가짜 Hub(mock_hub)로 박람회 서버 전체 흐름 확인.

  python tests/test_expo_integration.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> None:
    port = free_port()
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_hub.py"), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    os.environ.update({"OASH_BASE_URL": f"http://127.0.0.1:{port}/v1", "OASH_API_KEY": "test",
                       "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                       "EXPO_OUTPUT_DIR": tempfile.mkdtemp(prefix="expo_int_"), "EXPO_ROUNDS": "2",
                       "EXPO_FAST": os.getenv("EXPO_FAST", "0")})
    fast = os.environ["EXPO_FAST"] == "1"
    learned = ROOT / "knowledge" / "learned_terms.json"
    had_learned = learned.exists()
    try:
        from fastapi.testclient import TestClient
        from tests.test_webapp import sample_data_url
        from webapp import server
        with TestClient(server.app) as c:
            items = c.get("/api/gallery?gender=female").json()["items"]
            hush = next(i for i in items if i["term"] == "허쉬컷")
            r = c.post("/api/jobs", json={"photo": sample_data_url(), "consent": True,
                                          "style_id": hush["id"], "request": "앞머리는 테스트펌 느낌으로"})
            assert r.status_code == 200, r.text
            jid = r.json()["job_id"]
            evs = []
            with c.stream("GET", f"/api/jobs/{jid}/events") as s:
                for line in s.iter_lines():
                    if line.startswith("data: "):
                        ev = json.loads(line[6:])
                        evs.append(ev)
                        if ev.get("type") == "end":
                            break
            stages = [e.get("stage") or e.get("type") for e in evs]
            assert evs[-1]["status"] == "done", evs[-1]
            terms_ev = [e for e in evs if e.get("stage") == "terms" and e.get("terms")][0]
            names = {t["term"]: t for t in terms_ev["terms"]}
            assert "허쉬컷" in names and names["허쉬컷"]["attrs_ko"], names
            if not fast:   # 전체 모드: 모르는 용어를 속성으로 분해
                assert "테스트펌" in names and names["테스트펌"]["source"] == "decomposed", names
            else:          # 빠른 모드: LLM 없이 용어집만, Hub 호출 = 후보당 편집+검증
                import urllib.request
                st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/__stats").read())
                assert st["429"] == 0 and st["ok"] <= 2 * 2 * 2, st
                print("빠른 모드 Hub POST 호출 수:", st["ok"])
            gen = [e for e in evs if e.get("stage") == "verify" and e.get("score") is not None]
            assert gen and all("cand" in e and "round" in e for e in gen)
            snap = c.get(f"/api/jobs/{jid}").json()
            assert c.get(snap["final"]["url"]).status_code == 200
            print("통합 흐름 OK:", " → ".join(dict.fromkeys(stages)))
            print("용어:", {k: (v["source"], v["attrs_ko"][:3]) for k, v in names.items()})
    finally:
        mock.terminate()
        if not had_learned and learned.exists():
            learned.unlink()


if __name__ == "__main__":
    main()
