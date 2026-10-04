r"""The agent CLI's own logic: delivery names, never overwriting, describing takes.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sfx_cli", Path(__file__).resolve().parent.parent / "scripts" / "sfx.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

RES = {"id": "20261004-120000-abcd", "cat_id": "METLImpt", "name": "SwordHit", "text": "sword hit",
       "prompt": "sword hit, dry", "engine": "sa3", "post": {"fps": 24}}


@pytest.fixture()
def take(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUTPUT", tmp_path / "output")
    src = tmp_path / "output" / RES["id"]
    src.mkdir(parents=True)
    (src / "1.wav").write_bytes(b"RIFF-new")
    (src / "1.png").write_bytes(b"png")
    return {"ref": f"{RES['id']}:1", "seed": 1, "rank": 1,
            "files": {"wav": f"{RES['id']}/1.wav", "png": f"{RES['id']}/1.png"}}


def test_delivery_names_and_sidecar(tmp_path, take):
    out = tmp_path / "out"
    paths = cli.deliver(RES, [take], out, png=True)
    assert [p.name for p in paths] == ["METLImpt_SwordHit_v01.wav", "METLImpt_SwordHit_v01.png"]
    side = json.loads((out / "METLImpt_SwordHit_v01.json").read_text(encoding="utf-8"))
    assert side["ref"] == take["ref"] and side["prompt"] == "sword hit, dry"


def test_delivery_never_overwrites_even_with_different_case(tmp_path, take):
    out = tmp_path / "out"
    out.mkdir()
    (out / "METLImpt_SWORDHIT_v01.wav").write_bytes(b"precious")
    paths = cli.deliver(RES, [take, take], out, png=False, name="SwordHit")
    assert [p.name for p in paths] == ["METLImpt_SwordHit_v02.wav", "METLImpt_SwordHit_v03.wav"]
    assert (out / "METLImpt_SWORDHIT_v01.wav").read_bytes() == b"precious"


def test_delivery_skips_a_file_that_appears_meanwhile(tmp_path, take, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(cli, "_used_versions", lambda o, b: set())  # as if it raced in after the scan
    (out / "METLImpt_SwordHit_v01.wav").write_bytes(b"precious")
    paths = cli.deliver(RES, [take], out, png=False)
    assert paths[0].name == "METLImpt_SwordHit_v02.wav"
    assert (out / "METLImpt_SwordHit_v01.wav").read_bytes() == b"precious"


def test_names_cant_escape_the_folder(tmp_path, take):
    for bad in ("..\\..\\escaped", "../x", "a/b", ""):
        with pytest.raises(ValueError):
            cli.deliver(RES, [take], tmp_path / "out", png=False, name=bad)
    with pytest.raises(ValueError):
        cli.deliver(RES, [take], tmp_path / "out", png=False, cat="../X")


def test_describe_handles_a_layered_take_and_a_loop():
    layer = {"rank": 1, "ref": "x:0", "rank_score": 0.0, "post": {"length_s": 1.0, "onset_s": 0.003}}
    assert "1.00s" in cli.describe(layer, 24)
    loop = {"rank": 1, "ref": "x:1", "rank_score": 0.5,
            "post": {"length_s": 4.0, "length_frames": 96, "seam_ok": False}}
    assert "96 fr" in cli.describe(loop, 24) and "SEAM?" in cli.describe(loop, 24)


def test_layer_specs():
    assert cli.parse_layer("A:1") == {"take": "A:1"}
    assert cli.parse_layer("A:1,gain=-6,offset=2f") == {"take": "A:1", "gain_db": -6.0, "offset_frames": 2.0}
    assert cli.parse_layer("A:1,offset=120ms") == {"take": "A:1", "offset_ms": 120.0}
    with pytest.raises(ValueError):
        cli.parse_layer("A:1,volume=3")


def test_no_align_sends_an_explicit_null():
    class A:
        mode = duration = fps = align_frame = preroll = reverb = channels = crossfade = None
        no_align = True
    assert cli.post_fields(A()) == {"align_frame": None}
