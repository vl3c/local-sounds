"""Command-line front end for the local-sounds server (standard library only).

Everything stays on 127.0.0.1. If the server isn't running, the CLI starts it with start.cmd,
in its own visible window (close that window, or run stop.cmd, to stop it and free the GPU),
unless another program already holds the GPU.

    python scripts/sfx.py "sword hit on a shield"                       # 4 takes, ranked
    python scripts/sfx.py "sword hit" --duration 1.2 --out D:/anim/sfx    # deliver the best one
    python scripts/sfx.py "door slam" --align-frame 12 --fps 24 --out D:/anim/sfx
    python scripts/sfx.py "rain on a tin roof" --loop --duration 8 --out D:/anim/sfx
    python scripts/sfx.py "footstep on gravel" --variations 5 --out D:/anim/sfx
    python scripts/sfx.py "laser zap" --like 20261004-120000-ab12:1234      # more like that take
    python scripts/sfx.py --post 20261004-120000-ab12 --align-frame 6       # re-process, no regeneration
    python scripts/sfx.py --layer A:1 --layer B:7,gain=-6,offset=2f --out D:/anim/sfx
    python scripts/sfx.py "a flock of seagulls" --stack 4 --out D:/anim/sfx  # 4 takes in one, richer
    python scripts/sfx.py --batch cues.json --out D:/anim/sfx                # a whole cue sheet, resumable
    python scripts/sfx.py --show-prompt "sword hit"                        # what the model would get

A take is '<request id>:<seed>' (the CLI prints it as `ref`). Delivered files get UCS-style
names (METLImpt_SwordHit_v01.wav) and a JSON sidecar; they never overwrite anything.

A cue sheet (--batch) is JSON: {"defaults": {...}, "cues": [{...}, ...]} (or just the list of
cues). A cue takes the same options as the command line, by their long names ("text",
"duration", "align_frame", "mode", "n", "name", "cat", "stack", "variations", "all", ...),
plus an "id" (default: its name, or one made from its text). "like": "@<id>" means the best
take of an earlier cue, so a family of sounds can grow from one anchor. Options given on the
command line with --batch apply to every cue that doesn't set its own; "defaults" in the file
come before both. The summary, <cue sheet name>.summary.json next to the delivered files, is
rewritten after every cue; a re-run skips cues already delivered (unless the cue changed, or
--redo names it) and retries the rest.

Exit status: 0 done, 1 failed or no server, 2 bad arguments, 3 every take was rejected
(batch: some cue delivered nothing).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
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
REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "output"
START_WAIT = 240.0  # seconds a fresh start may take: SA3 and the checkers load in ~30 s
BUSY_GPU_MB = 4000  # VRAM in use above this means another program holds the GPU (a desktop uses ~1-2 GB)


class ServerError(Exception):
    """The server refused a request (code 2), or isn't there / failed (code 1)."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


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
        raise ServerError(f"server said {exc.code}: {detail}", 2 if exc.code in (400, 404, 422) else 1) from None
    except (OSError, http.client.HTTPException) as exc:
        # Refused, reset or timed out: the server is down, restarting or was stopped mid-request.
        raise ServerError(f"lost the server at {SERVER}: {exc}", 1) from None


# --------------------------------------------------------------------------- the server

def server_up() -> bool:
    try:
        _OPENER.open(SERVER + "/api/health", timeout=5)
        return True
    except OSError:
        return False


def wait_for_server(seconds: float) -> bool:
    deadline = time.time() + seconds
    while True:
        if server_up():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def server_starting() -> bool:
    """Whether a server from this folder is loading: it holds output/.server.lock from before it
    loads anything until it exits (the port only opens once the models are ready)."""
    lock = OUTPUT / ".server.lock"
    if not lock.is_file():
        return False
    try:
        fh = open(lock, "a+")
    except OSError:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    finally:
        fh.close()


def gpu_used_mb() -> int | None:
    """VRAM in use on the first GPU, in MiB (None if nvidia-smi can't tell)."""
    exe = shutil.which("nvidia-smi") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                                     "nvidia-smi.exe")
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.split()
        return int(out[0]) if out and out[0].isdigit() else None
    except (OSError, subprocess.SubprocessError):
        return None


