# HAIR Studio — 헤어 용어 지식 기반 AI 헤어스타일 체험 서비스

> 경북IT융합산업기술원 AI클라우드센터 **Open AI Service Hub**(H100 GPU 기반 공공 AI 모델 서빙 플랫폼)의 언어·비전·이미지 편집 모델을 엮어, 사람이 말하는 헤어 용어("애즈펌으로, 옆은 다운펌")를 이해하고 사진 속 인물의 헤어스타일만 바꿔 주는 서비스입니다. 명령줄 도구와 박람회용 키오스크 웹 서비스 두 가지 형태로 동작합니다.

---

## 목차

1. [개요](#1-개요)
2. [주요 기능](#2-주요-기능)
3. [시스템 구조](#3-시스템-구조)
4. [파이프라인 단계](#4-파이프라인-단계)
5. [적용 논문](#5-적용-논문)
6. [헤어 용어 지식 구조](#6-헤어-용어-지식-구조)
7. [전체 모드와 빠른 모드](#7-전체-모드와-빠른-모드)
8. [기술 스택](#8-기술-스택)
9. [Open AI Service Hub 사용 규격](#9-open-ai-service-hub-사용-규격)
10. [프로젝트 구조](#10-프로젝트-구조)
11. [설치와 실행](#11-설치와-실행)
12. [설정](#12-설정-env)
13. [테스트와 평가](#13-테스트와-평가)
14. [개발 과정에서 해결한 문제](#14-개발-과정에서-해결한-문제)
15. [한계와 향후 과제](#15-한계와-향후-과제)
16. [개인정보 보호](#16-개인정보-보호)
17. [참고문헌](#17-참고문헌)

---

## 1. 개요

### 문제

범용 이미지 편집 모델은 한국어 문장은 어느 정도 읽지만, **미용실 용어가 어떤 모양인지**는 모릅니다. 첫 실험에서 "애즈펌으로, 앞머리는 C컬로 살짝 넘기고 옆은 다운펌"을 그대로 넣었더니, 한쪽에 큰 컬 덩어리가 생기고 옆머리가 밀린 결과가 나왔습니다. 결과를 판정하는 비전 모델도 이 결과에 만점을 줬습니다.

### 접근

1. **용어를 먼저 이해한다.** 헤어스타일을 17가지 시각 속성(기장·가르마·앞머리·컬·옆머리·볼륨…)의 조합으로 정의한 **닫힌 속성 어휘**와, 공개 자료를 조사해 만든 **남녀 용어집 163개**로 용어를 "눈에 보이는 모양"으로 번역합니다.
2. **만든 뒤 스스로 검사하고 고친다.** 같은 속성 정의에서 이미지 프롬프트와 검증 질문을 함께 만들고, 비전 모델이 결과를 채점해 부족하면 반성 내용을 반영해 다시 만듭니다.
3. **인프라 제약에 맞춘다.** 동시 요청 1건인 개발키에서도 돌아가도록, 용어집으로 LLM 호출을 대신하는 빠른 모드를 둡니다.

### 결과물

| 형태 | 용도 |
|---|---|
| `hair_studio.py` (명령줄) | 요청 문장·사진 → 결과 이미지·영상 + 단계별 리포트(`report.html`) |
| `webapp` (웹 서비스) | 박람회 키오스크: 촬영 → 스타일 선택 → AI 처리 과정 실시간 시연 → 전후 비교·QR 다운로드 |

---

## 2. 주요 기능

- **헤어 용어 이해**: 남성 73개·여성 90개 용어(컷·펌·앞머리·스타일링·묶음), 별칭 매칭, 성별별 정의 분리
- **처음 보는 용어 분해**: 용어집에 없는 말은 LLM이 같은 속성 어휘 안에서만 분해하고 신뢰도를 표시
- **참고 사진 반영**: `knowledge/images/<용어>/`에 사진을 넣으면 비전 모델이 정의를 보강하고 검증에도 사용
- **자기 검증·반성 루프**: 후보 N장 생성 → 체크리스트·자연스러움·요청 일치로 채점 → 최고 후보 선택 → 부족하면 다음 라운드
- **얼굴 보존**: 정체성 질문에 가중치 2배, 실패 시 점수 하드 페널티
- **사진 → 영상**: 결과 이미지를 첫 프레임으로 짧은 영상 생성(`wan2.2-i2v`)
- **박람회 웹 서비스**: 웹캠 촬영, 남녀 스타일 갤러리·검색·자유 입력, 진행 과정 실시간 표시, 전후 슬라이더, QR 다운로드, 대기열, 자동 삭제
- **갤러리 썸네일 자동 생성**: 용어집 속성으로 텍스트→이미지 모델이 스타일 예시 사진을 생성
- **리포트**: 용어 해석·계획·체크리스트·라운드별 후보·점수·실패 근거·반성을 한 페이지로 기록

---

## 3. 시스템 구조

```
            ┌──────────────── 박람회 키오스크 (Chrome, 웹캠) ────────────────┐
            │ 동의 → 촬영 → 스타일 갤러리/자유 입력 → 진행 화면 → 전후 비교·QR │
            └───────────────┬─────────────────────────────▲──────────────────┘
                     POST /api/jobs                  SSE /api/jobs/{id}/events
            ┌───────────────▼─────────────────────────────┴──────────────────┐
            │ webapp/server.py (FastAPI)  대기열 · 작업자 · 보관기간 자동 삭제   │
            └───────────────┬────────────────────────────────────────────────┘
                            │ RunConfig(on_event=…)
            ┌───────────────▼────────────────────────────────────────────────┐
            │ pipeline/orchestrator.py                                         │
            │  관찰 → 용어 이해 → 계획 → 체크리스트 → [설계→생성→검증→선택→반성]×R │
            │      ▲ pipeline/lexicon.py ◀── knowledge/ (속성 어휘·용어집)       │
            └───────────────┬────────────────────────────────────────────────┘
                            │ pipeline/hub.py (동시 한도 제어 · 재시도)
            ┌───────────────▼────────────────────────────────────────────────┐
            │ Open AI Service Hub  (https://open.hasa.re.kr/v1, H100 GPU)      │
            │  qwen3-next-80b(언어) · qwen3-vl-32b(비전) · Qwen-Image-Edit(편집) │
            │  Qwen-Image(생성) · wan2.2-i2v / Wan2.2-T2V(영상)                  │
            └────────────────────────────────────────────────────────────────┘
```

---

## 4. 파이프라인 단계

| 단계 | 담당 | 하는 일 | 근거 논문 |
|---|---|---|---|
| 0 관찰 | 비전 모델 | 사진에서 지켜야 할 정체성 단서(얼굴형·안경·옷·배경·구도) 추출 | — |
| 0.5 용어 이해 | 용어집 (+ 언어·비전 모델) | 용어 검색 → 없으면 속성 분해 → 참고 사진 반영 → 영어 시각 묘사·검증 질문 생성 | RAVEL, ImageRAG |
| 1 계획 | 언어 모델 | 요청을 원자적 하위 편집 + 보존 목록 + 가정으로 분해 | X-Planner |
| 2 체크리스트 | 언어 모델 + 용어집 | 하위 편집·보존·품질에 대한 yes/no 질문과 가중치 | TIFA |
| 3 설계 | 언어 모델 | 이전 라운드 기록(메모리)을 읽고 서로 다른 후보 프롬프트 N개 작성 | Idea2Img |
| 생성 | 이미지 편집 모델 | 후보별 편집 (긍정 프롬프트 + negative_prompt 분리) | — |
| 4 검증 | 비전 모델 | 원본·참고 사진·결과를 보고 질문마다 근거→답, 자연스러움·요청 일치 1~5점 | TIFA |
| 선택 | 규칙 | 최고 점수 후보 선택, 기준 점수 이상이면 종료 | Idea2Img |
| 5 반성 | 언어 모델 | 실패 원인 진단 → 수정 경로 라우팅 → 다음 라운드 지시문 | GenMAC, Reflect-DiT |

**점수 산식**

```
점수 = 0.5 × 체크리스트 가중 통과율 + 0.25 × (자연스러움−1)/4 + 0.25 × (요청 일치−1)/4
- 얼굴 정체성 질문(가중치 2.0) 실패 → 점수 × 0.6
- 자연스러움 또는 요청 일치가 2점 이하 → 점수 상한 0.5 (그 라운드에서 끝나지 않고 반성으로)
```

**반성 경로**: `strengthen_edit`(요청 특징이 약함) · `protect_identity`(얼굴·옷 변형) · `fix_quality`(인공적인 모양) · `tone_down`(과한 컬·옆머리 밀림 등 요청 밖 변화) · `clarify_spec`(계획 자체 오류)

---

## 5. 적용 논문

| 논문 | 학회 | 핵심 아이디어 | 이 프로젝트에 적용한 방식 |
|---|---|---|---|
| **X-Planner** — Beyond Simple Edits: X-Planner for Complex Instruction-Based Image Editing | AAAI 2026 | MLLM이 chain-of-thought로 복잡·간접 지시를 단순한 하위 지시로 분해하고, 하위 지시마다 편집 유형과 마스크를 붙여 정체성을 보존하는 국소 편집으로 만듦 | `agents.plan()`: 요청 → `sub_edits[{type, region, instruction}]` + `preserve[]`. Hub에 분할 모델이 없어 마스크 대신 영역 라벨 사용 |
| **TIFA** — Accurate and Interpretable Text-to-Image Faithfulness Evaluation with Question Answering | ICCV 2023 | 프롬프트에서 질문을 만들고 VQA 모델이 답하게 해 충실도를 해석 가능한 점수로 측정 | `agents.checklist()`·`verify()`: edit/preserve/quality 질문, 가중치, 근거 먼저 쓰고 답하기. 여기에 자연스러움·요청 일치 총평을 추가 |
| **Idea2Img** — Iterative Self-Refinement with GPT-4V for Automatic Image Design and Generation | ECCV 2024 | 멀티모달 모델이 프롬프트 생성 → 초안 선택 → 피드백 반영을 반복하며 탐색 이력을 메모리로 유지 | `agents.design()`: 메모리를 읽고 다른 후보 N개 작성, 오케스트레이터가 최고 점수 후보 선택 |
| **GenMAC** — Compositional Text-to-Video Generation with Multi-Agent Collaboration | AAAI 2026 | Design → Generation → Redesign 반복. Redesign을 검증·제안·보정·출력 구조화로 나누고, 상황에 맞는 보정 에이전트를 스스로 고르는 self-routing | `agents.reflect()`: failures(검증) → route(라우팅) → reflection(보정) → JSON(구조화) |
| **Reflect-DiT** — Inference-Time Scaling for Text-to-Image Diffusion Transformers via In-Context Reflection | ICCV 2025 | 단순 best-of-N 대신 이전 생성물과 텍스트 피드백을 맥락으로 넣어 다음 생성을 개선 | 라운드마다 `{prompt, score, failed, reflection}`을 메모리에 쌓아 다음 설계의 맥락으로 제공 (모델 학습 없이 프롬프트 수준) |
| **RAVEL** — Rare Concept Generation and Editing via Graph-driven Relational Guidance | NeurIPS 2026 | 생성 모델이 모르는 희귀 개념을 지식에서 속성·관계로 검색해 프롬프트를 보강하고, 목표 속성 충족을 반복 평가해 부족한 속성만 수정 | `lexicon.py`: 용어집에서 속성 조합 검색 → 시각 묘사·검증 질문을 같은 속성에서 생성. 모르는 용어는 속성 어휘 안에서 분해 |
| **ImageRAG** — Dynamic Image Retrieval for Reference-Guided Image Generation | ICLR 2026 | 모델이 모르는 개념은 참조 이미지를 찾아 보여줘서 보완 | `knowledge/images/<용어>/` 참고 사진을 비전 모델이 읽어 속성 보정, 검증 때 결과와 비교 (Hub 편집 API가 참조 이미지 1장만 받아 얼굴 사진에 쓰므로 생성이 아닌 이해·검증에 사용) |

**논문과 다르게 한 부분**

- 논문들은 GPT-4V급 모델을 가정하지만, 여기서는 Hub에서 키로 쓸 수 있는 공개 모델을 역할별로 자동 선택해 사용합니다.
- X-Planner의 마스크 생성, GenMAC의 레이아웃(바운딩 박스) 제어는 Hub에 해당 기능이 확인되지 않아 텍스트 지시 수준으로만 반영했습니다.
- 도구 호출(function calling) 에이전트 대신 고정 파이프라인을 택했습니다. Hub 문서도 도구 호출이 불안정한 환경에서는 "해석 → 증강 → 추론 → 생성" 고정 파이프라인이 재현성·디버깅에 유리하다고 권합니다.

---

## 6. 헤어 용어 지식 구조

**닫힌 속성 어휘 + 열린 용어집**으로 구성됩니다. 모든 스타일은 속성 값의 조합이므로, 처음 보는 용어도 같은 어휘로 분해할 수 있고, 어휘 밖의 키·값은 버려져 정의가 엉뚱하게 퍼지지 않습니다.

```
knowledge/
├── attributes.json      닫힌 속성 어휘: 17개 속성 · 98개 값 (값마다 한국어/영어 묘사)
├── corpus/
│   ├── male.json        남성 용어 73개
│   └── female.json      여성 용어 90개
├── hair_terms.json      직접 추가·수정 (용어집보다 우선)
├── learned_terms.json   용어집에 없어 LLM이 분해한 초안 (검토 후 확정, git 제외)
├── images/<용어>/        참고 사진 (선택)
└── gallery/             갤러리 썸네일 (tools/build_gallery.py로 생성, git 제외)
```

**속성 어휘 17종**: 전체 기장 · 윗머리 · 가르마 · 앞머리 형태 · 앞머리 길이 · 이마 노출 · 컬·웨이브 · 컬 크기 · 컬 위치 · 옆머리 · 뒷머리 · 층 · 끝 모양 · 볼륨 위치 · 마무리 질감 · 실루엣 · 묶음

**용어 항목 형식**

```json
{
  "term": "애즈펌",
  "aliases": ["애즈 펌", "애즈", "as perm"],
  "gender": "male", "category": "perm",
  "attrs": {"length": "short", "bangs": "full_straight", "bangs_length": "brow",
            "forehead": "little", "texture": "c_curl", "curl_size": "small",
            "curl_zone": "bang_ends", "sides": "flat_down"},
  "visual_en": "Short men's perm: the bangs fall down onto the forehead and their ends curve softly inward…",
  "must_not": ["a clear wide side part with the forehead mostly exposed", "big or loose curls", "shaved sides"],
  "description_ko": "앞머리를 내리되 … 끝을 C컬로 말아 준 펌",
  "popularity": "high", "confidence": "medium",
  "sources": ["https://…"]
}
```

**용어집 현황**

| | 남성 | 여성 |
|---|---|---|
| 용어 수 | 73 | 90 |
| 분류 | 컷 36 · 펌 23 · 앞머리 5 · 가르마 4 · 스타일링 3 · 시술 2 | 펌 35 · 컷 30 · 묶음 9 · 앞머리 8 · 시술 5 · 가르마 3 |
| 신뢰도 (상/중/하) | 30 / 35 / 8 | 43 / 39 / 8 |
| 주요 출처 | GQ Korea, Esquire Korea, Creatrip, 미용 커뮤니티 | Allure Korea, Vogue Korea, W Korea, Harper's Bazaar |

- 용어마다 출처 URL 1~3개를 기록했습니다. 출처끼리 설명이 엇갈린 용어는 신뢰도를 낮추고 `description_ko`에 이견을 적었습니다.
- 같은 이름이 남녀 모두에 있으면(가르마펌·울프컷·리프컷 등) 성별 정의를 따로 쓰고, 성별을 지정하면 다른 성별 용어집은 매칭에서 제외합니다("C컬로"가 남성 요청에서 여성 C컬펌으로 잡히는 것 방지).
- 이미지 프롬프트와 검증 질문이 **같은 속성**에서 만들어지므로, "다운펌 = 옆머리 밀기" 같은 오해는 검증 단계에서 걸립니다.

---

## 7. 전체 모드와 빠른 모드

Hub 개발키는 **동시 요청 1건** 제한이 있어 모든 호출이 줄을 섭니다. 갤러리에서 고른 스타일은 용어집에 속성이 이미 있으므로, 계획·체크리스트·프롬프트·반성을 규칙으로 대체하는 빠른 모드를 둡니다.

| | 전체 모드 | 빠른 모드 (`--fast`, `EXPO_FAST`) |
|---|---|---|
| 관찰·용어 분석·계획·체크리스트·설계 | 언어·비전 모델 5~6회 | 0회 (용어집 규칙) |
| 후보 1장 | 편집 1 + 검증 1 | 편집 1 + 검증 1 |
| 반성 | 언어 모델 1회 | 0회 (실패 질문 → 다음 프롬프트·negative 보강) |
| 2라운드 × 후보 2장 | 약 15회 | **8회** |
| 적용 대상 | 모든 요청 | 용어집에서 용어가 잡힌 요청 (못 찾으면 자동으로 전체 모드) |

박람회 서버는 `OASH_MAX_CONCURRENCY=1`이면 빠른 모드를 자동으로 켭니다.

---

## 8. 기술 스택

### AI 모델 (Open AI Service Hub)

| 역할 | 모델 | 용도 |
|---|---|---|
| 언어 | `qwen3-next-80b` | 계획·체크리스트·프롬프트 설계·반성·용어 분해 |
| 비전 | `qwen3-vl-32b` | 사진 관찰·결과 검증·참고 사진 분석 |
| 이미지 편집 | `Qwen-Image-Edit` | 원본 사진의 헤어스타일 편집 |
| 텍스트→이미지 | `Qwen-Image` | 갤러리 썸네일 생성 |
| 영상 | `wan2.2-i2v` / `Wan2.2-T2V` | 결과 사진 → 짧은 영상 |

역할별 모델은 `/v1/models` 목록에서 자동 선택합니다. 범용 모델을 우선하고 OCR·코딩 전용 모델(`paddleocr-vl`, `qwen3-coder` 등)은 제외하며, `.env`로 직접 지정할 수 있습니다.

### 소프트웨어

| 영역 | 기술 |
|---|---|
| 언어 | Python 3.9+ |
| HTTP 클라이언트 | `requests` — 프로세스 전체 동시 요청 세마포어, 429/503 `retry_after` 준수 재시도, 연결 끊김 재시도 |
| 설정 | `python-dotenv` (`.env`) |
| 이미지 처리 | `Pillow` (크기 조정·EXIF 제거·비교 이미지), `opencv-python-headless` (영상 프레임 추출) |
| 웹 서버 | `FastAPI` + `uvicorn`, Server-Sent Events로 진행 상황 스트리밍, `ThreadPoolExecutor` 대기열 |
| QR 코드 | `qrcode[pil]` |
| 프런트엔드 | 빌드 없는 HTML/CSS/JavaScript, `getUserMedia` 웹캠, `EventSource`(SSE), 오프라인 동작 |
| 리포트 | 정적 HTML (`report.html`) + JSON (`run.json`) |
| 테스트 | 가짜 Hub 서버(`http.server`, 동시 한도·연결 끊김 흉내), FastAPI `TestClient`, Playwright 화면 캡처 |

### 인프라

- 경북IT융합산업기술원 AI클라우드센터 **Open AI Service Hub** (H100 GPU, OpenAI 호환 API)
- 박람회 부스: Windows 노트북/미니PC + 웹캠 + 가로형 모니터, Chrome 키오스크 모드

---

## 9. Open AI Service Hub 사용 규격

포털 Playground가 실제로 쓰는 규격을 기준으로 구현했습니다.

| 기능 | 요청 | 비고 |
|---|---|---|
| 모델 목록 | `GET /v1/models` | 공개 (키 검사 없음) |
| 채팅·비전 | `POST /v1/chat/completions` | OpenAI 호환, 비전은 `content`에 `image_url`(data URL) |
| 이미지 생성 | `POST /v1/images/generations` `{model, prompt, size, steps, response_format:"b64_json"}` | |
| 이미지 편집 | 위와 같은 경로 + `reference`: 원본 사진 data URL | `/images/edits`가 아님. `negative_prompt`는 서버가 거부하면 자동으로 빼고 재요청 |
| 영상 생성 | `POST /v1/videos/generations` `{model, prompt, size, length(프레임), steps, seed, negative_prompt, image}` | 응답 `job_id` |
| 작업 상태 | `GET /v1/jobs/{job_id}` | `QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED`, 완료 시 `artifact_url` |
| 인증 | `Authorization: Bearer <API Key>` | |

---

## 10. 프로젝트 구조

```
HAIR_Studio/
├── hair_studio.py            명령줄 도구 (models · run · hair · terms)
├── pipeline/
│   ├── hub.py                Hub 클라이언트, 모델 자동 선택, 동시 한도·재시도
│   ├── lexicon.py            용어 이해 계층 (검색·분해·참고 사진·렌더·갤러리)
│   ├── agents.py             단계별 에이전트 (LLM 버전 + 빠른 모드 규칙 버전)
│   ├── prompts.py            에이전트 시스템 프롬프트
│   ├── orchestrator.py       단계 실행·라운드 루프·이벤트 발행
│   ├── report.py             report.html 생성
│   └── utils.py              JSON 추출, 이미지 인코딩, 영상 프레임
├── knowledge/                속성 어휘 · 남녀 용어집 · 직접 추가 용어
├── webapp/
│   ├── server.py             FastAPI 서버 (대기열·SSE·QR·자동 삭제)
│   ├── static/               키오스크 화면 (index.html · app.js · style.css)
│   └── README_EXPO.md        박람회 부스 운영 안내
├── tools/build_gallery.py    갤러리 썸네일 생성
├── tests/
│   ├── mock_hub.py           가짜 Hub 서버
│   ├── test_webapp.py        웹 API·SSE·화면 캡처 테스트
│   ├── test_expo_integration.py  실제 파이프라인 + 가짜 Hub 통합 테스트
│   └── eval_terms.py         용어 인식 회귀 테스트
├── PIPELINE.md               파이프라인 설계 상세
├── requirements.txt
└── .env.example
```

---

## 11. 설치와 실행

### 설치

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env          :: OASH_API_KEY 에 개발키 입력
python hair_studio.py models     :: 맨 아래 "개발키 정상 ✔" 확인
```

### 명령줄

```bat
:: 요구사항이 어떻게 이해됐는지만 보기 (생성 없음)
python hair_studio.py run --photo 내사진.jpg --request "애즈펌" --gender male --plan-only

:: 이미지 만들기 (빠른 모드)
python hair_studio.py run --photo 내사진.jpg --request "애즈펌" --gender male --fast --name 애즈펌

:: 추천 4개 스타일 일괄 + 비교 이미지
python hair_studio.py hair --photo 내사진.jpg

:: 결과 사진 → 영상
python hair_studio.py run --photo output\…\cand1.png --output video --seconds 4 --request "고개를 좌우로 천천히 돌려 헤어스타일을 보여줘"

:: 용어집
python hair_studio.py terms list
python hair_studio.py terms show 허쉬컷
python hair_studio.py terms learn 크러쉬컷
```

결과 폴더마다 `report.html`(용어 해석·계획·체크리스트·라운드별 후보·점수·실패 근거·반성)과 `run.json`이 생깁니다.

### 박람회 웹 서비스

```bat
python tools\build_gallery.py --limit 40        :: 인기 스타일 썸네일 미리 생성 (선택)
python -m webapp.server                         :: http://localhost:8000
```

부스 PC에서는 PowerShell로 키오스크 모드를 엽니다.

```powershell
Start-Process chrome -ArgumentList '--kiosk','--use-fake-ui-for-media-stream','http://localhost:8000'
```

장비·네트워크·처리량 계산·행사 전 체크리스트는 [`webapp/README_EXPO.md`](webapp/README_EXPO.md)를 참고하세요.

---

## 12. 설정 (`.env`)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `OASH_API_KEY` | — | Hub 개발키 (필수, git에 올리지 않음) |
| `OASH_BASE_URL` | `https://open.hasa.re.kr/v1` | Hub 주소 |
| `OASH_TEXT_MODEL` / `OASH_VISION_MODEL` / `OASH_IMAGE_MODEL` / `OASH_IMAGE_EDIT_MODEL` / `OASH_VIDEO_MODEL` | 자동 | 역할별 모델 직접 지정 |
| `OASH_MAX_CONCURRENCY` | 1 | 키의 동시 요청 한도. 실증키·운영키는 그 한도로 |
| `OASH_MAX_RETRIES` | 6 | 429/503/연결 끊김 재시도 횟수 |
| `OASH_IMAGE_STEPS` | 20 | 이미지 생성·편집 스텝 |
| `OASH_TIMEOUT` | 600 | 요청 타임아웃(초) |
| `EXPO_PORT` | 8000 | 웹 서버 포트 |
| `EXPO_WORKERS` | 2 | 동시 처리 관람객 수 |
| `EXPO_ROUNDS` / `EXPO_CANDIDATES` / `EXPO_THRESHOLD` | 2 / 2 / 0.8 | 라운드·후보 수·조기 종료 점수 |
| `EXPO_FAST` | auto | 빠른 모드 (auto: 동시 한도 1이면 켬) |
| `EXPO_RETENTION_MIN` | 30 | 사진·결과 자동 삭제(분) |
| `EXPO_IDLE_SEC` | 90 | 결과 화면 무입력 시 첫 화면 복귀(초) |
| `EXPO_PUBLIC_URL` | 자동 | QR에 넣을 주소 (휴대폰이 접속 가능한 주소) |

---

## 13. 테스트와 평가

실제 Hub 없이 실행할 수 있습니다. 가짜 Hub 서버는 동시 요청 한도(`MOCK_CONCURRENCY`)와 주기적 연결 끊김(`MOCK_RESET_EVERY`)을 흉내 냅니다.

```bat
python tests\test_webapp.py               :: 웹 API·SSE 흐름 (+ --shots: 화면 캡처)
python tests\test_expo_integration.py     :: 실제 파이프라인 + 가짜 Hub, 전체 모드
set EXPO_FAST=1 && python tests\test_expo_integration.py   :: 빠른 모드 (Hub 호출 수 검증)
python tests\eval_terms.py                :: 용어 인식 회귀 테스트
```

| 항목 | 결과 |
|---|---|
| 웹 API·SSE (동의 거부·경로 탈출 차단·EXIF 제거·삭제 포함) | 통과 |
| 통합 흐름: 관찰 → 용어 → 계획 → 체크리스트 → 설계 → 생성 → 검증 → 반성 → 완료 | 통과 (Python 3.9 · 3.11) |
| 빠른 모드 Hub 호출 수 (2라운드 × 후보 2장) | 8회, 429 발생 0회 |
| 동시 한도 1건 서버에 3건 동시 요청 | 429 발생 0회 (세마포어) |
| 연결 끊김(3회 중 1회) 상황 | 재시도로 정상 완료 |
| 용어 인식 회귀 세트 36문장 | 36/36 |

> 용어 인식 세트는 이 세트에 맞춰 별칭을 보강했으므로 일반적인 정확도 지표가 아니라 **회귀 확인용**입니다.

---

## 14. 개발 과정에서 해결한 문제

| 문제 | 원인 | 해결 |
|---|---|---|
| "가르마펌"이 5:5 가운데 가르마로 해석됨 | LLM이 미용 용어를 추측 | 용어집 도입, 계획 단계에서 용어 지식을 권위 있는 근거로 주입 |
| 애즈펌 결과가 한쪽 큰 컬 + 옆머리 밀림 | 한국어 용어를 이미지 모델에 그대로 전달, 금지어를 본문에 서술 | 용어를 시각 속성으로 번역, 금지 모양은 `negative_prompt`로 분리, `tapered` 묘사를 "피부가 보이지 않게"로 수정 |
| 비전 모델이 어색한 결과에 만점 | yes/no 질문만으로는 관대한 판정 | 근거 먼저 쓰기, "확실하지 않으면 no", 자연스러움·요청 일치 총평과 점수 상한, 표준 품질 질문 추가 |
| 이미지 편집 404 | 공개 문서에 이미지 API 명세 없음 | 포털 Playground 코드에서 실제 규격(`reference` 방식, `/v1/jobs`) 확인 |
| 비전 모델로 OCR 전용 모델 선택 | 이름에 `vl`만 보고 선택 | 범용 VLM 우선, OCR·코딩 전용 제외 |
| 429 동시 요청 한도 초과 | 개발키 동시 1건 | 프로세스 전체 세마포어, `retry_after` 준수, 빠른 모드 |
| 연결 끊김으로 실행 전체 중단 | 네트워크 예외 미처리 | 연결 끊김 재시도, 검증 실패 후보만 "미확정" 처리 |
| Python 3.9에서 서버 기동 실패 | FastAPI가 `str \| None` 표기를 해석 못 함 | `Optional[...]` 표기로 변경, 3.9 환경에서 전체 테스트 |
| `pip install` cp949 디코드 오류 | 구버전 pip가 requirements의 한글 주석을 cp949로 읽음 | requirements를 ASCII로 유지 |

---

## 15. 한계와 향후 과제

- **실제 결과 품질은 편집 모델 성능에 좌우됩니다.** 얼굴 보존과 미세한 헤어 변화(컬 크기, 가르마 비율)는 모델 한계로 어긋날 수 있습니다.
- **용어집 검증이 필요합니다.** 출처가 주로 패션지·블로그·커뮤니티이며, NCS 헤어미용 학습모듈이나 예약 앱 분류는 반영하지 못했습니다. 신뢰도 하 16개(쉐도우펌·빈티지펌·그레이스펌 등)는 미용 전문가 검토를 권합니다.
- **속성 어휘의 표현력.** 페이드 시작 높이(로우·미드·하이), 이마 위 단일 컬 같은 세부는 어휘에 없어 묘사 문장으로만 구분됩니다.
- **Hub 기능 확인 필요.** 이미지 편집의 `negative_prompt` 실제 반영 여부, 편집 강도(`true_cfg_scale` 등), 참조 이미지 여러 장 지원 여부, JSON 출력 모드(`response_format`) 지원 여부. 지원되면 참고 사진을 생성에도 쓰고 JSON 재시도를 없앨 수 있습니다.
- **처리량.** 개발키(동시 1건)에서는 관람객을 한 명씩 처리합니다. 박람회에는 동시 한도가 큰 실증키·운영키를 권장합니다.
- **향후**: 참조 이미지 기반 스타일 전이, 분할 모델을 이용한 머리 영역 마스크 편집, 미용 전문가 검수 데이터로 평가 세트 확장, 다각도 영상 시연.

---

## 16. 개인정보 보호

- 촬영 전 동의 화면을 거치며, 서버도 동의 없는 요청을 거부합니다.
- 사진은 EXIF(촬영 위치·기기 정보)를 제거한 뒤 부스 서버의 `output/expo/`에만 저장합니다.
- 기본 30분 뒤 자동 삭제되며, 결과 화면의 **내 사진 삭제**로 즉시 지울 수 있습니다.
- 모델 호출은 Open AI Service Hub로만 나갑니다.
- 개인 사진(`증명사진.jpg` 등), `.env`, 실행 결과는 `.gitignore`로 저장소에서 제외합니다.

---

## 17. 참고문헌

1. X-Planner — *Beyond Simple Edits: X-Planner for Complex Instruction-Based Image Editing*. AAAI 2026. [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/38187) · [arXiv:2507.05259](https://arxiv.org/abs/2507.05259)
2. TIFA — *Accurate and Interpretable Text-to-Image Faithfulness Evaluation with Question Answering*. ICCV 2023. [CVF Open Access](https://openaccess.thecvf.com/content/ICCV2023/html/Hu_TIFA_Accurate_and_Interpretable_Text-to-Image_Faithfulness_Evaluation_with_Question_Answering_ICCV_2023_paper.html)
3. Idea2Img — *Iterative Self-Refinement with GPT-4V for Automatic Image Design and Generation*. ECCV 2024. [ECVA](https://ecva.net/papers/eccv_2024/papers_ECCV/html/5515_ECCV_2024_paper.php) · [arXiv:2310.08541](https://arxiv.org/abs/2310.08541)
4. GenMAC — *Compositional Text-to-Video Generation with Multi-Agent Collaboration*. AAAI 2026. [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/37418) · [arXiv:2412.04440](https://arxiv.org/abs/2412.04440)
5. Reflect-DiT — *Inference-Time Scaling for Text-to-Image Diffusion Transformers via In-Context Reflection*. ICCV 2025. [CVF Open Access](https://openaccess.thecvf.com/content/ICCV2025/html/Li_Reflect-DiT_Inference-Time_Scaling_for_Text-to-Image_Diffusion_Transformers_via_In-Context_Reflection_ICCV_2025_paper.html) · [arXiv:2503.12271](https://arxiv.org/abs/2503.12271)
6. RAVEL — *Rare Concept Generation and Editing via Graph-driven Relational Guidance*. NeurIPS 2026. [NeurIPS](https://neurips.cc/virtual/2026/poster/150127) · [arXiv:2412.09614](https://arxiv.org/abs/2412.09614)
7. ImageRAG — *Dynamic Image Retrieval for Reference-Guided Image Generation*. ICLR 2026. [ICLR](https://iclr.cc/virtual/2026/poster/10009135) · [arXiv:2502.09411](https://arxiv.org/abs/2502.09411)
8. Open AI Service Hub 문서 — [포털](https://api.gbcloud.ai) · [Hub 문서](https://dolzi-gitc.github.io/GITC-Open-AI-Service-Hub/) · [도구 호출 가이드](https://dolzi-gitc.github.io/GITC-Open-AI-Service-Hub/guides/tool-calling/)

용어집 각 항목의 출처 URL은 `knowledge/corpus/*.json`의 `sources` 필드에 있습니다.
