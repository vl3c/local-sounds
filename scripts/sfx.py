"""Command-line front end for the local-sounds server (standard library only).

The server must be running (start.cmd). Everything stays on 127.0.0.1.

    python scripts/sfx.py "sword hit on a shield"                       # 4 takes, ranked
    python scripts/sfx.py "sword hit" --duration 1.2 --out D:/anim/sfx    # deliver the best one
    python scripts/sfx.py "door slam" --align-frame 12 --fps 24 --out D:/anim/sfx
    python scripts/sfx.py "rain on a tin roof" --loop --duration 8 --out D:/anim/sfx
    python scripts/sfx.py "footstep on gravel" --variations 5 --out D:/anim/sfx
    python scripts/sfx.py "laser zap" --like 20261004-120000-ab12:1234      # more like that take
    python scripts/sfx.py --post 20261004-120000-ab12 --align-frame 6       # re-process, no regeneration
    python scripts/sfx.py --layer A:1 --layer B:7,gain=-6,offset=2f --out D:/anim/sfx
    python scripts/sfx.py "a flock of seagulls" --stack 4 --out D:/anim/sfx  # 4 takes in one, richer
    python scripts/sfx.py --show-prompt "sword hit"                        # what the model would get

A take is '<request id>:<seed>' (the CLI prints it as `ref`). Delivered files get UCS-style
names (METLImpt_SwordHit_v01.wav) and a JSON sidecar; they never overwrite anything.

Exit status: 0 done, 1 failed or no server, 2 bad arguments, 3 every take was rejected.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER = "http://127.0.0.1:8191"
# Never through a proxy: urllib honours HTTP_PROXY even for 127.0.0.1 unless NO_PROXY
# says otherwise, which would hand the prompts to whatever the proxy is.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OUTPUT = Path(__file__).resolve().parent.parent / "output"


def call(path: str, payload: dict | None = None, timeout: float = 3600, method: str | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(SERVER + path, data=data, headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail")
        except Exception:
            detail = exc.reason
        if isinstance(detail, list):  # pydantic validation errors
            detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
        print(f"server said {exc.code}: {detail}", file=sys.stderr)
        sys.exit(2 if exc.code in (400, 404, 422) else 1)
    except (OSError, http.client.HTTPException) as exc:
        # Refused, reset or timed out: the server is down, restarting or was stopped mid-request.
        print(f"lost the server at {SERVER}: {exc}", file=sys.stderr)
        sys.exit(1)


def wait_for_server(seconds: float) -> bool:
    deadline = time.time() + seconds
    while True:
        try:
            _OPENER.open(SERVER + "/api/health", timeout=5)
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(2)


def play(path: Path) -> None:
    """Play a WAV on this machine's default output device, blocking until it ends."""
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    players = [["afplay"]] if sys.platform == "darwin" else [
        ["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]
    for cmd in players:
        if shutil.which(cmd[0]):
            subprocess.run([*cmd, str(path)], check=True)
            return
    raise RuntimeError("no audio player found (tried " + ", ".join(c[0] for c in players) + ")")


# --------------------------------------------------------------------------- delivery

NAME_PART = re.compile(r"^[A-Za-z0-9]+$")  # UCS CatID / FXName: letters and digits only


def _used_versions(out: Path, base: str) -> set[int]:
    """Version numbers already taken in `out` for this base name (case-insensitive, like Windows)."""
    rx = re.compile(re.escape(base) + r"_v(\d+)\.(wav|json|png)", re.I)
    return {int(m.group(1)) for p in out.iterdir() if (m := rx.fullmatch(p.name))}


def sidecar(res: dict, take: dict, extra: dict | None = None) -> dict:
    keys = ("seed", "rank", "rank_score", "clap", "clap_cos", "clap_top", "aes", "caption", "fails", "warnings",
            "post", "final", "signal")
    return {"request": res["id"], "ref": take.get("ref"), "text": res.get("text"), "prompt": res.get("prompt"),
            "negative": res.get("negative"), "engine": res.get("engine"), "model": res.get("model"),
            "steps": res.get("steps"), "cfg": res.get("cfg"), "category": res.get("category"),
            **{k: take[k] for k in keys if k in take}, **(extra or {})}


def deliver(res: dict, takes: list[dict], out: Path, png: bool, name: str | None = None,
            cat: str | None = None) -> list[Path]:
    """Copy takes to `out` as CatID_FXName_vNN.wav (+ .json sidecar, + .png with --png).

    Never overwrites: numbering continues after whatever is there (compared case-insensitively),
    and each WAV is created exclusively, so even a file appearing meanwhile isn't clobbered.
    """
    for part in (name, cat):
        if part is not None and not NAME_PART.fullmatch(part):
            raise ValueError(f"--name / --cat take letters and digits only, not {part!r}")
    out.mkdir(parents=True, exist_ok=True)
    base = f"{cat or res['cat_id']}_{name or res['name']}"
    used = _used_versions(out, base)
    v = 1
    while any((v + k) in used for k in range(len(takes))):
        v += 1
    paths = []
    for t in takes:
        src = OUTPUT / t["file"] if "file" in t else OUTPUT / t["files"]["wav"]
        while True:
            stem = f"{base}_v{v:02d}"
            try:
                with open(out / f"{stem}.wav", "xb") as fh, open(src, "rb") as fs:
                    shutil.copyfileobj(fs, fh)
                break
            except FileExistsError:
                v += 1
        (out / f"{stem}.json").write_text(json.dumps(sidecar(res, t.get("_take", t), t.get("_extra")), indent=2,
                                                     ensure_ascii=False), encoding="utf-8")
        paths.append(out / f"{stem}.wav")
        if png and "files" in t:
            shutil.copyfile(OUTPUT / t["files"]["png"], out / f"{stem}.png")
            paths.append(out / f"{stem}.png")
        v += 1
    return paths


# --------------------------------------------------------------------------- printing

def describe(t: dict, fps: float) -> str:
    bits = [f"#{t.get('rank', '?')}", f"ref {t.get('ref')}", f"score {t.get('rank_score', 0):.2f}"]
    if "clap_cos" in t:
        bits.append(f"CLAP {t['clap_cos']:.2f} ({t['clap']:.2f})")
    if (t.get("aes") or {}).get("PQ") is not None:
        bits.append(f"PQ {t['aes']['PQ']:.1f}")
    p = t.get("post", {})
    if "length_s" in p:
        frames = f" / {p['length_frames']:g} fr @{fps:g}" if "length_frames" in p else ""
        bits.append(f"{p['length_s']:.2f}s{frames}")
    if "onset_s" in p:
        bits.append(f"onset {p['onset_s'] * 1000:.0f} ms (frame {p.get('onset_frame', 0):g})")
    if "seam_ok" in p:
        bits.append("seam ok" if p["seam_ok"] else "SEAM?")
    line = "  ".join(bits)
    if t.get("rejected"):
        line += "  REJECTED: " + "; ".join(t["fails"])
    for w in t.get("warnings", []):
        line += f"\n      warning: {w}"
    if t.get("caption"):
        line += f"\n      heard: {t['caption']}"
    return line


def print_request(res: dict) -> None:
    fps = res.get("post", {}).get("fps", 24)
    print(f"request {res['id']}  {res.get('engine')}  {res['cat_id']}_{res['name']}  ({res.get('category')}, "
          f"{res.get('post', {}).get('mode')})")
    if res.get("prompt"):
        print(f"prompt:   {res['prompt']}")
    if res.get("negative"):
        print(f"negative: {res['negative']}" + ("  (ignored by this engine)" if "negative" in res.get("ignored", []) else ""))
    if "gen_s" in res:
        print(f"{len(res['takes'])} take(s) in {res['gen_s']}s + {res.get('score_s', 0)}s checks; {res['kept']} kept")
    for t in res["takes"]:
        print("  " + describe(t, fps))
        print(f"      {t['paths']['wav']}")
        print(f"      {t['paths']['png']}")
    for w in res.get("warnings", []):
        print(f"WARNING: {w}")


# --------------------------------------------------------------------------- main

def parse_layer(spec: str) -> dict:
    """REF[,gain=DB][,offset=MS|Nf] -> a layer item."""
    parts = [p.strip() for p in spec.split(",")]
    item: dict = {"take": parts[0]}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k == "gain":
            item["gain_db"] = float(v)
        elif k == "offset" and v.endswith("f"):
            item["offset_frames"] = float(v[:-1])
        elif k == "offset":
            item["offset_ms"] = float(v.removesuffix("ms"))
        else:
            raise ValueError(f"unknown layer option {p!r} (use gain=DB, offset=MS or offset=Nf)")
    return item


def stack_body(res: dict, n: int, spread_ms: float = 0.0, fps: float | None = None, name: str | None = None,
               cat: str | None = None, check: bool | None = None) -> dict:
    """The /api/layer request that stacks the best n kept takes of a request into one sound.

    Layers are aligned on their hits (spread 0) or staggered by spread_ms each, all at the same
    volume: the limiter and level stage of the export balance the total.
    """
    kept = sorted((t for t in res["takes"] if not t.get("rejected")), key=lambda t: t.get("rank", 99))
    if len(kept) < 2:
        raise ValueError(f"--stack needs at least 2 kept takes; this request has {len(kept)}")
    layers = [{"take": t["ref"], "offset_ms": round(k * spread_ms, 1)} for k, t in enumerate(kept[:n])]
    return given({"layers": layers, "mode": res.get("post", {}).get("mode", "oneshot"), "fps": fps,
                  "name": name, "cat_id": cat, "check": check})


def print_stack(mix: dict) -> None:
    """How the stack compares with its best single take (the agent's only way to judge it)."""
    t = mix["takes"][0]
    print(f"stack of {len(mix.get('layers', []))} takes -> {mix['cat_id']}_{mix['name']}  ref {t['ref']}")
    for ly in mix.get("layers", []):
        sc = f"CLAP {ly['clap_cos']:.2f}" if ly.get("clap_cos") is not None else ""
        pq = f"PQ {ly['PQ']:.1f}" if ly.get("PQ") is not None else ""
        print(f"  layer {ly['take']}  {ly['gain_db']:+g} dB  {ly['offset_s'] * 1000:+.0f} ms  {sc}  {pq}")
    print("  mix:   " + describe(t, mix.get("post", {}).get("fps", 24)).split("  ", 1)[1])
    c = mix.get("compare")
    if c:
        print(f"  scores: stack {c['mix']:.2f}, best single take {c['best_layer']:.2f} ({c['best_layer_take']}).")
    if not c or c["mix"] < c["best_layer"]:
        print("  Stacks often score LOWER than their best single take (a denser sound reads as noise to the\n"
              "  scorers) while sounding fuller: don't reject a stack on its score. Give the user both to compare.")
    print(f"      {t['paths']['wav']}")


def given(d: dict) -> dict:
    """Drop options left unset, so the server's (or the user's saved) defaults apply."""
    return {k: v for k, v in d.items() if v is not None}


def post_fields(a) -> dict:
    out = given({"mode": a.mode, "duration": a.duration, "fps": a.fps, "align_frame": a.align_frame,
                 "preroll_ms": a.preroll, "reverb": a.reverb, "channels": a.channels, "crossfade_s": a.crossfade})
    if getattr(a, "no_align", False):
        out["align_frame"] = None  # an explicit null clears it on the server
    return out


def main() -> int:
    if not sys.stdout.isatty():  # piped (as agents run it): Windows would default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="the sound, e.g. 'sword hit on a shield' (turned into a full prompt "
                    "by the category recipe unless --raw)")
    g = ap.add_argument_group("generation")
    g.add_argument("--duration", type=float, help="seconds: one-shots are at most this long (trimmed to the sound); "
                   "ambiences and loops exactly this long. Default: the category's")
    g.add_argument("--n", type=int, help="takes to make and rank (best-of-N; default 4)")
    g.add_argument("--seed", help="start seed (takes use seed, seed+1, ...), or 'random' (the default)")
    g.add_argument("--seeds", help="exact seeds, comma-separated (e.g. to remake one take: --seeds 1234 --n 1)")
    g.add_argument("--engine", choices=["sa3", "moss"], help="sa3 (Stable Audio 3 Small-SFX) or moss "
                   "(MOSS-SoundEffect v2); default: the user's default")
    g.add_argument("--category", help="force a recipe/category (see --categories); default: detected from the text")
    g.add_argument("--raw", action="store_true", default=None, help="send the text as written, no recipe")
    g.add_argument("--negative", help="negative prompt (moss; sa3 small-sfx ignores it)")
    g.add_argument("--steps", type=int, help="sampling steps (default: sa3 8, moss 100)")
    g.add_argument("--cfg", type=float, help="guidance scale (moss; default 4)")
    g.add_argument("--window", type=int, help="moss only: denoising window in seconds (5-30; default 30; smaller is a little faster, "
                   "but quieter or cut-off takes are more likely)")
    g.add_argument("--like", metavar="REF", help="more like this take (REQ:SEED): audio-to-audio from it (sa3)")
    g.add_argument("--like-strength", type=float, help="how far from --like to go: 0.05 (nearly a copy) .. 1 "
                   "(unrelated); default 0.5")
    g.add_argument("--caption", action="store_true", default=None,
                   help="caption the best take with the audio captioner (says what it actually hears)")
    g.add_argument("--check", dest="check", action="store_true", default=None, help="CLAP + Aesthetics scores (default on)")
    g.add_argument("--no-check", dest="check", action="store_false", help="skip the model scores (signal checks still run)")
    p = ap.add_argument_group("post-processing (also with --post)")
    p.add_argument("--mode", choices=["oneshot", "ambience", "loop"], help="default: the category's")
    p.add_argument("--loop", action="store_const", const="loop", dest="mode", help="same as --mode loop")
    p.add_argument("--fps", type=float, help="frame rate for alignment, padding and the plot grid (default 24)")
    p.add_argument("--align-frame", type=int, help="put the transient exactly on this frame (one-shots)")
    p.add_argument("--no-align", action="store_true", help="with --post: remove an earlier --align-frame "
                   "(the hit goes back to t = 0)")
    p.add_argument("--preroll", type=float, help="ms of silence before the transient (default 3)")
    p.add_argument("--reverb", choices=["none", "room", "hall"], help="default none (dry)")
    p.add_argument("--channels", choices=["auto", "mono", "stereo"], help="auto: mono one-shots, stereo ambiences")
    p.add_argument("--crossfade", type=float, help="loops: crossfade seconds (default a quarter of the loop, max 1)")
    o = ap.add_argument_group("other actions")
    o.add_argument("--post", metavar="REQ", help="re-run post-processing on a request's takes; no regeneration")
    o.add_argument("--variations", type=int, metavar="N", help="also make a round-robin set of N (v01 = the take) "
                   "from the best take, or from --take")
    o.add_argument("--amount", type=float, help="variation strength (default 1: up to +-0.8 semitones, +-2 dB EQ)")
    o.add_argument("--take", metavar="REF", help="the take for --variations / --caption-only (REQ:SEED)")
    o.add_argument("--stack", type=int, metavar="N", help="mix the best N kept takes of this request into one fuller "
                   "sound (great for groups, crowds, flocks, creatures, textures); also with --post REQ")
    o.add_argument("--spread", type=float, default=0.0, metavar="MS", help="with --stack: stagger each layer by this "
                   "many ms (default 0: all hits together)")
    o.add_argument("--layer", action="append", metavar="SPEC", help="mix takes: REF[,gain=DB][,offset=MS|Nf], "
                   "repeat per layer; offsets place each transient relative to the first layer's")
    o.add_argument("--caption-only", action="store_true", help="caption --take (or every take of --post REQ)")
    o.add_argument("--show-prompt", action="store_true", help="print the prompt the model would get, and exit")
    o.add_argument("--categories", action="store_true", help="list categories and their recipes")
    o.add_argument("--settings", action="store_true", help="show the defaults in effect")
    o.add_argument("--engines", action="store_true", help="engines: installed / running")
    o.add_argument("--delete", metavar="REQ", help="delete a request's files from output/")
    d = ap.add_argument_group("delivery")
    d.add_argument("--out", type=Path, help="copy results here as CatID_FXName_vNN.wav + .json (never overwrites)")
    d.add_argument("--best", type=int, default=1, help="with --out: how many of the best kept takes (default 1)")
    d.add_argument("--all", action="store_true", help="with --out: every kept take")
    d.add_argument("--png", action="store_true", help="with --out: copy the spectrogram PNGs too")
    d.add_argument("--name", help="UCS FXName for the files (CamelCase letters/digits; default from the text)")
    d.add_argument("--cat", help="UCS CatID for the files (default: the category's, e.g. METLImpt)")
    d.add_argument("--play", action="store_true", help="play the best take (or the first delivered file) on the speakers")
    d.add_argument("--no-save", action="store_true", help="delete the request from output/ when done (after --out/--play)")
    d.add_argument("--json", action="store_true", help="print only JSON (the server's response plus `delivered`)")
    d.add_argument("--wait", type=float, default=5, help="seconds to wait for the server to come up")
    a = ap.parse_args()

    if a.seed is not None and a.seed.lower() != "random" and not a.seed.isdigit():
        ap.error("--seed takes a number or 'random'")
    actions = [a.text, a.post, a.layer, a.categories, a.settings, a.engines, a.delete, a.caption_only and a.take,
               a.variations and a.take]
    if not any(actions):
        ap.error("give the sound to make, or --post, --layer, --take ... --variations / --caption-only, --categories, "
                 "--settings, --engines or --delete")
    if a.stack is not None and not 2 <= a.stack <= 12:
        ap.error("--stack takes 2 to 12 layers")
    if a.stack and not (a.text or a.post):
        ap.error("--stack needs the sound to make, or --post REQ for an earlier request")
    for part, flag in ((a.name, "--name"), (a.cat, "--cat")):
        if part is not None and not NAME_PART.fullmatch(part):
            ap.error(f"{flag} takes letters and digits only (UCS style, e.g. SwordHit / METLImpt)")
    if not wait_for_server(a.wait):
        print(f"No server at {SERVER}. Start it with start.cmd (agents: start.cmd --no-browser).", file=sys.stderr)
        return 1

    if a.categories:
        for c in call("/api/categories"):
            print(f"{c['key']:15s} {c['cat_id']:10s} {c['mode']:8s} {c['duration']:>4g}s  {c['envelope']}")
        return 0
    if a.settings:
        st = call("/api/settings")
        print("in effect: " + (f"your saved defaults ({st['file']}) over the built-in ones" if st["file"]
                               else "the built-in defaults"))
        for k, v in st["effective"].items():
            shown = "random" if (k == "seed" and v is None) else "engine default" if (v is None) else v
            print(f"  {k:14s} {shown}{'  (saved)' if k in st['saved'] else ''}")
        return 0
    if a.engines:
        for k, e in call("/api/engines").items():
            print(f"{k:5s} {e['label']:28s} {'installed' if e['installed'] else 'NOT installed'}"
                  f"{', running' if e['running'] else ''}")
        return 0
    if a.delete:
        r = call(f"/api/requests/{a.delete}", method="DELETE")
        print(f"deleted request {r['id']} ({r['deleted']} files)")
        return 0
    if a.show_prompt and a.text:
        q = urllib.parse.urlencode(given({"text": a.text, "category": a.category, "mode": a.mode,
                                          "raw": "true" if a.raw else None}))
        r = call(f"/api/prompt?{q}")
        print(json.dumps(r, indent=2) if a.json else
              f"category {r['category']} ({r['cat_id']}, {r['mode']}, {r['duration']}s)\nprompt:   {r['prompt']}\n"
              f"negative: {r['negative']}\nfile:     {r['name']}")
        return 0
    if a.caption_only:
        refs = [a.take] if a.take else [t["ref"] for t in call(f"/api/requests/{a.post}")["takes"]]
        for r in call("/api/caption", {"takes": refs}):
            print(f"{r['take']}: {r['caption']}")
        return 0

    try:
        if a.layer:
            res = call("/api/layer", given({"layers": [parse_layer(s) for s in a.layer], "fps": a.fps,
                                            "mode": a.mode or "oneshot", "align_frame": a.align_frame,
                                            "name": a.name, "cat_id": a.cat}))
        elif a.post:
            first = call(f"/api/requests/{a.post}")["takes"][0]["ref"]
            res = call("/api/post", {"take": first, **post_fields(a), **given({"check": a.check})})
        elif a.variations and a.take and not a.text:
            res = call(f"/api/requests/{a.take.replace('#', ':').split(':')[0]}")
        else:
            body = {"text": a.text, **post_fields(a), **given({
                "n": a.n, "engine": a.engine, "category": a.category, "raw": a.raw, "negative": a.negative,
                "steps": a.steps, "cfg": a.cfg, "window": a.window, "like": a.like,
                "like_strength": a.like_strength, "caption": a.caption, "check": a.check, "name": a.name,
                "cat_id": a.cat})}
            if a.stack and not a.n:
                body["n"] = max(4, a.stack + 1)  # a spare, in case a take is rejected
            if a.seeds:
                body["seeds"] = [int(s) for s in a.seeds.split(",") if s.strip()]
            elif a.seed is not None:
                body["seed"] = None if a.seed.lower() == "random" else int(a.seed)
            res = call("/api/generate", body)
    except ValueError as exc:
        ap.error(str(exc))

    kept = [t for t in res["takes"] if not t.get("rejected")]
    stack = None
    if a.stack:
        try:
            stack = call("/api/layer", stack_body(res, a.stack, a.spread, a.fps, a.name, a.cat, a.check))
        except ValueError as exc:
            print(f"no stack: {exc}", file=sys.stderr)
    vari = None
    if a.variations:
        ref = a.take or (kept[0]["ref"] if kept else None)
        if ref is None:
            print("no kept take to make variations from", file=sys.stderr)
        else:
            vari = call("/api/variations", given({"take": ref, "n": a.variations, "amount": a.amount}))

    delivered: list[Path] = []
    if a.out:
        if stack is not None:  # the stack, and the best single take to compare it with
            delivered = deliver(stack, stack["takes"], a.out, png=a.png)
            if kept:
                delivered += deliver(res, kept[:1], a.out, png=a.png, name=a.name, cat=a.cat)
        elif vari is not None:
            items = [{"file": v["file"], "_take": vari, "_extra": {"variation": v.get("variation"), "version": v["version"]}}
                     for v in vari["variations"]]
            delivered = deliver(res, items, a.out, png=False, name=a.name, cat=a.cat)
        else:
            pick = kept if a.all else kept[:max(1, a.best)]
            if res.get("kind") == "layer" or res.get("engine") == "layer":
                pick = res["takes"]
            delivered = deliver(res, pick, a.out, png=a.png, name=a.name, cat=a.cat)

    if a.json:
        print(json.dumps({**res, "variations": vari, "stack": stack, "delivered": [str(p) for p in delivered]},
                         indent=2, ensure_ascii=False))
    else:
        print_request(res)
        if stack is not None:
            print_stack(stack)
        if vari is not None:
            print(f"variations of {vari['ref']}:")
            for v in vari["variations"]:
                print(f"  v{v['version']:02d}  {v['variation']}  {v['path']}")
        if delivered:
            print("delivered:")
            for p in delivered:
                print(f"  {p}")

    status = 0 if kept or res.get("engine") == "layer" else 3
    try:
        if a.play:
            target = next((p for p in delivered if p.suffix == ".wav"), None) or \
                (OUTPUT / (stack or {"takes": [kept[0] if kept else res["takes"][0]]})["takes"][0]["files"]["wav"])
            try:
                sys.stdout.flush()
                play(target)
                if not a.json:
                    print(f"played {target.name}")
            except Exception as exc:  # the take exists; only the playback failed
                print(f"could not play {target}: {exc}", file=sys.stderr)
                status = 1
    finally:
        if a.no_save and res.get("id"):
            call(f"/api/requests/{res['id']}", method="DELETE")
            if not a.json:
                print(f"not saved: removed request {res['id']} from output/")
    if status == 3 and not a.json:
        print("every take was rejected: try another seed, a longer --duration, or rephrase", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
