# 파이프라인 구조와 근거 논문

"머리만 가르마펌으로, 안경이랑 얼굴은 그대로" 같은 사람의 말은 이미지 모델이 그대로 알아듣기 어렵습니다.
그래서 Hub의 LLM(텍스트)과 VLM(비전)을 앞뒤에 붙여서 **요구사항을 먼저 이해하고, 만든 뒤 스스로 검사하고, 고쳐서 다시 만드는** 구조로 짰습니다.

```
요구사항(한국어) + 사진
   │
   ▼
[0] 관찰 Observe      VLM   사진에서 지켜야 할 정체성 단서 추출 (얼굴형, 안경, 옷, 배경…)
[0.5] 용어 이해       지식  헤어 용어 → 닫힌 속성 어휘 (용어집 검색 → 없으면 속성 분해 → 참고사진 반영) ← RAVEL, ImageRAG
[1] 계획 Plan         LLM   요구사항 → 원자적 하위 편집(e1, e2…) + 보존 목록 + 가정        ← X-Planner
[2] 체크리스트        LLM   하위 편집·보존 항목마다 yes/no 검증 질문 (가중치)                  ← TIFA
   │
   ├──── 라운드 루프 (최대 --rounds) ─────────────────────────────────────┐
   │ [3] 설계 Design   LLM   메모리(이전 프롬프트·점수·반성)를 보고 후보 프롬프트 N개    ← Idea2Img
   │     생성          이미지 편집 / 이미지 생성 / 영상 모델로 후보 N개 생성
   │ [4] 검증 Verify   VLM   원본+결과를 보고 체크리스트에 답 → 가중 점수              ← TIFA
   │     선택                최고 점수 후보 선택 (best-of-N)                           ← Idea2Img
   │ [5] 반성 Reflect  LLM   실패 원인 → 수정 경로 라우팅 → 다음 라운드용 지시         ← GenMAC, Reflect-DiT
   └──── 점수 ≥ --threshold 이면 조기 종료 ────────────────────────────────┘
   ▼
report.html (계획·체크리스트·라운드별 후보·점수·반성) + run.json
```

## 단계별로 가져온 아이디어

| 단계 | 논문 | 가져온 것 | 이 코드에서 |
|---|---|---|---|
| 0.5 용어 이해 | **RAVEL** — *Rare Concept Generation and Editing via Graph-driven Relational Guidance* (NeurIPS 2026) | 생성 모델이 모르는 희귀 개념을 지식에서 속성·관계로 검색해 프롬프트를 보강하고, 생성물이 목표 속성을 만족하는지 반복 평가해 부족한 속성만 고친다 | `knowledge/hair_terms.json`(검증 사전: 용어 → 속성·시각 묘사·금지 모양·검증 질문)에서 별칭 매칭으로 검색해 계획·체크리스트·검증에 그대로 주입. 사전에 없는 용어는 LLM이 같은 스키마로 정의해 `learned_terms.json`에 쌓음 |
| 0.5 용어 이해(시각) | **ImageRAG** — *Dynamic Image Retrieval for Reference-Guided Image Generation* (ICLR 2026) | 모델이 모르는 개념은 참조 이미지를 찾아 보여줘서 보완 | `knowledge/images/<용어>/`에 참고 사진을 넣으면 VLM이 사진을 보고 정의를 갱신하고, 검증 때 참고 사진을 함께 보여 결과 머리 모양과 비교. (Hub 편집 API는 참조 이미지를 1장만 받아 얼굴 사진에 쓰므로, 참고 사진은 생성이 아니라 이해·검증 쪽에 사용) |
| 1 계획 | **X-Planner** — *Beyond Simple Edits: X-Planner for Complex Instruction-Based Image Editing* (AAAI 2026) | MLLM이 chain-of-thought로 복잡·간접적인 지시를 단순한 하위 지시로 쪼개고, 하위 지시마다 편집 유형과 영역을 붙여 정체성을 보존하는 국소 편집으로 만든다 | `agents.plan()` — `sub_edits[{type, region, instruction}]`, `preserve[]`. 한국어 헤어 용어를 시각적 묘사로 풀어 씀. (논문의 마스크 자동 생성은 Hub에 분할 모델이 없어 영역 라벨로 대체) |
| 2·4 체크리스트·검증 | **TIFA** — *Accurate and Interpretable Text-to-Image Faithfulness Evaluation with Question Answering* (ICCV 2023) | 프롬프트에서 질문을 생성하고 VQA 모델이 답하게 해서 충실도를 해석 가능한 점수로 만든다 | `agents.checklist()`, `agents.verify()` — edit/preserve/quality 질문, 정체성 질문은 가중치 2.0, 정체성 실패 시 점수 ×0.6 하드 페널티 |
| 3 설계·선택 | **Idea2Img** — *Iterative Self-Refinement with GPT-4V for Automatic Image Design and Generation* (ECCV 2024) | 멀티모달 모델이 프롬프트 생성 → 초안 이미지 선택 → 피드백 반영을 반복하고, 탐색 이력을 메모리로 유지 | `agents.design()`이 메모리를 읽고 서로 다른 후보 N개 작성, 오케스트레이터가 최고 점수 후보 선택 |
| 5 반성·라우팅 | **GenMAC** — *Compositional Text-to-Video Generation with Multi-Agent Collaboration* (AAAI 2026) | Design → Generation → Redesign 반복. Redesign을 검증 → 제안 → 보정 → 출력 구조화로 나누고, 상황에 맞는 보정 에이전트를 스스로 고르는 self-routing | `agents.reflect()` — failures(검증) → route(라우팅: strengthen_edit / protect_identity / fix_quality / clarify_spec) → reflection(보정) JSON(구조화) |
| 5 반성 메모리 | **Reflect-DiT** — *Inference-Time Scaling for Text-to-Image Diffusion Transformers via In-Context Reflection* (ICCV 2025) | 단순 best-of-N 대신, 이전 생성물과 그에 대한 텍스트 피드백을 맥락으로 넣어 다음 생성을 개선하는 추론 시점 스케일링 | 라운드마다 `{prompt, score, failed, reflection}`을 메모리에 쌓아 다음 설계 단계의 맥락으로 제공. 생성 모델 자체를 학습시키진 않으므로 프롬프트 수준에서 적용 |