def ensure_server(wait: float = 5, start: bool = True, anyway: bool = False) -> None:
    """Make sure the server answers, starting it in its own visible window if it isn't running.

    Raises ServerError when there's no server and it can't (or mustn't) be started: another
    program holds the GPU, or the start failed.
    """
    if server_up():
        return
    if server_starting():  # started by the user, an agent or another tool: give it time to load
        if wait_for_server(max(wait, START_WAIT)):
            return
        raise ServerError("the local-sounds server is starting but hasn't answered in time: look at its "
                          "window, or logs/server.log")
    starter = REPO / "start.cmd"
    if not start or sys.platform != "win32" or not starter.is_file():
        if wait_for_server(wait):
            return
        raise ServerError(f"No server at {SERVER}. Start it with start.cmd (agents: start.cmd --no-browser); "
                          "it opens in its own window.")
    used = gpu_used_mb()
    if used is not None and used > BUSY_GPU_MB and not anyway:
        raise ServerError(f"No server at {SERVER}, and {used / 1024:.1f} GB of the GPU's memory is already in use "
                          "by another program (another model server?). Two model servers on one card make both "
                          "crawl: stop the other one first (ask its owner), or pass --start-anyway.")
    print("starting the local-sounds server in its own window (close that window, or run stop.cmd, to stop it "
          "and free the GPU)...", file=sys.stderr, flush=True)
    subprocess.run(["cmd", "/c", str(starter), "--no-browser"], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    deadline = time.time() + max(wait, START_WAIT)
    t0 = time.time()
    while time.time() < deadline:
        if server_up():
            return
        # The window takes a few seconds to import its libraries before it locks; after that,
        # no lock means it has exited (a crash, a refused start): don't wait out the clock.
        if time.time() - t0 > 30 and not server_starting():
            break
        time.sleep(2)
    raise ServerError("the local-sounds server didn't come up: read its window, or logs/server.log")


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
            "engine": res.get("engine"), "model": res.get("model"), "steps": res.get("steps"),
            "category": res.get("category"),
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
    if "gen_s" in res:
        print(f"{len(res['takes'])} take(s) in {res['gen_s']}s + {res.get('score_s', 0)}s checks; {res['kept']} kept")
    for t in res["takes"]:
        print("  " + describe(t, fps))
        print(f"      {t['paths']['wav']}")
        print(f"      {t['paths']['png']}")
    for w in res.get("warnings", []):
        print(f"WARNING: {w}")


# --------------------------------------------------------------------------- one request

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


def run_request(a) -> dict:
    """Make (or re-process, or mix) one sound, then stack / vary it and deliver it, as `a` says.

    Returns {"res", "kept", "stack", "vari", "delivered"}. Raises ServerError or ValueError.
    """
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
            "n": a.n, "engine": a.engine, "category": a.category, "raw": a.raw,
            "steps": a.steps, "like": a.like,
            "like_strength": a.like_strength, "caption": a.caption, "check": a.check, "name": a.name,
            "cat_id": a.cat})}
        if a.stack and not a.n:
            body["n"] = max(4, a.stack + 1)  # a spare, in case a take is rejected
        if a.seeds:
            body["seeds"] = [int(s) for s in a.seeds.split(",") if s.strip()]
        elif a.seed is not None:
            body["seed"] = None if a.seed.lower() == "random" else int(a.seed)
        res = call("/api/generate", body)

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
    return {"res": res, "kept": kept, "stack": stack, "vari": vari, "delivered": delivered}


# --------------------------------------------------------------------------- arguments

class _RaisingParser(argparse.ArgumentParser):
    """For cues: a bad option is an error in the cue sheet, not the end of the program."""

    def error(self, message):
        raise ValueError(message)


