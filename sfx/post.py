"""Post-processing for animation: trim, frame alignment, loops, variations, layers, loudness.

Every function takes and returns float32 audio shaped (channels, samples) at 48 kHz.
The main entry point is `process(raw, sr, opts)`; `make_loop`, `variations` and `layer`
are used by it and by the server's loop / variation / layer requests.

One-shots (impacts, UI, footsteps...): trimmed to start a few ms before the transient, so
the hit lands at t = 0 (or exactly on a chosen frame), faded out exponentially, folded to
mono and peak-normalised to -1 dBTP. Ambiences and loops stay stereo and are normalised to
-23 LUFS with a -1 dBTP ceiling.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from . import analysis as an
from .audio import SR, as2d, mono, resample, stereo

MODES = ("oneshot", "ambience", "loop")
REVERBS = ("none", "room", "hall")


@dataclass
class PostOpts:
    """Post-processing settings. Every field has a default, so PostOpts() is a valid request."""
    mode: str = "oneshot"              # oneshot | ambience | loop
    duration: float | None = None      # seconds: one-shots are capped at it; ambiences/loops are exactly it
    preroll_ms: float = 3.0            # silence kept before the transient (2-5 ms)
    fade_in_ms: float = 1.0            # within the pre-roll, so the attack itself is untouched
    fade_out_ms: float = 60.0          # exponential, at most 30% of the clip
    fps: float = 24.0                  # frame grid for alignment and padding
    align_frame: int | None = None     # put the transient exactly on this frame (one-shots)
    pad_to_frames: bool = True         # pad the end to a whole number of frames
    channels: str = "auto"             # auto (mono one-shots, stereo ambiences) | mono | stereo
    reverb: str = "none"               # none | room | hall
    peak_dbtp: float = -1.0            # one-shots: peak-normalised to this true peak
    lufs: float = -23.0                # ambiences / loops: integrated loudness target
    ceiling_dbtp: float = -1.0         # ambiences / loops: true-peak ceiling
    highpass_hz: float = 20.0          # removes DC and sub-sonic rumble; 0 = off
    crossfade_s: float | None = None   # loops: crossfade length (default: a quarter of the loop, 0.05-1 s)

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {self.mode!r}")
        if self.reverb not in REVERBS:
            raise ValueError(f"reverb must be one of {REVERBS}, not {self.reverb!r}")
        if self.channels not in ("auto", "mono", "stereo"):
            raise ValueError(f"channels must be auto, mono or stereo, not {self.channels!r}")
        if not 1 <= self.fps <= 240:
            raise ValueError("fps must be between 1 and 240")
        if self.align_frame is not None and self.align_frame < 0:
            raise ValueError("align_frame must be 0 or more")
        if not 0 <= self.preroll_ms <= 50:
            raise ValueError("preroll_ms must be between 0 and 50")

    def asdict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- small pieces

def samples_per_frame(fps: float, sr: int = SR) -> float:
    return sr / fps


def frame_to_sample(frame: float, fps: float, sr: int = SR) -> int:
    return int(round(frame * sr / fps))


def pad_to_frames(x: np.ndarray, fps: float, sr: int = SR) -> np.ndarray:
    """Pad the end with silence to a whole number of frames (2000 samples a frame at 24 fps)."""
    x = as2d(x)
    frames = math.ceil(round(x.shape[1] * fps / sr, 6))
    total = frame_to_sample(frames, fps, sr)
    if total <= x.shape[1]:
        return x
    return np.pad(x, ((0, 0), (0, total - x.shape[1])))


def highpass(x: np.ndarray, sr: int, hz: float) -> np.ndarray:
    """Remove DC offset and sub-sonic rumble.

    Causal on purpose: a zero-phase (forward-backward) filter smears faint pre-ringing up
    to ~1/hz seconds *ahead* of a transient, which moves the detected onset early (it put
    hits ~20 ms late after alignment). A 2nd-order causal high-pass at 20 Hz delays
    nothing audible.
    """
    x = as2d(x)
    x = x - x.mean(axis=1, keepdims=True)
    if hz <= 0 or x.shape[1] < 64:
        return x.astype(np.float32)
    from scipy.signal import butter, sosfilt
    sos = butter(2, hz, btype="highpass", fs=sr, output="sos")
    return sosfilt(sos, x, axis=1).astype(np.float32)


def fade_in(x: np.ndarray, n: int) -> np.ndarray:
    x = as2d(x).copy()
    n = min(n, x.shape[1])
    if n > 0:
        x[:, :n] *= np.sin(0.5 * np.pi * np.linspace(0, 1, n, endpoint=False)) ** 2
    return x


def fade_out_exp(x: np.ndarray, n: int, depth_db: float = 60.0) -> np.ndarray:
    """Exponential fade over the last n samples: a straight line in dB down to -depth_db, then to 0."""
    x = as2d(x).copy()
    n = min(n, x.shape[1])
    if n > 1:
        t = np.linspace(0, 1, n)
        g = 10 ** (-depth_db * t / 20) * (1 - t ** 8)  # the (1 - t^8) lands it exactly on zero
        x[:, -n:] *= g.astype(np.float32)
    return x


def gain(x: np.ndarray, db_: float) -> np.ndarray:
    return (as2d(x) * np.float32(10 ** (db_ / 20))).astype(np.float32)


def limit(x: np.ndarray, sr: int, ceiling_db: float, release_ms: float = 80.0) -> np.ndarray:
    """Brick-wall limiter, then a final trim so the true peak really is under the ceiling."""
    from pedalboard import Limiter
    x = as2d(x)
    y = Limiter(threshold_db=ceiling_db - 0.5, release_ms=release_ms)(x, sr)
    for _ in range(4):  # inter-sample peaks can still poke over: trim the gain until they don't
        tp = an.true_peak_db(y, sr)
        if tp <= ceiling_db:
            break
        y = gain(y, ceiling_db - tp - 0.05)
    return as2d(y)


def peak_normalise(x: np.ndarray, sr: int, target_dbtp: float = -1.0) -> tuple[np.ndarray, float]:
    tp = an.true_peak_db(x, sr)
    if tp < -150:
        return as2d(x), 0.0
    g = target_dbtp - tp
    y = gain(x, g)
    if an.true_peak_db(y, sr) > target_dbtp + 0.01:  # resampler rounding: shave the difference
        y = gain(y, target_dbtp - an.true_peak_db(y, sr) - 0.01)
        g = target_dbtp - tp - 0.01
    return y, float(g)


def loudness_normalise(x: np.ndarray, sr: int, target_lufs: float = -23.0, ceiling_dbtp: float = -1.0,
                       loop: bool = False) -> tuple[np.ndarray, float]:
    """Gain to target_lufs; if that pushes the true peak over the ceiling, limit.

    For a loop the limiter runs over two copies back to back and keeps the second, so its
    gain at the seam matches its gain at the end and the loop stays seamless.
    """
    level = an.lufs(x, sr)
    if level < -100:
        return as2d(x), 0.0
    g = target_lufs - level
    y = gain(x, g)
    if an.true_peak_db(y, sr) > ceiling_dbtp:
        if loop:
            n = y.shape[1]
            y = limit(np.concatenate([y, y], axis=1), sr, ceiling_dbtp)[:, n:]
        else:
            y = limit(y, sr, ceiling_dbtp)
    return y, float(g)


REVERB_PARAMS = {
    # A small, fairly damped room and a medium hall. Mostly dry: the sound stays in front.
    "room": dict(room_size=0.25, damping=0.6, wet_level=0.12, dry_level=0.92, width=0.8),
    "hall": dict(room_size=0.75, damping=0.4, wet_level=0.22, dry_level=0.85, width=1.0),
}
REVERB_TAIL_S = {"room": 0.8, "hall": 2.5}


def reverb(x: np.ndarray, sr: int, kind: str) -> np.ndarray:
    if kind == "none":
        return as2d(x)
    from pedalboard import Reverb
    x = np.pad(as2d(x), ((0, 0), (0, int(REVERB_TAIL_S[kind] * sr))))
    return as2d(Reverb(**REVERB_PARAMS[kind])(x, sr))


# --------------------------------------------------------------------------- loops

def make_loop(x: np.ndarray, sr: int, length_s: float, crossfade_s: float | None = None) -> tuple[np.ndarray, dict]:
    """Cut a seamless loop of exactly length_s from a longer clip (ideally ~2x as long).

    The loop is s[0:L] of a well-chosen stretch s of the clip, with its first C samples
    replaced by an equal-power crossfade from the C samples that follow s[L-1] into s[0:C].
    Playing the end into the start then continues exactly as the original did, so the seam
    is continuous at the sample level. The stretch is chosen so the crossfade sits in a quiet,
    steady part (no transient smeared across it) and the file starts on a zero crossing.
    """
    x = as2d(x)
    L = int(round(length_s * sr))
    if crossfade_s is None:
        crossfade_s = min(1.0, max(0.05, 0.25 * length_s))
    C = min(int(round(crossfade_s * sr)), L // 2)
    n = x.shape[1]
    if n < L + C:
        raise ValueError(f"a {length_s:.2f} s loop needs at least {(L + C) / sr:.2f} s of audio, got {n / sr:.2f} s")

    hop = int(0.01 * sr)
    env = an.envelope_db(x, sr, hop_s=0.01)
    med = float(np.median(env))
    slack = n - L - C
    skip = min(int(0.5 * sr), slack // 2)  # models often open with an artefact: avoid the first 0.5 s
    best, best_a = None, skip
    for a in range(skip, slack + 1, hop):
        cost = 0.0
        for s0 in (a, a + L):  # the two stretches mixed in the crossfade
            seg = env[s0 // hop: (s0 + C) // hop + 1]
            if not len(seg):
                continue
            cost += max(0.0, float(seg.mean()) - med)   # louder than usual: something is happening
            cost += 0.5 * float(seg.max() - seg.mean())  # a transient inside the crossfade
        e1 = env[a // hop: (a + C) // hop + 1].mean()
        e2 = env[(a + L) // hop: (a + L + C) // hop + 1].mean()
        cost += 0.5 * abs(float(e1 - e2))                # the two sides at different levels
        if best is None or cost < best:
            best, best_a = cost, a

    # The file's first sample is the clip's sample a + L: put that on a zero crossing.
    m = x.mean(axis=0)
    win = int(0.003 * sr)
    lo, hi = max(0, best_a + L - win), min(n - C - 1, best_a + L + win)
    zc = an.zero_crossings(m[lo:hi + 1])
    if len(zc) and lo < hi:
        best_a = max(0, min(slack, int(lo + zc[np.argmin(np.abs(lo + zc - (best_a + L)))] + 1) - L))

    s = x[:, best_a: best_a + L + C]
    out = s[:, :L].copy()
    th = np.linspace(0, 0.5 * np.pi, C, endpoint=False) + 0.25 * np.pi / max(C, 1)
    out[:, :C] = s[:, :C] * np.sin(th) + s[:, L:L + C] * np.cos(th)
    return out.astype(np.float32), {"loop_start_s": round(best_a / sr, 3), "crossfade_s": round(C / sr, 3)}


# --------------------------------------------------------------------------- the main chain

def _channels(x: np.ndarray, opts: PostOpts) -> np.ndarray:
    want = opts.channels if opts.channels != "auto" else ("mono" if opts.mode == "oneshot" else "stereo")
    return mono(x) if want == "mono" else stereo(x)


CUT_WARN_DB = -20.0  # a duration cap that cuts the sound while louder than this (rel. peak) is reported:
#                     a 60 ms fade over a tail 20+ dB down is inaudible; louder than that, it is a chop


def _trim_oneshot(x: np.ndarray, sr: int, opts: PostOpts) -> tuple[np.ndarray, int, dict]:
    """Cut to [onset - pre-roll, end of the sound], capped at the duration.

    Returns (audio, onset index, report). When the duration cap cuts the sound before it has
    died away, the report says how loud it still was there (`cut_db`, rel. the peak), since
    the fade-out hides that from any check run on the delivered file.
    """
    i0 = an.onset(x, sr)
    pre = int(round(opts.preroll_ms * sr / 1000))
    start = max(0, i0 - pre)
    end = min(x.shape[1], an.last_sound(x, sr) + int(0.01 * sr))
    info: dict = {}
    if opts.duration:
        cap = start + int(round(opts.duration * sr))
        if cap < end:
            env = an.envelope_db(x, sr)
            k = cap * 1000 // sr  # 1 ms hops
            cut = float(np.max(env[max(0, k - 20): k + 1]) - env.max())
            if cut > CUT_WARN_DB:
                info["cut_db"] = round(cut, 1)
            end = cap
    end = max(end, start + 1)
    y = x[:, start:end]
    if i0 - start < pre:  # the sound starts right at the top: give it its pre-roll in silence
        y = np.pad(y, ((0, 0), (pre - (i0 - start), 0)))
        start -= pre - (i0 - start)
    y = fade_in(y, int(round(min(opts.fade_in_ms, opts.preroll_ms) * sr / 1000)))
    y = fade_out_exp(y, int(min(opts.fade_out_ms * sr / 1000, 0.3 * y.shape[1])))
    return y, i0 - start, info


def _place(y: np.ndarray, sr: int, onset_idx: int, opts: PostOpts) -> tuple[np.ndarray, int]:
    """Move the transient onto opts.align_frame (if set) and pad to whole frames."""
    if opts.align_frame is not None:
        target = frame_to_sample(opts.align_frame, opts.fps, sr)
        if target >= onset_idx:
            y = np.pad(y, ((0, 0), (target - onset_idx, 0)))
        else:  # frame 0 with a pre-roll: drop the pre-roll (a 0.5 ms fade keeps it click-free)
            y = fade_in(y[:, onset_idx - target:], int(0.0005 * sr))
        onset_idx = target
    if opts.pad_to_frames:
        y = pad_to_frames(y, opts.fps, sr)
    return y, onset_idx


def finish(y: np.ndarray, sr: int, onset_idx: int, opts: PostOpts) -> tuple[np.ndarray, dict]:
    """Channels, reverb, loudness and frame placement for an already-trimmed sound."""
    y = _channels(y, opts)
    rep: dict = {}
    if opts.reverb != "none":
        n, y0 = y.shape[1], y
        y = reverb(y, sr, opts.reverb)
        if opts.mode == "oneshot":
            end = max(n, an.last_sound(y, sr, rel_db=60) + int(0.01 * sr))
            y = fade_out_exp(y[:, :end], int(min(0.3 * sr, 0.3 * end)))
        elif opts.mode == "loop":
            # Reverb two copies back to back and keep the second: its start then carries the
            # tail of the loop's own end, exactly as when it plays looped.
            y = reverb(np.concatenate([y0, y0], axis=1), sr, opts.reverb)[:, n:2 * n]
        else:  # ambience: keep the requested length, and fade the cut tail
            y = fade_out_exp(y[:, :n], int(0.05 * sr))
    if opts.mode == "oneshot":
        y, g = peak_normalise(y, sr, opts.peak_dbtp)
    else:
        y, g = loudness_normalise(y, sr, opts.lufs, opts.ceiling_dbtp, loop=opts.mode == "loop")
    rep["gain_db"] = round(g, 2)
    if opts.mode == "oneshot":
        y, onset_idx = _place(y, sr, onset_idx, opts)
    elif opts.pad_to_frames and opts.mode == "ambience":
        y = pad_to_frames(y, opts.fps, sr)
    rep.update(onset_s=round(onset_idx / sr, 4), onset_frame=round(onset_idx * opts.fps / sr, 3),
               length_s=round(y.shape[1] / sr, 4), length_frames=round(y.shape[1] * opts.fps / sr, 3),
               channels=int(y.shape[0]))
    return y.astype(np.float32), rep


def process(raw: np.ndarray, sr_in: int, opts: PostOpts | None = None) -> tuple[np.ndarray, dict]:
    """Turn a model's raw output into a delivered sound (48 kHz). Returns (audio, report)."""
    opts = opts or PostOpts()
    x = highpass(resample(raw, sr_in, SR), SR, opts.highpass_hz)
    sr = SR
    if opts.mode == "oneshot":
        y, onset_idx, info = _trim_oneshot(x, sr, opts)
        y, rep = finish(y, sr, onset_idx, opts)
        return y, {**rep, **info}
    if opts.mode == "loop":
        dur = opts.duration or max(1.0, x.shape[1] / sr / 2)
        if opts.pad_to_frames:  # a loop must be a whole number of frames, or it drifts against the picture
            dur = max(1, round(dur * opts.fps)) / opts.fps
        y, lrep = make_loop(x, sr, dur, opts.crossfade_s)
        y, rep = finish(y, sr, 0, opts)
        return y, {**lrep, **rep}
    # ambience: a steady stretch of exactly the duration, skipping the model's opening
    dur = opts.duration or x.shape[1] / sr
    n = min(x.shape[1], int(round(dur * sr)))
    off = min(int(0.5 * sr), x.shape[1] - n)
    y = x[:, off:off + n]
    y = fade_out_exp(fade_in(y, int(0.02 * sr)), int(0.05 * sr))
    y, rep = finish(y, sr, 0, opts)
    return y, {"start_s": round(off / sr, 3), **rep}