## 용어 지식 구조: 닫힌 속성 어휘 + 열린 용어집

```
knowledge/attributes.json     닫힌 속성 어휘 17종 (기장·윗머리·가르마·앞머리·앞머리 길이·이마 노출·컬·컬 크기·컬 위치·
                              옆머리·뒷머리·층·끝 모양·볼륨·질감·실루엣·묶음) — 값마다 한국어/영어 묘사
knowledge/corpus/male.json    남성 용어 73개   ┐ 공개 자료(패션지·미용실·미용사 블로그 등) 조사, 용어마다 출처 1~3개,
knowledge/corpus/female.json  여성 용어 90개   ┘ 별칭·속성 조합·하면 안 되는 모양·신뢰도·인기도
knowledge/hair_terms.json     직접 추가/수정 (용어집보다 우선)
knowledge/learned_terms.json  용어집에 없어 LLM이 속성 어휘 안에서 분해한 초안 (검토 후 확정)
```

- 모든 스타일은 속성 값의 **조합**이라, 처음 보는 용어도 같은 어휘로 분해됩니다. LLM이 어휘에 없는 키·값을 내면 버리므로 정의가 엉뚱하게 퍼지지 않습니다.
- 이미지 모델에 넘기는 묘사와 검증 질문이 **같은 속성**에서 만들어지므로, "다운펌 = 옆머리 밀기" 같은 오해는 검증 단계에서 걸립니다.
- 같은 이름이 남녀 모두에 있으면(가르마펌, 울프컷, 리프컷 등) 성별 정의를 따로 씁니다.
- `tests/eval_terms.py`: 실제 요청 문장 36개 → 인식돼야 할 용어. 용어집을 고친 뒤 회귀 확인용입니다(이 세트에 맞춰 별칭을 보강했으므로 일반 정확도 지표는 아닙니다).

## 왜 용어 이해를 따로 두었나

이미지 편집 모델(Qwen-Image-Edit)은 한국어 문장은 어느 정도 읽지만 "애즈펌", "다운펌" 같은 미용실 은어가 어떤 **모양**인지는 모릅니다.
실제 첫 실행에서 "C컬로 넘기고 옆은 다운펌"이 "한쪽에 큰 컬 덩어리 + 옆머리 밀기"로 그려졌습니다.
그래서 디자이너가 일러스트레이터에게 설명하듯, 용어를 **눈에 보이는 속성**(앞머리 방향, 컬 크기, 이마 노출, 옆머리 상태)으로 풀고,
**하면 안 되는 모양**(must_not)과 **검증 질문**(checks)까지 같은 정의에서 꺼내 씁니다. 생성·검증이 같은 뜻을 공유하므로 "다운펌 = 옆머리 밀기" 같은 오해를 검증 단계에서 잡을 수 있습니다.

## 논문과 다르게 한 부분 (Hub 환경에 맞춘 단순화)

- 논문들은 GPT-4V급 모델을 가정합니다. 여기서는 키로 쓸 수 있는 Hub 모델을 자동으로 골라 씁니다(텍스트: qwen 계열 우선, 비전: `*-vl*` 등). 비전 모델이 약하면 검증 점수가 흔들릴 수 있으니 `report.html`에서 판정 근거(notes, 실패 질문)를 꼭 같이 보세요.
- X-Planner의 마스크·박스 생성, GenMAC의 레이아웃(바운딩 박스) 제어는 Hub에 해당 모델·파라미터가 확인되지 않아 빼고, 텍스트 지시 수준으로만 반영했습니다.
- 영상은 첫·중간·끝 프레임을 뽑아(opencv) 같은 체크리스트로 검증합니다. 시간적 일관성 자체를 따로 평가하진 않습니다.

## 조절 손잡이

| 옵션 | 기본 | 의미 |
|---|---|---|
| `--rounds` | 3 | 설계→생성→검증→반성 최대 반복 수 |
| `--candidates` | 2 | 라운드당 후보 수 (Hub 호출 수 ≈ rounds × candidates × 2) |
| `--threshold` | 0.9 | 이 점수 이상이면 조기 종료 |
| `--plan-only` | – | 생성 없이 0~3단계만. 요구사항을 모델이 어떻게 이해했는지 먼저 확인할 때 |
