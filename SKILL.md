---
name: local-sounds
description: Generate sound effects locally for animation (impacts, whooshes, UI blips, footsteps, cartoon boings and pops, magic, sci-fi machines, creatures, explosions, ambience loops) with Stable Audio 3 Small-SFX on this machine's GPU. Every take is checked automatically (clipping, silence, noise, onset; CLAP prompt match; Audiobox production quality) and ranked best-of-N, then trimmed so the hit lands at t=0 or on a chosen frame, looped seamlessly, varied round-robin or layered, and delivered as 48 kHz 24-bit WAVs with UCS names to a folder the user names; a whole film's cue sheet runs in one resumable batch. Use when the user (or an animation task) needs SFX, foley, a sound for a frame, an ambience loop, or wants sound effects made without a cloud service. Not for music or speech.
---

# local-sounds: frame-ready sound effects on the local GPU

You ask for one sound and a length; the server makes N takes (default 4), checks and ranks
them, and hands back clean WAVs: transient at t = 0 (or exactly on a frame), faded, mono
for one-shots and stereo for ambiences, loudness-normalised, named like
`METLImpt_SwordHit_v01.wav`, with a spectrogram PNG per take for you to look at.

**You cannot hear the audio.** You judge takes from the automatic checks, the CLAP and
Audiobox scores and the spectrogram (Read the PNG). The user is the only judge of how it
*sounds*: send the files early and let them steer.

## Quick start

The tool is installed at `~/.claude/skills/local-sounds` (this skill's folder). Use the
commands as written: `~` works in Bash, and `cygpath -w` gives `cmd` the Windows form.

```bash
~/.claude/skills/local-sounds/.venv/Scripts/python.exe ~/.claude/skills/local-sounds/scripts/sfx.py "sword hit on a shield" --duration 1.2 --out <dir>
```

If the server isn't running, that first starts it in **its own visible window** (~30 s; the
user sees it, and closing it stops the server), then makes 4 takes, prints them ranked with
their scores and PNG paths, and copies the best one to `<dir>` as
`METLImpt_SwordHitShield_v01.wav` + a `.json` sidecar. Look at the PNG of the top take; if the
user wants options, deliver more (`--best 3`) or send the PNGs too. Send files with
SendUserFile if you have it; otherwise give the paths. For many sounds at once, write a cue
sheet (section 2, "Cue sheets").

## How a request looks

"I need a sword clang on frame 36 of my 24 fps shot, and a loopable forge ambience."

1. **Check** the GPU (section 1): the CLI starts the server itself, but not while another
   program holds the GPU.
2. **Prompt tersely, one sound per request**: `"sword clang on a shield"`. The category
   recipe adds the envelope and recording words (section 3); `--show-prompt` shows them.
3. **Generate with the animation settings**: `--align-frame 36 --fps 24 --duration 1.5`;
   the ambience: `"forge ambience, fire roaring, hammer taps far away" --loop --duration 8`.
4. **Read the ranking** (section 4): rejected takes are listed but not delivered; check the
   warnings and Read the top take's PNG. If nothing is good: another `--n`, or rephrase.
5. **Iterate**: `--like <ref>` for "more like #3", `--post <request id> --align-frame 40`
   to move the hit without regenerating, `--variations 5` for round-robins.
6. **Deliver** to the folder the user named (`--out`), and **report**: the paths, each take's
   ref and seed, the scores, any warnings, and that the server is still running in its window,
   holding VRAM (section 6).

**When to ask the user:**
- Whether a take sounds right: you can't tell. Offer concrete knobs ("drier", "shorter tail",
  "more metallic", "lower", "less whoosh, more thud") rather than asking open-endedly.
- Before stopping any other program that holds the GPU (another model server, an image
  generator, a game): it may be in use.
- Where the files go, if they didn't say. Never write into their project unasked.

## Ground rules

- **Everything stays local.** The server listens on 127.0.0.1 only and never touches the
  network. Don't fall back to a cloud SFX service without asking.
- **Sound effects only**: not music, not speech. A creature *vocalisation* is fine; words are not.
- **Licences** (`LICENSE-MODELS.md`): Stable Audio 3 is under the Stability AI Community
  License (free for non-commercial use, and for commercial use under US $1M a year in revenue
  after registering with Stability AI; the user owns the sounds). If the user plans commercial
  use, mention it once.
