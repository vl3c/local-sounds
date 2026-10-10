# local-sounds

A private, fully local **sound-effects generator for animation**, built for AI agents (and
people) to call. Ask for one sound and a length; get back clean, checked, frame-ready WAVs:
the hit exactly on a frame, loops that loop, round-robin variations, layers, and names a
sound library would use (`METLImpt_SwordHit_v01.wav`).

It runs an open-weight text-to-audio model on your own GPU, **Stable Audio 3 Small-SFX**,
makes several takes per request, and judges each one automatically, the SFX equivalent of a
Whisper check: signal checks, a CLAP prompt-match score, Meta's Audiobox Aesthetics quality
score, and a spectrogram with frame gridlines. No accounts, no cloud: prompts and audio never
leave the machine. Sound effects only, not music or speech.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -AcceptModelLicenses   # ~10 GB download, ~17 GB on disk
.\start.cmd                                                                  # opens http://127.0.0.1:8191/
.venv\Scripts\python.exe scripts\sfx.py "sword hit on a shield" --out D:\anim\sfx
```

> **Licences, plainly.** This repo's code is MIT. The engine, **Stable Audio 3, is under the
> Stability AI Community License: free for personal, research and non-commercial use, and for
> commercial use while you (with your affiliates) make under US $1M a year, after registering
> with Stability AI.** You own the sounds you make. Details in
> [LICENSE-MODELS.md](LICENSE-MODELS.md); `-AcceptModelLicenses` says you've read it. Powered by
> Stability AI.
>
> **Windows 10/11 and an NVIDIA GPU (~4 GB of VRAM for the server) are required.**

## For AI agents (Claude Code skill)

`SKILL.md` is a Claude Code skill: prompt recipes per category, how to read the scores and the
spectrogram, cue sheets, and how to deliver to a folder. If Claude Code is installed (there is a
`~\.claude` folder), `install.ps1` links the repo into your personal skills folder (a junction,
so it follows the repo); by hand:

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\local-sounds" -Target (Resolve-Path .).Path
```

Then ask Claude Code for a sound ("a cartoon boing on frame 12, 24 fps, into D:\anim\sfx").
Any other agent can use the CLI (`scripts/sfx.py`, standard library only) or the HTTP API below.

## How it works

1. **Prompt.** A terse request ("sword hit") is detected as a category and rewritten the way
   these models were trained: one sound event, source and material, action, envelope, "close-mic,
   dry, no reverb" (`sfx/prompts.py`; `--raw` sends your text as is).
2. **Generate best-of-N.** N takes (default 4) on consecutive seeds, a little longer than asked
   (0.75 s more for one-shots, ~2x for loops), so there is room to trim and loop.
3. **Check every take** (`sfx/checks.py`, `sfx/scoring.py`): the signal checks on the model's raw
   output (where clipping, DC and truncation come from), the scores, true peak and loudness on the
   finished file:
   - signal: clipping, true peak, DC offset, silence or too little sound, noise floor, onset
     position, a cut-off tail, stereo correlation; a sound the `--duration` cap cut while still
     loud; loops: the seam.
   - CLAP (`laion/larger_clap_general`): the prompt's softmax against distractor captions (music,
     speech, silence, white noise, static, and near misses for the category), plus the raw
     cosine similarity. For ranking only, never as an absolute threshold.
   - Audiobox Aesthetics: production quality (PQ) as the noise / artefact gate (one-shots under 4 are
     rejected; for ambiences it only warns, since it reads rain and wind as noise).
   - a waveform + spectrogram PNG with frame gridlines and an onset marker.
   - optionally (`--caption`) an audio captioner, MiDashengLM 0.6B, which says what was actually
     generated. It is loaded only when asked and freed straight afterwards.
4. **Rank** (`rank_score`: CLAP match and quality combined, minus warnings), reject the failures,
   return them all with their seeds, so the agent can ask for "more like #3".
