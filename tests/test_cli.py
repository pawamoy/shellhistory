# SPDX-License-Identifier: ISC
#
# ISC License
#
# Copyright (c) 2020, Timothée Mazzucotelli and contributors
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""Tests for the CLI."""

from __future__ import annotations

from typing import Any

import pytest

from shellhistory import main
from shellhistory._internal import _secrets, debug


def test_main() -> None:
    """Basic CLI test."""
    assert main([]) == 0


def test_show_help(capsys: pytest.CaptureFixture) -> None:
    """Show help.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["-h"])
    captured = capsys.readouterr()
    assert "shellhistory" in captured.out


def test_show_version(capsys: pytest.CaptureFixture) -> None:
    """Show version.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["-V"])
    captured = capsys.readouterr()
    assert debug._get_version() in captured.out


def test_show_debug_info(capsys: pytest.CaptureFixture) -> None:
    """Show debug information.

    Parameters:
        capsys: Pytest fixture to capture output.
    """
    with pytest.raises(SystemExit):
        main(["--debug-info"])
    captured = capsys.readouterr().out.lower()
    assert "python" in captured
    assert "system" in captured
    assert "environment" in captured
    assert "packages" in captured


def test_secrets_subcommand_reviews_findings_by_default(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default scans pass an interactive review callback to the scanner."""
    called = {}

    def scan_database(*_args: Any, **kwargs: Any) -> _secrets._ScanSummary:
        called.update(kwargs)
        return _secrets._ScanSummary(10, 2, 3, 0, {"github-pat": 2})

    monkeypatch.setattr(_secrets, "_scan_database", scan_database)
    assert main(["secrets", "--batch-size", "25"]) == 0
    captured = capsys.readouterr()
    assert "found 3 findings in 2 commands" in captured.out
    assert called["apply"] is False
    assert called["batch_size"] == 25
    assert callable(called["review"])


def test_secrets_subcommand_prints_a_command_and_applies_the_suggestion(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive review displays the finding and returns the chosen action."""

    def scan_database(*_args: Any, **kwargs: Any) -> _secrets._ScanSummary:
        decision = kwargs["review"](
            _secrets._RowScan(
                row=_secrets._Row(42, "tool --token=example-token-123456789"),
                redacted="tool --token=[REDACTED]",
                rules=("shell-sensitive-option",),
                findings=1,
            ),
        )
        assert decision is True
        return _secrets._ScanSummary(1, 1, 1, 1, {"shell-sensitive-option": 1})

    monkeypatch.setattr(_secrets, "_scan_database", scan_database)
    monkeypatch.setattr("builtins.input", lambda _prompt: "apply")
    assert main(["secrets"]) == 0
    captured = capsys.readouterr()
    assert "Command 42: 1 finding(s) (shell-sensitive-option)" in captured.out
    assert "Recorded command:\ntool --token=example-token-123456789" in captured.out
    assert "Redacted command:\ntool --token=[REDACTED]" in captured.out


def test_secrets_subcommand_accepts_a_manual_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The redact action returns the replacement command written by the user."""

    def scan_database(*_args: Any, **kwargs: Any) -> _secrets._ScanSummary:
        decision = kwargs["review"](
            _secrets._RowScan(
                row=_secrets._Row(42, "tool --token=example-token-123456789"),
                redacted="tool --token=[REDACTED]",
                rules=("shell-sensitive-option",),
                findings=1,
            ),
        )
        assert decision == "tool --token=custom-redaction"
        return _secrets._ScanSummary(1, 1, 1, 1, {"shell-sensitive-option": 1})

    answers = iter(("redact", "tool --token=custom-redaction"))
    monkeypatch.setattr(_secrets, "_scan_database", scan_database)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert main(["secrets"]) == 0


def test_secrets_subcommand_reports_scanner_errors_without_secret_output(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scanner diagnostics can contain findings, so only show our safe exception."""

    def scan_database(*_args: Any, **_kwargs: Any) -> _secrets._ScanSummary:
        raise _secrets._SecretsError("Gitleaks failed with exit status 2")

    monkeypatch.setattr(_secrets, "_scan_database", scan_database)
    assert main(["secrets", "--apply"]) == 1
    captured = capsys.readouterr()
    assert "exit status 2" in captured.err
    assert "Secret" not in captured.err
