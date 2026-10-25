"""실행 결과를 브라우저로 볼 수 있는 report.html로 정리 (run 폴더 기준 상대 경로)."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path


def _rel(path: str | None, base: Path) -> str:
    if not path:
        return ""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _media(path: str | None, base: Path) -> str:
    if not path:
        return '<div class="empty">생성 실패</div>'
    src = html.escape(_rel(path, base))
    if path.endswith((".mp4", ".webm")):
        return f'<video src="{src}" controls muted loop></video>'
    return f'<img src="{src}" alt="">'


def write_report(state: dict, run_dir: Path) -> Path:
    e = html.escape
    spec = state.get("spec", {})
    qs = state.get("checklist", [])
    parts = []

    parts.append(f"<h1>{e(state['config'].get('name', 'run'))}</h1>")
    parts.append(f"<p class='req'>{e(state['config'].get('request', ''))}</p>")
    parts.append("<p class='meta'>" + " · ".join(f"{k}: {e(str(v))}" for k, v in state.get("models", {}).items() if v) + "</p>")

    if state.get("final"):
        f = state["final"]
        parts.append(f"<section class='final'><h2>최종 결과 · 점수 {f['score']:.2f}</h2>"
                     f"<div class='pair'>{_media(state['config'].get('photo') if state['config'].get('photo') != 'None' else None, run_dir)}"
                     f"{_media(f['file'], run_dir)}</div><p class='prompt'>{e(f['prompt'])}</p></section>")

    terms = state.get("terms") or []
    if terms:
        src = {"kb": "검증 사전", "learned": "학습 초안(미검토)", "new": "이번에 새로 학습"}
        parts.append("<h2>0. 용어 이해</h2><table class='terms'><tr><th>용어</th><th>출처</th>"
                     "<th>이미지 모델에 전달한 뜻</th><th>하면 안 되는 모양</th></tr>")
        for t in terms:
            refs = "".join(f"<img class='ref' src='{html.escape(_rel(r, run_dir))}' alt=''>" for r in t.get("refs", []))
            parts.append(f"<tr><td><b>{e(t['term'])}</b>{refs}</td><td>{e(src.get(t.get('source'), str(t.get('source'))))}"
                         f"{'<br><small>' + e(str(t.get('notes_ko', ''))) + '</small>' if t.get('notes_ko') else ''}</td>"
                         f"<td>{e(str(t.get('visual_en', '')))}</td><td>{e(', '.join(map(str, t.get('must_not', []))))}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>1. 계획 (요구사항 분해)</h2>")
    parts.append(f"<p><b>의도</b> {e(spec.get('intent', ''))}</p><ul>")
    for s in spec.get("sub_edits", []):
        parts.append(f"<li><code>{e(str(s.get('id')))}</code> [{e(str(s.get('type')))}] {e(str(s.get('instruction')))}</li>")
    parts.append("</ul><p><b>보존</b> " + e(", ".join(map(str, spec.get("preserve", [])))) + "</p>")
    if spec.get("assumptions"):
        parts.append("<p><b>가정</b> " + e(" / ".join(map(str, spec["assumptions"]))) + "</p>")

    parts.append("<h2>2. 검증 체크리스트</h2><ol>")
    for q in qs:
        parts.append(f"<li><span class='k {e(q['kind'])}'>{e(q['kind'])}</span> {e(q['question'])} <small>×{q['weight']}</small></li>")
    parts.append("</ol>")

    if state.get("preview_prompts"):
        parts.append("<h2>3. 첫 라운드 프롬프트 (plan-only)</h2><ul>")
        for p in state["preview_prompts"]:
            parts.append(f"<li><p class='prompt'>{e(p['prompt'])}</p><small>{e(str(p.get('rationale', '')))}</small></li>")
        parts.append("</ul>")

    for rnd in state.get("rounds", []):
        parts.append(f"<h2>라운드 {rnd['round']}</h2><div class='grid'>")
        for c in rnd["candidates"]:
            best = " best" if c.get("file") and c.get("file") == rnd.get("best") else ""
            failed = "".join(f"<li>{e(q['question'])}<br><small>{e(str(q.get('evidence', '')))}</small></li>" for q in c.get("failed", []))
            parts.append(f"<div class='card{best}'>{_media(c.get('file'), run_dir)}"
                         f"<div class='score'>점수 {c.get('score', 0):.2f}{' · 정체성 실패' if c.get('identity_fail') else ''}</div>"
                         f"{('<small>자연스러움 ' + str(c['naturalness']) + '/5 · 요청일치 ' + str(c['request_match']) + '/5</small>') if c.get('naturalness') else ''}"
                         f"<p class='prompt'>{e(c['prompt'])}</p>"
                         f"<p>{e(str(c.get('notes') or c.get('error') or ''))}</p>"
                         f"{'<details><summary>실패한 검증</summary><ul>' + failed + '</ul></details>' if failed else ''}</div>")
        parts.append("</div>")
        if rnd.get("reflection"):
            r = rnd["reflection"]
            parts.append(f"<p class='refl'><b>반성 [{e(str(r.get('route')))}]</b> {e(str(r.get('summary_ko', '')))}"
                         f"<br><small>{e(str(r.get('reflection', '')))}</small></p>")

    css = """
    :root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e3e2dc;--acc:#2f6f5e}
    @media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#9a9992;--card:#20201e;--line:#34332f;--acc:#7cc4ad}}
    body{background:var(--bg);color:var(--fg);font:15px/1.6 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px 16px}
    h1{margin:0}h2{margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px}
    .req{font-size:17px}.meta,small{color:var(--mut)}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
    .card.best{border:2px solid var(--acc)}
    img,video{width:100%;border-radius:6px;display:block}
    .pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:640px}
    .score{font-weight:700;margin-top:6px}.prompt{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:var(--mut)}
    .k{font-size:11px;padding:1px 6px;border-radius:8px;border:1px solid var(--line)}
    .refl{background:var(--card);border-left:3px solid var(--acc);padding:8px 12px}
    .terms{border-collapse:collapse;width:100%;font-size:13.5px}.terms td,.terms th{border:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}
    img.ref{width:64px;display:inline-block;margin:4px 4px 0 0}
    .empty{padding:40px;text-align:center;color:var(--mut);border:1px dashed var(--line);border-radius:6px}
    """
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>Hair Studio 리포트</title>"
           f"<style>{css}</style></head><body>{''.join(parts)}</body></html>")
    out = run_dir / "report.html"
    out.write_text(doc, encoding="utf-8")
    return out
