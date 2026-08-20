#!/usr/bin/env python3
"""Write a single history record straight into the database.

The shell runs this as a short-lived, detached process at the end of every
command, so it must start fast and must never make the shell wait: it uses
nothing but the standard library (importing SQLAlchemy alone costs more than
this whole process) and it is spawned in the background.

Usage:
    record.py START STOP UUID HOST USER TTY PARENTS SHELL LEVEL TYPE CODE PATH CMD

START and STOP are microseconds since the epoch. Every value is passed as a
separate argument and bound as a query parameter, so no amount of punctuation in
a command line can change the statement being run.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

FIELDS = (
    "start", "stop", "uuid", "host", "user", "tty", "parents",
    "shell", "level", "type", "code", "path", "cmd",
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
"""


def default_db_path():
    return os.environ.get("SHELLHISTORY_DB") or os.path.join(
        os.path.expanduser("~"), ".shellhistory", "db.sqlite3",
    )


def to_datetime(microseconds):
    """Render shell microseconds the way SQLAlchemy stores a DateTime in SQLite."""
    return datetime.fromtimestamp(int(microseconds) / 1000000.0).strftime("%Y-%m-%d %H:%M:%S.%f")


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def connect(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    # One writer per recorded command, any number of shells: WAL keeps writers
    # from blocking readers, and busy_timeout makes a writer that loses the race
    # wait its turn instead of raising SQLITE_BUSY and losing the command.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def session_id(connection, values):
    """Get, or create, the row describing the shell this command ran in."""
    key = (values["uuid"], values["host"], values["user"], values["tty"],
           values["shell"], to_int(values["level"]), values["parents"])
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


def record(values, db_path=None):
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


def save_unrecorded(values, error):
    """Never lose a command to a database problem: park it for a later import."""
    try:
        path = os.path.join(os.path.dirname(default_db_path()), "unrecorded.jsonl")
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"error": str(error), "record": values}) + "\n")
    except Exception:  # noqa: BLE001 - a failed fallback must still not break the shell
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != len(FIELDS):
        print("usage: record.py %s" % " ".join(f.upper() for f in FIELDS), file=sys.stderr)
        return 2

    values = dict(zip(FIELDS, argv))
    try:
        record(values)
    except Exception as error:  # noqa: BLE001 - the shell must never see a traceback
        save_unrecorded(values, error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
