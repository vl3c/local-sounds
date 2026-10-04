"""Signal checks on a take: the numbers an agent that can't listen uses to reject bad takes.

`signal_report(x, sr, mode)` measures clipping, true peak, DC offset, how much sound there is,
noise floor, onset position, a cut-off tail and stereo correlation, and sorts problems into
`fails` (reject the take) and `warnings` (keep it, but say so). Run it on the model's raw
output: that's where clipping, DC and truncation come from. `seam_report` checks a loop.

The model-based scores (CLAP, Audiobox Aesthetics) live in the server, which owns the GPU.
"""

from __future__ import annotations

import numpy as np

from . import analysis as an
from .audio import as2d

# Thresholds. Hard fails are things no sound designer would ship; warnings are worth a look.
CLIP_LEVEL = 0.999            # |sample| at or above this is (probably) clipped
CLIP_FAIL_FRACTION = 0.001    # 0.1% of samples in clipped runs: audible distortion
DC_WARN = 0.01                # -40 dBFS of DC: an offset worth removing (post-processing does)
DC_FAIL = 0.1
SILENT_PEAK_DB = -60.0        # a take whose loudest moment is below this is silence
MIN_ACTIVE_S = {"oneshot": 0.01, "ambience": 0.5, "loop": 0.5}
NOISE_WARN_DB = {"oneshot": -40.0, "ambience": -12.0, "loop": -12.0}   # floor relative to the peak
LATE_ONSET_S = 1.5            # a one-shot whose sound starts this late wasted the model's time
TAIL_CUT_DB = -30.0           # a one-shot still this loud (rel. peak) in its last 50 ms was cut off
SEAM_FLUX_MAX = 2.0         # loop seam: spectral change across it vs. the loop's 95th percentile
SEAM_LEVEL_MAX = 2.0        # loop seam: level change across it, likewise


def _runs_at_or_above(v: np.ndarray, level: float, min_run: int = 3) -> int:
    """How many samples sit in runs of at least min_run consecutive samples with |v| >= level."""
    hot = np.abs(v) >= level
    if not hot.any():
        return 0
    d = np.diff(np.r_[0, hot.astype(np.int8), 0])
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    lens = ends - starts
    return int(lens[lens >= min_run].sum())


def signal_report(x: np.ndarray, sr: int, mode: str = "oneshot") -> dict:
    """Measurements plus `fails` and `warnings` (lists of short sentences)."""
    x = as2d(x)
    n = x.shape[1]
    fails: list[str] = []
    warns: list[str] = []
    env = an.envelope_db(x, sr, hop_s=0.005)
    peak = an.sample_peak_db(x)
    tp = an.true_peak_db(x, sr)
    floor = an.noise_floor_db(env)
    env_peak = float(env.max())
    # "Sound" is within 40 dB of the peak and clear of the noise floor; for a dense, steady
    # sound (rain, static) the floor sits near the peak, so cap that margin at peak - 10 dB.
    active_s = float(np.sum(env > max(env_peak - 40, min(floor + 6, env_peak - 10))) * 0.005)
    clipped = sum(_runs_at_or_above(ch, CLIP_LEVEL) for ch in x) / max(1, x.size)
    dc = float(np.max(np.abs(x.mean(axis=1)))) if n else 0.0
    onset_s = an.onset(x, sr) / sr
    corr = an.stereo_correlation(x)
    tail = x[:, -int(0.05 * sr):]
    tail_rel = float(an.db(np.sqrt(np.mean(np.square(tail.astype(np.float64)))) + 1e-12)) - env_peak if n else -200

    if peak < SILENT_PEAK_DB:
        fails.append(f"silent: peak {peak:.0f} dBFS")
    elif active_s < MIN_ACTIVE_S.get(mode, 0.03):
        fails.append(f"too little sound: {active_s * 1000:.0f} ms within 40 dB of the peak")
    if clipped >= CLIP_FAIL_FRACTION:
        fails.append(f"clipped: {clipped:.2%} of samples")
    elif clipped > 0:
        warns.append(f"a few clipped samples ({clipped:.3%})")
    if dc >= DC_FAIL:
        fails.append(f"DC offset {dc:.2f}")
    elif dc >= DC_WARN:
        warns.append(f"DC offset {dc:.3f} (removed by post-processing)")
    if peak >= SILENT_PEAK_DB and floor - env_peak > NOISE_WARN_DB.get(mode, -40):
        warns.append(f"high noise floor: {floor - env_peak:.0f} dB below the peak" if mode == "oneshot"
                     else f"not much dynamic range: floor {floor - env_peak:.0f} dB below the peak")
    if mode == "oneshot":
        if onset_s > LATE_ONSET_S:
            warns.append(f"the sound starts late, at {onset_s:.2f} s")
        if tail_rel > TAIL_CUT_DB and n > sr * 0.2:
            warns.append("still loud at the very end: probably cut off, ask for a longer duration")
    if corr is not None and corr < 0:
        warns.append(f"stereo channels out of phase (correlation {corr:.2f}): mono fold-down would cancel")

    return {
        "duration_s": round(n / sr, 3),
        "peak_dbfs": round(peak, 2),
        "true_peak_dbtp": round(tp, 2),
        "clipped_fraction": round(clipped, 6),
        "dc_offset": round(dc, 5),
        "active_s": round(active_s, 3),
        "noise_floor_db": round(floor - env_peak, 1),  # relative to the loudest moment
        "onset_s": round(onset_s, 4),
        "tail_db": round(tail_rel, 1),
        "stereo_correlation": None if corr is None else round(corr, 3),
        "lufs": round(an.lufs(x, sr), 1),
        "fails": fails,
        "warnings": warns,
    }


def seam_report(loop: np.ndarray, sr: int) -> dict:
    """How audible a loop's seam is: compare the wrap point with the rest of the loop.

    The loop is rotated by half its length so the seam sits in the middle, then the level
    change and the spectral change across the seam are compared with the loop's typical
    variation (95th percentile). Under ~1.5 the seam looks like any other moment; 2+ fails.
    """
    x = as2d(loop).mean(axis=0).astype(np.float64)
    n = len(x)
    if n < int(0.2 * sr):
        return {"seam_ok": False, "seam_note": "too short to judge"}
    jump = abs(x[0] - x[-1])
    d = np.abs(np.diff(x))
    jump_ratio = float(jump / (np.percentile(d, 99) + 1e-9))
    rolled = np.roll(x, n // 2)
    hop, win = 512, 2048
    frames = np.lib.stride_tricks.sliding_window_view(rolled, win)[::hop] * np.hanning(win)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.sqrt(np.mean(np.diff(mag, axis=0) ** 2, axis=1))
    lvl = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    dl = np.abs(np.diff(lvl))
    c = (n // 2) // hop - win // (2 * hop)  # the frame pair straddling the seam
    near = slice(max(0, c - 2), min(len(flux), c + 3))
    # Against the loop's own 95th percentile, not its median: a few frames of ordinary
    # fluctuation near the seam must not look like a seam (a median made steady noise fail).
    flux_ratio = float(flux[near].max() / (np.percentile(flux, 95) + 1e-9))
    level_ratio = float(dl[near].max() / (np.percentile(dl, 95) + 1e-9))
    ok = jump_ratio < 1.5 and flux_ratio < SEAM_FLUX_MAX and level_ratio < SEAM_LEVEL_MAX
    return {"seam_ok": bool(ok), "seam_jump_ratio": round(jump_ratio, 2),
            "seam_flux_ratio": round(flux_ratio, 2), "seam_level_ratio": round(level_ratio, 2)}