- **The server is only started and stopped, and only ever runs in its own visible window.**
  Never run `server/app.py` yourself or start the server hidden (it refuses anyway), and don't
  look for ways to unload its models: stopping it is how the GPU is freed.
- `README.md` has the full details for humans.

## 1. GPU and server

```bash
curl -s http://127.0.0.1:8191/api/health          # JSON = the server is up: engines running, VRAM
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
```

The server holds Stable Audio 3 (~2.5 GB) and the checkers (~1.5 GB), ~4 GB in all. Another
model server on the same card doesn't fail: the driver silently pages the overflow into system
RAM and both crawl.

**Starting.** Just run `sfx.py`: when the server isn't running, it starts it with `start.cmd`
in its own visible console window titled "local-sounds server - close this window to stop it",
waits until it's ready (~30 s) and carries on.
It won't start it while another program holds over 4 GB of GPU memory: it exits 1 saying how
much is in use. Then **ask the user** before stopping that program (with its own stop command,
never by killing processes); `--start-anyway` only if they say to run both. If the `local-gpu`
skill is installed, `python ~/.claude/skills/local-gpu/gpu.py switch local-sounds` does this: it
shows what holds the GPU, stops it after the user confirms, and starts this server. The same
start by hand:

```bash
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/start.cmd)" --no-browser
```

That window is the only place the server runs (it refuses any other start), and it stays the
user's: leave it open. If the server doesn't come up, read the window or
`~/.claude/skills/local-sounds/logs/server.log` (previous run: `server.previous.log`). If `.venv`
or `models/` is missing, the tool isn't installed: `install.ps1 -AcceptModelLicenses` downloads
~10 GB (~17 GB on disk) under licences the user accepts, so ask the user first.

## 2. Commands

The CLI is standard-library Python; run it with the tool's own interpreter (plain `python` may be
Windows' Microsoft Store stub):

```bash
PY=~/.claude/skills/local-sounds/.venv/Scripts/python.exe
S=~/.claude/skills/local-sounds/scripts/sfx.py
$PY $S "sword hit on a shield" --duration 1.2 --out <dir>                 # best of 4 -> <dir>
$PY $S "door slam" --align-frame 36 --fps 24 --out <dir>                  # hit exactly on frame 36
$PY $S "rain on a tin roof" --loop --duration 8 --out <dir>               # seamless 8 s loop
$PY $S "footstep on gravel" --variations 5 --out <dir>                    # round-robin v01..v05
$PY $S "laser zap" --like 20261004-142803-c6ab:13 --out <dir>             # more like that take
$PY $S --post 20261004-142803-c6ab --align-frame 40                         # re-process, no regeneration
$PY $S --post 20261004-142803-c6ab --no-align                               # hit back to t = 0
$PY $S --variations 5 --take 20261004-142803-c6ab:13 --out <dir>             # round-robins of an earlier take
$PY $S "a flock of seagulls squawking" --stack 4 --out <dir>               # 4 takes in one: fuller, more natural
$PY $S --layer A:12 --layer B:7,gain=-6,offset=2f --out <dir>             # mix takes (refs from the output)
$PY $S --show-prompt "sword hit"                                          # the prompt the model would get
$PY $S --categories        # recipes     $PY $S --settings      # defaults in effect
$PY $S --engines           # installed / running
```

A **take ref** is `<request id>:<seed>`, printed as `ref` for every take. `--json` prints the
server's full response (plus `delivered`): parse that rather than the text.

