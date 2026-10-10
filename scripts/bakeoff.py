"""Engine bake-off: the same animation prompts and seeds on every engine, scored by the
automatic checks, then a blind A/B page to pick the default by ear.

    python scripts/bakeoff.py                      # all prompts, 3 seeds each, both engines
    python scripts/bakeoff.py --seeds 11,12 --only whoosh-sword,ui-confirm

Needs the server (started in its own window if it isn't running). Writes output/bakeoff/: results.json (every take's scores),
summary.md (per-engine averages), the best take of each engine per prompt as blind
A/B files, and index.html, served at http://127.0.0.1:8191/bakeoff/. Your votes on that
page are stored in output/bakeoff/votes.json. Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sfx import OUTPUT, ServerError, call, ensure_server  # noqa: E402  (scripts/sfx.py)

BAKEOFF = OUTPUT / "bakeoff"

# id, request, extra request fields. Short, terse requests on purpose: that's what an
# agent sends; the category recipe does the rest.
PROMPTS = [
    ("impact-sword", "two swords clashing", {"duration": 1.5}),
    ("impact-crate", "a wooden crate dropped on a wooden floor", {"duration": 1.5}),
    ("impact-glass", "a glass bottle shattering on concrete", {"duration": 2.0}),
    ("impact-rock", "a heavy rock falling onto stone ground", {"duration": 1.5}),
    ("punch", "a cartoon punch to the face", {"duration": 0.8}),
    ("whoosh-sword", "a fast sword swing whoosh", {"duration": 1.0}),
    ("whoosh-zoom", "a character zooming past the camera, whoosh", {"duration": 1.5}),
    ("ui-confirm", "a soft positive UI confirmation blip", {"duration": 0.6}),
    ("ui-error", "a short UI error buzz", {"duration": 0.6}),
    ("step-gravel", "a single footstep on gravel", {"duration": 0.8}),
    ("step-wood", "a single footstep on a creaky wooden floor", {"duration": 0.8}),
    ("step-snow", "a single footstep in deep snow", {"duration": 0.8}),
    ("boing", "a cartoon spring boing", {"duration": 1.0}),
    ("pop", "a cartoon bubble pop", {"duration": 0.5}),
    ("magic", "a magic wand sparkle spell", {"duration": 2.0}),
    ("scifi-door", "a sci-fi door opening with a hydraulic hiss", {"duration": 2.0}),
    ("robot-powerup", "a robot powering up", {"duration": 2.5}),
    ("creature-small", "a small creature growling", {"duration": 1.5}),
    ("creature-dragon", "a dragon roar", {"duration": 3.0}),
    ("explosion", "a big explosion with debris", {"duration": 4.0}),
    ("amb-forest", "quiet forest ambience with birds", {"duration": 8.0, "mode": "loop"}),
    ("amb-rain", "steady rain on a window ambience", {"duration": 8.0, "mode": "loop"}),
]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default="sa3,moss")
    ap.add_argument("--seeds", default="11,12,13")
    ap.add_argument("--only", help="comma-separated prompt ids")
    ap.add_argument("--page-only", action="store_true", help="rebuild the page from results.json")
    a = ap.parse_args()
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    seeds = [int(s) for s in a.seeds.split(",")]
    todo = [p for p in PROMPTS if not a.only or p[0] in a.only.split(",")]
    try:
        ensure_server(10)  # starts it in its own window if it isn't running
    except ServerError as exc:
        print(exc, file=sys.stderr)
        return 1
    BAKEOFF.mkdir(parents=True, exist_ok=True)
    results_path = BAKEOFF / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.is_file() else {}

    if not a.page_only:
        for pid, text, extra in todo:
            for eng in engines:
                key = f"{pid}|{eng}"
                if key in results:
                    continue
                t0 = time.time()
                res = call("/api/generate", {"text": text, "engine": eng, "seeds": seeds, **extra})
                res["wall_s"] = round(time.time() - t0, 1)
                results[key] = res
                best = res["takes"][0]
                print(f"{pid:16s} {eng:5s} {res['wall_s']:6.1f}s  kept {res['kept']}/{len(res['takes'])}  "
                      f"best score {best['rank_score']:.2f}  CLAP {best.get('clap_cos', 0):.2f}  "
                      f"PQ {(best.get('aes') or {}).get('PQ', 0):.1f}", flush=True)
                results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- summary
    lines = ["# Bake-off summary", "", f"Seeds {seeds} per prompt; scores are over every take.", "",
             "| engine | takes | rejected | mean CLAP cos | mean CLAP softmax | mean PQ | mean rank score (best take) | "
             "s per take |", "|---|---|---|---|---|---|---|---|"]
    for eng in engines:
        rs = [r for k, r in results.items() if k.endswith(f"|{eng}")]
        takes = [t for r in rs for t in r["takes"]]
        if not takes:
            continue
        lines.append(f"| {eng} | {len(takes)} | {sum(t.get('rejected', False) for t in takes)} | "
                     f"{mean(t.get('clap_cos') for t in takes)} | {mean(t.get('clap') for t in takes)} | "
                     f"{mean((t.get('aes') or {}).get('PQ') for t in takes)} | "
                     f"{mean(r['takes'][0]['rank_score'] for r in rs)} | "
                     f"{round(sum(r['gen_s'] for r in rs) / len(takes), 2)} |")
    lines += ["", "| prompt | " + " | ".join(f"{e} best (CLAP / PQ / score)" for e in engines) + " |",
              "|---|" + "---|" * len(engines)]
    for pid, text, _ in PROMPTS:
        cells = []
        for eng in engines:
            r = results.get(f"{pid}|{eng}")
            if not r:
                cells.append("-")
                continue
            b = r["takes"][0]
            cells.append(f"{b.get('clap_cos', 0):.2f} / {(b.get('aes') or {}).get('PQ', 0):.1f} / {b['rank_score']:.2f}"
                         + (" REJ" if b.get("rejected") else ""))
        lines.append(f"| {pid} | " + " | ".join(cells) + " |")
    (BAKEOFF / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    # ---------------------------------------------------------------- blind A/B page
    if len(engines) != 2:
        return 0
    rng = random.Random(20261004)
    pairs = []
    for pid, text, _ in PROMPTS:
        rs = [results.get(f"{pid}|{e}") for e in engines]
        if not all(rs):
            continue
        order = engines[:] if rng.random() < 0.5 else engines[::-1]
        sides = {}
        for side, eng in zip("AB", order):
            r = results[f"{pid}|{eng}"]
            b = r["takes"][0]
            for kind in ("wav", "png"):
                shutil.copyfile(OUTPUT / b["files"][kind], BAKEOFF / f"{pid}_{side}.{kind}")
            sides[side] = {"engine": eng, "score": b["rank_score"], "clap": b.get("clap_cos"),
                           "pq": (b.get("aes") or {}).get("PQ"), "seed": b["seed"], "ref": b["ref"],
                           "warnings": b.get("warnings", []), "rejected": b.get("rejected", False)}
        pairs.append({"id": pid, "text": text, "prompt": rs[0]["prompt"], "sides": sides})
    (BAKEOFF / "index.html").write_text(page(pairs), encoding="utf-8")
    print(f"\nA/B page: http://127.0.0.1:8191/bakeoff/  ({len(pairs)} pairs)")
    return 0


def page(pairs: list[dict]) -> str:
    data = json.dumps(pairs)
    rows = []
    for i, p in enumerate(pairs, 1):
        rows.append(f"""