# --------------------------------------------------------------------------- variations and layers

def _varispeed(x: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Tape-style speed change: pitch and length move together, like a sampler's round-robin."""
    rate = 2 ** (semitones / 12)
    return resample(x, int(round(sr * rate)), sr)


def _eq(x: np.ndarray, sr: int, low_db: float, high_db: float, mid_hz: float, mid_db: float) -> np.ndarray:
    from pedalboard import HighShelfFilter, LowShelfFilter, PeakFilter, Pedalboard
    board = Pedalboard([LowShelfFilter(cutoff_frequency_hz=250, gain_db=low_db),
                        HighShelfFilter(cutoff_frequency_hz=4000, gain_db=high_db),
                        PeakFilter(cutoff_frequency_hz=mid_hz, gain_db=mid_db, q=1.0)])
    return as2d(board(as2d(x), sr))


def variations(raw: np.ndarray, sr_in: int, opts: PostOpts | None = None, n: int = 5, seed: int = 0,
               amount: float = 1.0) -> list[tuple[np.ndarray, dict]]:
    """A round-robin set: v01 is the take itself, v02.. small pitch/speed and EQ changes of it.

    amount 1.0 means up to +-0.8 semitones (with the matching +-5% length) and +-2 dB shelves.
    Each variant is re-trimmed and re-normalised, so the transient sits in the same place.
    """
    opts = opts or PostOpts()
    rng = np.random.default_rng(seed)
    x = highpass(resample(raw, sr_in, SR), SR, opts.highpass_hz)
    out = []
    semis = np.linspace(-0.8, 0.8, max(2, n - 1)) * amount  # spread evenly, then shuffled
    rng.shuffle(semis)
    for k in range(n):
        if k == 0:
            v, params = x, {"semitones": 0.0, "low_db": 0.0, "high_db": 0.0, "mid_hz": 0.0, "mid_db": 0.0}
        else:
            params = {"semitones": round(float(semis[(k - 1) % len(semis)]), 2),
                      "low_db": round(float(rng.uniform(-1.5, 1.5) * amount), 2),
                      "high_db": round(float(rng.uniform(-2, 2) * amount), 2),
                      "mid_hz": round(float(np.exp(rng.uniform(np.log(400), np.log(3000)))), 0),
                      "mid_db": round(float(rng.uniform(-2, 2) * amount), 2)}
            v = _eq(_varispeed(x, SR, params["semitones"]), SR, params["low_db"], params["high_db"],
                    params["mid_hz"], params["mid_db"])
        if opts.mode == "oneshot":
            y, onset_idx, info = _trim_oneshot(v, SR, opts)
            y, rep = finish(y, SR, onset_idx, opts)
            rep.update(info)
        else:
            sub = PostOpts(**{**opts.asdict(), "highpass_hz": 0})
            y, rep = process(v, SR, sub)
        out.append((y, {**rep, "variation": params}))
    return out


@dataclass
class Layer:
    audio: np.ndarray            # a processed take, 48 kHz, its transient at onset_s
    gain_db: float = 0.0
    offset_s: float = 0.0        # where this layer's transient lands, relative to the first layer's
    onset_s: float = 0.0
    meta: dict = field(default_factory=dict)


def layer(layers: list[Layer], opts: PostOpts | None = None, sr: int = SR) -> tuple[np.ndarray, dict]:
    """Mix layers with per-layer gain and transient offset, through a limiter.

    One-shots: offsets are relative to the first layer's transient (a negative one starts
    before it); the mix is peak-normalised to -1 dBTP and placed like any one-shot.
    Loops: every layer is wrapped to the first layer's length (an offset rotates it), so the
    mix is still a seamless loop of that length, normalised loop-aware to -23 LUFS.
    Ambiences: offset and summed, normalised to -23 LUFS.
    """
    if not layers:
        raise ValueError("nothing to layer")
    opts = opts or PostOpts()
    chans = max(as2d(ly.audio).shape[0] for ly in layers)
    meta = [{"gain_db": ly.gain_db, "offset_s": ly.offset_s, **ly.meta} for ly in layers]

    def fit(a: np.ndarray) -> np.ndarray:
        a = as2d(a)
        return np.vstack([a] * chans) if a.shape[0] < chans else a

    if opts.mode == "loop":
        n = as2d(layers[0].audio).shape[1]
        mix = np.zeros((chans, n), dtype=np.float32)
        for ly in layers:
            a = fit(gain(ly.audio, ly.gain_db))
            a = np.tile(a, (1, -(-n // a.shape[1])))[:, :n]  # shorter loops repeat, longer ones are cut
            mix += np.roll(a, int(round(ly.offset_s * sr)) % n, axis=1)
        mix, g = loudness_normalise(mix, sr, opts.lufs, opts.ceiling_dbtp, loop=True)
        from .checks import seam_report
        return mix.astype(np.float32), {"gain_db": round(g, 2), "onset_s": 0.0, "onset_frame": 0.0,
                                        "length_s": round(n / sr, 4),
                                        "length_frames": round(n * opts.fps / sr, 3), "channels": chans,
                                        **seam_report(mix, sr), "layers": meta}

    starts = [int(round((ly.offset_s - ly.onset_s) * sr)) for ly in layers]
    shift = -min(starts)
    starts = [s + shift for s in starts]
    n = max(s + as2d(ly.audio).shape[1] for s, ly in zip(starts, layers))
    mix = np.zeros((chans, n), dtype=np.float32)
    for s, ly in zip(starts, layers):
        a = fit(gain(ly.audio, ly.gain_db))
        mix[:, s:s + a.shape[1]] += a
    onset_idx = int(round(layers[0].onset_s * sr)) + starts[0]
    mix = limit(mix, sr, opts.peak_dbtp - 0.5 if opts.mode == "oneshot" else opts.ceiling_dbtp)
    if opts.mode == "oneshot":
        mix, g = peak_normalise(mix, sr, opts.peak_dbtp)
        mix, onset_idx = _place(mix, sr, onset_idx, opts)
    else:
        mix, g = loudness_normalise(mix, sr, opts.lufs, opts.ceiling_dbtp)
        if opts.pad_to_frames:
            mix = pad_to_frames(mix, opts.fps, sr)
    return mix.astype(np.float32), {"gain_db": round(g, 2), "onset_s": round(onset_idx / sr, 4),
                                    "onset_frame": round(onset_idx * opts.fps / sr, 3),
                                    "length_s": round(mix.shape[1] / sr, 4),
                                    "length_frames": round(mix.shape[1] * opts.fps / sr, 3), "channels": chans,
                                    "layers": meta}