| Flag | Meaning |
|---|---|
| `--duration S` | One-shots: the most it may last (trimmed to the actual sound, then padded to whole frames). Ambiences and loops: exactly this. Default: the category's (0.5-4 s one-shots, 10 s ambiences). |
| `--n N` / `--seed N\|random` / `--seeds a,b` | Best-of-N on seeds seed, seed+1, ... (random start by default). `--seeds 1234 --n 1` remakes one take exactly. |
| `--engine sa3` | The engine; sa3 is the only one for now (see "Engines" below). |
| `--mode oneshot\|ambience\|loop`, `--loop` | Default: the category's (ambiences default to `loop`). |
| `--align-frame F --fps R` / `--no-align` | Put the transient exactly on frame F (one-shots); `--no-align` (with `--post`) removes it. Default fps 24 (2000 samples a frame at 48 kHz; 30 fps 1600; 60 fps 800). Without it the hit sits at 3 ms (the pre-roll). |
| `--preroll MS` | Silence before the transient, default 3 ms (2-5 is usual). |
| `--reverb none\|room\|hall` | Dry by default: the animation's mix adds the space. |
| `--channels auto\|mono\|stereo` | auto: mono one-shots, stereo ambiences. |
| `--category K` / `--raw` | Force a recipe / send the text exactly as written. |
| `--steps N` | Engine knob: sa3 uses 8 steps. Rarely needed. |
| `--like REF --like-strength X` | Audio-to-audio from a take: 0.3 close, 0.5 default, 0.8 loose. |
| `--variations N --amount X` | Round-robin set from the best take (or `--take REF`): v01 is the take, then +-0.8 semitone speed and +-2 dB EQ changes (`--amount 2` doubles them). |
| `--stack N [--spread MS]` | Make the sound, then mix its best N kept takes into one (all at the same volume, hits together; `--spread 40` staggers each by 40 ms). Generates at least N+1 takes. With `--out`, delivers the stack **and** the best single take. Works on an earlier request with `--post REQ --stack N`. See "Stacking" in section 5. |
| `--layer REF[,gain=DB][,offset=MS\|Nf]` | Repeat per layer; each layer's transient lands at its offset from the first layer's; mixed through a limiter to -1 dBTP. With `--loop`, loops are wrapped to the first one's length so the mix still loops. A layered mix can't be `--post`-processed: re-make it. |
| `--caption` / `--caption-only --take REF` | Ask the audio captioner what the take contains (optional 2.8 GB model; if it isn't downloaded you get a message saying how). |
| `--no-check` | Skip CLAP and Audiobox (signal checks still run). |
| `--out DIR --best K --all --png` | Deliver the best K kept takes (default 1), or all; `--png` copies the spectrograms. Never overwrites: numbering continues (`_v02`...). |
| `--name FXName --cat CatID` | Override the file name parts (UCS: `CatID_FXName_vNN.wav`). |
| `--play` | Play the best take (or first delivered file) on the user's speakers. Only when they ask. |
| `--no-save` | Delete the request from `output/` afterwards. |
| `--wait S` | Wait up to S seconds for a running server to answer (a server it starts gets up to 240 s). |
| `--no-start` / `--start-anyway` | Never start the server / start it even though another program holds over 4 GB of GPU memory (only when the user says so). |
| `--batch FILE [--dry-run] [--redo IDS]` | Make every cue of a cue sheet (below). |

**Exit codes:** 0 done; 1 no server (or the GPU is busy) / server error; 2 bad arguments; 3 every
take was rejected (try another seed batch, a longer duration, or rephrase); with `--batch`, 3
means some cue delivered nothing.

### Cue sheets: many sounds in one run

When a project needs more than a few sounds (a film's whole sound list), write them to a JSON
cue sheet next to the user's files and run it once, instead of one command per sound:

```json
{
  "defaults": {"fps": 24},
  "cues": [
    {"id": "door", "text": "heavy wooden door slam", "align_frame": 36, "name": "DoorSlam"},
    {"id": "rain", "text": "rain on a tin roof", "loop": true, "duration": 8},
    {"id": "glitch", "text": "digital glitch stutter, short", "n": 6},
    {"id": "glitches", "text": "digital glitch burst", "like": "@glitch", "like_strength": 0.6, "n": 8, "all": true}
  ]
}
```

```bash
$PY $S --batch <dir>/film.json --out <dir> --dry-run    # checks every cue; shows prompts if the server is up
$PY $S --batch <dir>/film.json --out <dir>              # runs them; one line per cue
```

- A cue takes the flags above by their long names, with `_` or `-` (`align_frame`, `like_strength`;
  switches are `true`/`false`, `"loop": true` is `--loop`, `"seeds": [1, 2]`), plus an `id`
  (default: its `name`, else made from its text). `defaults` apply to every cue; flags on the
  command line override `defaults`; the cue's own options override both. A relative `out` in
  the file is relative to the file.
- `"like": "@<id>"` is the best kept take of an earlier cue: the way to grow a family (section 5).
- The whole sheet is checked first: a bad cue stops it before anything is generated, naming
  the cue and the problem.
- `<sheet name>.summary.json` (in `--out`) is rewritten after every cue: per cue its `status`
  (`delivered`, `no keepers`, `failed` with an `error`), `request`, `prompt`, `best_ref`,
  `delivered` files, and every take's `ref`, `seed`, `score`, `clap_cos`, `pq`, `warnings`,
  `fails` and `png`. Read it, Read the PNGs of the delivered takes, and report from it.
- **Re-running the same command carries on**: delivered cues are skipped (unless the cue
  changed, its files are gone, or `--redo id1,id2` names it); the rest run again. So after a
  failure or an interruption, fix the cue (rephrase, another seed, a longer duration) and re-run.
  A lost server stops the batch (exit 1); a single failing cue doesn't.
- `--json` prints the summary at the end instead of the per-cue lines.

### Engines

sa3, Stable Audio 3 Small-SFX, is the engine: ~0.5 s a take on an RTX 5080; 44.1 kHz stereo
(resampled to 48 kHz); up to 120 s; more like this (`--like`) works. It ignores negative
prompts and CFG, so they aren't offered. Licence: Stability AI Community.

## 3. Prompting

The models are trained on sound-library captions. What works:

- **One sound event per request.** "a sword hits a shield", not "a fight". For a scene,
  generate the parts separately and layer them (`--layer`).
- **Source and material, action, envelope, recording**: *"heavy steel sword striking an iron
  shield, single sharp metallic impact, bright ringing tail, short decay, close-mic, dry"*.
  The recipe adds the last two parts for you when your text doesn't have them.
- **Concrete nouns beat adjectives.** "gravel", "oak plank", "rubber ball", "tin roof".
- **Length:** ask for the length you need; the server asks the model for ~0.75 s more and
  trims to the real sound. Loops are generated at ~2x and cut seamlessly.
- **Cartoon / magic / sci-fi** are fine as words: "cartoon", "magical", "futuristic".
- **Avoid** music words (melody, beat, song), speech ("saying"), and multiple events in one
  prompt ("then", "followed by"): generate those as separate takes.

Category recipes (`--categories`; detected from your words, forced with `--category`):

| Category | UCS CatID | Mode | Default s | Added to the prompt |
|---|---|---|---|---|
| impact-metal | METLImpt | oneshot | 1.5 | single sharp metallic impact, bright ringing tail, short decay, close-mic, dry, no reverb |
| impact-wood | WOODImpt | oneshot | 1.0 | single solid wooden knock, sharp attack, short dry decay, close-mic... |
| impact-glass | GLASImpt | oneshot | 1.5 | single glass impact, sharp attack, bright shards, short decay, close-mic... |
| impact-stone | ROCKImpt | oneshot | 1.2 | single heavy stone impact, sharp attack, gritty short decay, close-mic... |
| impact-body | FGHTImpt | oneshot | 0.8 | single punchy body impact, fleshy thump, sharp attack, very short decay, close-mic... |
| whoosh | WHSH | oneshot | 1.2 | single fast air whoosh passing by, swells in and fades out quickly, close-mic... |
| ui | UIClick | oneshot | 0.5 | single short clean electronic interface sound, instant attack, very short decay |
| footsteps | FEETHmn | oneshot | 2.0 | single footstep, crisp heel and sole contact, short decay, close-mic... |
| cartoon-boing | TOONBoing | oneshot | 1.0 | single cartoon boing, springy wobbling pitch, short decay, close-mic... |
| cartoon-pop | TOONPop | oneshot | 0.5 | single cartoon pop, round and bright, instant attack, very short, close-mic... |
| magic | MAGSpel | oneshot | 2.5 | single magical spell sound, shimmering sparkles rising, bright airy tail |
| scifi-machine | SCIMach | oneshot | 3.0 | single futuristic machine sound, clean electronic texture, clear start and end |
| bird | BIRDMisc | oneshot | 2.5 | natural bird calls, clear and close, no music, close-mic... |
| animal | ANMLMisc | oneshot | 2.0 | natural animal vocalisation, clear and close, close-mic... |
| creature | CREAMnstr | oneshot | 2.0 | single creature vocalisation, guttural and animal-like, clear start and end, close-mic... |
| explosion | EXPLReal | oneshot | 4.0 | single powerful explosion, sudden blast, deep low-end rumble, debris tail, close-mic... |
| ambience | AMBMisc | loop | 10 | continuous steady ambience, constant texture, no sudden events, wide stereo field recording |
| misc | DSGNMisc | oneshot | 2.0 | single clear sound event, clean start and end, close-mic... |

Words for several sources ("a group of", "a flock of", "several", "a herd of"...) drop "single" from
the recipe, so the model is asked for a group, not one event.

For a better file category than the recipe's, pass `--cat` with a UCS CatID (e.g. `AMBForst`,
`WATRSplsh`, `DOORWood`, `ELECZap`, `SCIWeap`); UCS has no IMPACTS category, impacts go under
their material.

## 4. Reading the results

Each take line: `#rank ref score CLAP cos (softmax) PQ length/frames onset [warnings]`.

- **Rejected** (`REJECTED: ...`, never delivered): silent, too little sound, clipped (>0.1% of
  samples), heavy DC offset, or (one-shots only) Audiobox PQ under 4 (noise / artefacts). PQ reads
  rain, wind and crowds as noise, so for ambiences and loops a low PQ is only a warning.
- **Warnings** (delivered, but say so): a few clipped samples, high noise floor, the sound starts
  late, *still loud at the very end* (the model cut it off: ask for a longer `--duration`), *cut at
  the requested duration while still -X dB below its peak* (your `--duration` chopped the tail),
  stereo out of phase, so-so PQ (4-5.5), *CLAP thinks it sounds more like: X* (the take may
  be the wrong sound: look at the PNG), loop seam may be audible.
- **Rank score** (0-1): CLAP match and production quality combined, minus 0.05 a warning.
  Only compare it **within one request**.
- **CLAP cos**: prompt-audio similarity, about 0.1 poor, 0.25 fair, 0.35+ good (0.45 earns full marks in the rank score); the number in
  brackets is the prompt's softmax against distractor captions (music, speech, silence, noise
  and near misses such as "a wooden knock" for a metal hit). A ranking signal, not a verdict:
  a low score on a good-sounding take happens.