<section class="pair" data-id="{p['id']}">
  <h2>{i}. {html.escape(p['text'])}</h2>
  <div class="prompt">{html.escape(p['prompt'])}</div>
  <div class="sides">
    <div class="side"><b>A</b><audio controls preload="none" src="{p['id']}_A.wav"></audio>
      <img loading="lazy" src="{p['id']}_A.png" alt="spectrogram A"><div class="reveal" data-side="A"></div></div>
    <div class="side"><b>B</b><audio controls preload="none" src="{p['id']}_B.wav"></audio>
      <img loading="lazy" src="{p['id']}_B.png" alt="spectrogram B"><div class="reveal" data-side="B"></div></div>
  </div>
  <div class="vote">
    <button data-w="A">A is better</button><button data-w="B">B is better</button>
    <button data-w="tie">Both good</button><button data-w="neither">Neither</button>
    <span class="done"></span>
  </div>
</section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SFX bake-off</title>
<style>
:root {{ --bg:#f6f6f4; --fg:#1c1c1c; --card:#fff; --muted:#666; --line:#ddd; --acc:#2c6bd6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#17181a; --fg:#e8e8e8; --card:#222326; --muted:#999; --line:#383a3e; --acc:#6ea2ff; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:0 auto; max-width:1200px; padding:16px; }}
h1 {{ font-size:22px; }} h2 {{ font-size:17px; margin:0 0 4px; }}
.pair {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; margin:14px 0; }}
.prompt {{ color:var(--muted); font-size:13px; margin-bottom:10px; }}
.sides {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
@media (max-width:760px) {{ .sides {{ grid-template-columns:1fr; }} }}
.side img {{ width:100%; border-radius:6px; margin-top:6px; }} .side audio {{ width:100%; margin-top:4px; }}
.vote {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
button {{ font:inherit; padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer; }}
button.picked {{ background:var(--acc); color:#fff; border-color:var(--acc); }}
.reveal {{ font-size:13px; color:var(--muted); margin-top:4px; min-height:1em; }}
#tally {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:2; }}
</style></head><body>
<h1>SFX bake-off: Stable Audio 3 Small-SFX vs MOSS-SoundEffect v2</h1>
<p>Each pair is the automatic checks' best take from each engine (same prompt, same 3 seeds).
Sides are shuffled; the engine is revealed after you vote. Listen on headphones, then pick.</p>
<div id="tally">No votes yet.</div>
{''.join(rows)}
<script>
const PAIRS = {data};
const byId = Object.fromEntries(PAIRS.map(p => [p.id, p]));
function reveal(sec) {{
  const p = byId[sec.dataset.id];
  sec.querySelectorAll('.reveal').forEach(el => {{
    const s = p.sides[el.dataset.side];
    el.textContent = `${{s.engine}} · seed ${{s.seed}} · score ${{s.score}} · CLAP ${{s.clap}} · PQ ${{s.pq}}` +
      (s.rejected ? ' · REJECTED' : '') + (s.warnings.length ? ' · ' + s.warnings.join('; ') : '');
  }});
}}
function showTally(t) {{
  document.getElementById('tally').textContent = 'Votes: ' + t.votes + ' — ' +
    Object.entries(t.tally).map(([k, v]) => `${{k}} ${{v}}`).join(', ');
}}
document.querySelectorAll('.pair').forEach(sec => {{
  sec.querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {{
    const p = byId[sec.dataset.id], w = b.dataset.w;
    const winner = (w === 'A' || w === 'B') ? p.sides[w].engine : w;
    const r = await fetch('/api/bakeoff/vote', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{prompt_id: p.id, winner}})}});
    if (!r.ok) {{ sec.querySelector('.done').textContent = 'vote failed: ' + r.status; return; }}
    sec.querySelectorAll('button').forEach(x => x.classList.toggle('picked', x === b));
    sec.querySelector('.done').textContent = 'saved';
    reveal(sec); showTally(await r.json());
  }}));
}});
fetch('/api/bakeoff/votes').then(r => r.json()).then(v => {{
  const t = {{}}; Object.values(v).forEach(x => t[x.winner] = (t[x.winner] || 0) + 1);
  if (Object.keys(v).length) showTally({{votes:Object.keys(v).length, tally:t}});
  for (const [id, x] of Object.entries(v)) {{
    const sec = document.querySelector(`.pair[data-id="${{id}}"]`); if (!sec) continue;
    const p = byId[id];
    const w = (x.winner === 'tie' || x.winner === 'neither') ? x.winner :
      Object.entries(p.sides).find(([k, s]) => s.engine === x.winner)?.[0];
    sec.querySelectorAll('button').forEach(b => b.classList.toggle('picked', b.dataset.w === w));
    reveal(sec);
  }}
}});
</script></body></html>"""


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ServerError as exc:  # results.json keeps what was done: a re-run carries on
        print(exc, file=sys.stderr)
        sys.exit(exc.code)
