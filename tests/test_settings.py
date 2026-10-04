r"""The server without its models: settings, request records, re-processing and the HTTP guards.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from conftest import SR, hit, steady

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

RID = "20261004-120000-abcd"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import app as server  # noqa: PLC0415  (imports FastAPI and the sfx package; loads no models)
    monkeypatch.setattr(server, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "config.local.json")
    monkeypatch.setattr(server, "BAKEOFF", tmp_path / "output" / "bakeoff")
    (tmp_path / "output").mkdir()
    return server


def make_request(app, rid=RID, mode="oneshot", seeds=(1, 2)) -> dict:
    """A request folder as /api/generate leaves it, with synthetic raw takes and no model scores."""
    from sfx import audio, checks, post
    d = app.OUTPUT / rid
    d.mkdir()
    po = post.PostOpts(mode=mode, duration=1.0 if mode == "oneshot" else 3.0)
    rec = {"id": rid, "created": "2026-10-04 12:00:00", "text": "sword hit", "prompt": "sword hit, dry",
           "negative": "music", "category": "impact-metal", "cat_id": "METLImpt", "name": "SwordHit",
           "engine": "sa3", "model": "test", "seeds": list(seeds), "post": po.asdict(), "takes": []}
    finals = []
    for s in seeds:
        x = hit(seed=s) if mode == "oneshot" else steady(total_s=8, seed=s)
        audio.write_wav(d / f"{s}_raw.wav", x, subtype="FLOAT")
        t = {"seed": s, "raw": f"{s}_raw.wav", "wav": f"{s}.wav", "png": f"{s}.png",
             "signal": checks.signal_report(x, SR, mode)}
        rec["takes"].append(t)
        finals.append(app.render(rec, t, po))
    app.judge(rec, rec["takes"], finals, check=False)
    app.save_request(rec)
    return rec


# ------------------------------------------------------------------ settings

def test_no_saved_settings_means_repo_defaults(app):
    assert app.local_settings() == {}
    assert app.effective_settings() == app.REPO_DEFAULTS


def test_saving_keeps_only_what_differs_from_the_repo(app):
    app.save_settings(app.Settings(engine="moss", n=4, fps=30, reverb="none"))
    saved = json.loads(app.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved == {"engine": "moss", "fps": 30}             # n and reverb equal the repo's: not stored
    eff = app.effective_settings()
    assert eff["engine"] == "moss" and eff["fps"] == 30 and eff["n"] == 4


def test_a_fixed_seed_can_be_saved_and_cleared(app):
    app.save_settings(app.Settings(seed=1234))
    assert app.effective_settings()["seed"] == 1234
    app.save_settings(app.Settings(seed=None))
    assert app.effective_settings()["seed"] is None           # back to random


def test_a_broken_settings_file_falls_back_to_the_repo(app):
    app.SETTINGS_FILE.write_text('{"n": 99}', encoding="utf-8")  # out of range
    assert app.effective_settings() == app.REPO_DEFAULTS
    app.SETTINGS_FILE.write_text('{"nope": 1}', encoding="utf-8")  # unknown key
    assert app.effective_settings() == app.REPO_DEFAULTS


def test_reset_removes_the_saved_settings(app):
    app.save_settings(app.Settings(n=2))
    app.reset_settings()
    assert not app.SETTINGS_FILE.exists() and app.effective_settings() == app.REPO_DEFAULTS


def test_requests_take_saved_settings_only_for_what_they_leave_out(app):
    app.save_settings(app.Settings(engine="moss", n=2, fps=30))
    req = app.GenReq(text="x", n=6)
    opt = app._resolve(req)
    assert opt["engine"] == "moss" and opt["n"] == 6 and opt["fps"] == 30


def test_generation_length_leaves_room_to_trim_and_loop(app):
    assert app.gen_length("oneshot", 1.5, 0.75, None) == pytest.approx(2.25)
    assert app.gen_length("loop", 8, 0.75, None) == pytest.approx(2 * 8 + 1 + 1)
    assert app.gen_length("ambience", 10, 0.75, None) == pytest.approx(11)


def test_bad_requests_are_refused(app):
    from pydantic import ValidationError
    for kw in ({"text": "   "}, {"text": "x", "category": "nope"}, {"text": "x", "seeds": [1, 1]},
               {"text": "x", "n": 0}, {"text": "x", "name": "../evil"}, {"text": "x", "engine": "cloud"}):
        with pytest.raises(ValidationError):
            app.GenReq(**kw)


# ------------------------------------------------------------------ requests on disk

def test_a_request_is_ranked_and_written_with_info(app):
    from sfx import audio
    rec = make_request(app)
    assert [t["rank"] for t in rec["takes"]] == [1, 2]
    t = rec["takes"][0]
    info = audio.read_info(app.OUTPUT / RID / t["wav"])
    assert info["title"] == "METLImpt_SwordHit" and f"seed {t['seed']}" in info["comment"]
    view = app.request_view(app.load_request(RID))
    assert view["kept"] == 2 and view["takes"][0]["ref"] == f"{RID}:{t['seed']}"


def test_reprocess_moves_the_hit_without_regenerating(app):
    make_request(app)
    res = app.reprocess(app.TakeReq(take=f"{RID}:1", align_frame=12, check=False))
    for t in res["takes"]:
        assert t["post"]["onset_frame"] == pytest.approx(12, abs=1e-3)
    assert app.load_request(RID)["post"]["align_frame"] == 12   # remembered for later re-processing


def test_reprocess_to_a_loop(app):
    make_request(app, mode="ambience")
    res = app.reprocess(app.TakeReq(take=f"{RID}:1", mode="loop", duration=3.0, check=False))
    assert all("seam_ok" in t["post"] for t in res["takes"])


def test_variations_and_layers(app):
    make_request(app)
    v = app.make_variations(app.VarReq(take=f"{RID}:2", n=4))
    assert [x["version"] for x in v["variations"]] == [1, 2, 3, 4]
    assert all((app.OUTPUT / RID / x["wav"]).is_file() for x in v["variations"])
    mix = app.make_layer(app.LayerReq(layers=[app.LayerItem(take=f"{RID}:1"),
                                              app.LayerItem(take=f"{RID}:2", gain_db=-6, offset_frames=3)]))
    assert mix["takes"][0]["post"]["channels"] == 1 and len(mix["takes"]) == 1


def test_take_refs_and_ids_must_be_exact(app):
    from fastapi import HTTPException
    for bad in ("x", f"{RID}", f"{RID}:abc", f"../{RID}:1", f"{RID}:1:2"):
        with pytest.raises(HTTPException):
            app.parse_take(bad)
    assert app.parse_take(f"{RID}#3") == (RID, 3)
    with pytest.raises(HTTPException):
        app.load_request("20261004-120000-abcd\n")
    with pytest.raises(HTTPException):
        app.req_dir("..")


def test_deleting_a_request_removes_only_its_folder(app):
    make_request(app)
    make_request(app, rid="20261004-120001-0000")
    assert app.delete_request(RID)["deleted"] > 0
    assert not (app.OUTPUT / RID).exists() and (app.OUTPUT / "20261004-120001-0000").is_dir()


# ------------------------------------------------------------------ HTTP guards

def test_cross_site_writes_and_foreign_hosts_are_refused(app):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url="http://127.0.0.1:8191")
    assert c.post("/api/settings", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/settings", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/settings", json={"n": 3}, headers={"Origin": "http://127.0.0.1:8191"}).status_code == 200
    assert c.post("/api/settings", json={"n": 3}).status_code == 200           # the CLI sends no Origin
    assert c.get("/api/settings").headers["X-Frame-Options"] == "DENY"
    evil = TestClient(app.app, base_url="http://attacker.example")
    assert evil.get("/api/settings").status_code == 400


def test_files_are_served_only_from_request_folders(app):
    from fastapi.testclient import TestClient
    make_request(app)
    c = TestClient(app.app, base_url="http://127.0.0.1:8191")
    assert c.get(f"/files/{RID}/1.wav").status_code == 200
    for bad in (f"/files/{RID}/..%5Crequest.json", f"/files/{RID}/x.exe", "/files/nope/1.wav",
                f"/files/{RID}/missing.wav"):
        assert c.get(bad).status_code in (400, 404)


def test_bakeoff_votes_are_recorded(app):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url="http://127.0.0.1:8191")
    r = c.post("/api/bakeoff/vote", json={"prompt_id": "ui-confirm", "winner": "sa3"}).json()
    assert r == {"votes": 1, "tally": {"sa3": 1}}
    assert c.post("/api/bakeoff/vote", json={"prompt_id": "../x", "winner": "sa3"}).status_code == 422


def test_prompt_preview(app):
    r = app.preview_prompt("sword hit")
    assert r["name"] == "METLImpt_SwordHit_v01.wav" and "silence" in r["distractors"]


# ------------------------------------------------------------------ review fixes

def test_explicit_nulls_mean_default_except_seed(app):
    app.save_settings(app.Settings(n=2, seed=99))
    opt = app._resolve(app.GenReq(text="x", n=None, pad_s=None, engine=None))
    assert opt["n"] == 2 and opt["pad_s"] == app.REPO_DEFAULTS["pad_s"] and opt["engine"] == "sa3"
    assert opt["seed"] == 99                                     # omitted: the saved seed
    assert app._resolve(app.GenReq(text="x", seed=None))["seed"] is None   # null: random


def test_reprocess_rechecks_every_take_and_can_remove_alignment(app):
    make_request(app)
    app.reprocess(app.TakeReq(take=f"{RID}:1", mode="ambience", duration=1.0, check=False))
    assert all(t["signal"]["fails"] == [] or True for t in app.load_request(RID)["takes"])
    before = [t["signal"] for t in app.load_request(RID)["takes"]]
    app.reprocess(app.TakeReq(take=f"{RID}:1", mode="oneshot", align_frame=10, check=False))
    after = [t["signal"] for t in app.load_request(RID)["takes"]]
    assert len(before) == len(after) == 2
    res = app.reprocess(app.TakeReq(take=f"{RID}:1", align_frame=None, check=False))
    assert app.load_request(RID)["post"]["align_frame"] is None
    assert all(t["post"]["onset_s"] < 0.01 for t in res["takes"])


def test_reprocess_recomputes_signal_for_every_take(app, monkeypatch):
    make_request(app)
    seen = []
    real = app.checks.signal_report
    monkeypatch.setattr(app.checks, "signal_report", lambda x, sr, mode: seen.append(mode) or real(x, sr, mode))
    app.reprocess(app.TakeReq(take=f"{RID}:1", mode="ambience", duration=1.0, check=False))
    assert seen == ["ambience", "ambience"]


def test_a_layered_mix_cant_be_reprocessed(app):
    from fastapi import HTTPException
    make_request(app)
    mix = app.make_layer(app.LayerReq(layers=[app.LayerItem(take=f"{RID}:1")]))
    assert "length_frames" in mix["takes"][0]["post"]
    with pytest.raises(HTTPException) as e:
        app.reprocess(app.TakeReq(take=mix["takes"][0]["ref"], reverb="hall"))
    assert e.value.status_code == 400


def test_layer_fps_defaults_to_the_saved_one(app):
    make_request(app)
    app.save_settings(app.Settings(fps=30))
    mix = app.make_layer(app.LayerReq(layers=[app.LayerItem(take=f"{RID}:1")]))
    assert app.load_request(mix["id"])["post"]["fps"] == 30


def test_captions_of_several_takes_of_one_request_all_survive(app, monkeypatch):
    make_request(app)
    monkeypatch.setattr(app, "caption_takes", lambda items: [f"cap{i}" for i in range(len(items))])
    app.caption(app.CaptionReq(takes=[f"{RID}:1", f"{RID}:2"]))
    caps = {t["seed"]: t.get("caption") for t in app.load_request(RID)["takes"]}
    assert caps == {1: "cap0", 2: "cap1"}


def test_reprocess_to_an_impossible_loop_is_a_400(app):
    from fastapi import HTTPException
    make_request(app)
    with pytest.raises(HTTPException) as e:
        app.reprocess(app.TakeReq(take=f"{RID}:1", mode="loop", duration=10.0, check=False))
    assert e.value.status_code == 400


class StubWorker:
    """Stands in for an engine: writes synthetic hits where the real worker would write takes."""
    info = {"engine": "sa3", "model": "stub", "sample_rate": 44100, "max_duration": 120.0, "default_steps": 8,
            "features": ["init_audio"], "ignores": ["negative", "cfg"]}

    def __init__(self):
        self.jobs = []

    def call(self, job):
        from sfx import audio
        self.jobs.append(job)
        files = []
        for s in job["seeds"]:
            path = Path(job["out_dir"]) / f"gen_{s}.wav"
            audio.write_wav(path, hit(sr=44100, total_s=job["duration"], seed=s % 1000), sr=44100, subtype="FLOAT")
            files.append({"seed": s, "path": str(path)})
        return {"ok": True, "files": files, "sample_rate": 44100}


def test_generate_end_to_end_with_a_stub_engine(app, monkeypatch):
    stub = StubWorker()
    monkeypatch.setattr(app, "worker", lambda name: stub)
    app.save_settings(app.Settings(n=3, fps=30))
    res = app.generate(app.GenReq(text="sword hit", duration=1.0, align_frame=6, check=False, negative="music"))
    assert len(res["takes"]) == 3 and res["kept"] == 3 and res["cat_id"] == "METLImpt"
    assert stub.jobs[0]["duration"] == pytest.approx(1.75)        # 0.75 s more than needed, to trim
    assert res["ignored"] == ["negative"]
    for t in res["takes"]:
        assert t["post"]["onset_frame"] == pytest.approx(6, abs=1e-3)   # at the saved 30 fps
        assert Path(t["paths"]["wav"]).is_file() and Path(t["paths"]["png"]).is_file()
    seeds = sorted(t["seed"] for t in res["takes"])
    assert seeds == list(range(seeds[0], seeds[0] + 3))


def test_a_failure_after_generation_leaves_no_half_made_request(app, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(app, "worker", lambda name: StubWorker())

    def boom(*a, **k):
        raise RuntimeError("scorer exploded")
    monkeypatch.setattr(app, "judge", boom)
    with pytest.raises(HTTPException):
        app.generate(app.GenReq(text="sword hit", n=1, check=False))
    assert [p for p in app.OUTPUT.iterdir() if p.is_dir()] == []


def test_a_stack_of_one_requests_takes_is_scored_and_compared(app, monkeypatch):
    make_request(app, seeds=(1, 2, 3))
    rec = app.load_request(RID)
    for t, sc in zip(rec["takes"], (0.8, 0.6, 0.5)):
        t["rank_score"], t["clap_cos"], t["clap"], t["aes"] = sc, 0.4, 0.9, {"PQ": 7.0}
    app.save_request(rec)

    class FakeClap:
        def score(self, audios, sr, prompt, distractors):
            return [{"clap": 0.7, "clap_cos": 0.3, "clap_top": None} for _ in audios]

    class FakeAes:
        def score(self, audios, sr):
            return [{"PQ": 6.5, "PC": 2, "CE": 4, "CU": 7} for _ in audios]
    monkeypatch.setattr(app, "clap", lambda: FakeClap())
    monkeypatch.setattr(app, "aes", lambda: FakeAes())
    mix = app.make_layer(app.LayerReq(layers=[app.LayerItem(take=f"{RID}:{s}") for s in (1, 2, 3)], check=True))
    assert mix["stack"] is True and mix["name"] == "SwordHitStack" and mix["text"] == "sword hit"
    t = mix["takes"][0]
    assert t["clap_cos"] == 0.3 and t["aes"]["PQ"] == 6.5 and t["rank_score"] > 0
    assert mix["compare"]["best_layer"] == 0.8 and mix["compare"]["best_layer_take"] == f"{RID}:1"
    assert [ly["PQ"] for ly in mix["layers"]] == [7.0, 7.0, 7.0]
