"""Retired competition command: reject all modes before runtime/state access.

Historical product behavior is not part of the current CLI. Frozen record validation,
semantic audit, provenance and transaction coverage remain in the dedicated suites.
"""
from __future__ import annotations

import pytest
from test_v1_compatibility import _snapshot, build_compatibility_fixture

from molt.cli import main


@pytest.mark.parametrize("flags", [['--scenario', 'demo', '--min-pass', '3'], ['--scenario', 'demo', '--min-pass', '4'], ['--scenario', 'svc-config', '--min-pass', '0'], []])
def test_retired_command_cannot_execute_or_modify_state(tmp_path, monkeypatch, capsys, flags):
    root = tmp_path / "existing-state"
    build_compatibility_fixture(root)
    before = _snapshot(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("retired command attempted kernel or external access")

    monkeypatch.setattr("molt.cli.KernelClient", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(['calibrate', *flags, "--state", str(root)])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert _snapshot(root) == before
    assert not (tmp_path / "new-state").exists()
    with pytest.raises(SystemExit) as exc:
        main(['calibrate', *flags, "--state", str(tmp_path / "new-state")])
    assert exc.value.code == 2
    assert not (tmp_path / "new-state").exists()