5. **Post-process for animation** (`sfx/post.py`; pedalboard, soxr, pyloudnorm, scipy):
   - one-shots: trimmed to a 3 ms pre-roll (1 ms fade-in) so the transient lands at t = 0, an
     exponential fade-out, folded to mono, peak-normalised to **-1 dBTP**, padded to whole frames.
     `--align-frame F --fps 24` puts the hit exactly on frame F (48 kHz: 2000 samples a frame at
     24 fps, 1600 at 30, 800 at 60).
   - loops: cut from ~2x the length where the sound is steady, with the file starting on a zero
     crossing; an equal-power crossfade of the tail into the head makes the end continue
     exactly into the start; the seam is then checked; length is a whole number of frames.
   - ambiences and loops: stereo, **-23 LUFS** with a -1 dBTP ceiling (limited if needed).
   - variations: a round-robin set `_v01.._v05` (v01 the take, the rest +-0.8 semitone
     tape-speed and +-2 dB EQ changes, each re-trimmed and re-normalised).
   - layering: takes mixed with per-layer gain and transient offset (ms or frames), through a
     limiter; loops layered in loop mode are wrapped so the mix still loops.
   - stacking (`--stack N`): the best N takes of one request mixed into one sound. Several
     slightly different performances together sound fuller and more natural, which suits groups
     (a flock, a pack), textures (rain, fire, swarms) and thick "big" sounds; a single crisp hit
     just smears. Mixes are scored like takes, but stacks usually score *lower* than their best
     single take (denser reads as noise to the scorers), so the CLI delivers both to compare by ear.
   - reverb: dry by default; `room` or `hall` optionally.
