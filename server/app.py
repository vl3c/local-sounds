"""local-sounds server: text-to-SFX engines + automatic checks + animation post-processing.

Loopback only (127.0.0.1:8191). Each engine runs in its own venv as a child process
(server/workers/); this process holds the checkers (CLAP, Audiobox Aesthetics, and the
optional captioner) and does all post-processing, so every take is judged the same way.

    .venv\\Scripts\\python.exe server\\app.py [--port 8191] [--engines sa3] [--lazy]

A request makes N takes (best-of-N, default 4) on consecutive seeds, checks and scores
each, rejects the failures and returns them all ranked. Its files live in output/<id>/:

    request.json         the request, the prompt actually sent, and every take's scores
    <seed>_raw.wav       the engine's output, as generated (float, its own sample rate)
    <seed>.wav           the delivered sound: 48 kHz 24-bit, trimmed / aligned / normalised,
                         with prompt, seed, model and scores in its INFO chunk
    <seed>.png           waveform + spectrogram with frame gridlines and the onset marker
    <seed>_v01.wav ...   round-robin variations (on request)
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import numpy as np

REPO = Path(__file__).resolve().parent.parent
MODELS = REPO / "models"
OUTPUT = REPO / "output"
LOGS = REPO / "logs"
PID_FILE = OUTPUT / ".server.pid"
SETTINGS_FILE = REPO / "config.local.json"
BAKEOFF = OUTPUT / "bakeoff"
sys.path.insert(0, str(REPO))
# Everything is loaded from models/: never let a library reach for the network.
for _k, _v in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
               "DO_NOT_TRACK": "1", "TRANSFORMERS_VERBOSITY": "error"}.items():
    os.environ[_k] = _v

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

import serverlog  # noqa: E402  (server/, on sys.path as the script's folder)
from sfx import __version__, analysis, audio, checks, plot, post, prompts  # noqa: E402

PORT = 8191
REQ_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.(wav|png|json)$")
TAKE_REF = re.compile(r"^(?P<rid>[0-9]{8}-[0-9]{6}-[0-9a-f]{4})[:#/](?P<seed>[0-9]+)$")

app = FastAPI(title="local-sounds", docs_url=None, redoc_url=None, openapi_url=None)
# Refuse any Host but our own: a web page that DNS-rebinds its name to 127.0.0.1
# must not be able to read takes or drive the server.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def _same_origin_only(request, call_next):
    """Refuse writes that a web page on another site makes the user's browser send.

    Browsers label those with Origin / Sec-Fetch-Site; the CLI and curl send neither, and
    the server's own page sends its own origin. Also forbid framing the page elsewhere.
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin is not None and origin not in _own_origins(request)):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _own_origins(request) -> set[str]:
    port = request.url.port or 80
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


_lock = threading.Lock()  # one GPU job at a time: generation, scoring and captioning share the card
_file_lock = threading.Lock()  # settings and votes: small files that mustn't wait for a GPU job


# --------------------------------------------------------------------------- engines

ENGINES = {
    "sa3": {"venv": ".venv-sa3", "script": "sa3_worker.py", "weights": "sa3-small-sfx/model.safetensors",
            "label": "Stable Audio 3 Small-SFX"},
    # Trial: Stable Audio 3 Medium (the general model: music, stems and SFX), same venv and worker.
    "medium": {"venv": ".venv-sa3", "script": "sa3_worker.py", "args": ["medium"],
               "weights": "sa3-medium/model.safetensors", "label": "Stable Audio 3 Medium"},
}


def engine_installed(name: str) -> bool:
    e = ENGINES[name]
    return (REPO / e["venv"] / "Scripts" / "python.exe").is_file() and (MODELS / e["weights"]).is_file()


