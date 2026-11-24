"""``molt`` 无参 TUI 入口与 ``molt tui`` 子命令的行为。

TUI 的启动只做两件事：判断是否该进 TUI、找到可执行文件后 ``os.execv``。
本文件用 monkeypatch 覆盖 TTY 与二进制探测，不依赖真实终端或 molt-tui。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from molt import cli


class _FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _record_execv(monkeypatch, calls: list[list[str]]) -> None:
    def fake_execv(path: str, argv: list[str]) -> None:
        calls.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr(cli.os, "execv", fake_execv)


def test_no_args_non_tty_prints_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_tui_wanted", lambda: False)
    assert cli.main([]) == 0
    assert "usage:" in capsys.readouterr().out


def test_no_args_tty_missing_binary_prints_help(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MOLT_TUI_BIN", raising=False)
    monkeypatch.setattr(cli, "_tui_wanted", lambda: True)
    monkeypatch.setattr(cli, "_tui_bin", lambda: None)
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    assert "usage: molt" in captured.out
    assert captured.err == ""


def test_no_args_tty_execs_binary(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_tui_wanted", lambda: True)
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    _record_execv(monkeypatch, calls)
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 0
    assert calls == [["/tmp/molt-tui"]]


def test_tui_subcommand_forwards_args(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    _record_execv(monkeypatch, calls)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["tui", "--mock", "--selftest"])
    assert excinfo.value.code == 0
    assert calls == [["/tmp/molt-tui", "--mock", "--selftest"]]


def test_tui_subcommand_missing_binary_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_tui_bin", lambda: None)
    assert cli.main(["tui"]) == 2
    assert "MOLT_TUI_BIN" in capsys.readouterr().err


def test_tui_subcommand_ignores_tty_state(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("MOLT_NO_TUI", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStream(False))
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(False))
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    _record_execv(monkeypatch, calls)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["tui", "--selftest"])
    assert excinfo.value.code == 0
    assert calls == [["/tmp/molt-tui", "--selftest"]]


def test_tui_subcommand_respects_molt_no_tui(monkeypatch, capsys) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("MOLT_NO_TUI", "1")
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    _record_execv(monkeypatch, calls)
    assert cli.main(["tui"]) == 2
    assert calls == []
    assert "MOLT_NO_TUI" in capsys.readouterr().err


def test_molt_no_tui_env_disables(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _FakeStream(True))
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(True))
    monkeypatch.setenv("MOLT_NO_TUI", "1")
    assert cli._tui_wanted() is False


def test_tui_wanted_requires_both_tty(monkeypatch) -> None:
    monkeypatch.delenv("MOLT_NO_TUI", raising=False)
    for stdin_tty, stdout_tty, expected in (
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStream(stdin_tty))
        monkeypatch.setattr(cli.sys, "stdout", _FakeStream(stdout_tty))
        assert cli._tui_wanted() is expected, (stdin_tty, stdout_tty)


def test_tui_bin_prefers_env_and_requires_executable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MOLT_TUI_BIN", raising=False)
    executable = tmp_path / "molt-tui"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setenv("MOLT_TUI_BIN", str(executable))
    assert cli._tui_bin() == os.path.abspath(executable)

    not_executable = tmp_path / "molt-tui-noexec"
    not_executable.write_text("#!/bin/sh\nexit 0\n")
    not_executable.chmod(0o644)
    monkeypatch.setenv("MOLT_TUI_BIN", str(not_executable))
    assert cli._tui_bin() is None


@pytest.mark.parametrize("invalid", ["", ".", "missing"])
def test_invalid_override_keeps_diagnostic(monkeypatch, tmp_path: Path, capsys, invalid: str) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOLT_TUI_BIN", invalid)
    assert cli._tui_bin() is None

    monkeypatch.setattr(cli, "_tui_wanted", lambda: True)
    assert cli.main([]) == 2
    assert f"MOLT_TUI_BIN does not name an executable file: {invalid!r}" in capsys.readouterr().err

    assert cli.main(["tui"]) == 2
    assert "MOLT_TUI_BIN" in capsys.readouterr().err


def test_execv_oserror_exits_2(monkeypatch, capsys) -> None:
    def failing_execv(path: str, argv: list[str]) -> None:
        raise OSError("exec failed")

    monkeypatch.setattr(cli, "_tui_wanted", lambda: True)
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    monkeypatch.setattr(cli.os, "execv", failing_execv)
    assert cli.main([]) == 2
    assert "error: exec failed" in capsys.readouterr().err

    assert cli.main(["tui", "--mock"]) == 2
    assert "error: exec failed" in capsys.readouterr().err


@pytest.mark.parametrize(("system", "machine"), [("darwin", "x86_64"), ("linux", "aarch64")])
def test_tui_requires_linux_x64(monkeypatch, capsys, system: str, machine: str) -> None:
    monkeypatch.setattr(cli, "_tui_wanted", lambda: True)
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    monkeypatch.setattr(cli.sys, "platform", system)
    monkeypatch.setattr(cli.platform, "machine", lambda: machine)

    assert cli._tui_platform_ok() is False
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    assert "usage: molt" in captured.out
    assert captured.err == ""

    assert cli.main(["tui"]) == 2
    assert "Linux x86_64" in capsys.readouterr().err


def test_doctor_still_runs_off_platform(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.main(["doctor", "--deps"]) == 0


def test_tui_help_is_forwarded(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_tui_bin", lambda: "/tmp/molt-tui")
    _record_execv(monkeypatch, calls)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["tui", "--help"])
    assert excinfo.value.code == 0
    assert calls == [["/tmp/molt-tui", "--help"]]


def test_non_tui_unknown_arguments_still_error(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["current", "--nope"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_help_still_works(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_subcommand_help_still_works(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["current", "--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_tui_bin_uses_path_when_env_absent(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "molt-tui"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.delenv("MOLT_TUI_BIN", raising=False)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: str(executable) if name == "molt-tui" else None,
    )
    assert cli._tui_bin() == str(executable)


def test_tui_bin_rejects_a_which_result_that_is_not_a_file(monkeypatch) -> None:
    monkeypatch.delenv("MOLT_TUI_BIN", raising=False)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/opt/bin" if name == "molt-tui" else None,
    )
    assert cli._tui_bin() is None
