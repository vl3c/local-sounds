r"""Post-processing for animation: trim, frame alignment, loops, variations, layers, loudness.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import numpy as np
import pytest
from conftest import SR, hit, steady

from sfx import analysis as an
from sfx import checks, post
from sfx.audio import mono


# ------------------------------------------------------------------ frames

@pytest.mark.parametrize("fps,spf", [(24, 2000), (30, 1600), (60, 800), (25, 1920)])
def test_samples_per_frame_at_48k(fps, spf):
    assert post.samples_per_frame(fps) == spf
    assert post.frame_to_sample(12, fps) == 12 * spf


@pytest.mark.parametrize("fps", [24, 30, 60, 29.97, 23.976])
def test_padding_lands_on_a_whole_number_of_frames(fps):
    for n in (1, 1999, 2000, 2001, 47_999, 48_123):
        y = post.pad_to_frames(np.zeros((1, n), np.float32), fps)
        frames = y.shape[1] * fps / SR
        assert abs(frames - round(frames)) < 0.02 and y.shape[1] >= n
        assert y.shape[1] - n < SR / fps + 1  # never more than one frame of padding


# ------------------------------------------------------------------ one-shots

def test_oneshot_puts_the_transient_right_after_the_preroll():
    y, rep = post.process(hit(lead_s=0.4), SR, post.PostOpts(preroll_ms=3))
    assert y.shape[0] == 1                                   # one-shots are mono
    assert abs(rep["onset_s"] - 0.003) < 0.0005
    assert abs(an.onset(y, SR) / SR - 0.003) < 0.0015       # and the audio agrees
    assert np.max(np.abs(y[:, :100])) < 0.02                 # the pre-roll is (near) silent
    assert y.shape[1] % 2000 == 0                            # whole frames at 24 fps


def test_oneshot_peak_is_minus_one_dbtp():
    y, _ = post.process(hit(amp=0.05), SR, post.PostOpts())
    assert -1.05 <= an.true_peak_db(y, SR) <= -1.0 + 1e-6


def test_oneshot_ends_in_silence_and_is_capped_at_the_duration():
    y, rep = post.process(hit(decay_s=3.0, total_s=5.0), SR, post.PostOpts(duration=1.0))
    assert rep["length_s"] <= 1.0 + 1 / 24 + 1e-9           # the cap, plus padding to a whole frame
    assert abs(float(y[0, -1])) < 1e-4                        # faded out to zero


@pytest.mark.parametrize("frame,fps", [(12, 24), (0, 24), (5, 30), (40, 60)])
def test_align_frame_puts_the_hit_exactly_on_the_frame(frame, fps):
    opts = post.PostOpts(align_frame=frame, fps=fps)
    y, rep = post.process(hit(), SR, opts)
    assert rep["onset_s"] == pytest.approx(frame / fps, abs=1e-4)
    assert rep["onset_frame"] == pytest.approx(frame, abs=1e-3)
    assert abs(an.onset(y, SR) - post.frame_to_sample(frame, fps)) <= SR // 1000 + 1
    frames = y.shape[1] * fps / SR
    assert abs(frames - round(frames)) < 1e-6


def test_a_sound_that_starts_at_sample_zero_still_gets_its_preroll():
    x = hit(lead_s=0.0)
    y, rep = post.process(x, SR, post.PostOpts(preroll_ms=4))
    assert abs(rep["onset_s"] - 0.004) < 0.0005
    assert np.max(np.abs(y[:, : int(0.003 * SR)])) < 1e-6


def test_out_of_phase_stereo_folds_to_mono_without_cancelling():
    x = hit()[0]
    y = mono(np.vstack([x, -x]))
    assert np.max(np.abs(y)) > 0.4


def test_the_reported_onset_is_where_the_sound_really_starts():
    for seed in range(6):
        y, rep = post.process(hit(f=300 + 400 * seed, seed=seed), SR, post.PostOpts())
        first = int(np.argmax(np.abs(y[0]) > 0.05))  # the attack is instant in these test hits
        assert abs(first / SR - rep["onset_s"]) < 0.0015, (seed, first / SR, rep["onset_s"])


def test_dc_offset_is_removed():
    x = hit() + 0.2
    y, _ = post.process(x, SR, post.PostOpts())
    assert abs(float(np.mean(y))) < 0.01


def test_reverb_adds_a_tail():
    dry, _ = post.process(hit(decay_s=0.1), SR, post.PostOpts(duration=2.0))
    wet, _ = post.process(hit(decay_s=0.1), SR, post.PostOpts(duration=2.0, reverb="hall"))
    assert wet.shape[1] > dry.shape[1]


def test_bad_options_are_refused():
    for kw in ({"mode": "nope"}, {"reverb": "cathedral"}, {"fps": 0}, {"align_frame": -1}, {"channels": "5.1"}):
        with pytest.raises(ValueError):
            post.PostOpts(**kw)


# ------------------------------------------------------------------ ambiences and loops

def test_ambience_is_stereo_at_minus_23_lufs_under_the_ceiling():
    y, rep = post.process(steady(total_s=6), SR, post.PostOpts(mode="ambience", duration=4.0))
    assert y.shape[0] == 2
    assert an.lufs(y, SR) == pytest.approx(-23, abs=0.5)
    assert an.true_peak_db(y, SR) <= -1.0 + 1e-6
    assert rep["length_s"] == pytest.approx(4.0, abs=1 / 24)


def test_loud_ambience_is_limited_to_the_ceiling():
    x = steady(total_s=6) * 4.5  # peaky: reaching -12 LUFS needs the limiter
    y, _ = post.process(x, SR, post.PostOpts(mode="ambience", duration=4.0, lufs=-12))
    assert an.true_peak_db(y, SR) <= -1.0 + 1e-6


def test_loop_is_exactly_the_length_in_whole_frames_and_seamless():
    y, rep = post.process(steady(total_s=10), SR, post.PostOpts(mode="loop", duration=4.0))
    assert y.shape[1] == 4 * SR                               # 96 frames at 24 fps
    seam = checks.seam_report(y, SR)
    assert seam["seam_ok"], seam
    # Playing the end into the start: the jump is no bigger than an ordinary sample step.
    d = np.abs(np.diff(y[0]))
    assert abs(y[0, 0] - y[0, -1]) <= np.percentile(d, 99.9)


def test_loop_duration_is_rounded_to_whole_frames():
    y, _ = post.process(steady(total_s=10), SR, post.PostOpts(mode="loop", duration=3.33, fps=24))
    assert y.shape[1] == 80 * 2000                            # 3.33 s -> 80 frames


def test_a_naive_cut_has_an_audible_seam_and_the_check_says_so():
    t = np.arange(4 * SR) / SR
    tone = (0.5 * np.sin(2 * np.pi * 101.3 * t))[None, :].astype(np.float32)  # not a whole number of cycles
    cut = tone[:, : int(3.71 * SR)]
    assert not checks.seam_report(cut, SR)["seam_ok"]
    looped, _ = post.make_loop(np.tile(tone, (1, 3)), SR, 3.0)
    assert checks.seam_report(looped, SR)["seam_jump_ratio"] < 1.5


def test_loop_needs_enough_audio():
    with pytest.raises(ValueError):
        post.make_loop(steady(total_s=3), SR, 4.0)


# ------------------------------------------------------------------ variations and layers

def test_variations_keep_the_take_first_and_the_transient_in_place():
    x = hit()
    vs = post.variations(x, SR, post.PostOpts(), n=5, seed=7)
    assert len(vs) == 5
    base, _ = post.process(x, SR, post.PostOpts())
    assert np.allclose(vs[0][0], base, atol=1e-6)            # v01 is the take itself
    for y, rep in vs:
        assert abs(rep["onset_s"] - 0.003) < 0.0005
        assert -1.05 <= an.true_peak_db(y, SR) <= -1.0 + 1e-6
    assert any(rep["variation"]["semitones"] != 0 for _, rep in vs[1:])
    lengths = {y.shape[1] for y, _ in vs}
    assert len(lengths) > 1 or not np.allclose(vs[1][0], vs[0][0])  # they really differ


def test_variations_are_reproducible():
    a = post.variations(hit(), SR, n=3, seed=3)
    b = post.variations(hit(), SR, n=3, seed=3)
    for (ya, _), (yb, _) in zip(a, b):
        assert np.array_equal(ya, yb)


def test_layers_land_at_their_offsets_through_a_limiter():
    a, ra = post.process(hit(f=300), SR, post.PostOpts())
    b, rb = post.process(hit(f=2000, seed=5), SR, post.PostOpts())
    mix, rep = post.layer([post.Layer(a, 0, 0.0, ra["onset_s"]), post.Layer(b, -6, 0.25, rb["onset_s"])])
    assert -1.05 <= an.true_peak_db(mix, SR) <= -1.0 + 1e-6
    first = an.onset(mix, SR) / SR
    assert first == pytest.approx(rep["onset_s"], abs=0.002)
    # the second layer's transient shows up ~250 ms later as a jump in level
    env = an.envelope_db(mix, SR)
    k = int((rep["onset_s"] + 0.25) * 1000)
    assert env[k + 2] - env[k - 5] > 6
    assert mix.shape[1] % 2000 == 0


def test_a_negative_offset_starts_a_layer_before_the_first():
    a, ra = post.process(hit(), SR, post.PostOpts())
    mix, rep = post.layer([post.Layer(a, 0, 0.0, ra["onset_s"]), post.Layer(a, 0, -0.1, ra["onset_s"])])
    assert rep["onset_s"] == pytest.approx(0.1 + ra["onset_s"], abs=0.001)


# ------------------------------------------------------------------ review fixes

def test_a_duration_that_chops_the_tail_is_reported():
    _, rep = post.process(hit(decay_s=3.0, total_s=5.0), SR, post.PostOpts(duration=0.6))
    assert rep["cut_db"] > -20                                 # still loud where it was cut
    _, ok = post.process(hit(decay_s=0.2), SR, post.PostOpts(duration=1.5))
    assert "cut_db" not in ok                                   # it had died away: nothing to report


def test_reverb_keeps_a_loop_seamless():
    x = steady(total_s=10) * 0.3
    rng = np.random.default_rng(4)
    drip = (0.8 * np.exp(-np.arange(240) / 40) * np.sin(2 * np.pi * 1800 * np.arange(240) / SR)).astype(np.float32)
    for at in np.sort(rng.integers(0, x.shape[1] - 240, 40)):  # sparse drips: reverb tails matter
        x[:, at:at + 240] += drip
    y, _ = post.process(x, SR, post.PostOpts(mode="loop", duration=3.0, reverb="hall"))
    assert checks.seam_report(y, SR)["seam_ok"]


def test_reverb_on_an_ambience_ends_faded():
    y, _ = post.process(steady(total_s=6), SR, post.PostOpts(mode="ambience", duration=4.0, reverb="hall"))
    assert np.max(np.abs(y[:, -48:])) < 0.01


def test_layering_loops_wraps_them_into_a_loop():
    a, _ = post.process(steady(total_s=10, seed=1), SR, post.PostOpts(mode="loop", duration=4.0))
    b, _ = post.process(steady(total_s=10, seed=2), SR, post.PostOpts(mode="loop", duration=4.0))
    mix, rep = post.layer([post.Layer(a), post.Layer(b, -3, 0.5)], post.PostOpts(mode="loop"))
    assert mix.shape[1] == a.shape[1] and rep["seam_ok"]
    assert an.lufs(mix, SR) == pytest.approx(-23, abs=0.5)