class Worker:
    """An engine in its own venv, as a child process speaking workers/protocol.py."""

    START_TIMEOUT = 600.0  # loads several GB and warms up
    JOB_TIMEOUT = 1800.0

    def __init__(self, name: str):
        if not engine_installed(name):
            raise HTTPException(503, f"engine {name!r} isn't installed: re-run install.ps1")
        e = ENGINES[name]
        self.name = name
        t0 = time.time()
        # The worker's stderr (its logs, progress bars) goes through this process's log
        # and console filter, like everything else the server prints.
        self.proc = subprocess.Popen(
            [str(REPO / e["venv"] / "Scripts" / "python.exe"), str(Path(__file__).parent / "workers" / e["script"]),
             *e.get("args", [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(REPO),
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        # Reader threads, so a hung worker times out instead of holding _lock forever,
        # and its stderr pipe never fills up and blocks it.
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()
        try:
            hello = self._read(self.START_TIMEOUT)
            if not hello.get("ready"):
                raise RuntimeError(f"{name} worker failed to start: {hello.get('error', hello)}")
        except Exception:
            self.close()
            raise
        self.info = hello
        print(f"[local-sounds] {e['label']} ready in {time.time() - t0:.1f}s", flush=True)

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")  # EOF

    def _pump_err(self) -> None:
        for line in self.proc.stderr:
            sys.stderr.write(line)

    def _read(self, timeout: float) -> dict:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"{self.name} worker gave no answer in {timeout:.0f}s") from None
        if not line:
            raise RuntimeError(f"{self.name} worker exited (code {self.proc.poll()}); see logs/server.log")
        return json.loads(line)

    def call(self, req: dict) -> dict:
        try:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            reply = self._read(self.JOB_TIMEOUT)
        except Exception:
            # Hung, dead or out of sync: kill it, so the next request starts a fresh one.
            self.close()
            raise
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "unknown worker error"))
        return reply

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()


_workers: dict[str, Worker] = {}


def worker(name: str) -> Worker:
    """The engine's worker, started (or restarted, if it died) on demand. Call with _lock held."""
    if name not in ENGINES:
        raise HTTPException(400, f"unknown engine {name!r}; choose from {sorted(ENGINES)}")
    w = _workers.get(name)
    if w is None or not w.alive():
        _workers[name] = w = Worker(name)
    return w


@atexit.register
def _stop_workers() -> None:
    for w in list(_workers.values()):
        w.close()


# --------------------------------------------------------------------------- scorers

_clap = _aes = None


def clap():
    global _clap
    if _clap is None:
        from sfx.scoring import Clap
        if not (MODELS / "clap" / "pytorch_model.bin").is_file():
            raise HTTPException(503, "CLAP weights are missing: run scripts/download_models.py")
        _clap = Clap(MODELS / "clap")
    return _clap


def aes():
    global _aes
    if _aes is None:
        from sfx.scoring import Aesthetics
        if not (MODELS / "aesthetics" / "model.safetensors").is_file():
            raise HTTPException(503, "Audiobox Aesthetics weights are missing: run scripts/download_models.py")
        _aes = Aesthetics(MODELS / "aesthetics")
    return _aes


def captioner_installed() -> bool:
    return any((MODELS / "captioner").glob("*.safetensors"))


def caption_takes(items: list[tuple[np.ndarray, int]]) -> list[str]:
    """Caption several sounds with one load of the captioner, then free it. Call with _lock held."""
    if not captioner_installed():
        raise HTTPException(503, "the captioner isn't downloaded (2.8 GB): run "
                                 "`.venv\\Scripts\\python.exe scripts\\download_models.py --only captioner`")
    from sfx.scoring import Captioner
    with Captioner(MODELS / "captioner") as cap:
        return [cap.caption(a, sr) for a, sr in items]


def _release_cache() -> None:
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# --------------------------------------------------------------------------- settings

# The built-in defaults. The user's own, saved from the page, live in config.local.json
# (gitignored) and override them for the page and for every request that leaves a key out.
REPO_DEFAULTS = {
    "engine": "sa3",          # see README "Engines"
    "n": 4,                   # best-of-N
    "seed": None,             # None: a random start seed; takes use seed, seed+1, ...
    "steps": None,            # None: the engine's default (sa3 8)
    "raw": False,             # True: send the text as written, no recipe
    "fps": 24.0,
    "preroll_ms": 3.0,
    "reverb": "none",
    "pad_s": 0.75,            # extra length asked of the model for one-shots, trimmed off
    "check": True,            # CLAP + Audiobox Aesthetics scores
    "caption": False,         # also caption the top take (loads a 2.8 GB model for a moment)
    "like_strength": 0.5,     # 'more like #3': 0 = a copy, 1 = unrelated
    "peak_dbtp": -1.0,
    "lufs": -23.0,
}
Reverb = Literal["none", "room", "hall"]
Mode = Literal["oneshot", "ambience", "loop"]


class Settings(BaseModel):
    """What can be saved. Omitted keys fall back to the repo defaults; seed null = random."""
    model_config = {"extra": "forbid"}
    engine: Literal["sa3", "medium"] | None = None
    n: int | None = Field(None, ge=1, le=16)
    seed: int | None = Field(None, ge=0, lt=2**31)
    steps: int | None = Field(None, ge=1, le=250)
    raw: bool | None = None
    fps: float | None = Field(None, ge=1, le=240)
    preroll_ms: float | None = Field(None, ge=0, le=50)
    reverb: Reverb | None = None
    pad_s: float | None = Field(None, ge=0, le=5)
    check: bool | None = None
    caption: bool | None = None
    like_strength: float | None = Field(None, ge=0.05, le=1.0)
    peak_dbtp: float | None = Field(None, ge=-24, le=0)
    lufs: float | None = Field(None, ge=-40, le=-6)


def _keep(data: dict) -> dict:
    """Drop empty values: only `seed` may be null (it means "random"). A cleared field on the
    page arrives as null and must not override the defaults with nothing."""
    return {k: v for k, v in data.items() if v is not None or k == "seed"}


# Saved settings that no longer exist: the MOSS engine and its negative prompt and guidance.
# A file that still has them keeps its other settings.
RETIRED_SETTINGS = {"cfg", "negative"}


def local_settings() -> dict:
    """The user's saved settings, validated; {} if none (or the file is unreadable)."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = {k: v for k, v in raw.items() if k not in RETIRED_SETTINGS
                   and not (k == "engine" and v not in ENGINES)}
        return _keep(Settings(**raw).model_dump(exclude_unset=True))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # a hand-edited file with a typo: say so, use the repo defaults
        print(f"[local-sounds] ignoring {SETTINGS_FILE.name}: {exc}", flush=True)
        return {}


def effective_settings(saved: dict | None = None) -> dict:
    return {**REPO_DEFAULTS, **(local_settings() if saved is None else saved)}


def _resolve(req: BaseModel, saved: dict | None = None) -> dict:
    """Every setting: the request's own value where it gave one, the saved/repo default otherwise.

    An explicit null counts as "not given", except for the seed, where null means random
    (overriding a saved seed).
    """
    eff = effective_settings(saved)
    out = {}
    for k in eff:
        v = getattr(req, k, None)
        given = k in req.model_fields_set and (v is not None or k == "seed")
        out[k] = v if given else eff[k]
    return out


# --------------------------------------------------------------------------- requests

class PostFields(BaseModel):
    """Post-processing knobs shared by generate and re-process. None = default."""
    mode: Mode | None = None
    duration: float | None = Field(None, gt=0.02, le=120)
    fps: float | None = Field(None, ge=1, le=240)
    align_frame: int | None = Field(None, ge=0, le=100000)
    preroll_ms: float | None = Field(None, ge=0, le=50)
    reverb: Reverb | None = None
    channels: Literal["auto", "mono", "stereo"] | None = None
    crossfade_s: float | None = Field(None, gt=0, le=5)
    peak_dbtp: float | None = Field(None, ge=-24, le=0)
    lufs: float | None = Field(None, ge=-40, le=-6)


class GenReq(PostFields):
    text: str = Field(min_length=1, max_length=600)
    raw: bool | None = None
    category: str | None = None
    n: int | None = Field(None, ge=1, le=16)
    seed: int | None = Field(None, ge=0, lt=2**31)  # omitted: the saved default (random unless saved); null: random
    seeds: list[int] | None = Field(None, max_length=16)
    engine: Literal["sa3", "medium"] | None = None
    steps: int | None = Field(None, ge=1, le=250)
    like: str | None = None                         # "<request id>:<seed>": more like that take
    like_strength: float | None = Field(None, ge=0.05, le=1.0)
    pad_s: float | None = Field(None, ge=0, le=5)
    check: bool | None = None
    caption: bool | None = None
    name: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9]*$")  # UCS FXName override
    cat_id: str | None = Field(None, max_length=12, pattern=r"^[A-Za-z0-9]*$")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("text is empty")
        return v

    @field_validator("category")
    @classmethod
    def _known_category(cls, v):
        if v is not None and v not in prompts.BY_KEY:
            raise ValueError(f"unknown category {v!r}; choose from {sorted(prompts.BY_KEY)}")
        return v

    @field_validator("seeds")
    @classmethod
    def _seed_range(cls, v):
        if v is not None and (not v or any(not 0 <= s < 2**31 for s in v) or len(set(v)) != len(v)):
            raise ValueError("seeds must be distinct numbers from 0 to 2^31-1")
        return v


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def req_dir(rid: str) -> Path:
    if not REQ_ID.fullmatch(rid):
        raise HTTPException(400, f"bad request id {rid!r}")
    return OUTPUT / rid


def load_request(rid: str) -> dict:
    p = req_dir(rid) / "request.json"
    if not p.is_file():
        raise HTTPException(404, f"no request {rid}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_request(rec: dict) -> None:
    p = req_dir(rec["id"]) / "request.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def parse_take(ref: str) -> tuple[str, int]:
    m = TAKE_REF.fullmatch(ref.strip())
    if not m:
        raise HTTPException(400, f"a take is '<request id>:<seed>', e.g. 20261004-120000-ab12:1234; got {ref!r}")
    return m["rid"], int(m["seed"])


def find_take(rec: dict, seed: int) -> dict:
    for t in rec["takes"]:
        if t["seed"] == seed:
            return t
    raise HTTPException(404, f"request {rec['id']} has no take with seed {seed}")


def gen_length(mode: str, duration: float, pad_s: float, crossfade_s: float | None) -> float:
    """How much audio to ask the model for, so post-processing has room to trim or loop."""
    if mode == "oneshot":
        return duration + pad_s
    if mode == "loop":
        xf = crossfade_s if crossfade_s is not None else min(1.0, max(0.05, 0.25 * duration))
        return 2 * duration + xf + 1.0  # ~2x, plus the crossfade, plus the skipped opening
    return duration + 1.0


CLEARABLE = ("align_frame", "duration", "crossfade_s")


def post_opts(rec: dict, override: PostFields | None = None) -> post.PostOpts:
    """The request's post-processing options, with any re-process overrides applied."""
    o = dict(rec["post"])
    if override is not None:
        for k, v in override.model_dump(exclude_unset=True).items():
            # null clears the optional ones (align_frame: back to t = 0); elsewhere it means "keep"
            if k in o and (v is not None or k in CLEARABLE):
                o[k] = v
    return post.PostOpts(**o)


def _info(rec: dict, take: dict) -> dict:
    aes_ = take.get("aes") or {}
    scores = (f"CLAP {take['clap_cos']:.3f} ({take['clap']:.2f})" if "clap_cos" in take else "") + \
             (f" PQ {aes_['PQ']:.1f}" if "PQ" in aes_ else "")
    return {"title": f"{rec['cat_id']}_{rec['name']}", "genre": rec["category"],
            "comment": f"{rec['prompt']} | seed {take['seed']} | {rec['model']} | {scores.strip()} | "
                       f"rank {take.get('rank', '?')}/{len(rec['takes'])} | request {rec['id']}",
            "software": f"local-sounds {__version__}", "date": rec["created"][:10],
            "keywords": f"{rec['category']}, {rec['engine']}, seed {take['seed']}"}


def render(rec: dict, take: dict, opts: post.PostOpts) -> np.ndarray:
    """Raw -> delivered WAV + PNG for one take. Updates take["post"] / ["final"]. Returns the audio."""
    d = req_dir(rec["id"])
    raw, sr = audio.read(d / take["raw"])
    y, rep = post.process(raw, sr, opts)
    if opts.mode == "loop":
        rep.update(checks.seam_report(y, audio.SR))
    take["post"] = rep
    take["final"] = {"true_peak_dbtp": round(analysis.true_peak_db(y, audio.SR), 2),
                     "lufs": round(analysis.lufs(y, audio.SR), 1)}
    title = f"{rec['cat_id']}_{rec['name']}  seed {take['seed']}  {rec['engine']}  |  {rec['prompt'][:110]}"
    plot.draw(y, audio.SR, d / take["png"], title=title, fps=opts.fps, onset_s=rep.get("onset_s"),
              align_frame=opts.align_frame if opts.mode == "oneshot" else None, seam=opts.mode == "loop")
    return y


def judge(rec: dict, takes: list[dict], finals: list[np.ndarray], check: bool) -> None:
    """Scores, fails and warnings for each take; then rank and write the WAVs with their INFO chunk."""
    from sfx.scoring import PQ_FAIL, PQ_WARN, rank_score
    if check:
        try:
            cl = clap().score(finals, audio.SR, rec["prompt"], prompts.distractors(rec["prompt"], rec["category"]))
            ae = aes().score(finals, audio.SR)
            for t, c, a in zip(takes, cl, ae):
                t.update(c)
                t["aes"] = a
        except HTTPException:
            raise
        except Exception as exc:  # the takes exist either way: say the scoring failed
            rec.setdefault("warnings", []).append(f"scoring failed: {type(exc).__name__}: {exc}")
    for t in takes:
        sig = t["signal"]
        t["fails"] = list(sig["fails"])
        t["warnings"] = list(sig["warnings"])
        pq = (t.get("aes") or {}).get("PQ")
        # Audiobox PQ reads rain, wind and crowds as "noisy": for ambiences it only warns.
        if pq is not None and pq < PQ_FAIL and rec["post"]["mode"] == "oneshot":
            t["fails"].append(f"low production quality (Audiobox PQ {pq:.1f} < {PQ_FAIL})")
        elif pq is not None and pq < PQ_WARN:
            t["warnings"].append(f"so-so production quality (Audiobox PQ {pq:.1f})"
                                 + ("; normal for noise-like ambiences (rain, wind)" if rec["post"]["mode"] != "oneshot"
                                    else ""))
        if t.get("clap_top"):
            t["warnings"].append(f"CLAP thinks it sounds more like: {t['clap_top']}")
        if t["post"].get("cut_db") is not None:
            t["warnings"].append(f"cut at the requested duration while still {-t['post']['cut_db']:.0f} dB below "
                                 "its peak: the tail is chopped, ask for a longer --duration")
        if rec["post"]["mode"] == "loop" and not t["post"].get("seam_ok", True):
            t["warnings"].append("the loop seam may be audible (see seam_* numbers)")
        t["rejected"] = bool(t["fails"])
        t["rank_score"] = rank_score(t)
    order = sorted(range(len(takes)), key=lambda i: (-takes[i]["rank_score"], takes[i]["seed"]))
    ranked = [takes[i] for i in order]
    for r, t in enumerate(ranked, 1):
        t["rank"] = r
    rec["takes"] = ranked
    d = req_dir(rec["id"])
    for t, y in zip(takes, finals):
        audio.write_wav(d / t["wav"], y, info=_info(rec, t))


def take_view(rec: dict, take: dict) -> dict:
    """A take as the API returns it: its ref, file names and paths, and every score."""
    d = req_dir(rec["id"])
    out = {k: v for k, v in take.items() if k not in ("raw", "wav", "png")}
    out["ref"] = f"{rec['id']}:{take['seed']}"
    out["files"] = {"wav": f"{rec['id']}/{take['wav']}", "png": f"{rec['id']}/{take['png']}",
                    "raw": f"{rec['id']}/{take['raw']}"}
    out["paths"] = {"wav": str(d / take["wav"]), "png": str(d / take["png"])}
    if take.get("variations"):
        out["variations"] = [{**v, "path": str(d / v["wav"]), "file": f"{rec['id']}/{v['wav']}"}
                             for v in take["variations"]]
    return out


def request_view(rec: dict) -> dict:
    keep = ("id", "created", "text", "prompt", "raw", "category", "cat_id", "name", "engine", "model",
            "seeds", "steps", "like", "post", "gen_length_s", "gen_s", "score_s", "warnings",
            "kind", "stack", "layers", "compare")
    out = {k: rec[k] for k in keep if k in rec}
    out["takes"] = [take_view(rec, t) for t in rec["takes"]]
    out["kept"] = sum(not t.get("rejected") for t in rec["takes"])
    return out


@app.post("/api/generate")
def generate(req: GenReq):
    saved = local_settings()  # read once: a save mid-request can't mix old and new
    opt = _resolve(req, saved)
    built = prompts.build_prompt(req.text, req.category, raw=bool(opt["raw"]), mode=req.mode)
    cat = prompts.BY_KEY[built["category"]]
    mode = req.mode or cat.mode
    duration = req.duration or cat.duration
    engine = opt["engine"]
    if req.seeds:
        seeds = list(req.seeds)
    else:
        start = opt["seed"] if opt["seed"] is not None else secrets.randbelow(2**31 - 64)
        seeds = [(start + k) % 2**31 for k in range(opt["n"])]
    length = gen_length(mode, duration, opt["pad_s"], req.crossfade_s)
    po = post.PostOpts(mode=mode, duration=duration, fps=opt["fps"] if req.fps is None else req.fps,
                       align_frame=req.align_frame, preroll_ms=opt["preroll_ms"] if req.preroll_ms is None else req.preroll_ms,
                       reverb=req.reverb or opt["reverb"], channels=req.channels or "auto",
                       crossfade_s=req.crossfade_s, peak_dbtp=opt["peak_dbtp"] if req.peak_dbtp is None else req.peak_dbtp,
                       lufs=opt["lufs"] if req.lufs is None else req.lufs)
    like_path = None
    if req.like:
        lrid, lseed = parse_take(req.like)
        like_path = req_dir(lrid) / find_take(load_request(lrid), lseed)["raw"]

    with _lock:
        try:
            w = worker(engine)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, f"engine {engine} failed to start: {exc} (see logs/server.log)") from exc
        info = w.info
        if length > info["max_duration"]:
            if mode == "loop":
                raise HTTPException(400, f"{engine} makes at most {info['max_duration']:.0f} s, and a {duration:g} s "
                                         f"loop needs ~{length:.0f} s: ask for a shorter loop or use another engine")
            if duration > info["max_duration"]:
                raise HTTPException(400, f"{engine} makes at most {info['max_duration']:.0f} s")
            length = info["max_duration"]
        if like_path is not None and "init_audio" not in info["features"]:
            raise HTTPException(400, f"{engine} can't do 'more like this' (audio-to-audio): reuse the take's "
                                     "seed with a tweaked prompt instead")
        rid = new_id()
        d = OUTPUT / rid
        d.mkdir(parents=True)
        rec = {"id": rid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "text": req.text, "prompt": built["prompt"],
               "raw": bool(opt["raw"]), "category": cat.key,
               "cat_id": req.cat_id or cat.cat_id, "name": req.name or prompts.fx_name(req.text),
               "engine": engine, "model": info["model"], "seeds": seeds,
               "steps": opt["steps"] or info["default_steps"],
               "like": req.like, "post": po.asdict(), "gen_length_s": round(length, 2), "takes": []}
        job = {"op": "generate", "prompt": built["prompt"], "duration": length, "seeds": seeds,
               "steps": rec["steps"], "out_dir": str(d), "stem": "gen"}
        if like_path is not None:
            job.update(init_audio=str(like_path), init_strength=opt["like_strength"])
        t0 = time.time()
        try:
            reply = w.call(job)
        except Exception as exc:
            shutil.rmtree(d, ignore_errors=True)
            print(f"[local-sounds] generation FAILED: {type(exc).__name__}: {exc}", flush=True)
            raise HTTPException(500, f"generation failed: {type(exc).__name__}: {exc}") from exc
        rec["gen_s"] = round(time.time() - t0, 2)
        try:
            _finish_generation(rec, d, reply, po, mode, opt)
        except Exception as exc:  # don't leave a half-made request folder behind
            shutil.rmtree(d, ignore_errors=True)
            if isinstance(exc, HTTPException):
                raise
            status = 400 if isinstance(exc, ValueError) else 500
            print(f"[local-sounds] checks FAILED: {type(exc).__name__}: {exc}", flush=True)
            raise HTTPException(status, f"post-processing or checks failed: {type(exc).__name__}: {exc}") from exc
    kept = sum(not t["rejected"] for t in rec["takes"])
    top = rec["takes"][0]
    print(f"[local-sounds] {time.strftime('%H:%M:%S')} {rid} {engine}: {len(seeds)} take(s) of "
          f"{rec['cat_id']}_{rec['name']} in {rec['gen_s']:.1f}s + {rec['score_s']:.1f}s checks, {kept} kept; "
          f"best seed {top['seed']} (score {top['rank_score']})", flush=True)
    return request_view(rec)


