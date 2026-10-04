r"""Signal checks, WAV files with INFO chunks, prompt recipes and UCS names.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import numpy as np
import pytest
import soundfile as sf
from conftest import SR, hit, steady

from sfx import audio, checks, plot, prompts


# ------------------------------------------------------------------ signal checks

def test_a_clean_hit_passes():
    r = checks.signal_report(hit(), SR)
    assert r["fails"] == [] and r["warnings"] == []
    assert r["onset_s"] == pytest.approx(0.4, abs=0.003)


def test_silence_fails():
    r = checks.signal_report(np.zeros((1, SR), np.float32), SR)
    assert any("silent" in f for f in r["fails"])


def test_clipping_fails():
    t = np.arange(SR) / SR
    x = np.clip(3 * np.sin(2 * np.pi * 220 * t), -1, 1)[None, :].astype(np.float32)
    r = checks.signal_report(x, SR)
    assert any("clipped" in f for f in r["fails"])


def test_dc_offset_is_flagged():
    assert any("DC" in w for w in checks.signal_report(hit() * 0.5 + 0.03, SR)["warnings"])
    assert any("DC" in f for f in checks.signal_report(hit() * 0.5 + 0.3, SR)["fails"])


def test_a_short_ui_click_is_enough_sound():
    x = np.zeros((1, SR // 2), np.float32)
    x[0, 1000:1480] = 0.5 * np.sin(2 * np.pi * 2000 * np.arange(480) / SR)  # 10 ms
    assert checks.signal_report(x, SR)["fails"] == []


def test_dense_steady_sound_is_not_too_little_sound():
    r = checks.signal_report(steady(), SR, "loop")
    assert r["fails"] == []


def test_a_hiss_floor_under_a_hit_is_flagged():
    x = hit() + 0.03 * np.random.default_rng(0).standard_normal((1, int(1.5 * SR))).astype(np.float32)
    assert any("noise floor" in w for w in checks.signal_report(x, SR)["warnings"])


def test_late_onset_and_cut_off_tail_are_flagged():
    late = checks.signal_report(hit(lead_s=2.0, total_s=3.0), SR)
    assert any("late" in w for w in late["warnings"])
    cut = checks.signal_report(hit(decay_s=5.0, total_s=1.0), SR)
    assert any("cut off" in w for w in cut["warnings"])


def test_out_of_phase_stereo_is_flagged():
    x = hit()[0]
    r = checks.signal_report(np.vstack([x, -x]), SR)
    assert r["stereo_correlation"] < -0.9 and any("phase" in w for w in r["warnings"])


# ------------------------------------------------------------------ WAV + INFO

def test_wav_is_48k_24bit_with_info_before_the_data(tmp_path):
    p = audio.write_wav(tmp_path / "x.wav", hit(), info={"title": "METLImpt_SwordHit_v01",
                                                        "comment": "prompt | seed 7 | CLAP 0.31", "genre": "x"})
    info = sf.info(str(p))
    assert info.samplerate == 48000 and info.subtype == "PCM_24" and info.channels == 1
    assert audio.read_info(p) == {"title": "METLImpt_SwordHit_v01", "comment": "prompt | seed 7 | CLAP 0.31",
                                  "genre": "x"}
    raw = p.read_bytes()
    assert raw.index(b"LIST") < raw.index(b"data")
    y, sr = audio.read(p)
    assert np.allclose(y, hit(), atol=2 ** -22)


def test_wav_clamps_overs_and_handles_odd_length_text(tmp_path):
    p = audio.write_wav(tmp_path / "x.wav", np.full((2, 100), 1.7, np.float32), info={"title": "abc"})
    y, _ = audio.read(p)
    assert y.max() <= 1.0 and y.shape == (2, 100)
    assert audio.read_info(p)["title"] == "abc"


def test_unicode_survives_the_info_chunk(tmp_path):
    p = audio.write_wav(tmp_path / "x.wav", hit(), info={"comment": "cârlig de oțel — clang"})
    assert audio.read_info(p)["comment"] == "cârlig de oțel — clang"


def test_plot_writes_a_png(tmp_path):
    p = plot.draw(hit(), SR, tmp_path / "x.png", title="t", onset_s=0.4, align_frame=10)
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    q = plot.draw(steady(), SR, tmp_path / "y.png", seam=True)
    assert q.stat().st_size > 1000


# ------------------------------------------------------------------ prompts and names

@pytest.mark.parametrize("text,key", [
    ("sword hit", "impact-metal"), ("knock on a wooden door", "impact-wood"), ("bottle shatter", "impact-glass"),
    ("a punch", "impact-body"), ("fast whoosh", "whoosh"), ("ui click", "ui"), ("footsteps on gravel", "footsteps"),
    ("cartoon boing", "cartoon-boing"), ("bubble pop", "cartoon-pop"), ("magic sparkle", "magic"),
    ("sci-fi power up", "scifi-machine"), ("dragon roar", "creature"), ("big explosion", "explosion"),
    ("forest ambience at night", "ambience"), ("rain on a tin roof loop", "ambience"), ("a kazoo", "misc"),
    # plurals and -ing forms, and several keywords outvoting one (the surface it lands on)
    ("two swords clashing", "impact-metal"), ("a glass bottle shattering on concrete", "impact-glass"),
    ("a fast sword swing whoosh", "whoosh"), ("a single footstep on a creaky wooden floor", "footsteps"),
    ("a character zooming past the camera, whoosh", "whoosh"), ("a robot powering up", "scifi-machine"),
    ("a small creature growling", "creature"), ("knocked on a crate", "impact-wood")])
def test_categories_are_detected(text, key):
    assert prompts.detect(text).key == key


def test_recipe_adds_envelope_and_dry_once():
    p = prompts.build_prompt("sword hit")
    assert p["prompt"].startswith("sword hit, ") and "close-mic, dry, no reverb" in p["prompt"]
    assert p["mode"] == "oneshot" and p["cat_id"] == "METLImpt"
    q = prompts.build_prompt("sword hit, dry, sharp attack")
    assert "close-mic" not in q["prompt"] and "short decay" not in q["prompt"]


def test_ambience_recipe_is_not_close_miked_and_allows_reverb():
    p = prompts.build_prompt("forest ambience")
    assert p["mode"] == "loop" and "close-mic" not in p["prompt"] and "stereo" in p["prompt"]
    assert "reverb" not in p["negative"]


def test_a_loop_request_gets_the_ambience_recipe():
    p = prompts.build_prompt("steady rain on a window", mode="loop")
    assert p["category"] == "ambience" and "close-mic" not in p["prompt"]
    assert prompts.build_prompt("glass window", mode="oneshot")["category"] == "impact-glass"
    assert prompts.build_prompt("glass window", category="impact-glass", mode="loop")["category"] == "impact-glass"


def test_raw_sends_the_text_as_written():
    assert prompts.build_prompt("  sword   hit. ", raw=True)["prompt"] == "sword hit"


def test_distractors_include_failures_and_near_misses_but_not_the_prompt():
    d = prompts.distractors("a person speaking loudly", "creature")
    assert "silence" in d and "a person speaking" not in d and "a lion roaring at a zoo" in d


def test_ucs_names():
    assert prompts.fx_name("heavy sword hit on a shield") == "HeavySwordHitShield"
    assert prompts.fx_name("!!!") == "Sound"
    assert prompts.ucs_filename("METLImpt", "SwordClang", 1) == "METLImpt_SwordClang_v01.wav"
    assert prompts.ucs_filename("WHSH", "Swing", None) == "WHSH_Swing.wav"
    assert prompts.ucs_filename("../x", "a/b", 12) == "x_ab_v12.wav"
