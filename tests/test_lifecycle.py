r"""The server's life: only ever in its own visible window, stopped (never unloaded) by closing
that window or by stop.cmd, which also closes it.

    .venv\Scripts\python.exe -m pytest tests -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
spec = importlib.util.spec_from_file_location("sfx_cli_life", REPO / "scripts" / "sfx.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_the_server_refuses_to_start_outside_its_window(monkeypatch):
    import app
    monkeypatch.delenv(app.WINDOW_MARK, raising=False)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    with pytest.raises(SystemExit) as e:
        app.main()
    assert "start.cmd" in str(e.value)


def test_start_cmd_marks_only_the_window_it_opens():
    import app
    cmd = (REPO / "start.cmd").read_text(encoding="utf-8")
    assert f'set "{app.WINDOW_MARK}=1"' in cmd and "--here" not in cmd
    mark, launch = cmd.index(f'set "{app.WINDOW_MARK}=1"'), cmd.index('start "local-sounds server')
    assert mark < launch  # set just before the new console is opened, which inherits it


def test_stop_cmd_closes_the_window_it_stops():
    start = (REPO / "start.cmd").read_text(encoding="utf-8")
    stop = (REPO / "stop.cmd").read_text(encoding="utf-8")
    assert ".stop-requested" in stop and ".stop-requested" in start
    # The window clears a stale request before the server starts, and exits 0 (so a Windows
    # Terminal tab closes too) when stop.cmd asked for the stop.
    assert start.index('del "%HERE_DIR%output\\.stop-requested"') < start.index('"%HERE_DIR%server\\app.py"')
    after = start[start.index('if exist "%HERE_DIR%output\\.stop-requested"'):]
    assert after.index("exit 0") < after.index("closes in 60 seconds")
    assert "exit /b %FAILED%" in stop  # a caller can tell whether anything survived


def test_the_server_cant_be_partly_unloaded():
    import app
    paths = {getattr(r, "path", "") for r in app.app.routes}
    assert not any("unload" in p or p.endswith("/stop") for p in paths)


@pytest.mark.skipif(sys.platform != "win32", reason="the server's lock is msvcrt's on Windows")
def test_the_cli_sees_a_server_that_is_still_loading(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "OUTPUT", tmp_path)
    monkeypatch.setattr(cli, "OUTPUT", tmp_path)
    assert app._single_instance()
    try:
        assert cli.server_starting()
    finally:
        app._instance_lock.close()
        app._instance_lock = None
    assert not cli.server_starting()
