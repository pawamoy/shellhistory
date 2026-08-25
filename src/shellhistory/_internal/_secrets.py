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

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_CANARY = "shellhistory-gitleaks-canary-4e39b23a"

_CONFIG = r"""
title = "shellhistory"

[extend]
useDefault = true

[[rules]]
id = "shellhistory-scanner-canary"
description = "Internal scanner health check"
regex = '''shellhistory-gitleaks-canary-4e39b23a'''

[[rules]]
id = "shell-sensitive-option-quoted"
description = "Sensitive value passed to a long command-line option"
regex = '''(?i)--(?:password|passwd|passphrase|token|api[-_]?key|secret|client[-_]?secret)(?:=|[ \t]+)["']([^"'\r\n]{3,})["']'''
secretGroup = 1
keywords = ["password", "passwd", "passphrase", "token", "api-key", "api_key", "secret"]

    [[rules.allowlists]]
    regexTarget = "secret"
    regexes = ['''^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$''', '''^\[REDACTED(?::[^]]+)?\]$''']

[[rules]]
id = "shell-sensitive-option"
description = "Sensitive value passed to a long command-line option"
regex = '''(?i)--(?:password|passwd|passphrase|token|api[-_]?key|secret|client[-_]?secret)(?:=|[ \t]+)([^"' \t\r\n;|&]{3,})'''
secretGroup = 1
keywords = ["password", "passwd", "passphrase", "token", "api-key", "api_key", "secret"]

    [[rules.allowlists]]
    regexTarget = "secret"
    regexes = ['''^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$''', '''^\[REDACTED(?::[^]]+)?\]$''']

[[rules]]
id = "shell-authorization-header"
description = "Credential in an HTTP authorization or API-key header"
regex = '''(?i)(?:authorization[ \t]*:[ \t]*(?:bearer|basic)[ \t]+|x-api-key[ \t]*:[ \t]*)([A-Za-z0-9_./+~=-]{6,})'''
secretGroup = 1
keywords = ["authorization", "x-api-key"]

[[rules]]
id = "shell-url-password"
description = "Password embedded in a URL"
regex = '''(?i)[a-z][a-z0-9+.-]*://[^/@:"' \t\r\n]+:([^/@"' \t\r\n]+)@'''
secretGroup = 1

[[rules]]
id = "shell-basic-auth-password"
description = "Password passed through a user:password option"
regex = '''(?i)(?:-u|--user)(?:=|[ \t]+)["']?[^:"' \t\r\n]+:([^"' \t\r\n]+)'''
secretGroup = 1
keywords = ["-u", "--user"]

[[rules]]
id = "shell-password-short-option"
description = "Password passed to a command-specific short option"
regex = '''(?i)(?:mysql|mariadb|sshpass|docker[ \t]+login|redis-cli)(?:[^\r\n]*[ \t])(?:-p|-a)(?:=|[ \t]+)?["']?([^"' \t\r\n-][^"' \t\r\n]*)'''
secretGroup = 1
keywords = ["mysql", "mariadb", "sshpass", "docker", "redis-cli"]
""".strip()

_SCAN_TABLE = """
CREATE TABLE IF NOT EXISTS secret_scans (
    history_id INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    scanner VARCHAR NOT NULL,
    scanned_at DATETIME NOT NULL,
    rules TEXT NOT NULL,
    PRIMARY KEY (history_id),
    FOREIGN KEY(history_id) REFERENCES history (id) ON DELETE CASCADE
)
"""

_STAGE_TABLE = """
CREATE TEMP TABLE secret_scan_stage (
    history_id INTEGER NOT NULL PRIMARY KEY,
    command_mac BLOB NOT NULL,
    redacted TEXT,
    status VARCHAR NOT NULL,
    rules TEXT NOT NULL,
    new_redaction INTEGER NOT NULL
)
"""


class _SecretsError(RuntimeError):
    pass


class _CrossCommandFindingError(_SecretsError):
    """A batched finding cannot be safely attributed to one history row."""


@dataclass(frozen=True)
class _Row:
    id: int
    cmd: str
    status: str | None = None
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RowScan:
    row: _Row
    redacted: str
    rules: tuple[str, ...]
    findings: int