6. **Deliver**: 48 kHz 24-bit PCM WAV (not FLAC: After Effects can't import it) with the prompt,
   seed, model and scores in the RIFF INFO chunk, plus a JSON sidecar, named the UCS way:
   `CatID_FXName_vNN.wav` with real [UCS v8.2](https://universalcategorysystem.com) CatIDs
   (UCS has no IMPACTS category: a metal impact is `METLImpt`).

### Engines

The engine has its own venv and runs as a child process of the server (`server/workers/`), so
its pinned dependencies (Stable Audio 3 pins torch 2.7 and transformers 5) stay apart from the
server's, and another engine could be added beside it. Both venvs use CUDA 12.8 wheels, the
oldest that support RTX 50-series (Blackwell, sm_120) cards.

**sa3: Stable Audio 3 Small-SFX** (May 2026), a 433M DiT with a T5Gemma text encoder, a 3.3 GB
download. ~0.5 s a take (8 steps) on an RTX 5080, warm, in ~2.5 GB of VRAM; 44.1 kHz stereo
(resampled to 48 kHz), up to 120 s; audio-to-audio ("more like this") supported. This
post-trained checkpoint ignores negative prompts and CFG (Stability's docs), so they aren't
offered. Licence: Stability AI Community License (gated).

**The bake-off** (`scripts/bakeoff.py --engines sa3,<other>`): 22 animation-style prompts
(impacts on metal, wood, glass and stone; a punch; whooshes; UI blips; footsteps on gravel, wood
and snow; a cartoon boing and pop; magic; sci-fi machines; creatures; an explosion; two ambience
loops), 3 seeds each on every engine named, scored by the automatic checks, then a blind A/B page
(http://127.0.0.1:8191/bakeoff/) to pick by ear. It's how sa3 was chosen: see "Bake-off results" below.

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11 (the scripts are `.cmd` and PowerShell) |
| GPU | NVIDIA; the server needs ~4 GB of VRAM (SA3 ~2.5 GB + the checkers ~1.5 GB). RTX 50-series supported (CUDA 12.8 wheels); driver 570 or newer |
| Disk | ~17 GB (weights ~4.5 GB, two venvs ~12 GB) plus pip cache |
| Software | Python 3.12+ from [python.org](https://www.python.org/downloads/windows/) with the `py` launcher (3.13 by default), [Git for Windows](https://git-scm.com/download/win) |
| Hugging Face | A free account: Stable Audio 3 is gated (see Install) |

## Install

1. On [stabilityai/stable-audio-3-small-sfx](https://huggingface.co/stabilityai/stable-audio-3-small-sfx),
   log in to Hugging Face and accept the licence (it's granted at once). Create a **read**
   access token under Settings > Access Tokens.
2. Install:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1 -AcceptModelLicenses
   ```

3. When it reaches the Stable Audio 3 download, it stops with "This repo is gated" unless you
   are logged in already. Log in with the token from step 1, then run the same install command
   again (finished steps are skipped):

   ```powershell
   .venv\Scripts\hf.exe auth login
   ```

The installer clones the Stable Audio 3 inference code at a pinned commit into `engine\`,
builds `.venv\` (server and checks) and `.venv-sa3\` with CUDA 12.8 PyTorch builds (a
constraints file stops any requirement from swapping them for a CPU build), downloads the
weights into `models\`, and links the skill. Re-running is safe: finished steps are skipped
and downloads resume. The token stays in Hugging Face's own login store; nothing here reads it.

**Weights are pinned:** `models.json` fixes each repo to a revision, and `models.lock.json`
records every file's size and SHA256, so an upstream re-upload fails loudly. Files you already
have are reused instead of downloaded, from another install (`-ReuseModels <its models folder>`)
or from your Hugging Face cache, hard-linked when on the same drive.

| Model | Size | Role | Licence |
|---|---|---|---|
| [Stable Audio 3 Small-SFX](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) | 3.3 GB | engine | Stability AI Community License |
| [CLAP larger_clap_general](https://huggingface.co/laion/larger_clap_general) | 0.74 GB | prompt-match score | Apache-2.0 |
| [Audiobox Aesthetics](https://huggingface.co/facebook/audiobox-aesthetics) | 0.40 GB | quality score | CC-BY-4.0 |
| [MiDashengLM 0.6B](https://huggingface.co/mispeech/midashenglm-0.6b-fp32) (optional, `-WithCaptioner`) | 2.8 GB | captioner | Apache-2.0 |

Options: `-WithCaptioner`, `-ReuseModels <path>`, `-SkipModels`, `-NoSkillLink`,
`-PythonVersion 3.12`, `-NoPipCache`. (`-CudaIndex` exists, but cu128 is what was tested; SA3's
torch 2.7.1 has no wheels for newer CUDA indexes.) Verify the weights at any time
without downloading anything: `.venv\Scripts\python.exe scripts\download_models.py --verify`.

## Using it

**The server is started and stopped, nothing else, and always runs in its own window.**
**`.\start.cmd`** opens it in a console window titled "local-sounds server - close this window
to stop it" and returns at once; the page opens when it's ready (SA3 and the checkers load in
~30 s). `start.cmd --no-browser` skips the page. The command line (`scripts\sfx.py`) starts it the same way by itself when it isn't running, unless another
program already holds over 4 GB of the GPU's memory: then it says so and starts nothing
(`--start-anyway` overrides; `--no-start` never starts it).

The server refuses to run anywhere else (a hidden background shell, `python server\app.py`), so
it can't be left holding the GPU out of sight. It keeps its models loaded until it stops:
**close its window, or run `.\stop.cmd`**, which stops it and its engine workers, frees the GPU,
closes the window and reports the VRAM in use afterwards (exit code 1 if anything survived). A
second start while one runs exits at once ("port in use").

**The server's log.** The window shows milestones, one line per request and any errors; the
full output (library notices, progress bars) goes to `logs\server.log`, timestamped. Each start
keeps the previous run as `logs\server.previous.log`.

### Command line (what agents use)

The CLI is standard-library Python: any Python 3.10+ runs it, the venv's is simply always there.

```powershell
$py = ".venv\Scripts\python.exe"
& $py scripts\sfx.py "sword hit on a shield" --duration 1.2 --out D:\anim\sfx
& $py scripts\sfx.py "door slam" --align-frame 36 --fps 24 --out D:\anim\sfx
& $py scripts\sfx.py "rain on a tin roof" --loop --duration 8 --out D:\anim\sfx
& $py scripts\sfx.py "footstep on gravel" --variations 5 --out D:\anim\sfx
& $py scripts\sfx.py "laser zap" --like 20261004-142803-c6ab:13              # more like that take
& $py scripts\sfx.py --post 20261004-142803-c6ab --align-frame 40             # re-process only
& $py scripts\sfx.py --variations 5 --take 20261004-142803-c6ab:13 --out D:\anim\sfx   # round-robins of a take
& $py scripts\sfx.py --layer 20261004-142803-c6ab:13 --layer 20261004-143001-9f2e:5,gain=-6,offset=2f --out D:\anim\sfx
& $py scripts\sfx.py "a group of pelicans chatting" --stack 4 --out D:\anim\sfx   # 4 takes stacked into one
& $py scripts\sfx.py --batch film.json --out D:\anim\sfx                     # a whole cue sheet (below)
& $py scripts\sfx.py "sword hit" --json                                       # machine-readable
& $py scripts\sfx.py --show-prompt "sword hit"     # the prompt and file name, nothing generated
& $py scripts\sfx.py --settings                    # the defaults in effect
```

`SKILL.md` has the full flag table, what every score and warning means, and how to read the
spectrogram. Exit codes: 0 done, 1 no server / server error, 2 bad arguments, 3 every take was
rejected (with `--batch`: some cue delivered nothing).

### Cue sheets (a whole film's sounds)

A film needs dozens of sounds. Put them in one JSON file and run them in one go:

```json
{
  "defaults": {"fps": 24},
  "cues": [
    {"id": "door", "text": "heavy wooden door slam", "align_frame": 36, "name": "DoorSlam"},
    {"id": "rain", "text": "rain on a tin roof", "loop": true, "duration": 8},
    {"id": "flock", "text": "a flock of seagulls squawking", "stack": 4},
    {"id": "glitch", "text": "digital glitch stutter, short", "n": 6},
    {"id": "glitches", "text": "digital glitch burst", "like": "@glitch", "like_strength": 0.6, "n": 8, "all": true}
  ]
}
```

```powershell
& $py scripts\sfx.py --batch film.json --out D:\anim\sfx --dry-run   # check it; prompts if the server is up
& $py scripts\sfx.py --batch film.json --out D:\anim\sfx
```

A cue takes the command line's options by their long names (`text`, `duration`, `mode` or
`"loop": true`, `align_frame`, `n`, `seed`/`seeds`, `engine`, `category`, `name`, `cat`, `stack`,
`variations`, `best`/`all`, `out`, ...) plus an `id` (default: its `name`, or one made from its
text). The whole sheet is checked before anything is generated. Options in `defaults` apply to
every cue; options given on the command line override `defaults`; a cue's own options override
both. `"like": "@glitch"` means the best take of the earlier cue `glitch`.

Cues run one after another, delivering into `--out` as they go. `film.summary.json` next to the
delivered files is rewritten after every cue: each cue's status, request, prompt, every take's
ref, seed, scores, warnings and spectrogram, and the delivered files. **Running the same command
again carries on**: cues already delivered are skipped (unless the cue changed, its files are
gone, or `--redo glitch,door` names it); failed ones and ones whose every take was rejected run
again. A failing cue doesn't stop the batch; a lost server does (re-run to resume).

### Families of related sounds

For a set that should read as one family in the mix (eight glitches, a creature's calls, one
machine's noises), grow the set from one anchor take instead of eight unrelated prompts: make the
anchor, pick the take you like, then ask for many takes like it with `--like`. Each seed gives
a genuinely different sound that keeps the anchor's character; the text can change per call.

```powershell
& $py scripts\sfx.py "digital glitch stutter, short" --n 6                                 # the anchor; pick a take
& $py scripts\sfx.py "digital glitch burst" --like <ref> --like-strength 0.6 --n 8 --all --out D:\anim\sfx
```

`--like-strength` sets the distance: 0.3 stays close, 0.5 (default) varies, 0.8 only keeps the
broad colour. In a cue sheet, `"like": "@anchor"` does the same with the anchor cue's best take.
`--variations` is for something else: round-robin copies of *one* take (a slightly different
pitch and EQ each), so a repeated footstep doesn't sound identical; as a family they'd sound
like the same sound eight times.

**Your own defaults.** On the page, under Advanced, **Make these my defaults** stores the settings
(engine, takes, seed, fps, reverb, steps, raw wording, scoring) in `config.local.json`
(local, gitignored); **Restore original defaults** deletes it. Both ask first. Saved settings apply to
the page and to every API/CLI request that leaves a setting out; the repo's defaults stay in
`server/app.py` (`REPO_DEFAULTS`). `sfx.py --settings` shows what's in effect.

### The page

http://127.0.0.1:8191/ is kept simple: describe a sound, pick the kind (Auto, One-shot, Loop,
Ambience), optionally a length and the frame the hit should land on, and press **Generate**
(Ctrl+Enter). The line under the text says what it will make ("Sounds like: impact glass ·
one-shot · 1.5 s"). Takes come back best first, each with a player, plain **Match** and
**Quality** ratings, **Download** (named like a library file, `GLASImpt_..._v01.wav`), **More like
this**, **Make 5 variations** and **Add to mix**; the spectrogram, seed, raw scores
and caption sit under **Details**, and rejected takes are folded away. **Adjust timing (all
takes)** re-trims a request without regenerating: move the hit to another frame, turn it into a
loop, change the length. **Recent** reopens earlier requests and puts their text back in the box.

**The mixer** appears when you add takes to it: each layer has a position slider (where its hit
lands, with one-frame nudges) and a volume slider, and the timeline shows them on the frame grid
(drag a layer to move it). **Play mix** previews instantly in the browser; **Export mix** renders it
on the server (limiter, level, whole frames; loops stay loops) and gives you the WAV.

Everything else (engine, when more than one is installed; category, number of takes, seed, fps,
reverb, steps, raw wording, scoring, and saving your defaults to `config.local.json`, which asks first) is under
**Advanced**, which shows how many settings differ from your defaults. Hover over a control for
half a second for an explanation.

### HTTP API (127.0.0.1:8191)

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/generate` | `{"text", "duration"?, "n"?, "seed"?, "seeds"?, "engine"?, "category"?, "mode"?, "raw"?, "steps"?, "like"?, "like_strength"?, "fps"?, "align_frame"?, "preroll_ms"?, "reverb"?, "channels"?, "crossfade_s"?, "check"?, "caption"?, "name"?, "cat_id"?}` | the request: id, prompt, engine, and `takes` ranked, each with `ref`, `seed`, `rank`, `rank_score`, `clap`, `clap_cos`, `aes`, `signal`, `post`, `final`, `fails`, `warnings`, `rejected`, `files` and `paths` |
| `POST /api/post` | `{"take", ...post-processing fields}` (`"align_frame": null` removes an alignment) | the request, every take re-processed from its raw audio (not for layered mixes: re-make those) |
| `POST /api/variations` | `{"take", "n"?, "amount"?}` | the take with `variations` |
| `POST /api/layer` | `{"layers": [{"take", "gain_db"?, "offset_ms"? or "offset_frames"?}], "fps"?, "mode"?, "align_frame"?}` | a new request with the mix (fps defaults to the saved one) |
| `POST /api/caption` | `{"takes": [...]}` | what the captioner hears in each |
| `GET /api/prompt?text=` | | the prompt, category, file name and CLAP distractors |
| `GET /api/requests`, `GET` / `DELETE /api/requests/<id>` | | recent requests; one request; delete its files |
| `GET /api/categories`, `/api/engines`, `/api/health` | | recipes; engines; status and VRAM |
| `GET` / `POST` / `DELETE /api/settings` | the settings to save | `effective`, `saved`, `repo`; save / reset `config.local.json` |
| `GET /files/<id>/<name>` | | a WAV, PNG or JSON from a request |

A take ref is `"<request id>:<seed>"`. Bad input is a 4xx with a message. In `/api/generate`, an
explicit `null` means "use the default", except `"seed": null`, which means a random seed. Requests must use Host
`127.0.0.1` or `localhost`, and writes from other web origins are refused.

### Requests and files

Every generation is a *request* with an id like `20261004-142803-c6ab`, in `output\<id>\`:

| File | What |
|---|---|
| `request.json` | the request, the prompt actually sent, and every take's checks and scores |
| `<seed>_raw.wav` | the engine's output as generated (float WAV, 44.1 kHz) |
| `<seed>.wav` | the delivered sound, 48 kHz 24-bit, INFO chunk included |
| `<seed>.png` | waveform + spectrogram with frame grid and onset marker |
| `<seed>_v01.wav`... | round-robin variations |

Besides requests, `output\` holds the bake-off (`output\bakeoff\`) and, while the server runs,
`.server.pid` and `.server.lock`: clear the rest any time. Delivered copies (`--out`) are yours.

## Bake-off results

MOSS-SoundEffect v2.0 was the second engine until October 2026 and was removed after this
bake-off: sa3 won by ear, 9 to 4, and is ~40x faster.

October 2026, RTX 5080: 22 prompts, seeds 11-13 on each engine (132 takes), then a blind A/B
vote by ear on each prompt's best take per engine.

| | sa3 (Stable Audio 3 Small-SFX) | moss (MOSS-SoundEffect v2.0) |
|---|---|---|
| **Votes by ear** | **9 wins** (sword clash, crate drop, glass shatter, character zoom, UI error, wood footstep, sci-fi door, small creature, forest ambience) | 4 wins (rock fall, cartoon boing, magic spell, explosion) |
| Ties / neither | 8 both good, 1 neither (gravel footstep) | |
| Mean CLAP cos (all takes) | 0.378 | 0.315 |
| Mean Audiobox PQ | 6.70 | 6.77 |
| Rejected takes | 2 of 66 | 3 of 66 |
| Time per take | 0.3 s | 13.3 s |

The automatic scores preferred moss on several impacts that the ear gave to sa3: CLAP ranks
takes of one prompt, it doesn't pick engines. To compare a new engine, run
`scripts/bakeoff.py --engines sa3,<other>`; the page is at http://127.0.0.1:8191/bakeoff/.

## Notes and gotchas

- **Stable Audio 3 Small-SFX and fractional lengths.** Some fractional durations (0.5, 1.25,
  2.2, 2.25 s...) make it output full-scale clipped static; every whole second from 1 to 30
  tested clean. The worker always asks for whole seconds and post-processing trims.
- **SA3 Small-SFX ignores negative prompts and CFG** (only Stability's `-base` checkpoints use
  them), so local-sounds doesn't offer them.
- **No seamless loops from SA3 itself** (upstream issue #109): loop mode handles it.
- **CLAP is a rough proxy.** It ranks takes of the same prompt well enough; across prompts the
  numbers aren't comparable, and a good-sounding take can score low. Look, listen, decide.

## Privacy

The server binds to 127.0.0.1 only (hard-coded) and forces Hugging Face offline, so after
installation nothing contacts the network; there is no telemetry. `output\` holds every request
(gitignored); delete it whenever you like.

## Before pushing

```powershell
.venv\Scripts\python.exe scripts\check_repo_safe.py            # what git would publish
.venv\Scripts\python.exe scripts\check_repo_safe.py --history  # and every past commit
```

It fails on weights, audio, images, large files, local paths, emails, IP addresses and
credential-shaped strings. It can't judge prose, so read your own diff too. CI runs it (with
the history) and the tests on every pull request; `main` only takes changes through a pull
request whose `tests` check passed.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Post-processing (frames, alignment, loops, loudness, variations, layers), the signal checks,
WAV/INFO writing, prompts and names, settings, request handling and the HTTP guards, cue sheets,
and starting and stopping the server. No models or GPU are needed: CI runs them on a CPU-only
Windows runner with `requirements-ci.txt`.

## Licensing

This repo's code is MIT ([LICENSE](LICENSE)). The weights keep their own licences: Stable Audio 3
under the Stability AI Community License. [LICENSE-MODELS.md](LICENSE-MODELS.md) says plainly
what that means for the sounds you make (in short: they're yours, free for non-commercial use
and for commercial use under US $1M a year in revenue, after registering with Stability AI).
Powered by Stability AI.

## Repo layout

```
install.ps1 / start.cmd / stop.cmd     setup, launch, shutdown
models.json, models.lock.json          pinned, checksummed weight manifest
requirements*.txt                      the two venvs' dependencies, and CI's
server/app.py, server/page.html        the loopback server and its page
server/workers/                        one worker per engine (own venv), and their protocol
server/serverlog.py                    clean console, full log file
sfx/                                   post-processing, checks, scores, plots, prompts, names
scripts/sfx.py                         CLI (standard library only)
scripts/bakeoff.py                     the engine bake-off and its A/B page
scripts/download_models.py             resumable, verifying downloader
scripts/check_repo_safe.py             pre-publish scan
tests/                                 pytest
.github/workflows/tests.yml            CI: the tests and the safety scan, on Windows
SKILL.md                               the Claude Code skill
engine/, models/, .venv*/, output/, logs/   created at install / run time, gitignored
```
