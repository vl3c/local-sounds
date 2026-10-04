"""Measurements shared by post-processing and the checks: envelope, onset, peaks, loudness."""

from __future__ import annotations

import numpy as np

from .audio import as2d

HOP_S = 0.001  # 1 ms envelope resolution: an onset is placed to within a millisecond


def db(v: float | np.ndarray) -> float | np.ndarray:
    return 20 * np.log10(np.maximum(np.abs(v), 1e-10))


def envelope_db(x: np.ndarray, sr: int, hop_s: float = HOP_S) -> np.ndarray:
    """RMS level per hop (dBFS), of the channel sum's power."""
    x = as2d(x)
    hop = max(1, int(round(hop_s * sr)))
    n = x.shape[1] // hop
    if n == 0:
        return np.full(1, -200.0)
    p = np.mean(np.square(x[:, : n * hop].astype(np.float64)), axis=0).reshape(n, hop).mean(axis=1)
    return 10 * np.log10(p + 1e-20)


def noise_floor_db(env: np.ndarray) -> float:
    """The quiet end of the level distribution (10th percentile), dBFS."""
    return float(np.percentile(env, 10))


def onset(x: np.ndarray, sr: int, rel_db: float = 30.0) -> int:
    """Sample index where the first sound event starts.

    The first 1 ms hop that rises above both (peak - rel_db) and (noise floor + 12 dB), then
    walked back while the level keeps falling towards the floor, so a soft attack
    (a whoosh's swell) is caught at its start rather than at its loudest point.
    """
    env = envelope_db(x, sr)
    hop = max(1, int(round(HOP_S * sr)))
    peak, floor = float(env.max()), noise_floor_db(env)
    if peak < -90:
        return 0
    thr = max(peak - rel_db, floor + 12)
    above = np.nonzero(env > thr)[0]
    if not len(above):
        return 0
    i = int(above[0])
    start_thr = max(floor + 10, peak - 45)  # a swell, not the noise or filter ringing before it
    while i > 0 and env[i - 1] > start_thr and env[i - 1] < env[i] + 1.5:
        i -= 1
    return i * hop


def last_sound(x: np.ndarray, sr: int, rel_db: float = 60.0) -> int:
    """Sample index just after the last hop within rel_db of the peak (and above the noise floor)."""
    env = envelope_db(x, sr)
    hop = max(1, int(round(HOP_S * sr)))
    peak, floor = float(env.max()), noise_floor_db(env)
    thr = max(peak - rel_db, floor + 3)
    above = np.nonzero(env > thr)[0]
    if not len(above):
        return as2d(x).shape[1]
    return min(as2d(x).shape[1], (int(above[-1]) + 1) * hop)


def true_peak_db(x: np.ndarray, sr: int) -> float:
    """ITU-R BS.1770-style true peak: the sample peak after 4x oversampling (dBTP)."""
    x = as2d(x)
    if x.size == 0:
        return -200.0
    import soxr
    up = soxr.resample(x.T.astype(np.float64), sr, sr * 4, quality="HQ")
    return float(db(max(np.max(np.abs(up)), np.max(np.abs(x)))))


def sample_peak_db(x: np.ndarray) -> float:
    return float(db(np.max(np.abs(as2d(x))))) if np.size(x) else -200.0


def lufs(x: np.ndarray, sr: int) -> float:
    """Integrated loudness (ITU-R BS.1770-4). Clips under 0.4 s are measured looped to 0.4 s."""
    import pyloudnorm
    x = as2d(x)
    need = int(0.4 * sr) + 1
    if x.shape[1] < need:
        x = np.tile(x, (1, need // max(1, x.shape[1]) + 1))
    with np.errstate(divide="ignore"):
        v = pyloudnorm.Meter(sr).integrated_loudness(x.T.astype(np.float64))
    return float(v) if np.isfinite(v) else -200.0


def stereo_correlation(x: np.ndarray) -> float | None:
    """Pearson correlation of L and R (1 = mono, 0 = wide/unrelated, < 0 = phase problems)."""
    x = as2d(x)
    if x.shape[0] < 2:
        return None
    l, r = x[0].astype(np.float64), x[1].astype(np.float64)
    den = np.sqrt(np.sum(l * l) * np.sum(r * r))
    return float(np.sum(l * r) / den) if den > 1e-12 else None


def zero_crossings(v: np.ndarray) -> np.ndarray:
    """Indices i where v changes sign between i and i+1."""
    s = np.signbit(v)
    return np.nonzero(s[1:] != s[:-1])[0]