def build_parser(cls=argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap = cls(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="the sound, e.g. 'sword hit on a shield' (turned into a full prompt "
                    "by the category recipe unless --raw)")
    g = ap.add_argument_group("generation")
    g.add_argument("--duration", type=float, help="seconds: one-shots are at most this long (trimmed to the sound); "
                   "ambiences and loops exactly this long. Default: the category's")
    g.add_argument("--n", type=int, help="takes to make and rank (best-of-N; default 4)")
    g.add_argument("--seed", help="start seed (takes use seed, seed+1, ...), or 'random' (the default)")
    g.add_argument("--seeds", help="exact seeds, comma-separated (e.g. to remake one take: --seeds 1234 --n 1)")
    g.add_argument("--engine", choices=["sa3"], help="sa3 (Stable Audio 3 Small-SFX, the only engine now); "
                   "default: the user's default")
    g.add_argument("--category", help="force a recipe/category (see --categories); default: detected from the text")
    g.add_argument("--raw", action="store_true", default=None, help="send the text as written, no recipe")
    g.add_argument("--steps", type=int, help="sampling steps (default: the engine's, 8 for sa3; more rarely helps)")
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
    b = ap.add_argument_group("cue sheets")
    b.add_argument("--batch", type=Path, metavar="CUES.json", help="make every cue in this JSON cue sheet (see above); "
                   "resumable")
    b.add_argument("--dry-run", action="store_true", help="with --batch: check the cue sheet and show what would run "
                   "(and each prompt, if the server is up); generate nothing")
    b.add_argument("--redo", metavar="ID,...", help="with --batch: make these cues again even if delivered")
    d = ap.add_argument_group("delivery")
    d.add_argument("--out", type=Path, help="copy results here as CatID_FXName_vNN.wav + .json (never overwrites)")
    d.add_argument("--best", type=int, default=1, help="with --out: how many of the best kept takes (default 1)")
    d.add_argument("--all", action="store_true", help="with --out: every kept take")
    d.add_argument("--png", action="store_true", help="with --out: copy the spectrogram PNGs too")
    d.add_argument("--name", help="UCS FXName for the files (CamelCase letters/digits; default from the text)")
    d.add_argument("--cat", help="UCS CatID for the files (default: the category's, e.g. METLImpt)")
    d.add_argument("--play", action="store_true", help="play the best take (or the first delivered file) on the speakers")
    d.add_argument("--no-save", action="store_true", help="delete the request from output/ when done (after --out/--play)")
    d.add_argument("--json", action="store_true", help="print only JSON (the server's response plus `delivered`; "
                   "with --batch, the summary)")
    s = ap.add_argument_group("server")
    s.add_argument("--wait", type=float, default=5, help="seconds to wait for a running server to answer")
    s.add_argument("--no-start", action="store_true", help="don't start the server if it isn't running")
    s.add_argument("--start-anyway", action="store_true", help="start the server even though another program "
                   "holds over 4 GB of the GPU's memory (both will be slow)")
    return ap


def validate(a, batch: bool = False) -> None:
    """Checks argparse can't express. Raises ValueError. `batch`: options given with --batch,
    which apply to each cue (and its text)."""
    if a.seed is not None and a.seed.lower() != "random" and not a.seed.isdigit():
        raise ValueError("--seed takes a number or 'random'")
    if a.stack is not None and not 2 <= a.stack <= 12:
        raise ValueError("--stack takes 2 to 12 layers")
    if a.stack and not (a.text or a.post or batch):
        raise ValueError("--stack needs the sound to make, or --post REQ for an earlier request")
    for part, flag in ((a.name, "--name"), (a.cat, "--cat")):
        if part is not None and not NAME_PART.fullmatch(part):
            raise ValueError(f"{flag} takes letters and digits only (UCS style, e.g. SwordHit / METLImpt)")


# --------------------------------------------------------------------------- cue sheets

# What a cue may set: one sound's generation, post-processing, stack/variations and delivery.
CUE_KEYS = ("text", "duration", "n", "seed", "seeds", "engine", "category", "raw", "steps",
            "like", "like_strength", "caption", "check", "mode", "loop", "fps", "align_frame", "preroll",
            "reverb", "channels", "crossfade", "variations", "amount", "stack", "spread", "out", "best", "all",
            "png", "name", "cat")
CUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def cue_id(cue: dict) -> str:
    if cue.get("id") is not None:
        return str(cue["id"])
    if cue.get("name"):
        return str(cue["name"])
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", str(cue.get("text", ""))))[:40]


def cue_tokens(ap: argparse.ArgumentParser, opts: dict) -> list[str]:
    """A cue's options as command-line arguments, so the parser checks them exactly like flags."""
    tokens: list[str] = []
    for key, v in opts.items():
        if key in ("text", "loop") or v is None:
            continue
        if key == "check":
            if not isinstance(v, bool):
                raise ValueError("check: true or false")
            tokens.append("--check" if v else "--no-check")
            continue
        flag = "--" + key.replace("_", "-")
        action = ap._option_string_actions[flag]
        if action.nargs == 0:  # an on/off switch: true turns it on
            if not isinstance(v, bool):
                raise ValueError(f"{key}: true or false")
            if v:
                tokens.append(flag)
            continue
        if isinstance(v, (dict, bool)) or (isinstance(v, list) and key != "seeds"):
            raise ValueError(f"{key}: expected a single value, got {json.dumps(v)}")
        tokens += [flag, ",".join(str(x) for x in v) if isinstance(v, list) else str(v)]
    if opts.get("loop") is True and "mode" not in opts:
        tokens += ["--mode", "loop"]
    if "text" in opts:
        tokens += ["--", str(opts["text"])]  # after '--': a text starting with '-' is still the text
    return tokens


def _norm(d: dict, where: str) -> dict:
    if not isinstance(d, dict):
        raise ValueError(f"{where}: expected an object, got {json.dumps(d)[:60]}")
    out = {}
    for k, v in d.items():
        key = k.replace("-", "_")
        if key not in CUE_KEYS and key != "id":
            raise ValueError(f"{where}: unknown option {k!r} (a cue takes: id, {', '.join(CUE_KEYS)})")
        out[key] = v
    return out


def load_cues(path: Path, cli: dict) -> tuple[list[dict], Path | None]:
    """Read and check a whole cue sheet before anything is generated.

    `cli` holds the options given on the command line with --batch. Returns the cues, each with
    its id, its merged options (`spec`) and its parsed arguments (`args`), and the folder the
    summary goes to. Raises ValueError naming the cue and the problem.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"can't read the cue sheet {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} isn't valid JSON: {exc}") from None
    if isinstance(data, list):
        data = {"cues": data}
    if not isinstance(data, dict) or not isinstance(data.get("cues"), list) or set(data) - {"defaults", "cues"}:
        raise ValueError(f"{path.name}: expected {{\"defaults\": {{...}}, \"cues\": [...]}} or a list of cues")
    defaults = _norm(data.get("defaults") or {}, "defaults")
    if "id" in defaults or "text" in defaults:
        raise ValueError("defaults: 'id' and 'text' belong to each cue")
    base = path.resolve().parent
    for src in (defaults,):  # a relative "out" in the file is relative to the file
        if src.get("out") is not None and not Path(src["out"]).is_absolute():
            src["out"] = str(base / src["out"])
    out_default = cli.get("out") or defaults.get("out")
    ap = build_parser(_RaisingParser)
    cues, seen = [], {}
    for k, raw in enumerate(data["cues"], 1):
        where = f"cue {k}"
        cue = _norm(raw, where)
        if not str(cue.get("text") or "").strip():
            raise ValueError(f"{where}: needs a \"text\" (the sound to make)")
        cid = cue_id(cue)
        where = f"cue {k} ({cid})"
        if not CUE_ID.fullmatch(cid):
            raise ValueError(f"{where}: an id takes letters, digits, '-' and '_' (set \"id\")")
        if cid.lower() in seen:
            raise ValueError(f"{where}: the id is already used by cue {seen[cid.lower()]}; give one of them an \"id\"")
        seen[cid.lower()] = k
        if cue.get("out") is not None and not Path(cue["out"]).is_absolute():
            cue["out"] = str(base / cue["out"])
        spec = {key: v for key, v in {**defaults, **cli, **cue}.items() if key != "id"}
        like = spec.get("like")
        if isinstance(like, str) and like.startswith("@"):
            if like[1:].lower() not in seen or seen[like[1:].lower()] == k:
                raise ValueError(f"{where}: \"like\": {like!r} must name an earlier cue's id")
        if not spec.get("out"):
            raise ValueError(f"{where}: where should it go? Give --out, or \"out\" in the defaults or the cue")
        try:
            args = ap.parse_args(cue_tokens(ap, spec))
            validate(args)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"{where}: {exc}") from None
        cues.append({"id": cid, "spec": spec, "args": args})
    if not cues:
        raise ValueError(f"{path.name} has no cues")
    return cues, (Path(out_default) if out_default else None)


def _take_summary(t: dict) -> dict:
    return {"ref": t.get("ref"), "seed": t.get("seed"), "rank": t.get("rank"), "score": t.get("rank_score"),
            "clap_cos": t.get("clap_cos"), "pq": (t.get("aes") or {}).get("PQ"), "rejected": bool(t.get("rejected")),
            "fails": t.get("fails", []), "warnings": t.get("warnings", []), "png": (t.get("paths") or {}).get("png")}


def _write_json(path: Path, data: dict) -> None:
    """Write via a temporary file, so an interrupted run never leaves half a summary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def explicit_options(ap: argparse.ArgumentParser, a) -> dict:
    """The cue options given on the command line (anything not left at its default)."""
    out = {}
    for key in CUE_KEYS:
        if key in ("text", "loop"):
            continue
        v = getattr(a, key, None)
        if v is not None and v != ap.get_default(key):
            out[key] = str(v) if isinstance(v, Path) else v
    return out


def run_batch(ap: argparse.ArgumentParser, a) -> int:
    try:
        cues, folder = load_cues(a.batch, explicit_options(ap, a))
    except ValueError as exc:
        print(f"cue sheet: {exc}", file=sys.stderr)
        return 2
    redo = {r.strip().lower() for r in (a.redo or "").split(",") if r.strip()}
    unknown = redo - {c["id"].lower() for c in cues}
    if unknown:
        print(f"--redo: no cue with id {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    summary_path = (folder or a.batch.resolve().parent) / f"{a.batch.stem}.summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert isinstance(summary.get("cues"), dict)
    except (OSError, ValueError, AssertionError):
        summary = {"cues": {}}
    summary["cue_sheet"] = str(a.batch.resolve())

    def done(c) -> bool:
        prev = summary["cues"].get(c["id"])
        return (c["id"].lower() not in redo and prev is not None and prev.get("status") == "delivered"
                and prev.get("spec") == c["spec"] and all(Path(p).is_file() for p in prev.get("delivered", [])))

    if a.dry_run:
        up = server_up()
        for k, c in enumerate(cues, 1):
            s = c["spec"]
            bits = [f"{key}={json.dumps(v, ensure_ascii=False)}" for key, v in s.items() if key not in ("text", "out")]
            print(f"[{k}/{len(cues)}] {c['id']}  {'done, skipped' if done(c) else 'to make'}: {s['text']!r}  "
                  + " ".join(bits))
            if up:
                q = urllib.parse.urlencode(given({"text": s["text"], "category": s.get("category"),
                                                  "mode": c["args"].mode, "raw": "true" if s.get("raw") else None}))
                try:
                    r = call(f"/api/prompt?{q}")
                    print(f"      {r['category']} ({r['cat_id']}, {r['mode']}, {r['duration']}s): {r['prompt']}")
                except ServerError as exc:
                    print(f"      prompt: {exc}")
        print(f"{len(cues)} cue(s) checked; summary: {summary_path}" + ("" if up else
              " (start the server to see the prompts)"))
        return 0

    todo = {c["id"] for c in cues if not done(c)}
    if todo:
        try:
            ensure_server(a.wait, start=not a.no_start, anyway=a.start_anyway)
        except ServerError as exc:
            print(exc, file=sys.stderr)
            return 1
    counts = {"delivered": 0, "skipped": 0, "no keepers": 0, "failed": 0}
    status = 0
    for k, c in enumerate(cues, 1):
        tag = f"[{k}/{len(cues)}] {c['id']}"
        prev = summary["cues"].get(c["id"])
        if c["id"] not in todo:
            counts["skipped"] += 1
            if not a.json:
                print(f"{tag}  done before: " + ", ".join(Path(p).name for p in prev["delivered"] if p.endswith(".wav")))
            continue
        args = c["args"]
        entry = {"status": "failed", "spec": c["spec"], "text": c["spec"]["text"],
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        like = c["spec"].get("like")
        if isinstance(like, str) and like.startswith("@"):
            src = next((v for key, v in summary["cues"].items() if key.lower() == like[1:].lower()), None)
            ref = (src or {}).get("best_ref")
            if not ref:
                entry["error"] = f"{like} has no kept take to be like"
            args = argparse.Namespace(**{**vars(args), "like": ref})
            entry["like"] = ref
        if "error" not in entry:
            try:
                r = run_request(args)
            except ServerError as exc:
                entry["error"] = str(exc)
                if exc.code == 1:  # the server is gone: the rest would fail the same way
                    summary["cues"][c["id"]] = entry
                    _write_json(summary_path, summary)
                    counts["failed"] += 1
                    print(f"{tag}  FAILED: {exc}\nstopped: re-run the same command to carry on from here "
                          f"(summary: {summary_path})", file=sys.stderr)
                    return 1
            except (ValueError, OSError) as exc:
                entry["error"] = str(exc)
            else:
                res = r["res"]
                entry.update({
                    "status": "delivered" if r["delivered"] else "no keepers",
                    "request": res.get("id"), "engine": res.get("engine"), "category": res.get("category"),
                    "prompt": res.get("prompt"), "best_ref": r["kept"][0]["ref"] if r["kept"] else None,
                    "delivered": [str(p) for p in r["delivered"]],
                    "takes": [_take_summary(t) for t in res["takes"]], "warnings": res.get("warnings", [])})
                if r["stack"]:
                    entry["stack"] = {"ref": r["stack"]["takes"][0]["ref"], "compare": r["stack"].get("compare"),
                                      **{key: v for key, v in _take_summary(r["stack"]["takes"][0]).items()
                                         if key in ("score", "clap_cos", "pq", "warnings", "png")}}
                if r["vari"]:
                    entry["variations_of"] = r["vari"].get("ref")
        counts[entry["status"]] += 1
        if entry["status"] != "delivered":
            status = 3
        summary["cues"][c["id"]] = entry
        summary["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_json(summary_path, summary)
        if not a.json:
            if entry["status"] == "delivered":
                best = next((t for t in entry["takes"] if t["ref"] == entry["best_ref"]), {})
                score = (f"  (ref {best.get('ref')}, score {best.get('score') or 0:.2f}"
                         + (f", CLAP {best['clap_cos']:.2f}" if best.get("clap_cos") is not None else "")
                         + (f", PQ {best['pq']:.1f}" if best.get("pq") is not None else "") + ")") if best else ""
                print(f"{tag}  -> " + ", ".join(Path(p).name for p in entry["delivered"] if p.endswith(".wav")) + score)
                for w in best.get("warnings", []) + entry.get("warnings", []):
                    print(f"      warning: {w}")
            elif entry["status"] == "no keepers":
                print(f"{tag}  every take was rejected: " + "; ".join(
                    sorted({f for t in entry["takes"] for f in t["fails"]})))
            else:
                print(f"{tag}  FAILED: {entry['error']}")
            sys.stdout.flush()
    if a.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"batch: {counts['delivered']} delivered, {counts['skipped']} done before, "
              f"{counts['no keepers']} with every take rejected, {counts['failed']} failed; summary: {summary_path}")
        if status:
            print("re-run the same command to retry the ones that didn't deliver (rephrase or change their cue first)")
    return status


# --------------------------------------------------------------------------- main

def main() -> int:
    if not sys.stdout.isatty():  # piped (as agents run it): Windows would default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = build_parser()
    a = ap.parse_args()
    try:
        validate(a, batch=bool(a.batch))
    except ValueError as exc:
        ap.error(str(exc))
    if a.batch:
        if a.text or a.post or a.layer or a.take:
            ap.error("--batch makes the cues in the file: no sound text, --post, --layer or --take with it")
        try:
            return run_batch(ap, a)
        except KeyboardInterrupt:
            print("\ninterrupted: re-run the same command to carry on", file=sys.stderr)
            return 1
    if a.dry_run or a.redo:
        ap.error("--dry-run and --redo go with --batch")
    actions = [a.text, a.post, a.layer, a.categories, a.settings, a.engines, a.delete, a.caption_only and a.take,
               a.variations and a.take]
    if not any(actions):
        ap.error("give the sound to make, or --batch, --post, --layer, --take ... --variations / --caption-only, "
                 "--categories, --settings, --engines or --delete")
    try:
        return _main(a)
    except ServerError as exc:
        print(exc, file=sys.stderr)
        return exc.code
    except ValueError as exc:
        ap.error(str(exc))


def _main(a) -> int:
    ensure_server(a.wait, start=not a.no_start, anyway=a.start_anyway)

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
              f"file:     {r['name']}")
        return 0
    if a.caption_only:
        refs = [a.take] if a.take else [t["ref"] for t in call(f"/api/requests/{a.post}")["takes"]]
        for r in call("/api/caption", {"takes": refs}):
            print(f"{r['take']}: {r['caption']}")
        return 0

    r = run_request(a)
    res, kept, stack, vari, delivered = r["res"], r["kept"], r["stack"], r["vari"], r["delivered"]

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