def _finish_generation(rec: dict, d: Path, reply: dict, po: post.PostOpts, mode: str, opt: dict) -> None:
    """The worker's raw files -> checked, scored, ranked and saved takes. Call with _lock held."""
    t1 = time.time()
    finals = []
    for f in reply["files"]:
        seed = int(f["seed"])
        raw_name = f"{seed}_raw.wav"
        os.replace(f["path"], d / raw_name)
        x, sr = audio.read(d / raw_name)
        take = {"seed": seed, "raw": raw_name, "wav": f"{seed}.wav", "png": f"{seed}.png",
                "signal": checks.signal_report(x, sr, mode)}
        rec["takes"].append(take)
        finals.append(render(rec, take, po))
    judge(rec, rec["takes"], finals, bool(opt["check"]))
    if opt["caption"]:
        best = [t for t in rec["takes"] if not t.get("rejected")][:1] or rec["takes"][:1]
        try:
            for t, c in zip(best, caption_takes([audio.read(d / t["wav"]) for t in best])):
                t["caption"] = c
        except HTTPException as exc:
            rec.setdefault("warnings", []).append(f"no caption: {exc.detail}")
    rec["score_s"] = round(time.time() - t1, 2)
    save_request(rec)
    _release_cache()


@app.get("/api/requests/{rid}")
def get_request(rid: str):
    return request_view(load_request(rid))


