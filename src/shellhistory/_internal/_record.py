#!/usr/bin/env python3
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
# Write a single history record straight into the database.
#
# The shell runs this as a short-lived, detached process at the end of every
# command, so it must start fast and must never make the shell wait: it uses
# nothing but the standard library (importing SQLAlchemy alone costs more than
# this whole process) and it is spawned in the background.
#
# Usage:
#     _record.py START STOP UUID HOST USER TTY PARENTS SHELL LEVEL TYPE CODE PATH CMD
#
# START and STOP are microseconds since the epoch. Every value is passed as a
# separate argument and bound as a query parameter, so no amount of punctuation in
# a command line can change the statement being run.

# This module deliberately stays on os.path rather than pathlib. It runs once
# per command, and importing pathlib measurably costs ~7ms on top of os and
# sqlite3 (17ms -> 24ms here): a 40% increase in the very startup time this
# module is written to keep small.
# ruff: noqa: PTH103, PTH111, PTH112, PTH118, PTH120, PTH123

import json
import os
import sqlite3
import sys
from datetime import datetime

FIELDS = (
    "start",
    "stop",
    "uuid",
    "host",
    "user",
    "tty",
    "parents",
    "shell",
    "level",
    "type",
    "code",
    "path",
    "cmd",
)

# Must stay in step with the models in db.py; tests/test_record.py compares the
# schema this produces against the one SQLAlchemy generates.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER NOT NULL,
    uuid VARCHAR,
    host VARCHAR,
    user VARCHAR,
    tty VARCHAR,
    shell VARCHAR,
    level INTEGER,
    parents TEXT,
    PRIMARY KEY (id),
    UNIQUE (uuid, host, user, tty, shell, level, parents)
);
CREATE INDEX IF NOT EXISTS ix_sessions_uuid ON sessions (uuid);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    start DATETIME,
    stop DATETIME,
    type VARCHAR,
    code INTEGER,
    path VARCHAR,
    cmd TEXT,
    PRIMARY KEY (id),
    UNIQUE (start, session_id),
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE INDEX IF NOT EXISTS ix_history_session_id ON history (session_id);
CREATE INDEX IF NOT EXISTS ix_history_start ON history (start);
CREATE TABLE IF NOT EXISTS secret_scans (
    history_id INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    scanner VARCHAR NOT NULL,
    scanned_at DATETIME NOT NULL,
    rules TEXT NOT NULL,
    PRIMARY KEY (history_id),
    FOREIGN KEY(history_id) REFERENCES history (id) ON DELETE CASCADE
);
"""


def default_db_path() -> str:
    """Return the database path, honouring $SHELLHISTORY_DB.

    Returns:
        The path of the SQLite database to write to.
    """
    return os.environ.get("SHELLHISTORY_DB") or os.path.join(
        os.path.expanduser("~"),
        ".shellhistory",
        "db.sqlite3",
    )


def to_datetime(microseconds: str) -> str:
    """Render shell microseconds the way SQLAlchemy stores a DateTime in SQLite.

    Parameters:
        microseconds: Microseconds since the epoch, as passed by the shell.

    Returns:
        The timestamp formatted the way SQLAlchemy writes a DateTime.
    """
    # Local time on purpose: it is what the reader and every chart assume.
    return datetime.fromtimestamp(int(microseconds) / 1000000.0).strftime("%Y-%m-%d %H:%M:%S.%f")  # noqa: DTZ006


def to_int(value: str | None) -> int | None:
    """Return the value as an integer, or None if it is not one.

    Parameters:
        value: The value to convert.

    Returns:
        The integer value, or None.
    """
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def connect(db_path: str) -> sqlite3.Connection:
    """Open the database, configured for many small concurrent writers.

    Parameters:
        db_path: The path of the database to open.

    Returns:
        An open connection.
    """
    connection = sqlite3.connect(db_path, timeout=10)
    # One writer per recorded command, any number of shells: WAL keeps writers
    # from blocking readers, and busy_timeout makes a writer that loses the race
    # wait its turn instead of raising SQLITE_BUSY and losing the command.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def session_id(connection: sqlite3.Connection, values: dict[str, str]) -> int:
    """Get, or create, the row describing the shell this command ran in.

    Parameters:
        connection: The open database connection.
        values: The record fields as passed by the shell.

    Returns:
        The primary key of the session row.
    """
    key = (
        values["uuid"],
        values["host"],
        values["user"],
        values["tty"],
        values["shell"],
        to_int(values["level"]),
        values["parents"],
    )
    connection.execute(
        "INSERT OR IGNORE INTO sessions (uuid, host, user, tty, shell, level, parents) VALUES (?,?,?,?,?,?,?)",
        key,
    )
    row = connection.execute(
        """SELECT id FROM sessions
           WHERE uuid IS ? AND host IS ? AND user IS ? AND tty IS ?
             AND shell IS ? AND level IS ? AND parents IS ?""",
        key,
    ).fetchone()
    return row[0]


def record(values: dict[str, str], db_path: str | None = None) -> None:
    """Write one record into the database.

    Parameters:
        values: The record fields as passed by the shell.
        db_path: The database to write to. Defaults to `default_db_path()`.
    """
    db_path = db_path or default_db_path()
    directory = os.path.dirname(db_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)

    connection = connect(db_path)
    try:
        connection.executescript(SCHEMA)
        with connection:
            connection.execute(
                """INSERT OR IGNORE INTO history (session_id, start, stop, type, code, path, cmd)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    session_id(connection, values),
                    to_datetime(values["start"]),
                    to_datetime(values["stop"]),
                    values["type"],
                    to_int(values["code"]),
                    values["path"],
                    values["cmd"],
                ),
            )
    finally:
        connection.close()


def save_unrecorded(values: dict[str, str], error: Exception) -> None:
    """Never lose a command to a database problem: park it for a later import.

    Parameters:
        values: The record fields as passed by the shell.
        error: The problem that stopped the record from being written.
    """
    try:
        path = os.path.join(os.path.dirname(default_db_path()), "unrecorded.jsonl")
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"error": str(error), "record": values}) + "\n")
    except Exception:  # noqa: BLE001, S110 - a failed fallback must still not break the shell
        pass


def main(argv: list[str] | None = None) -> int:
    """Write the record described by the command line arguments.

    Parameters:
        argv: The arguments to read. Defaults to `sys.argv[1:]`.

    Returns:
        An exit code.
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != len(FIELDS):
        usage = " ".join(field.upper() for field in FIELDS)
        print(f"usage: _record.py {usage}", file=sys.stderr)  # noqa: T201 - this is a command line tool
        return 2

    values = dict(zip(FIELDS, argv, strict=True))
    try:
        record(values)
    except Exception as error:  # noqa: BLE001 - the shell must never see a traceback
        save_unrecorded(values, error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
