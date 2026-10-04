"""Synthetic test sounds. No models are loaded anywhere in tests/."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 48000


def hit(sr: int = SR, lead_s: float = 0.4, decay_s: float = 0.25, total_s: float = 1.5, f: float = 900.0,
        amp: float = 0.5, seed: int = 0) -> np.ndarray:
    """Silence, then an impact: a noisy click plus a ringing partial, decaying exponentially. (1, n)."""
    rng = np.random.default_rng(seed)
    n, lead = int(total_s * sr), int(lead_s * sr)
    t = np.arange(n - lead) / sr
    env = np.exp(-t / (decay_s / 5))
    body = env * (0.6 * np.sin(2 * np.pi * f * t) + 0.4 * rng.standard_normal(len(t)))
    x = np.concatenate([np.zeros(lead), amp * body / np.max(np.abs(body))])
    x[:lead] += 1e-4 * rng.standard_normal(lead)  # a little room noise
    return x[None, :].astype(np.float32)


def steady(sr: int = SR, total_s: float = 6.0, seed: int = 1, stereo: bool = True) -> np.ndarray:
    """Ambience: filtered noise with a slow level wobble. (2, n) or (1, n)."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(seed)
    n = int(total_s * sr)
    sos = butter(2, [200, 4000], btype="bandpass", fs=sr, output="sos")
    ch = 2 if stereo else 1
    x = sosfilt(sos, rng.standard_normal((ch, n)), axis=1)
    x *= 1 + 0.2 * np.sin(2 * np.pi * 0.3 * np.arange(n) / sr)
    return (0.2 * x / np.max(np.abs(x))).astype(np.float32)


@pytest.fixture()
def sr():
    return SR