- **PQ** (Audiobox production quality, 1-10): 7+ clean, 5-7 usable, under 5 noisy or smeared.
- **seam** (loops): `seam ok`, or `SEAM?` with `seam_jump_ratio` / `seam_flux_ratio` /
  `seam_level_ratio` in the JSON: the change across the seam against the loop's own 95th percentile (under 1.5 ordinary, 2+ fails the check; calibrated on 96 good and 12 bad loops).
- `heard:` (with `--caption`): the captioner's description; trust it over your expectations.

**The spectrogram PNG** (Read it): waveform on top, spectrogram below (log frequency), thin
grey lines every frame at the request's fps (numbered every 6th), a **red line at the onset**,
an orange band on the `--align-frame` frame, a dashed cyan line at a loop's seam (the plot is
rotated so the seam is in the middle). What to look for:
- an impact: a vertical bright stripe at the red line, then energy fading to the right; a
  long bright smear means a long ring or reverb.
- a whoosh: a soft swell (energy rising over several frames), not a click.
- UI: short, with a few clean horizontal lines (tones) or a tight burst.
- a loop: no vertical stripe or level step at the cyan seam.
- trouble: a solid wall of energy (noise or static), energy cut off hard at the right edge
  (truncated), nothing but low-frequency blur (mud), regular horizontal stripes on something
  that shouldn't be tonal (hum or music).

