r"""Cue sheets (sfx.py --batch) and starting the server, with the server faked.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sfx_cli_batch", Path(__file__).resolve().parent.parent / "scripts" / "sfx.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def sheet(tmp_path, data) -> Path:
    p = tmp_path / "film.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- checking a sheet

def test_a_cue_sheet_is_checked_like_the_command_line(tmp_path):
    p = sheet(tmp_path, {"defaults": {"fps": 24, "out": "sfx"}, "cues": [
        {"text": "door slam", "align_frame": 36, "name": "DoorSlam"},
        {"id": "rain", "text": "rain on a tin roof", "loop": True, "duration": 8, "check": False},
        {"text": "-dash- whoosh", "seeds": [5, 6], "all": True}]})
    cues, folder = cli.load_cues(p, {"engine": "sa3"})
    assert [c["id"] for c in cues] == ["DoorSlam", "rain", "DashWhoosh"]
    door, rain, dash = (c["args"] for c in cues)
    assert door.align_frame == 36 and door.fps == 24 and door.engine == "sa3" and door.text == "door slam"
    assert rain.mode == "loop" and rain.duration == 8.0 and rain.check is False
    assert dash.text == "-dash- whoosh" and dash.seeds == "5,6" and dash.all
    assert folder == tmp_path / "sfx" and door.out == tmp_path / "sfx"  # relative to the sheet


def test_a_cue_overrides_the_command_line_which_overrides_the_defaults(tmp_path):
    p = sheet(tmp_path, {"defaults": {"reverb": "hall", "n": 2, "out": "sfx"},
                         "cues": [{"text": "a"}, {"text": "b", "reverb": "hall"}]})
    a, b = (c["args"] for c in cli.load_cues(p, {"reverb": "room"})[0])
    assert (a.reverb, a.n, b.reverb) == ("room", 2, "hall")


@pytest.mark.parametrize("cues, message", [
    ([{"duration": 1}], "needs a \"text\""),
    ([{"text": "a"}, {"text": "a"}], "already used"),
    ([{"text": "a", "volume": 3}], "unknown option"),
    ([{"text": "a", "duration": "long"}], "duration"),
    ([{"text": "a", "engine": "moss"}], "engine"),        # removed engines are refused too
    ([{"text": "a", "negative": "music"}], "unknown option"),
    ([{"id": "a", "text": "a", "name": "Bad Name"}], "letters and digits"),
    ([{"text": "a", "id": "no spaces"}], "an id takes"),
    ([{"text": "a", "like": "@b"}, {"text": "b"}], "earlier cue"),
    ([{"text": "a", "all": "yes"}], "true or false"),
    ([{"text": "a", "stack": 40}], "2 to 12"),
])
def test_a_bad_cue_is_named_before_anything_runs(tmp_path, cues, message):
    with pytest.raises(ValueError) as e:
        cli.load_cues(sheet(tmp_path, {"defaults": {"out": "sfx"}, "cues": cues}), {})
    assert message in str(e.value) and "cue " in str(e.value)


def test_a_cue_sheet_needs_somewhere_to_deliver(tmp_path):
    with pytest.raises(ValueError, match="--out"):
        cli.load_cues(sheet(tmp_path, [{"text": "a"}]), {})
    cues, _ = cli.load_cues(sheet(tmp_path, [{"text": "a"}]), {"out": str(tmp_path / "x")})
    assert cues[0]["args"].out == tmp_path / "x"


def test_command_line_options_apply_to_every_cue():
    ap = cli.build_parser()
    a = ap.parse_args(["--batch", "x.json", "--reverb", "room", "--no-check", "--out", "o"])
    assert cli.explicit_options(ap, a) == {"reverb": "room", "check": False, "out": "o"}


# --------------------------------------------------------------------------- running a sheet

@pytest.fixture()
def batch(tmp_path, monkeypatch):
    """--batch with a fake run_request. Returns (run, calls)."""
    calls = []

    def fake_run(a):
        calls.append(a)
        if a.text == "fails":
            raise cli.ServerError("server said 400: nope", 2)
        if a.text == "rejected":
            return {"res": {"id": "R0", "takes": [{"ref": "R0:1", "rejected": True, "fails": ["clipped"]}]},
                    "kept": [], "stack": None, "vari": None, "delivered": []}
        rid = f"R{len(calls)}"
        wav = a.out / f"X_{a.text.replace(' ', '')}_v{len(calls):02d}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"RIFF")
        take = {"ref": f"{rid}:7", "seed": 7, "rank": 1, "rank_score": 0.7, "clap_cos": 0.4,
                "aes": {"PQ": 7.0}, "warnings": [], "paths": {"png": "p.png"}}
        return {"res": {"id": rid, "takes": [take], "prompt": a.text}, "kept": [take], "stack": None,
                "vari": None, "delivered": [wav]}

    monkeypatch.setattr(cli, "run_request", fake_run)
    monkeypatch.setattr(cli, "ensure_server", lambda *a, **k: None)

    def run(data, *extra):
        p = sheet(tmp_path, data)
        ap = cli.build_parser()
        a = ap.parse_args(["--batch", str(p), "--out", str(tmp_path / "out"), *extra])
        status = cli.run_batch(ap, a)
        path = tmp_path / "out" / "film.summary.json"
        return status, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    return run, calls


def test_a_batch_delivers_records_and_resumes(batch, capsys):
    run, calls = batch
    cues = [{"id": "anchor", "text": "glitch"}, {"id": "fam", "text": "glitch burst", "like": "@anchor", "n": 8}]
    status, summary = run(cues)
    assert status == 0 and [c.text for c in calls] == ["glitch", "glitch burst"]
    assert calls[1].like == "R1:7"  # the best take of the anchor cue
    a = summary["cues"]["anchor"]
    assert a["status"] == "delivered" and a["best_ref"] == "R1:7" and a["takes"][0]["pq"] == 7.0
    assert summary["cues"]["fam"]["like"] == "R1:7"

    status, _ = run(cues)  # everything delivered: nothing is made again
    assert status == 0 and len(calls) == 2 and "done before" in capsys.readouterr().out

    cues[1]["n"] = 6  # a changed cue is made again, still like the anchor
    run(cues)
    assert [c.text for c in calls[2:]] == ["glitch burst"] and calls[2].like == "R1:7"
    run(cues, "--redo", "anchor")  # --redo forces one
    assert [c.text for c in calls[3:]] == ["glitch"]


def test_a_deleted_delivery_is_made_again(batch, tmp_path):
    run, calls = batch
    _, summary = run([{"id": "a", "text": "x"}])
    Path(summary["cues"]["a"]["delivered"][0]).unlink()
    run([{"id": "a", "text": "x"}])
    assert len(calls) == 2


def test_a_failed_cue_doesnt_stop_the_batch_and_is_retried(batch):
    run, calls = batch
    cues = [{"id": "a", "text": "fails"}, {"id": "b", "text": "rejected"}, {"id": "c", "text": "fine"},
            {"id": "d", "text": "fam", "like": "@b"}]
    status, summary = run(cues)
    assert status == 3 and [c.text for c in calls] == ["fails", "rejected", "fine"]
    s = summary["cues"]
    assert s["a"]["status"] == "failed" and "400" in s["a"]["error"]
    assert s["b"]["status"] == "no keepers" and s["c"]["status"] == "delivered"
    assert s["d"]["status"] == "failed" and "no kept take" in s["d"]["error"]
    run(cues)  # only what didn't deliver runs again
    assert [c.text for c in calls[3:]] == ["fails", "rejected"]


def test_a_lost_server_stops_the_batch(batch, monkeypatch):
    run, calls = batch

    def gone(a):
        raise cli.ServerError("lost the server", 1)
    monkeypatch.setattr(cli, "run_request", gone)
    status, summary = run([{"id": "a", "text": "x"}, {"id": "b", "text": "y"}])
    assert status == 1 and list(summary["cues"]) == ["a"]


def test_a_bad_sheet_or_redo_is_refused(batch):
    run, calls = batch
    assert run([{"text": "a", "volume": 1}])[0] == 2
    assert run([{"id": "a", "text": "x"}], "--redo", "zzz")[0] == 2
    assert not calls


def test_a_dry_run_makes_nothing(batch, monkeypatch, capsys):
    run, calls = batch
    monkeypatch.setattr(cli, "server_up", lambda: False)
    status, summary = run([{"text": "door slam"}], "--dry-run")
    assert status == 0 and not calls and summary is None and "to make" in capsys.readouterr().out


# --------------------------------------------------------------------------- starting the server

@pytest.fixture()
def down(monkeypatch):
    """No server and none starting, on Windows. Returns the commands run."""
    ran = []
    monkeypatch.setattr(cli, "server_up", lambda: False)
    monkeypatch.setattr(cli, "server_starting", lambda: False)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return ran


def test_the_server_is_started_in_its_own_window_when_down(down, monkeypatch):
    up = iter([False, True])
    monkeypatch.setattr(cli, "server_up", lambda: next(up))
    monkeypatch.setattr(cli, "gpu_used_mb", lambda: 1500)
    cli.ensure_server()
    assert down == [["cmd", "/c", str(cli.REPO / "start.cmd"), "--no-browser"]]


def test_the_server_isnt_started_while_another_program_holds_the_gpu(down, monkeypatch):
    monkeypatch.setattr(cli, "gpu_used_mb", lambda: 12000)
    with pytest.raises(cli.ServerError, match="11.7 GB"):
        cli.ensure_server()
    assert not down


def test_no_start_only_waits(down, monkeypatch):
    monkeypatch.setattr(cli, "wait_for_server", lambda s: False)
    with pytest.raises(cli.ServerError, match="start.cmd"):
        cli.ensure_server(start=False)
    assert not down


def test_a_server_already_starting_is_waited_for_not_started_again(down, monkeypatch):
    monkeypatch.setattr(cli, "server_starting", lambda: True)
    waited = []
    monkeypatch.setattr(cli, "wait_for_server", lambda s: waited.append(s) or True)
    cli.ensure_server(5)
    assert waited == [cli.START_WAIT] and not down


def test_a_start_that_dies_is_reported_without_waiting_out_the_clock(down, monkeypatch):
    monkeypatch.setattr(cli, "gpu_used_mb", lambda: None)
    clock = iter(range(0, 10000, 10))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    with pytest.raises(cli.ServerError, match="didn't come up"):
        cli.ensure_server()
    assert len(down) == 1


def test_server_starting_reads_the_servers_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUTPUT", tmp_path)
    assert not cli.server_starting()  # no lock file
    (tmp_path / ".server.lock").write_text("")
    assert not cli.server_starting()  # nobody holds it
