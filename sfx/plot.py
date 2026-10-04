"""Waveform + spectrogram PNG with frame gridlines and an onset marker, for an agent to look at."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .audio import as2d


def draw(x: np.ndarray, sr: int, path: Path | str, title: str = "", fps: float = 24.0,
         onset_s: float | None = None, align_frame: int | None = None, seam: bool = False) -> Path:
    """Write a PNG: waveform on top (dBFS-scaled amplitude), log-frequency spectrogram below.

    Thin vertical lines mark every frame at `fps` (numbered every 6th, or every 12th / 24th
    on long clips), a red line marks the detected onset, and an orange band the frame the
    hit was aligned to. For loops (`seam=True`) a dashed line marks where the end meets the
    start, half-way through a copy rotated so the seam is visible.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = as2d(x)
    m = x.mean(axis=0)
    if seam:
        m = np.roll(m, len(m) // 2)
    dur = len(m) / sr
    t = np.arange(len(m)) / sr
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 5.5), height_ratios=[1, 2.4], sharex=True)
    ax1.plot(t, m, lw=0.5, color="#1f3b5a")
    ax1.set_ylim(-1.05, 1.05)
    ax1.set_ylabel("amplitude")
    ax1.set_title(title, fontsize=9, loc="left", pad=14)
    nfft = 1024 if dur < 2 else 2048
    ax2.specgram(m + 1e-9, NFFT=nfft, Fs=sr, noverlap=nfft * 3 // 4, cmap="magma", vmin=-140)
    ax2.set_yscale("symlog", linthresh=500)
    ax2.set_ylim(20, sr / 2)
    ax2.set_ylabel("Hz")
    ax2.set_xlabel(f"seconds  (grid: frames at {fps:g} fps)")

    frames = int(np.ceil(dur * fps))
    label_every = 6 if frames <= 48 else 12 if frames <= 120 else 24
    if frames <= 600:  # past ~25 s at 24 fps the grid is just grey
        for f in range(frames + 1):
            ft = f / fps
            strong = f % label_every == 0
            for ax in (ax1, ax2):
                ax.axvline(ft, color="#888" if strong else "#ccc", lw=0.6 if strong else 0.3,
                           alpha=0.9 if ax is ax1 else 0.35, zorder=0)
            if strong:
                ax1.text(ft, 1.02, str(f), fontsize=7, ha="center", va="bottom", color="#555",
                         transform=ax1.get_xaxis_transform())
    if align_frame is not None:
        for ax in (ax1, ax2):
            ax.axvspan(align_frame / fps, (align_frame + 1) / fps, color="orange", alpha=0.25, lw=0)
    if onset_s is not None and not seam:
        for ax in (ax1, ax2):
            ax.axvline(onset_s, color="red", lw=1.0)
        ax1.text(onset_s, -1.0, f" onset {onset_s * 1000:.0f} ms / frame {onset_s * fps:.2f}", color="red",
                 fontsize=7, va="bottom")
    if seam:
        for ax in (ax1, ax2):
            ax.axvline(dur / 2, color="cyan", lw=1.0, ls="--")
        ax1.text(dur / 2, -1.0, " loop seam (end -> start)", color="#08a", fontsize=7, va="bottom")
    ax1.set_xlim(0, dur)
    fig.tight_layout()
    path = Path(path)
    fig.savefig(path, dpi=80)
    plt.close(fig)
    return path