## 5. Animation details

- **Delivered files**: 48 kHz, 24-bit PCM WAV (After Effects can't import FLAC), INFO chunk
  with title (UCS name), prompt, seed, model, scores and request id; a JSON sidecar next to
  each with everything (prompt, engine, seed, scores, post-processing report).
- **One-shots**: trimmed with a 3 ms pre-roll and 1 ms fade-in, exponential fade-out,
  mono, peak-normalised to **-1 dBTP**, padded to whole frames. `onset_frame` in the
  output says exactly where the hit is.
- **Ambiences and loops**: stereo, **-23 LUFS** integrated with a -1 dBTP ceiling. Loops are a
  whole number of frames long, cut where the sound is steady and crossfaded (equal power)
  so the end runs into the start.
- **Round-robins** (`--variations 5`): footsteps, impacts and UI sounds that repeat should
  get them so repeats don't sound identical. Delivered as `_v01.._v05`.
- **Layering**: build a big hit from parts (a low thud + a metal clang + debris), each
  generated separately; offsets in ms or frames (`offset=2f`).
- **Stacking** (`--stack N`): several takes of the *same* sound mixed into one. Each take is a
  slightly different performance, so together they sound fuller and more natural (4 stacked takes
  of a group of pelicans beat any single one by ear). Use it for:
  - groups and crowds of animals or creatures (a flock, a pack, birds chattering, frogs at night),
  - textures and ambiences (rain, fire, wind, water, machinery hum, a swarm),
  - "big" sounds that should feel thick (a monster roar, an explosion's rumble, a magic swell).

  Don't stack a single crisp event (one footstep, a UI blip, a single knock, a sword clang):
  the hits never line up exactly, so the attack smears into a flam. Start with 3-4 layers and
  `--spread 0` (hits together); for a looser group (chatter, a flock taking off) try
  `--spread 30`-`80`. **Stacks usually score lower than their best single take** (a denser sound
  reads as noise to Audiobox and CLAP), so never reject a stack on its score: deliver both files
  (`--out` does) and let the user choose by ear.
- **Families** (a set that should read as one in the mix: eight glitches, one creature's calls,
  one machine's noises): don't write eight unrelated prompts, and don't use `--variations`
  (those are pitch/EQ copies of one take: eight of the same glitch). Grow the set from one anchor:
  make the anchor (`--n 6`), have the user pick the take (or take the best), then
  `"<text>" --like <ref> --like-strength 0.6 --n 8 --all --out <dir>`. Each seed is a different
  sound with the anchor's character, and the text may change per call ("glitch stutter",
  "glitch burst"). Strength 0.3 stays close, 0.5 varies, 0.8 keeps only the broad colour. In a
  cue sheet: `"like": "@anchor"`.

## 6. Delivering and finishing

Deliver with `--out <dir>` (created if needed; existing files are never overwritten), or
re-deliver an earlier take: `--post <request id> --out <dir>` (re-processes with any new
post-processing flags first). Then tell the user the paths, refs, seeds and scores, and relay
warnings.

The server keeps its models in VRAM until it stops: that's what makes the next request fast,
but it blocks other GPU programs. **Leave it running and tell the user** it's up (in its
window); stop it when they're done or need the GPU. Stopping is the only way to free it:

```bash
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/stop.cmd)"
```

`stop.cmd` stops the server and its engine workers, frees the VRAM, closes the server's window
and prints the VRAM now in use; exit code 1 means something survived (tell the user; don't
reach for anything stronger). The user closing the window does the same.

When the user wants to fine-tune a stack or a layered mix themselves, the page's mixer is the
tool: they hear changes instantly. The page at http://127.0.0.1:8191/ is for the user: generate
and compare takes by ear, adjust timing, and a **mixer** (Add to mix on takes, then position and
volume sliders per layer, instant preview, Export mix). Point them to it when they want to
audition or hand-place layers themselves.

## Troubleshooting

- `lost the server` / exit 1: read the server's window, or the end of `logs/server.log`. An
  engine that crashed is restarted on the next request. If the window is gone, the user closed
  it (or stopped the server): the next `sfx.py` call starts it again.
- `... GB of the GPU's memory is already in use by another program`: something else holds the
  GPU. Ask the user whether to stop it (with its own stop command) or run anyway.
- Every take rejected for clipping or static: rephrase or change seeds; report it if it
  repeats (sa3 needs whole-second lengths internally; the worker rounds up for you).
- After changing code, run the tests: `cd ~/.claude/skills/local-sounds && .venv/Scripts/python.exe -m pytest tests -q`.
