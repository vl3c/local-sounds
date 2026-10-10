r"""Engines: finding their weights (here, or a sibling repository's copy of an optional one),
refusing to load without the GPU memory for it, and the optional Medium download.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def model_folder(path: Path, size: int = 10) -> Path:
    """A folder with every file an SA3 model folder must have."""
    (path / "t5gemma-b-b-ul2").mkdir(parents=True)
    (path / "model.safetensors").write_bytes(b"x" * size)
    (path / "model_config.json").write_text("{}", encoding="utf-8")
    (path / "t5gemma-b-b-ul2" / "model.safetensors").write_bytes(b"x")
    return path


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """The server module with its repository at tmp/code/local-sounds, next to sibling repos."""
    import app as server  # noqa: PLC0415
    repo = tmp_path / "code" / "local-sounds"
    (repo / ".venv-sa3" / "Scripts").mkdir(parents=True)
    (repo / ".venv-sa3" / "Scripts" / "python.exe").write_bytes(b"")
    monkeypatch.setattr(server, "REPO", repo)
    monkeypatch.setattr(server, "MODELS", repo / "models")
    return server


def test_an_engine_is_found_in_this_install(app):
    assert app.model_dir("sa3") is None and not app.engine_installed("sa3")
    model_folder(app.MODELS / "sa3-small-sfx")
    assert app.model_dir("sa3") == app.MODELS / "sa3-small-sfx" and app.engine_installed("sa3")


def test_medium_is_found_in_a_sibling_repository(app):
    assert app.model_dir("medium") is None
    music = model_folder(app.REPO.parent / "music-tool" / "models" / "sa3-medium")
    assert app.model_dir("medium") == music and app.engine_installed("medium")
    own = model_folder(app.MODELS / "sa3-medium")
    assert app.model_dir("medium") == own  # this install's own copy comes first


def test_small_sfx_is_never_borrowed_from_a_sibling(app):
    model_folder(app.REPO.parent / "other" / "models" / "sa3-small-sfx")
    assert app.model_dir("sa3") is None


def test_an_incomplete_or_different_checkpoint_doesnt_count(app):
    music = app.REPO.parent / "music-tool" / "models" / "sa3-medium"
    model_folder(music)
    (music / "t5gemma-b-b-ul2" / "model.safetensors").unlink()
    assert app.model_dir("medium") is None                              # no text encoder
    (music / "t5gemma-b-b-ul2" / "model.safetensors").write_bytes(b"x")
    (app.REPO / "models.lock.json").write_text(json.dumps(
        {"sa3-medium": {"files": [{"path": "model.safetensors", "bytes": 99}]}}), encoding="utf-8")
    assert app.model_dir("medium") is None                              # not the pinned revision's size
    (music / "model.safetensors").write_bytes(b"x" * 99)
    assert app.model_dir("medium") == music


def test_the_real_lock_pins_medium_like_its_manifest():
    lock = json.loads((REPO / "models.lock.json").read_text(encoding="utf-8"))
    entry = next(r for r in json.loads((REPO / "models.json").read_text(encoding="utf-8"))["repos"]
                 if r["name"] == "sa3-medium")
    assert lock["sa3-medium"]["revision"] == entry["revision"] and entry["on_request"]
    assert all(f["sha256"] for f in lock["sa3-medium"]["files"])


def test_a_missing_engine_says_how_to_get_it(app):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        app.Worker("medium")
    assert e.value.status_code == 503 and "--only sa3-medium" in e.value.detail


def test_an_engine_isnt_loaded_without_the_gpu_memory_for_it(app, monkeypatch):
    from fastapi import HTTPException
    model_folder(app.REPO.parent / "music-tool" / "models" / "sa3-medium")
    monkeypatch.setattr(app, "_gpu_free_gb", lambda: 5.2)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: pytest.fail("started without the memory"))
    with pytest.raises(HTTPException) as e:
        app.Worker("medium")
    assert e.value.status_code == 503 and "5.2 GB is free" in e.value.detail


def test_the_worker_is_told_which_model_and_where(app, monkeypatch):
    music = model_folder(app.REPO.parent / "music-tool" / "models" / "sa3-medium")
    monkeypatch.setattr(app, "_gpu_free_gb", lambda: 14.0)
    started = []

    def popen(cmd, **k):
        started.append(cmd)
        raise RuntimeError("stop here")
    monkeypatch.setattr(app.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError):
        app.Worker("medium")
    assert started[0][-3].endswith("sa3_worker.py") and started[0][-2:] == ["medium", str(music)]


def test_a_saved_default_whose_weights_are_gone_falls_back(app, monkeypatch, tmp_path):
    used = []

    class Stub:
        info = {"engine": "sa3", "model": "stub", "sample_rate": 44100, "max_duration": 120.0, "default_steps": 8,
                "features": []}

        def call(self, job):
            raise RuntimeError("generated nothing")
    monkeypatch.setattr(app, "worker", lambda name: used.append(name) or Stub())
    monkeypatch.setattr(app, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(app, "SETTINGS_FILE", tmp_path / "config.local.json")
    (tmp_path / "output").mkdir()
    app.save_settings(app.Settings(engine="medium"))
    with pytest.raises(Exception):
        app.generate(app.GenReq(text="sword hit", check=False))
    with pytest.raises(Exception):
        app.generate(app.GenReq(text="sword hit", engine="medium", check=False))
    assert used == ["sa3", "medium"]  # asked for by name: no fallback


def test_start_cmd_takes_medium():
    cmd = (REPO / "start.cmd").read_text(encoding="utf-8")
    assert '"%~1"=="medium"' in cmd


@pytest.mark.parametrize("args, expected", [
    ([], {"sa3-small-sfx", "clap", "aesthetics"}),
    (["--with-optional"], {"sa3-small-sfx", "clap", "aesthetics", "captioner"}),
    (["--only", "sa3-medium"], {"sa3-medium"}),
])
def test_medium_is_only_downloaded_when_asked_for(monkeypatch, args, expected):
    spec = importlib.util.spec_from_file_location("dl", REPO / "scripts" / "download_models.py")
    dl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl)
    seen = []
    monkeypatch.setattr(dl, "fetch", lambda e, lock, reuse, verify: seen.append(e["name"]) or True)
    monkeypatch.setattr(sys, "argv", ["download_models.py", "--verify", *args])
    assert dl.main() == 0 and set(seen) == expected