@dataclass(frozen=True)
class _ReviewDecision:
    """The action chosen for one finding during interactive review."""

    status: str
    redacted: str | None = None


@dataclass(frozen=True)
class _ScanSummary:
    scanned: int
    flagged: int
    findings: int
    redacted: int
    rule_counts: dict[str, int]
    kept: int = 0
    skipped: int = 0


def _batches(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    rescan: bool,
) -> Iterator[list[_Row]]:
    last_id = 0
    condition = "" if rescan else "AND s.history_id IS NULL"
    while True:
        rows = connection.execute(
            f"""SELECT h.id, h.cmd, s.status, s.rules
                FROM history h
                LEFT JOIN secret_scans s ON s.history_id = h.id
                WHERE h.id > ? AND h.cmd IS NOT NULL {condition}
                ORDER BY h.id LIMIT ?""",  # noqa: S608 - condition is selected above, never user input
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            return
        batch = []
        for row_id, cmd, status, serialized_rules in rows:
            try:
                rules = tuple(json.loads(serialized_rules)) if serialized_rules else ()
            except (TypeError, json.JSONDecodeError):
                rules = ()
            batch.append(_Row(row_id, cmd, status, rules))
        yield batch
        last_id = batch[-1].id


def _payload(rows: Sequence[_Row]) -> tuple[str, list[int | None]]:
    """Join rows for one Gitleaks process and map its one-based line numbers back to rows."""
    parts: list[str] = [_CANARY, "\n"]
    owners: list[int | None] = [None]
    for row in rows:
        parts.extend((row.cmd, "\n\0\n"))
        owners.extend([row.id] * (row.cmd.count("\n") + 1))
        owners.append(None)
    return "".join(parts), owners


def _run_gitleaks(
    rows: Sequence[_Row],
    *,
    executable: str,
    timeout: int,
) -> list[dict[str, Any]]:
    payload, _ = _payload(rows)
    env = os.environ.copy()
    env["GITLEAKS_CONFIG_TOML"] = _CONFIG
    try:
        process = subprocess.run(  # noqa: S603 - the executable is an explicit CLI option
            [
                executable,
                "stdin",
                "--no-banner",
                "--no-color",
                "--log-level",
                "error",
                "--report-format",
                "json",
                "--report-path",
                "-",
                "--exit-code",
                "0",
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise _SecretsError(f"{executable}: Gitleaks executable not found") from error
    except subprocess.TimeoutExpired as error:
        raise _SecretsError(f"Gitleaks timed out after {timeout} seconds") from error
    if process.returncode != 0:
        # Gitleaks may include raw findings in diagnostics. Do not copy stderr
        # into our exception or terminal output.
        raise _SecretsError(f"Gitleaks failed with exit status {process.returncode}")
    try:
        findings = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise _SecretsError("Gitleaks did not return a valid JSON report") from error
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise _SecretsError("Gitleaks returned an unexpected JSON report")
    canaries = [item for item in findings if item.get("RuleID") == "shellhistory-scanner-canary"]
    if not canaries:
        raise _SecretsError("Gitleaks self-test failed; the scanner or its configuration did not detect the canary")
    return [item for item in findings if item.get("RuleID") != "shellhistory-scanner-canary"]


def _gitleaks_version(executable: str, *, timeout: int) -> str:
    try:
        process = subprocess.run(  # noqa: S603 - the executable is an explicit CLI option
            [executable, "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise _SecretsError(f"{executable}: Gitleaks executable not found") from error
    except subprocess.TimeoutExpired as error:
        raise _SecretsError(f"Gitleaks version check timed out after {timeout} seconds") from error
    if process.returncode != 0:
        raise _SecretsError(f"Gitleaks version check failed with exit status {process.returncode}")
    version = process.stdout.strip().splitlines()[0] if process.stdout.strip() else "unknown"
    return f"gitleaks {version[:100]}"


def _owner(finding: dict[str, Any], rows: Sequence[_Row], owners: Sequence[int | None]) -> _Row:
    try:
        start_line = int(finding["StartLine"])
        end_line = int(finding.get("EndLine", start_line))
        start_owner = owners[start_line - 1]
        end_owner = owners[end_line - 1]
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise _SecretsError("Gitleaks returned a finding with an invalid location") from error
    if start_owner is None or start_owner != end_owner:
        raise _CrossCommandFindingError("Gitleaks returned a finding that crossed command boundaries")
    return next(row for row in rows if row.id == start_owner)


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    spans = []
    start = 0
    while (index := text.find(needle, start)) >= 0:
        spans.append((index, index + len(needle)))
        start = index + len(needle)
    return spans


def _redact(text: str, spans: Sequence[tuple[int, int]]) -> str:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + "[REDACTED]" + text[end:]
    return text


def _scan_batch(
    rows: Sequence[_Row],
    *,
    executable: str,
    timeout: int,
) -> list[_RowScan]:
    findings = _run_gitleaks(rows, executable=executable, timeout=timeout)
    _, owners = _payload(rows)
    by_id: dict[int, list[dict[str, Any]]] = {row.id: [] for row in rows}
    try:
        for finding in findings:
            by_id[_owner(finding, rows, owners).id].append(finding)
    except _CrossCommandFindingError:
        if len(rows) == 1:
            raise
        # A rule can unexpectedly include our record separator (for example,
        # after a Gitleaks rule-set change).  Never guess which row owns that
        # finding: split the batch until the offending commands are isolated.
        middle = len(rows) // 2
        return _scan_batch(rows[:middle], executable=executable, timeout=timeout) + _scan_batch(
            rows[middle:],
            executable=executable,
            timeout=timeout,
        )

    scans = []
    for row in rows:
        spans: list[tuple[int, int]] = []
        rules = set()
        for finding in by_id[row.id]:
            rule = finding.get("RuleID")
            if isinstance(rule, str) and rule:
                rules.add(rule)
            secret = finding.get("Secret")
            match = finding.get("Match")
            candidate = secret if isinstance(secret, str) and secret else match
            located = _spans(row.cmd, candidate) if isinstance(candidate, str) else []
            if not located:
                # A scanner location can cover a decoded or otherwise transformed
                # value. Decoding is disabled here, but fail closed if a future
                # Gitleaks version changes its output model.
                located = [(0, len(row.cmd))]
            spans.extend(located)
        scans.append(
            _RowScan(
                row=row,
                redacted=_redact(row.cmd, spans),
                rules=tuple(sorted(rules)),
                findings=len(by_id[row.id]),
            ),
        )
    return scans


def _stage(
    connection: sqlite3.Connection,
    scans: Sequence[_RowScan],
    *,
    key: bytes,
    decisions: dict[int, _ReviewDecision] | None = None,
) -> None:
    """Stage scan results, optionally using a per-finding review decision.

    Kept findings are deliberately distinct from ``clean`` so intentional
    exceptions remain visible while preventing another scan. Skipped findings
    are omitted entirely, leaving any existing scan state unchanged.
    """
    decisions = decisions or {}
    rows = []
    for scan in scans:
        if scan.findings:
            decision = decisions.get(scan.row.id, _ReviewDecision("redacted", scan.redacted))
            if decision.status == "skip":
                continue
            status = decision.status
            command = decision.redacted if status == "redacted" else None
        else:
            status = "redacted" if scan.row.status == "redacted" else "clean"
            command = None
        rows.append(
            (
                scan.row.id,
                hmac.digest(key, scan.row.cmd.encode(), "sha256"),
                command,
                status,
                json.dumps(scan.rules or scan.row.rules, separators=(",", ":")),
                int(status == "redacted" and bool(scan.findings)),
            ),
        )
    with connection:
        connection.executemany(
            """INSERT INTO secret_scan_stage
               (history_id, command_mac, redacted, status, rules, new_redaction) VALUES (?,?,?,?,?,?)""",
            rows,
        )


def _review_decision(scan: _RowScan, *, result: bool | str | None) -> _ReviewDecision:
    """Normalize legacy boolean and interactive review results."""
    if result is True:
        return _ReviewDecision("redacted", scan.redacted)
    if result is False:
        return _ReviewDecision("kept")
    if result is None:
        return _ReviewDecision("skip")
    if isinstance(result, str):
        return _ReviewDecision("redacted", result)
    raise _SecretsError("review callback must return apply, redact, keep, or skip")


def _apply_staged(connection: sqlite3.Connection, *, key: bytes, scanner: str) -> int:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")  # noqa: DTZ005 - database uses local naive time
    connection.execute("PRAGMA secure_delete=ON")
    redacted = 0
    with connection:
        staged = connection.execute(
            "SELECT history_id, command_mac, redacted, status, rules, new_redaction FROM secret_scan_stage ORDER BY history_id",
        )
        for history_id, command_mac, command, status, rules, new_redaction in staged:
            current = connection.execute("SELECT cmd FROM history WHERE id = ?", (history_id,)).fetchone()
            current_mac = (
                hmac.digest(key, current[0].encode(), "sha256")
                if current is not None and isinstance(current[0], str)
                else b""
            )
            if not hmac.compare_digest(current_mac, command_mac):
                raise _SecretsError(f"history row {history_id} changed while it was being scanned")
            if new_redaction:
                cursor = connection.execute(
                    "UPDATE history SET cmd = ? WHERE id = ? AND cmd = ?",
                    (command, history_id, current[0]),
                )
                if cursor.rowcount != 1:
                    raise _SecretsError(f"history row {history_id} changed while it was being scanned")
                redacted += 1
            connection.execute(
                """INSERT OR REPLACE INTO secret_scans
                   (history_id, status, scanner, scanned_at, rules) VALUES (?,?,?,?,?)""",
                (history_id, status, scanner, timestamp, rules),
            )
    return redacted


def _purge_deleted_content(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("VACUUM")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as error:
        raise _SecretsError(
            "redactions were applied, but deleted-content cleanup failed; "
            "close other database users, then run VACUUM and PRAGMA wal_checkpoint(TRUNCATE)",
        ) from error
    if checkpoint and checkpoint[0]:
        raise _SecretsError(
            "redactions were applied, but the WAL could not be truncated; close other database users and run VACUUM",
        )


def _scan_database(
    db_path: str | Path,
    *,
    apply: bool = False,
    rescan: bool = False,
    batch_size: int = 500,
    executable: str = "gitleaks",
    timeout: int = 120,
    review: Callable[[_RowScan], bool | str | None] | None = None,
) -> _ScanSummary:
    """Scan a database and optionally review individual findings.

    When ``review`` is supplied, it receives every flagged command and returns
    ``True`` to apply the proposed redaction, a replacement command to redact
    manually, ``False`` to keep it, or ``None`` to skip it. Applied and kept
    outcomes are stored; skipped findings leave the database unchanged.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if timeout < 1:
        raise ValueError("timeout must be positive")
    path = Path(db_path)
    if not path.exists():
        raise _SecretsError(f"{path}: no such database")

    connection: sqlite3.Connection | None = None
    rule_counts: Counter[str] = Counter()
    scanned = flagged = findings = kept = skipped = 0
    scanner = "gitleaks unknown"
    stage_key = os.urandom(32)
    try:
        connection = sqlite3.connect(str(path), timeout=10)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(_SCAN_TABLE)
        connection.commit()
        if apply or review is not None:
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute(_STAGE_TABLE)
        for batch in _batches(connection, batch_size=batch_size, rescan=rescan):
            if not scanned:
                scanner = _gitleaks_version(executable, timeout=min(timeout, 10))
            batch_scans = _scan_batch(batch, executable=executable, timeout=timeout)
            decisions: dict[int, _ReviewDecision] = {}
            if review is not None:
                for scan in batch_scans:
                    if scan.findings:
                        decision = _review_decision(scan, result=review(scan))
                        decisions[scan.row.id] = decision
                        kept += int(decision.status == "kept")
                        skipped += int(decision.status == "skip")
            if apply or review is not None:
                _stage(connection, batch_scans, key=stage_key, decisions=decisions)
            scanned += len(batch_scans)
            for scan in batch_scans:
                if scan.findings:
                    flagged += 1
                    findings += scan.findings
                    rule_counts.update(scan.rules)
        redacted = _apply_staged(connection, key=stage_key, scanner=scanner) if apply or review is not None else 0
        if redacted:
            _purge_deleted_content(connection)
    except sqlite3.Error as error:
        raise _SecretsError(f"database operation failed: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    return _ScanSummary(scanned, flagged, findings, redacted, dict(sorted(rule_counts.items())), kept, skipped)