@app.get("/api/requests")
def list_requests(limit: int = 20):
    out = []
    if OUTPUT.is_dir():
        for d in sorted((p for p in OUTPUT.iterdir() if p.is_dir() and REQ_ID.fullmatch(p.name)), reverse=True):
            try:
                rec = json.loads((d / "request.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"id": rec["id"], "text": rec.get("text"), "engine": rec.get("engine"),
                        "name": f"{rec.get('cat_id')}_{rec.get('name')}", "takes": len(rec.get("takes", [])),
                        "kind": rec.get("kind", "generate")})
            if len(out) >= max(1, min(limit, 200)):
                break
    return out


@app.delete("/api/requests/{rid}")
def delete_request(rid: str):
    d = req_dir(rid)
    if not d.is_dir():
        raise HTTPException(404, f"no request {rid}")
    with _lock:
        n = sum(1 for p in d.iterdir() if p.is_file())
        shutil.rmtree(d)
    return {"id": rid, "deleted": n}


class TakeReq(PostFields):
    take: str                      # "<request id>:<seed>"
    check: bool | None = None


@app.post("/api/post")
def reprocess(req: TakeReq):
    """Re-run post-processing on a request's takes (no regeneration): alignment, mode, reverb, length..."""
    rid, seed = parse_take(req.take)
    check = req.check if req.check is not None else effective_settings()["check"]
    with _lock:  # read, change and save the record under the lock, so no other change is lost
        rec = load_request(rid)
        find_take(rec, seed)
        if rec.get("kind") == "layer":
            raise HTTPException(400, "a layered mix can't be re-processed (its source is the mix itself): "
                                     "make the layer again with the new settings")
        try:
            opts = post_opts(rec, req)
            rec["post"] = opts.asdict()
            # Every take shares the request's options: re-check and re-render all of them, so they
            # stay comparable (the signal checks depend on the mode).
            finals = []
            for t in rec["takes"]:
                x, sr = audio.read(req_dir(rid) / t["raw"])
                t["signal"] = checks.signal_report(x, sr, opts.mode)
                finals.append(render(rec, t, opts))
        except ValueError as exc:  # e.g. a loop longer than the raw audio allows
            raise HTTPException(400, str(exc)) from exc
        judge(rec, rec["takes"], finals, check)
        save_request(rec)
        _release_cache()
    print(f"[local-sounds] {time.strftime('%H:%M:%S')} re-processed {rid} ({opts.mode})", flush=True)
    return request_view(rec)


