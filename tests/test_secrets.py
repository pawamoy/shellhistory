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

"""Tests for batched secret scanning and redaction."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from shellhistory._internal import _record, _secrets

TOKEN = "example-token-123456789"  # noqa: S105 - scanner fixture


def _write(db_path: Path, *, start: str, cmd: str) -> None:
    _record.record(
        {
            "start": start,
            "stop": str(int(start) + 100),
            "uuid": "uuid-1",
            "host": "host",
            "user": "user",
            "tty": "/dev/pts/1",
            "parents": "zsh",
            "shell": "/bin/zsh",
            "level": "1",
            "type": "command",
            "code": "0",
            "path": "/home/user/project",
            "cmd": cmd,
        },
        db_path=str(db_path),
    )


def _fake_gitleaks(monkeypatch: pytest.MonkeyPatch, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        if argv[-1] == "version":
            return subprocess.CompletedProcess(argv, 0, stdout="8.29.1\n", stderr="")
        canary = {
            "StartLine": 1,
            "EndLine": 1,
            "Secret": _secrets._CANARY,
            "Match": _secrets._CANARY,
            "RuleID": "shellhistory-scanner-canary",
        }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([canary, *findings]), stderr="")

    monkeypatch.setattr(_secrets.subprocess, "run", run)
    return calls


def _commands(db_path: Path) -> list[str]:
    connection = sqlite3.connect(db_path)
    try:
        return [row[0] for row in connection.execute("SELECT cmd FROM history ORDER BY id")]
    finally:
        connection.close()


def _states(db_path: Path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT status, rules FROM secret_scans ORDER BY history_id").fetchall()
    finally:
        connection.close()


def test_shell_rules_extend_the_gitleaks_defaults() -> None:
    config = tomllib.loads(_secrets._CONFIG)
    assert config["extend"]["useDefault"] is True
    assert "shell-authorization-header" in {rule["id"] for rule in config["rules"]}


def test_dry_run_batches_rows_without_changing_the_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd=f"curl -H 'Authorization: Bearer {TOKEN}' example.com")
    _write(db_path, start="1787000001000000", cmd="echo harmless")
    findings = [
        {
            "StartLine": 2,
            "EndLine": 2,
            "Secret": TOKEN,
            "Match": f"Authorization: Bearer {TOKEN}",
            "RuleID": "shell-authorization-header",
        },
    ]
    calls = _fake_gitleaks(monkeypatch, findings)

    report = _secrets._scan_database(db_path, batch_size=10)

    assert report.scanned == 2
    assert report.flagged == 1
    assert report.findings == 1
    assert report.redacted == 0
    assert _commands(db_path)[0].endswith(f"{TOKEN}' example.com")
    assert _states(db_path) == []
    assert len(calls) == 2
    assert TOKEN in calls[1]["input"]
    assert calls[1]["argv"][-4:] == ["--report-path", "-", "--exit-code", "0"]
    assert "shell-url-password" in calls[1]["env"]["GITLEAKS_CONFIG_TOML"]


def test_review_records_kept_and_redacted_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    first = f"tool --token={TOKEN}"
    second = f"tool --password={TOKEN}"
    _write(db_path, start="1787000000000000", cmd=first)
    _write(db_path, start="1787000001000000", cmd=second)
    findings = [
        {"StartLine": 2, "Secret": TOKEN, "Match": TOKEN, "RuleID": "shell-sensitive-option"},
        {"StartLine": 4, "Secret": TOKEN, "Match": TOKEN, "RuleID": "shell-sensitive-option"},
    ]
    _fake_gitleaks(monkeypatch, findings)
    reviewed = []

    def review(scan: _secrets._RowScan) -> bool:
        reviewed.append(scan)
        return scan.row.cmd == second

    report = _secrets._scan_database(db_path, batch_size=10, review=review)

    assert [scan.row.cmd for scan in reviewed] == [first, second]
    assert report.redacted == 1
    assert report.kept == 1
    assert _commands(db_path) == [first, second.replace(TOKEN, "[REDACTED]")]
    assert _states(db_path) == [
        ("kept", '["shell-sensitive-option"]'),
        ("redacted", '["shell-sensitive-option"]'),
    ]
    assert _secrets._scan_database(db_path).scanned == 0


def test_review_can_redact_manually_or_skip_without_recording_a_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history.sqlite3"
    first = f"tool --token={TOKEN}"
    second = f"tool --password={TOKEN}"
    _write(db_path, start="1787000000000000", cmd=first)
    _write(db_path, start="1787000001000000", cmd=second)
    findings = [
        {"StartLine": 2, "Secret": TOKEN, "Match": TOKEN, "RuleID": "shell-sensitive-option"},
        {"StartLine": 4, "Secret": TOKEN, "Match": TOKEN, "RuleID": "shell-sensitive-option"},
    ]
    _fake_gitleaks(monkeypatch, findings)

    def review(scan: _secrets._RowScan) -> str | None:
        return None if scan.row.cmd == first else "tool --password=custom-redaction"

    report = _secrets._scan_database(db_path, batch_size=10, review=review)

    assert report.redacted == 1
    assert report.skipped == 1
    assert _commands(db_path) == [first, "tool --password=custom-redaction"]
    assert _states(db_path) == [("redacted", '["shell-sensitive-option"]')]


def test_apply_redacts_secret_spans_and_records_scan_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history.sqlite3"
    command = f"curl -H 'Authorization: Bearer {TOKEN}' -d token={TOKEN} example.com"
    _write(db_path, start="1787000000000000", cmd=command)
    _write(db_path, start="1787000001000000", cmd="echo harmless")
    findings = [
        {
            "StartLine": 2,
            "EndLine": 2,
            "Secret": TOKEN,
            "Match": TOKEN,
            "RuleID": "shell-authorization-header",
        },
    ]
    _fake_gitleaks(monkeypatch, findings)

    report = _secrets._scan_database(db_path, apply=True, batch_size=10)

    assert report.redacted == 1
    assert _commands(db_path) == [command.replace(TOKEN, "[REDACTED]"), "echo harmless"]
    states = _states(db_path)
    assert states[0] == ("redacted", '["shell-authorization-header"]')
    assert states[1] == ("clean", "[]")
    assert TOKEN.encode() not in db_path.read_bytes()
    wal = Path(str(db_path) + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_scanned_rows_are_skipped_unless_rescan_is_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd=f"tool --token={TOKEN}")
    findings = [
        {
            "StartLine": 2,
            "Secret": TOKEN,
            "Match": TOKEN,
            "RuleID": "shell-sensitive-option",
        },
    ]
    calls = _fake_gitleaks(monkeypatch, findings)
    _secrets._scan_database(db_path, apply=True)

    assert _secrets._scan_database(db_path).scanned == 0
    assert len(calls) == 2

    calls.clear()
    _fake_gitleaks(monkeypatch, [])
    assert _secrets._scan_database(db_path, apply=True, rescan=True).scanned == 1
    assert _states(db_path)[0][0] == "redacted"


def test_multiline_commands_map_findings_to_the_right_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd="printf '%s\n' first second")
    _write(db_path, start="1787000001000000", cmd=f"tool \\\n+  --password {TOKEN}")
    findings = [
        {
            # The canary owns line 1, row one line 2, line 3 is the NUL
            # separator, and the second line of row two is therefore line 5.
            "StartLine": 5,
            "EndLine": 5,
            "Secret": TOKEN,
            "Match": TOKEN,
            "RuleID": "shell-sensitive-option",
        },
    ]
    _fake_gitleaks(monkeypatch, findings)

    report = _secrets._scan_database(db_path, apply=True, batch_size=2)

    assert report.flagged == 1
    assert _commands(db_path)[1] == "tool \\\n+  --password [REDACTED]"


def test_cross_command_finding_is_rescanned_in_smaller_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd="echo harmless")
    _write(db_path, start="1787000001000000", cmd=f"tool --token={TOKEN}")
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs.get("input"))
        if argv[-1] == "version":
            return subprocess.CompletedProcess(argv, 0, stdout="8.29.1\n", stderr="")
        canary = {
            "StartLine": 1,
            "EndLine": 1,
            "Secret": _secrets._CANARY,
            "Match": _secrets._CANARY,
            "RuleID": "shellhistory-scanner-canary",
        }
        payload = kwargs["input"]
        if "echo harmless" in payload and TOKEN in payload:
            # This points from the first command into the second one, so it
            # must never be attributed to either row in the original batch.
            findings = [{"StartLine": 2, "EndLine": 4, "Secret": TOKEN, "Match": TOKEN, "RuleID": "cross"}]
        elif TOKEN in payload:
            findings = [
                {
                    "StartLine": 2,
                    "EndLine": 2,
                    "Secret": TOKEN,
                    "Match": TOKEN,
                    "RuleID": "shell-sensitive-option",
                },
            ]
        else:
            findings = []
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([canary, *findings]), stderr="")

    monkeypatch.setattr(_secrets.subprocess, "run", run)

    report = _secrets._scan_database(db_path, apply=True, batch_size=2)

    assert report.flagged == 1
    assert _commands(db_path) == ["echo harmless", "tool --token=[REDACTED]"]
    # One batch plus one isolated scan per command.
    assert len([call for call in calls if call is not None]) == 3


def test_unlocatable_finding_redacts_the_whole_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd="encoded credential")
    _fake_gitleaks(
        monkeypatch,
        [{"StartLine": 2, "Secret": "decoded-value", "Match": "decoded-value", "RuleID": "decoded"}],
    )

    _secrets._scan_database(db_path, apply=True)

    assert _commands(db_path) == ["[REDACTED]"]


def test_invalid_scanner_output_does_not_modify_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd=f"tool --token={TOKEN}")

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr=TOKEN)

    monkeypatch.setattr(_secrets.subprocess, "run", run)
    with pytest.raises(_secrets._SecretsError, match="valid JSON"):
        _secrets._scan_database(db_path, apply=True)
    assert _commands(db_path) == [f"tool --token={TOKEN}"]
    assert _states(db_path) == []


def test_missing_canary_does_not_mark_rows_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd="echo harmless")

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(_secrets.subprocess, "run", run)
    with pytest.raises(_secrets._SecretsError, match="self-test failed"):
        _secrets._scan_database(db_path, apply=True)
    assert _states(db_path) == []


def test_apply_stages_batches_before_changing_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "history.sqlite3"
    _write(db_path, start="1787000000000000", cmd=f"tool --token={TOKEN}")
    _write(db_path, start="1787000001000000", cmd="echo harmless")
    scans = 0

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal scans
        if argv[-1] == "version":
            return subprocess.CompletedProcess(argv, 0, stdout="8.29.1\n", stderr="")
        scans += 1
        if scans == 2:
            return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")
        findings = [
            {
                "StartLine": 1,
                "EndLine": 1,
                "Secret": _secrets._CANARY,
                "Match": _secrets._CANARY,
                "RuleID": "shellhistory-scanner-canary",
            },
            {
                "StartLine": 2,
                "EndLine": 2,
                "Secret": TOKEN,
                "Match": TOKEN,
                "RuleID": "shell-sensitive-option",
            },
        ]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(findings), stderr="")

    monkeypatch.setattr(_secrets.subprocess, "run", run)
    with pytest.raises(_secrets._SecretsError, match="valid JSON"):
        _secrets._scan_database(db_path, apply=True, batch_size=1)
    assert _commands(db_path) == [f"tool --token={TOKEN}", "echo harmless"]
    assert _states(db_path) == []
