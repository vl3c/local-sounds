---
name: local-sounds
description: Generate sound effects locally for animation (impacts, whooshes, UI blips, footsteps, cartoon boings and pops, magic, sci-fi machines, creatures, explosions, ambience loops) with Stable Audio 3 Small-SFX or MOSS-SoundEffect v2 on this machine's GPU. Every take is checked automatically (clipping, silence, noise, onset; CLAP prompt match; Audiobox production quality) and ranked best-of-N, then trimmed so the hit lands at t=0 or on a chosen frame, looped seamlessly, varied round-robin or layered, and delivered as 48 kHz 24-bit WAVs with UCS names to a folder the user names. Use when the user (or an animation task) needs SFX, foley, a sound for a frame, an ambience loop, or wants sound effects made without a cloud service. Not for music or speech.
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
curl -s http://127.0.0.1:8191/api/health                                           # JSON = the server is up
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/start.cmd)" --no-browser       # if not: its own window, ~30 s
~/.claude/skills/local-sounds/.venv/Scripts/python.exe ~/.claude/skills/local-sounds/scripts/sfx.py "sword hit on a shield" --duration 1.2 --wait 180 --out <dir>
```

That makes 4 takes, prints them ranked with their scores and PNG paths, and copies the best
one to `<dir>` as `METLImpt_SwordHitShield_v01.wav` + a `.json` sidecar. Look at the PNG of
the top take; if the user wants options, deliver more (`--best 3`) or send the PNGs too.
Send files with SendUserFile if you have it; otherwise give the paths.

## How a request looks

"I need a sword clang on frame 36 of my 24 fps shot, and a loopable forge ambience."

1. **Check** the GPU and the server (section 1). Start it if needed.
2. **Prompt tersely, one sound per request**: `"sword clang on a shield"`. The category
   recipe adds the envelope and recording words (section 3); `--show-prompt` shows them.
3. **Generate with the animation settings**: `--align-frame 36 --fps 24 --duration 1.5`;
   the ambience: `"forge ambience, fire roaring, hammer taps far away" --loop --duration 8`.
4. **Read the ranking** (section 4): rejected takes are listed but not delivered; check the
   warnings and Read the top take's PNG. If nothing is good: another `--n`, rephrase, or the
   other engine (`--engine moss|sa3`).
5. **Iterate**: `--like <ref>` for "more like #3" (sa3), `--post <request id> --align-frame 40`
   to move the hit without regenerating, `--variations 5` for round-robins.
6. **Deliver** to the folder the user named (`--out`), and **report**: the paths, each take's
   ref and seed, the scores, any warnings, and that the server still holds VRAM (section 6).

**When to ask the user:**
- Whether a take sounds right: you can't tell. Offer concrete knobs ("drier", "shorter tail",
  "more metallic", "lower", "less whoosh, more thud") rather than asking open-endedly.
- Before stopping another GPU server (local-voice, local-image's ComfyUI, local-agent's
  llama-server): they may be in use.
- Where the files go, if they didn't say. Never write into their project unasked.

## Ground rules

- **Everything stays local.** The server listens on 127.0.0.1 only and never touches the
  network. Don't fall back to a cloud SFX service without asking.
- **Sound effects only**: not music, not speech. A creature *vocalisation* is fine; words are not.
- **Licences** (`LICENSE-MODELS.md`): MOSS-SoundEffect is Apache-2.0; Stable Audio 3 is under
  the Stability AI Community License. If the user plans commercial use, mention it once.
- `README.md` has the full details for humans.

## 1. GPU and server

```bash
curl -s http://127.0.0.1:8191/api/health          # engines running, VRAM
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
tasklist | grep -i -E "llama-server|python"
```

The server holds Stable Audio 3 (~2.5 GB) and the checkers (~1.5 GB) by default; MOSS adds
~7.5 GB (up to ~10 GB while generating) when first used. Both engines together peak around
14 GB of the 16 GB card. The local-voice (8.4-12.6 GB), local-image ComfyUI (~15.5 GB) and
local-agent llama-server (~11-12 GB) servers can't run alongside it: if one is up, **ask the
user** before stopping it with that tool's own `stop.cmd`.

**Start it in its own window** (it returns at once; the window is the user's to close):

```bash
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/start.cmd)" --no-browser
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/start.cmd)" --no-browser --engines sa3,moss   # both now
```

Startup: SA3 ~25 s, MOSS ~75 s more. `--wait 180` on the first `sfx.py` call covers it. If it
doesn't come up, read `~/.claude/skills/local-sounds/logs/server.log` (previous run:
`server.previous.log`). If `.venv` or `models/` is missing, the tool isn't installed:
`install.ps1` downloads ~20 GB (~35 GB on disk), so ask the user first.

## 2. Commands

The CLI is standard-library Python; run it with the tool's own interpreter (plain `python` may be
the Microsoft Store stub on this machine):

```bash
PY=~/.claude/skills/local-sounds/.venv/Scripts/python.exe
S=~/.claude/skills/local-sounds/scripts/sfx.py
$PY $S "sword hit on a shield" --duration 1.2 --out <dir>                 # best of 4 -> <dir>
$PY $S "door slam" --align-frame 36 --fps 24 --out <dir>                  # hit exactly on frame 36
$PY $S "rain on a tin roof" --loop --duration 8 --out <dir>               # seamless 8 s loop
$PY $S "footstep on gravel" --variations 5 --out <dir>                    # round-robin v01..v05
$PY $S "laser zap" --like 20261004-142803-c6ab:13 --out <dir>             # more like that take (sa3)
$PY $S --post 20261004-142803-c6ab --align-frame 40                         # re-process, no regeneration
$PY $S --post 20261004-142803-c6ab --no-align                               # hit back to t = 0
$PY $S --variations 5 --take 20261004-142803-c6ab:13 --out <dir>             # round-robins of an earlier take
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
| `--engine sa3\|moss` | See "Engines" below. Default: the user's default (`--settings`). |
| `--mode oneshot\|ambience\|loop`, `--loop` | Default: the category's (ambiences default to `loop`). |
| `--align-frame F --fps R` / `--no-align` | Put the transient exactly on frame F (one-shots); `--no-align` (with `--post`) removes it. Default fps 24 (2000 samples a frame at 48 kHz; 30 fps 1600; 60 fps 800). Without it the hit sits at 3 ms (the pre-roll). |
| `--preroll MS` | Silence before the transient, default 3 ms (2-5 is usual). |
| `--reverb none\|room\|hall` | Dry by default: the animation's mix adds the space. |
| `--channels auto\|mono\|stereo` | auto: mono one-shots, stereo ambiences. |
| `--category K` / `--raw` | Force a recipe / send the text exactly as written. |
| `--negative "..."` | moss only (sa3 small-sfx ignores negative prompts). Default: `low quality, distorted, music, speech, voice, reverb, background noise` plus the category's extras. |
| `--steps`, `--cfg`, `--window` | Engine knobs: sa3 8 steps; moss 100 steps, CFG 4, 30 s window. Rarely needed. |
| `--like REF --like-strength X` | Audio-to-audio from a take (sa3): 0.3 close, 0.5 default, 0.8 loose. |
| `--variations N --amount X` | Round-robin set from the best take (or `--take REF`): v01 is the take, then +-0.8 semitone speed and +-2 dB EQ changes (`--amount 2` doubles them). |
| `--layer REF[,gain=DB][,offset=MS\|Nf]` | Repeat per layer; each layer's transient lands at its offset from the first layer's; mixed through a limiter to -1 dBTP. With `--loop`, loops are wrapped to the first one's length so the mix still loops. A layered mix can't be `--post`-processed: re-make it. |
| `--caption` / `--caption-only --take REF` | Ask the audio captioner what the take contains (optional 2.8 GB model; if it isn't downloaded you get a message saying how). |
| `--no-check` | Skip CLAP and Audiobox (signal checks still run). |
| `--out DIR --best K --all --png` | Deliver the best K kept takes (default 1), or all; `--png` copies the spectrograms. Never overwrites: numbering continues (`_v02`...). |
| `--name FXName --cat CatID` | Override the file name parts (UCS: `CatID_FXName_vNN.wav`). |
| `--play` | Play the best take (or first delivered file) on the user's speakers. Only when they ask. |
| `--no-save` | Delete the request from `output/` afterwards. |
| `--wait S` | Wait up to S seconds for the server. |

**Exit codes:** 0 done; 1 no server / server error; 2 bad arguments; 3 every take was rejected
(try another seed batch, a longer duration, or rephrase).

### Engines

| | sa3: Stable Audio 3 Small-SFX | moss: MOSS-SoundEffect v2.0 |
|---|---|---|
| Speed (RTX 5080) | ~0.5 s a take | ~13 s a take (100 steps over a 30 s window) |
| Output | 44.1 kHz stereo (resampled to 48 kHz) | 48 kHz mono |
| Length | up to 120 s | up to 30 s (so loops up to ~13 s) |
| Negative prompt / CFG | ignored | used |
| More like this (`--like`) | yes | no |
| Licence | Stability AI Community | Apache-2.0 |

Default: sa3, the user's pick in a blind bake-off (9 wins to 4; moss won rock, boing, magic and
explosion). `--settings` shows the default in effect. For a quick draft or many
variants use sa3; when a sound needs the negative prompt or sa3's takes keep missing, try moss.

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
| creature | CREAMnstr | oneshot | 2.0 | single creature vocalisation, guttural and animal-like, clear start and end, close-mic... |
| explosion | EXPLReal | oneshot | 4.0 | single powerful explosion, sudden blast, deep low-end rumble, debris tail, close-mic... |
| ambience | AMBMisc | loop | 10 | continuous steady ambience, constant texture, no sudden events, wide stereo field recording |
| misc | DSGNMisc | oneshot | 2.0 | single clear sound event, clean start and end, close-mic... |

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
  each with everything (prompt, negative, engine, seed, scores, post-processing report).
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

## 6. Delivering and finishing

Deliver with `--out <dir>` (created if needed; existing files are never overwritten), or
re-deliver an earlier take: `--post <request id> --out <dir>` (re-processes with any new
post-processing flags first). Then tell the user the paths, refs, seeds and scores, and relay
warnings.

The server keeps the engines in VRAM: that's what makes the next request fast, but it blocks
the other local GPU tools. **Leave it running and tell the user** it's up; stop it when they're
done or need the GPU:

```bash
cmd //c "$(cygpath -w ~/.claude/skills/local-sounds/stop.cmd)"
```

The page at http://127.0.0.1:8191/ does everything the CLI does, for the user.

## Troubleshooting

- `lost the server` / exit 1: read the end of `logs/server.log`. An engine that crashed is
  restarted on the next request.
- Every take rejected for clipping or static: rephrase or change seeds; report it if it
  repeats (sa3 needs whole-second lengths internally; the worker rounds up for you).
- MOSS is slow by design (100 steps on a 30 s window): ~13 s a take, so `--n 2` for drafts.
- After changing code, run the tests: `cd ~/.claude/skills/local-sounds && .venv/Scripts/python.exe -m pytest tests -q`.