class VarReq(BaseModel):
    take: str
    n: int = Field(5, ge=2, le=12)
    amount: float = Field(1.0, ge=0.1, le=3.0)


@app.post("/api/variations")
def make_variations(req: VarReq):
    """Round-robin set from one take: v01 is the take, v02.. small pitch/speed and EQ changes."""
    rid, seed = parse_take(req.take)
    d = req_dir(rid)
    with _lock:
        rec = load_request(rid)
        take = find_take(rec, seed)
        if rec.get("kind") == "layer":
            raise HTTPException(400, "make variations of the layers' takes, then layer those")
        opts = post_opts(rec)
        x, sr = audio.read(d / take["raw"])
        out = []
        for k, (y, rep) in enumerate(post.variations(x, sr, opts, n=req.n, seed=seed, amount=req.amount), 1):
            name = f"{seed}_v{k:02d}.wav"
            info = _info(rec, take)
            info["title"] = f"{rec['cat_id']}_{rec['name']}_v{k:02d}"
            audio.write_wav(d / name, y, info=info)
            out.append({"wav": name, "version": k, **rep})
        take["variations"] = out
        save_request(rec)
    return take_view(rec, take)


class LayerItem(BaseModel):
    take: str
    gain_db: float = Field(0.0, ge=-48, le=12)
    offset_ms: float | None = Field(None, ge=-10000, le=60000)
    offset_frames: float | None = Field(None, ge=-1000, le=10000)


