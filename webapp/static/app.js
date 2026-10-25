/* 박람회 키오스크: 대기 → 동의 → 촬영 → 스타일 선택 → 진행(SSE) → 결과 */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    config: null, photo: null, gender: "", category: "", query: "",
    items: [], style: null, jobId: null, es: null, lastJob: null, stream: null,
  };

  // ------------------------------------------------------------ 화면 전환 + 유휴 타이머
  let idleTimer = null;
  function go(name) {
    $$(".screen").forEach((s) => s.classList.toggle("active", s.id === "s-" + name));
    state.screen = name;
    if (name === "camera") startCamera(); else stopCamera();
    if (name === "style") loadGallery();
    if (name === "attract") resetSession();
    armIdle();
  }
  function armIdle() {
    clearTimeout(idleTimer);
    if (["attract", "progress"].includes(state.screen)) return;
    const sec = state.screen === "result" ? (state.config?.idle_sec || 90) : Math.max(150, state.config?.idle_sec || 90);
    idleTimer = setTimeout(() => go("attract"), sec * 1000);
  }
  ["pointerdown", "keydown", "input"].forEach((ev) => document.addEventListener(ev, armIdle, { passive: true }));
  $$("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 2600);
  }

  function resetSession() {
    if (state.es) { state.es.close(); state.es = null; }
    state.photo = null; state.style = null; state.jobId = null; state.lastJob = null;
    $("#consent-check").checked = false; $("#consent-next").disabled = true;
    $("#free").value = ""; resetShot();
  }

  // ------------------------------------------------------------ 설정
  fetch("/api/config").then((r) => r.json()).then((c) => {
    state.config = c;
    $("#privacy").textContent = c.privacy;
    $("#retention-fact").textContent = `${Math.round(c.retention_min)}분 뒤 자동 삭제돼요`;
    if (c.infra) $("#infra").textContent = c.infra;
    const m = c.models || {};
    $("#models").innerHTML = [
      m.text && `언어 모델 <b>${esc(m.text)}</b>`,
      m.vision && `비전 모델 <b>${esc(m.vision)}</b>`,
      m.image_edit && `이미지 편집 <b>${esc(m.image_edit)}</b>`,
      `라운드 ${c.rounds} × 후보 ${c.candidates}`,
    ].filter(Boolean).map((x) => `<span>${x}</span>`).join("");
  }).catch(() => {});

  // ------------------------------------------------------------ 동의
  $("#consent-check").addEventListener("change", (e) => ($("#consent-next").disabled = !e.target.checked));
  $("#consent-next").addEventListener("click", () => go("camera"));

  // ------------------------------------------------------------ 촬영
  async function startCamera() {
    if (state.photo) return showShot(state.photo);
    resetShot();
    if (state.stream) return;
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 960 }, facingMode: "user" }, audio: false });
      $("#video").srcObject = state.stream;
      $("#no-cam").hidden = true; $("#btn-capture").disabled = false;
    } catch (e) {
      $("#no-cam").hidden = false; $("#btn-capture").disabled = true;
    }
  }
  function stopCamera() {
    if (state.stream) { state.stream.getTracks().forEach((t) => t.stop()); state.stream = null; }
  }
  function resetShot() {
    $("#shot").hidden = true; $("#video").hidden = false;
    $(".stage").classList.remove("has-shot");
    $("#cam-actions").hidden = false; $("#shot-actions").hidden = true;
  }
  function showShot(dataUrl) {
    state.photo = dataUrl;
    $("#shot").src = dataUrl; $("#shot").hidden = false; $("#video").hidden = true;
    $(".stage").classList.add("has-shot");
    $("#cam-actions").hidden = true; $("#shot-actions").hidden = false;
  }
  function capture() {
    const v = $("#video");
    if (!v.videoWidth) return toast("카메라가 아직 준비되지 않았어요");
    // 얼굴 가이드에 맞춰 가운데를 세로 3:4로 잘라 저장 (증명사진 비율 → 머리 위쪽까지 담김)
    const vw = v.videoWidth, vh = v.videoHeight;
    const cw = Math.min(vw, Math.round(vh * 0.75)), ch = Math.min(vh, Math.round(cw / 0.75));
    const sx = Math.round((vw - cw) / 2), sy = Math.round((vh - ch) / 2);
    const scale = Math.min(1, 1024 / ch);
    const c = document.createElement("canvas");
    c.width = Math.round(cw * scale); c.height = Math.round(ch * scale);
    const ctx = c.getContext("2d");
    ctx.translate(c.width, 0); ctx.scale(-1, 1);          // 화면에 보이던 거울 모습 그대로 저장
    ctx.drawImage(v, sx, sy, cw, ch, 0, 0, c.width, c.height);
    showShot(c.toDataURL("image/jpeg", 0.92));
  }
  $("#btn-capture").addEventListener("click", () => {
    const cd = $("#countdown"); let n = 3;
    $("#btn-capture").disabled = true; cd.hidden = false; cd.textContent = n;
    const t = setInterval(() => {
      n -= 1;
      if (n > 0) { cd.textContent = n; return; }
      clearInterval(t); cd.hidden = true; $("#btn-capture").disabled = false; capture();
    }, 1000);
  });
  $("#btn-retake").addEventListener("click", () => { state.photo = null; resetShot(); startCamera(); });
  $("#btn-use").addEventListener("click", () => go("style"));
  $("#file").addEventListener("change", (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      const s = Math.min(1, 1024 / Math.max(img.width, img.height));
      c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      showShot(c.toDataURL("image/jpeg", 0.92)); URL.revokeObjectURL(img.src);
    };
    img.src = URL.createObjectURL(f); e.target.value = "";
  });

  // ------------------------------------------------------------ 스타일 선택
  async function loadGallery() {
    $("#me").src = state.photo || "";
    try {
      const r = await fetch("/api/gallery" + (state.gender ? `?gender=${state.gender}` : ""));
      const d = await r.json();
      state.items = d.items || [];
      renderCats(d.categories || []);
    } catch { state.items = []; }
    renderGrid();
  }
  function renderCats(cats) {
    const present = new Set(state.items.map((i) => i.category));
    const list = ["", ...cats.filter((c) => present.has(c))];
    if (state.category && !present.has(state.category)) state.category = "";
    $("#cats").innerHTML = list.map((c) => `<button class="chip" data-cat="${esc(c)}" aria-pressed="${c === state.category}">${c ? esc(c) : "전체"}</button>`).join("");
    $$("#cats .chip").forEach((b) => b.addEventListener("click", () => { state.category = b.dataset.cat; renderCats(cats); renderGrid(); }));
  }
  const GL = { male: "남성", female: "여성", unisex: "공용" };
  function renderGrid() {
    const q = state.query.trim().toLowerCase();
    const items = state.items.filter((i) => (!state.category || i.category === state.category) &&
      (!q || [i.term, i.description_ko, ...(i.attrs_ko || [])].join(" ").toLowerCase().includes(q)));
    if (!items.length) { $("#grid").innerHTML = `<div class="empty">맞는 스타일이 없어요. 왼쪽 칸에 원하는 모습을 말로 적어도 돼요.</div>`; return; }
    $("#grid").innerHTML = items.map((i) => `
      <button class="tile" data-id="${esc(i.id)}" aria-pressed="${state.style?.id === i.id}">
        <div class="thumb">${i.thumb_url ? `<img src="${esc(i.thumb_url)}" alt="" loading="lazy">` : `<span class="ph ${i.term.length > 6 ? "s" : i.term.length > 4 ? "m" : ""}">${esc(i.term)}</span>`}
          <span class="gtag">${GL[i.gender] || "공용"} · ${esc(i.category)}</span></div>
        <div class="meta"><span class="name">${esc(i.term)}</span>
          <span class="attrs">${esc((i.attrs_ko || []).slice(0, 3).join(" · ") || i.description_ko || "")}</span></div>
      </button>`).join("");
    $$("#grid .tile").forEach((t) => t.addEventListener("click", () => {
      const it = state.items.find((x) => x.id === t.dataset.id);
      state.style = state.style?.id === it.id ? null : it;
      $$("#grid .tile").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.id === state.style?.id)));
      updatePick();
    }));
  }
  function updatePick() {
    const free = $("#free").value.trim();
    const s = state.style;
    $("#btn-make").disabled = !(s || free);
    $("#pick-summary").innerHTML = s
      ? `선택한 스타일 <b>${esc(s.term)}</b>${free ? ` · 추가 요청 “${esc(free)}”` : ""}`
      : free ? `내 설명 <b>“${esc(free)}”</b>` : "스타일을 고르거나 원하는 모습을 적어 주세요";
  }
  $("#free").addEventListener("input", updatePick);
  $("#search").addEventListener("input", (e) => { state.query = e.target.value; renderGrid(); });
  $$(".seg button").forEach((b) => b.addEventListener("click", () => {
    $$(".seg button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    state.gender = b.dataset.gender; state.style = null; updatePick(); loadGallery();
  }));

  // ------------------------------------------------------------ 작업 생성 + 진행
  $("#btn-make").addEventListener("click", startJob);
  $("#btn-retry").addEventListener("click", startJob);
  async function startJob() {
    if (!state.photo) return go("camera");
    $("#btn-make").disabled = true;
    try {
      const r = await fetch("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photo: state.photo, consent: true, gender: state.gender || null,
          style_id: state.style?.id || null, request: $("#free").value.trim() || null }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "요청 실패");
      state.jobId = d.job_id;
      showProgress();
    } catch (e) { showError(e.message); }
    finally { updatePick(); }
  }

  const STEP_OF = { terms: "terms", plan: "plan", checklist: "checklist", round: "generate", design: "generate",
    generate: "generate", verify: "verify", reflect: "reflect", observe: "terms" };
  const ORDER = ["terms", "plan", "checklist", "generate", "verify", "reflect"];
  function setStep(step) {
    const idx = ORDER.indexOf(step);
    if (idx < 0) return;
    if (step === "reflect") state.reflected = true;
    $$("#rail li").forEach((li, i) => {
      const keepReflect = li.dataset.step === "reflect" && state.reflected && step !== "reflect";
      li.classList.toggle("on", i === idx);
      li.classList.toggle("done", i < idx || keepReflect);   // 다음 라운드로 돌아가도 반성 완료 표시는 유지
    });
  }

  function showProgress() {
    go("progress");
    $("#p-input").src = state.photo;
    $("#terms").innerHTML = `<p class="muted">미용 용어를 눈에 보이는 모양으로 바꾸는 중…</p>`;
    $("#cands").innerHTML = ""; $("#status").textContent = "준비 중…";
    $$("#rail li").forEach((li) => li.classList.remove("on", "done"));
    state.reflected = false;
    if (state.es) state.es.close();
    const es = new EventSource(`/api/jobs/${state.jobId}/events`);
    state.es = es;
    es.onmessage = (m) => handle(JSON.parse(m.data));
    es.onerror = () => { if (state.screen === "progress") $("#status").textContent = "연결을 다시 시도하는 중…"; };
  }

  function candEl(round, cand) {
    const id = `c-${round}-${cand}`;
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div"); el.className = "cand"; el.id = id;
      el.innerHTML = `<div class="img">라운드 ${round} · 후보 ${cand} 생성 중…</div><div class="badges"><span class="badge">라운드 ${round} · 후보 ${cand}</span></div>`;
      $("#cands").appendChild(el);
    }
    return el;
  }

  function handle(ev) {
    if (ev.type === "queue") {
      if (ev.queue_position > 0) $("#status").textContent = `앞에 ${ev.queue_position - 1}명이 기다리고 있어요 · 곧 시작합니다`;
      return;
    }
    if (ev.type === "end") { state.es?.close(); state.es = null; if (ev.status !== "done" && state.screen === "progress") showError("결과를 만들지 못했어요."); return; }
    const st = ev.stage;
    if (ev.message) $("#status").textContent = ev.message;
    if (STEP_OF[st]) setStep(STEP_OF[st]);
    if (st === "terms" && ev.terms) renderTerms(ev.terms);
    if (st === "generate" && ev.round) {
      const el = candEl(ev.round, ev.cand || 1);
      if (ev.url) el.querySelector(".img").innerHTML = `<img src="${esc(ev.url)}" alt="">`;
    }
    if (st === "verify" && ev.round) {
      const el = candEl(ev.round, ev.cand || 1);
      if (ev.url && !el.querySelector("img")) el.querySelector(".img").innerHTML = `<img src="${esc(ev.url)}" alt="">`;
      const b = [];
      if (ev.score != null) b.push(`<span class="badge score">점수 ${Math.round(ev.score * 100)}</span>`);
      if (ev.naturalness != null) b.push(`<span class="badge">자연스러움 ${ev.naturalness}/5</span>`);
      if (ev.request_match != null) b.push(`<span class="badge">요청일치 ${ev.request_match}/5</span>`);
      el.querySelector(".badges").innerHTML = b.join("");
      if (ev.notes) { let n = el.querySelector(".note"); if (!n) { n = document.createElement("div"); n.className = "note"; el.appendChild(n); } n.textContent = ev.notes; }
    }
    if (st === "reflect") {
      const r = document.createElement("div"); r.className = "reflect";
      r.textContent = `반성: ${ev.summary_ko || ev.message || "다음 라운드에서 개선합니다"}`;
      $("#cands").appendChild(r);
    }
    if (st === "error") showError(ev.message);
    if (st === "done" && ev.final) {
      $$("#rail li").forEach((li) => { li.classList.remove("on"); li.classList.add("done"); });
      const url = ev.final.url;
      $$(".cand").forEach((c) => c.classList.toggle("best", !!c.querySelector(`img[src="${CSS.escape(url || "")}"]`)));
      setTimeout(() => showResult(ev), 1400);
    }
  }

  function renderTerms(terms) {
    if (!terms.length) { $("#terms").innerHTML = `<p class="muted">미용 용어 없이 문장 그대로 해석했어요.</p>`; return; }
    const SRC = { kb: "용어집", corpus: "용어집", learned: "학습됨", new: "새로 학습", decomposed: "속성 분해" };
    $("#terms").innerHTML = terms.map((t) => `
      <div class="term"><div class="tname">${esc(t.term)} <span class="src">${esc(SRC[t.source] || t.source || "")}</span></div>
        <div class="chips">${(t.attrs_ko || []).slice(0, 6).map((a) => `<span class="chip static">${esc(a)}</span>`).join("")}</div>
        ${t.visual_en ? `<div class="en">→ ${esc(t.visual_en)}</div>` : ""}</div>`).join("");
  }

  // ------------------------------------------------------------ 결과
  function showResult(doneEv) {
    const f = doneEv.final;
    state.lastJob = state.jobId;
    $("#r-before").src = `/api/jobs/${state.jobId}/image/input.jpg`;   // 서버가 저장한 원본(결과와 같은 기준)
    $("#r-after").src = f.url;
    $("#r-qr").src = `/api/jobs/${state.jobId}/qr.png`;
    $("#r-title").textContent = state.style ? `${state.style.term} 완성!` : "완성!";
    const verifies = $$(".cand.best .badge").map((b) => b.textContent);
    const nat = verifies.find((t) => t.startsWith("자연스러움"));
    const mat = verifies.find((t) => t.startsWith("요청일치"));
    $("#r-scores").innerHTML = [
      `<div><b>${Math.round((f.score || 0) * 100)}</b><small>종합 점수</small></div>`,
      `<div><b>${nat ? nat.replace("자연스러움 ", "") : "-"}</b><small>자연스러움</small></div>`,
      `<div><b>${mat ? mat.replace("요청일치 ", "") : "-"}</b><small>요청 일치</small></div>`,
    ].join("");
    $("#r-terms").innerHTML = $$("#terms .tname").map((t) => `<span class="chip static">${esc(t.childNodes[0].textContent.trim())}</span>`).join("");
    setSplit(50);
    go("result");
  }
  function setSplit(pct) {
    pct = Math.max(0, Math.min(100, pct));
    $("#before-clip").style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
    $("#handle").style.left = pct + "%";
    $("#handle").setAttribute("aria-valuenow", Math.round(pct));
  }
  (() => {
    const cmp = $("#compare"); let drag = false;
    const at = (e) => { const r = cmp.getBoundingClientRect(); setSplit(((e.clientX - r.left) / r.width) * 100); };
    cmp.addEventListener("pointerdown", (e) => { drag = true; cmp.setPointerCapture(e.pointerId); at(e); });
    cmp.addEventListener("pointermove", (e) => drag && at(e));
    cmp.addEventListener("pointerup", () => (drag = false));
    $("#handle").addEventListener("keydown", (e) => {
      const v = +$("#handle").getAttribute("aria-valuenow");
      if (e.key === "ArrowLeft") setSplit(v - 5); if (e.key === "ArrowRight") setSplit(v + 5);
    });
  })();
  $("#btn-again").addEventListener("click", () => { state.style = null; $("#free").value = ""; updatePick(); go("style"); });
  $("#btn-home").addEventListener("click", () => go("attract"));
  $("#btn-delete").addEventListener("click", async () => {
    if (state.lastJob) await fetch(`/api/jobs/${state.lastJob}/delete`, { method: "POST" }).catch(() => {});
    toast("사진과 결과를 삭제했어요. 이용해 주셔서 감사합니다!");
    setTimeout(() => go("attract"), 1200);
  });

  function showError(msg) {
    if (state.es) { state.es.close(); state.es = null; }
    $("#err-msg").textContent = msg || "잠시 후 다시 시도해 주세요.";
    go("error");
  }

  state.screen = "attract";
})();