class LayerReq(BaseModel):
    layers: list[LayerItem] = Field(min_length=1, max_length=12)
    fps: float | None = Field(None, ge=1, le=240)  # default: the saved / repo fps
    mode: Mode = "oneshot"
    align_frame: int | None = Field(None, ge=0, le=100000)
    name: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9]*$")
    cat_id: str | None = Field(None, max_length=12, pattern=r"^[A-Za-z0-9]*$")
    check: bool | None = None  # score the mix like a take (default: the saved setting)


@app.post("/api/layer")
def make_layer(req: LayerReq):
    """Mix delivered takes, each with its own gain and transient offset, through a limiter.

    Layers can be different sounds (a thud + a clang) or several takes of the same request
    (a "stack": fuller, more natural groups, crowds and textures). The mix is checked and
    scored like a take, and each source take's own scores come back alongside (`layers`), so
    a caller that can't listen can still see whether the mix beats its best single take.
    """
    fps = req.fps or effective_settings()["fps"]
    check = req.check if req.check is not None else effective_settings()["check"]
    layers, metas = [], []
    for item in req.layers:
        rid, seed = parse_take(item.take)
        rec = load_request(rid)
        t = find_take(rec, seed)
        y, _ = audio.read(req_dir(rid) / t["wav"])
        off = (item.offset_frames / fps) if item.offset_frames is not None else (item.offset_ms or 0) / 1000
        layers.append(post.Layer(audio=y, gain_db=item.gain_db, offset_s=off, onset_s=t["post"].get("onset_s", 0.0),
                                 meta={"take": item.take, "name": f"{rec['cat_id']}_{rec['name']}",
                                       "clap_cos": t.get("clap_cos"), "clap": t.get("clap"),
                                       "PQ": (t.get("aes") or {}).get("PQ"), "rank_score": t.get("rank_score")}))
        metas.append(rec)
    try:
        opts = post.PostOpts(mode=req.mode, fps=fps, align_frame=req.align_frame)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with _lock:
        mix, rep = post.layer(layers, opts)
        rid = new_id()
        d = OUTPUT / rid
        d.mkdir(parents=True)
        first = metas[0]
        stack = len({m["id"] for m in metas}) == 1  # every layer is a take of the same request
        uniq = list(dict.fromkeys(m["id"] for m in metas))
        by_id = {m["id"]: m for m in metas}
        rec = {"id": rid, "kind": "layer", "stack": stack, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "text": first["text"] if stack else " + ".join(by_id[i]["text"] for i in uniq),
               "prompt": first["prompt"] if stack else " + ".join(by_id[i]["prompt"] for i in uniq),
               "category": first["category"], "cat_id": req.cat_id or first["cat_id"],
               "name": req.name or (first["name"] + ("Stack" if stack else "Layered"))[:40], "engine": "layer",
               "model": "layer", "post": opts.asdict(), "layers": rep["layers"], "takes": []}
        take = {"seed": 0, "raw": "0.wav", "wav": "0.wav", "png": "0.png", "post": {k: v for k, v in rep.items()
                                                                                   if k != "layers"},
                "signal": checks.signal_report(mix, audio.SR, req.mode)}
        rec["takes"] = [take]
        # The same checks and scores as any take (and the WAV written with its INFO chunk).
        judge(rec, [take], [mix], check)
        best = max((ly for ly in rec["layers"] if ly.get("rank_score") is not None),
                   key=lambda ly: ly["rank_score"], default=None)
        if best and take.get("rank_score") is not None and "clap_cos" in take:
            rec["compare"] = {"mix": take["rank_score"], "best_layer": best["rank_score"], "best_layer_take": best["take"]}
        plot.draw(mix, audio.SR, d / "0.png", title=f"{rec['cat_id']}_{rec['name']}  |  {rec['text'][:110]}",
                  fps=fps, onset_s=rep["onset_s"] if req.mode == "oneshot" else None,
                  align_frame=req.align_frame, seam=req.mode == "loop")
        save_request(rec)
    return request_view(rec)


class CaptionReq(BaseModel):
    takes: list[str] = Field(min_length=1, max_length=16)


@app.post("/api/caption")
def caption(req: CaptionReq):
    """What the captioner hears in each take. Loads the model, captions, frees it."""
    refs = [parse_take(r) for r in req.takes]
    with _lock:
        recs = {rid: load_request(rid) for rid in dict.fromkeys(rid for rid, _ in refs)}  # one record per request
        takes = [find_take(recs[rid], seed) for rid, seed in refs]
        caps = caption_takes([audio.read(req_dir(rid) / t["wav"]) for (rid, _), t in zip(refs, takes)])
        for t, c in zip(takes, caps):
            t["caption"] = c
        for rec in recs.values():
            save_request(rec)
    return [{"take": r, "caption": c} for r, c in zip(req.takes, caps)]


@app.get("/api/prompt")
def preview_prompt(text: str, category: str | None = None, raw: bool = False, mode: str | None = None):
    """The prompt, category and UCS name a request would use. Nothing is generated."""
    if category is not None and category not in prompts.BY_KEY:
        raise HTTPException(400, f"unknown category {category!r}")
    b = prompts.build_prompt(text, category, raw=raw, mode=mode)
    return {**b, "name": prompts.ucs_filename(b["cat_id"], prompts.fx_name(text), 1),
            "distractors": prompts.distractors(b["prompt"], b["category"])}


@app.get("/api/categories")
def categories():
    return [{"key": c.key, "cat_id": c.cat_id, "mode": c.mode, "duration": c.duration, "envelope": c.envelope,
             "keywords": list(c.keywords), "dry": c.dry} for c in prompts.CATEGORIES]


@app.get("/api/engines")
def engines():
    return {k: {"label": e["label"], "installed": engine_installed(k),
                "running": k in _workers and _workers[k].alive(),
                "info": _workers[k].info if k in _workers and _workers[k].alive() else None}
            for k, e in ENGINES.items()}


@app.get("/api/health")
def health():
    import torch
    free, total = torch.cuda.mem_get_info() if torch.cuda.is_available() else (0, 0)
    return {"ok": True, "version": __version__,
            "engines": {k: (k in _workers and _workers[k].alive()) for k in ENGINES},
            "clap_loaded": _clap is not None, "aesthetics_loaded": _aes is not None,
            "captioner_installed": captioner_installed(), "busy": _lock.locked(),
            "vram_used_gb": round((total - free) / 2**30, 2), "vram_total_gb": round(total / 2**30, 2)}


@app.get("/api/settings")
def get_settings():
    local = local_settings()
    return {"effective": {**REPO_DEFAULTS, **local}, "saved": local, "repo": REPO_DEFAULTS,
            "file": SETTINGS_FILE.name if SETTINGS_FILE.is_file() else None}


@app.post("/api/settings")
def save_settings(new: Settings):
    # Only what differs from the repo's defaults is stored: if those change in an update,
    # anything the user never chose follows along.
    given = _keep(new.model_dump(exclude_unset=True))
    data = {k: v for k, v in given.items() if v != REPO_DEFAULTS.get(k)}
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    with _file_lock:  # a reader never sees a half-written file
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    return get_settings()


@app.delete("/api/settings")
def reset_settings():
    SETTINGS_FILE.unlink(missing_ok=True)
    return get_settings()


@app.get("/files/{rid}/{name}")
def files(rid: str, name: str):
    if not FILE_NAME.fullmatch(name):
        raise HTTPException(404)
    p = req_dir(rid) / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- bake-off page

class Vote(BaseModel):
    prompt_id: str = Field(max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    winner: str = Field(max_length=20, pattern=r"^[a-z0-9-]+$")  # an engine's key, "tie" or "neither"
    note: str | None = Field(None, max_length=500)


@app.get("/bakeoff", response_class=HTMLResponse)
@app.get("/bakeoff/", response_class=HTMLResponse)
def bakeoff_page():
    p = BAKEOFF / "index.html"
    if not p.is_file():
        raise HTTPException(404, "no bake-off yet: run scripts/bakeoff.py")
    return p.read_text(encoding="utf-8")


@app.get("/bakeoff/{name}")
def bakeoff_file(name: str):
    if not FILE_NAME.fullmatch(name):
        raise HTTPException(404)
    p = BAKEOFF / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.post("/api/bakeoff/vote")
def bakeoff_vote(v: Vote):
    path = BAKEOFF / "votes.json"
    with _file_lock:
        votes = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        votes[v.prompt_id] = {"winner": v.winner, "note": v.note, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        BAKEOFF.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(votes, indent=2), encoding="utf-8")
    tally: dict[str, int] = {}
    for x in votes.values():
        tally[x["winner"]] = tally.get(x["winner"], 0) + 1
    return {"votes": len(votes), "tally": tally}


@app.get("/api/bakeoff/votes")
def bakeoff_votes():
    path = BAKEOFF / "votes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@app.get("/", response_class=HTMLResponse)
def page():
    return (Path(__file__).parent / "page.html").read_text(encoding="utf-8")


_instance_lock = None


def _single_instance() -> bool:
    """Hold an exclusive lock on output/.server.lock for this process's lifetime."""
    global _instance_lock
    OUTPUT.mkdir(exist_ok=True)
    fh = open(OUTPUT / ".server.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _instance_lock = fh
    return True


WINDOW_MARK = "LOCAL_SOUNDS_WINDOW"  # set by start.cmd for the console window it opens, and only there


def main() -> None:
    # Always in its own visible window, so a server holding GPU memory can't be left running
    # out of sight: start.cmd opens that window and marks it; any other start is refused.
    # The models are only ever freed by stopping the server (close the window, or stop.cmd).
    if os.environ.get(WINDOW_MARK) != "1":
        sys.exit("local-sounds runs only in its own visible window: start it with start.cmd "
                 "(close that window, or run stop.cmd, to stop it).")
    ap = argparse.ArgumentParser(description="local-sounds server")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--engines", default=None,
                    help="comma-separated engines to start now (default: the default engine); others start on demand")
    ap.add_argument("--lazy", action="store_true", help="start nothing until the first request")
    args = ap.parse_args()
    # Fail fast if the port is taken (most likely by another copy of this server)
    # rather than loading models onto the GPU first and dying at bind time.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            sys.exit(f"127.0.0.1:{args.port} is in use: is local-sounds already running? (stop.cmd stops it)")
    # The port is only bound once the models are loaded; until then this lock stops a second copy.
    if not _single_instance():
        sys.exit("local-sounds is already starting up (output/.server.lock is held); stop.cmd stops it")
    log_path = serverlog.install(LOGS)
    print(f"[local-sounds] full log: {log_path}", flush=True)
    # stop.cmd stops exactly this process tree (and the engine workers under it)
    # by PID, whatever path - real folder or skills junction - started it.
    OUTPUT.mkdir(exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
    if not args.lazy:
        names = [s.strip() for s in (args.engines or effective_settings()["engine"]).split(",") if s.strip()]
        with _lock:
            for n in names:
                try:
                    worker(n)
                except Exception as exc:  # the server still serves the page and the other engine
                    print(f"[local-sounds] could not start {n}: {getattr(exc, 'detail', exc)}", flush=True)
            try:
                # A first call pays ~15 s for CUDA kernel selection: do it now, not on a request.
                noise = (np.random.default_rng(0).standard_normal((1, audio.SR)) * 0.1).astype(np.float32)
                clap().score([noise], audio.SR, "noise", ["silence"])
                aes().score([noise], audio.SR)
                _release_cache()
            except Exception as exc:
                print(f"[local-sounds] checkers not loaded: {getattr(exc, 'detail', exc)}", flush=True)
    # Loopback only, always: nothing here is meant to be reachable from the network.
    print(f"[local-sounds] ready on http://127.0.0.1:{args.port}/  (stop: close this window, or stop.cmd)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
