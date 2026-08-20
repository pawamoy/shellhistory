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

"""Migration tooling.

Two kinds of migration live here, both one-way and both preserving the original:

* `migrate_schema` converts a database from the old single-table layout to the
  `sessions` + `history` split.
* `import_file` reads the legacy colon-delimited text history that the shell
  used to append to, so archived history files can still be loaded.

Neither is needed in normal operation: the shell now writes to the database
directly (see `record.py`).
"""

import shutil
import sqlite3
import sys
from base64 import b64decode
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from tqdm import tqdm

from shellhistory import db


# Legacy text format ----------------------------------------------------------
# A record was one line of colon-separated fields, with `path` and `parents`
# base64-encoded because they could contain the delimiter. Continuation lines of
# a multi-line command were prefixed with ';'.
class LegacyTuple(NamedTuple):
    """The thirteen colon-separated fields of a legacy record line."""

    start: str
    stop: str
    uuid: str
    parents: str
    host: str
    user: str
    tty: str
    path: str
    shell: str
    level: str
    type: str
    code: str
    cmd: str


class InsertionReport(NamedTuple):
    """How an import went."""

    inserted: int
    duplicates: int


def line_to_tuple(line: str) -> LegacyTuple:
    """Split one legacy record line into its fields.

    Parameters:
        line: The line, without its leading colon.

    Returns:
        The parsed fields.
    """
    return LegacyTuple(*line.split(":", 12))


def tuple_to_row(nt: LegacyTuple) -> tuple[tuple, dict[str, Any]]:
    """Turn one legacy tuple into (session key, history values).

    Parameters:
        nt: The parsed fields of a legacy record.

    Returns:
        The session key and the history column values.
    """
    # Local time, matching what the recorder and every chart assume.
    start = datetime.fromtimestamp(float(nt.start) / 1000000.0)  # noqa: DTZ006
    stop = datetime.fromtimestamp(float(nt.stop) / 1000000.0)  # noqa: DTZ006
    session_key = (
        nt.uuid,
        nt.host,
        nt.user,
        nt.tty,
        nt.shell,
        int(nt.level) if str(nt.level).strip().lstrip("-").isdigit() else None,
        b64decode(nt.parents).decode("utf-8", "replace").rstrip("\n"),
    )
    values = {
        "start": start,
        "stop": stop,
        "type": nt.type,
        "code": int(nt.code) if str(nt.code).strip().lstrip("-").isdigit() else None,
        "path": b64decode(nt.path).decode("utf-8", "replace").rstrip("\n"),
        "cmd": nt.cmd,
    }
    return session_key, values


def yield_legacy_blocks(path: str | Path, size: int = 512) -> Iterator[list]:
    """Yield blocks of (session key, values) parsed from a legacy history file.

    Parameters:
        path: The legacy history file to read.
        size: How many records to yield at a time.

    Yields:
        Blocks of parsed records.
    """
    block = []

    with Path(path).open(encoding="utf-8", errors="ignore") as stream:
        num_lines = sum(1 for _ in stream)

    with Path(path).open(encoding="utf-8", errors="ignore") as stream:
        current = None

        for i, line in enumerate(tqdm(stream, total=num_lines, unit="lines"), 1):
            if not line:
                continue
            first_char, text = line[0], line[1:].rstrip("\n")

            if first_char == ":":
                if current is not None:
                    block.append(current)
                    current = None
                try:
                    current = tuple_to_row(line_to_tuple(text))
                except Exception as error:  # noqa: BLE001 - one bad line must not stop the import
                    print(f"Line {i}: {error}\n{text}", file=sys.stderr)  # noqa: T201 - tooling output
                    current = None
            elif first_char == ";":
                if current is not None:
                    current[1]["cmd"] += "\n" + text
            else:
                print(  # noqa: T201 - tooling output
                    f"Line {i}: invalid line starting with {first_char}\n{text}",
                    file=sys.stderr,
                )

            if len(block) == size:
                yield block
                block = []

        if current is not None:
            block.append(current)

    if block:
        yield block


def import_file(path: str | Path) -> InsertionReport:
    """Load a legacy text history file into the database, skipping records already present."""
    db.create_tables()
    sqla_session = db.Session()
    cache = db.SessionCache(sqla_session)
    # `INSERT OR IGNORE` leans on UNIQUE(start, session_id) to skip what is
    # already stored, which is both faster and simpler than probing per record.
    statement = db.History.__table__.insert().prefix_with("OR IGNORE")
    read = 0
    before = sqla_session.query(db.History.id).count()

    for block in yield_legacy_blocks(path):
        rows = []
        for session_key, values in block:
            values["session_id"] = cache.id_for(*session_key)
            rows.append(values)
            read += 1
        if rows:
            sqla_session.execute(statement, rows)
        sqla_session.commit()

    inserted = sqla_session.query(db.History.id).count() - before
    return InsertionReport(inserted, read - inserted)


def import_history() -> InsertionReport:
    """Import the legacy history file named by $SHELLHISTORY_FILE.

    Returns:
        How many records were inserted and how many were already present.
    """
    if not Path(db.HISTFILE_PATH).exists():
        raise ValueError(f"{db.HISTFILE_PATH}: no such file")
    return import_file(db.HISTFILE_PATH)


# Schema migration ------------------------------------------------------------
NEW_SCHEMA = """
CREATE TABLE sessions (
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
CREATE INDEX ix_sessions_uuid ON sessions (uuid);
CREATE TABLE history (
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
CREATE INDEX ix_history_session_id ON history (session_id);
CREATE INDEX ix_history_start ON history (start);
"""

BATCH_SIZE = 10000

INSERT_HISTORY = "INSERT INTO history (id, session_id, start, stop, type, code, path, cmd) VALUES (?,?,?,?,?,?,?,?)"

LEGACY_COLUMNS = "id, start, stop, host, user, uuid, tty, parents, shell, level, type, code, path, cmd"


def migrate_schema(
    db_path: str | Path | None = None,
    *,
    backup: bool = True,
    verify: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    """Convert a legacy single-table database to the sessions + history split.

    Builds the new database beside the old one and only swaps them once every
    row has been read back and compared, so a failure at any point leaves the
    original untouched.

    Parameters:
        db_path: The database to convert. Defaults to the configured one.
        backup: Whether to keep the original as a timestamped copy.
        verify: Whether to compare every migrated row against the original.
        progress: Whether to show a progress bar.

    Returns:
        How many rows and sessions were written, and where the backup went.
    """
    db_path = Path(db_path or db.DB_PATH)
    if not db_path.exists():
        raise ValueError(f"{db_path}: no such file")

    target = db_path.with_suffix(db_path.suffix + ".migrating")
    if target.exists():
        target.unlink()

    # Close any pooled connection of our own first. It would hold the database
    # in WAL mode, and the checks and the cleanup below both depend on the
    # sidecar files reflecting reality rather than an open handle of ours.
    if db_path == Path(db.DB_PATH):
        db.engine.dispose()

    wal = Path(str(db_path) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise RuntimeError(
            f"{db_path} has uncommitted WAL content. Close every process using the database "
            "and try again, so nothing is lost when the file is replaced.",
        )

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    columns = {row[1] for row in source.execute("PRAGMA table_info(history)")}
    if "parents" not in columns:
        source.close()
        raise ValueError(f"{db_path} is not a legacy database (no `parents` column in `history`)")

    total = source.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    new = sqlite3.connect(str(target))
    new.executescript(NEW_SCHEMA)

    session_ids = {}
    # LEGACY_COLUMNS is a constant defined above, not user input.
    rows = source.execute(f"SELECT {LEGACY_COLUMNS} FROM history ORDER BY id")  # noqa: S608
    stream = tqdm(rows, total=total, unit="rows") if progress else rows

    batch = []
    for row_id, start, stop, host, user, uuid, tty, parents, shell, level, type_, code, path, cmd in stream:
        key = (uuid, host, user, tty, shell, level, parents)
        session_id = session_ids.get(key)
        if session_id is None:
            cursor = new.execute(
                "INSERT INTO sessions (uuid, host, user, tty, shell, level, parents) VALUES (?,?,?,?,?,?,?)",
                key,
            )
            session_id = cursor.lastrowid
            session_ids[key] = session_id
        batch.append((row_id, session_id, start, stop, type_, code, path, cmd))
        if len(batch) >= BATCH_SIZE:
            new.executemany(INSERT_HISTORY, batch)
            batch = []
    if batch:
        new.executemany(INSERT_HISTORY, batch)
    new.commit()

    migrated = new.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    sessions = new.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if migrated != total:
        new.close()
        source.close()
        target.unlink()
        raise RuntimeError(f"migration produced {migrated} rows from {total}; aborted")

    if verify:
        mismatched = _verify(db_path, target)
        if mismatched:
            new.close()
            source.close()
            target.unlink()
            raise RuntimeError(f"migration verification failed on {mismatched} rows; aborted")

    new.close()
    source.close()

    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - a local timestamp names the backup
        keep = db_path.with_suffix(db_path.suffix + f".legacy-{stamp}")
        shutil.copy2(str(db_path), str(keep))
    else:
        keep = None

    target.replace(db_path)

    # Any -wal/-shm still lying around belongs to the file we just replaced.
    # Left in place, SQLite could try to replay a foreign write-ahead log onto
    # the new database; the backup keeps whatever they contained.
    for sidecar in (Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        if sidecar.exists():
            sidecar.unlink()

    return {"rows": migrated, "sessions": sessions, "backup": str(keep) if keep else None}


def _verify(legacy_path: str | Path, new_path: str | Path) -> int:
    """Re-join the new tables and compare every row against the legacy one."""
    conn = sqlite3.connect(f"file:{new_path}?mode=ro", uri=True)
    conn.execute("ATTACH DATABASE ? AS legacy", (f"file:{legacy_path}?mode=ro",))
    mismatched = conn.execute(
        """
        SELECT COUNT(*) FROM legacy.history l
        LEFT JOIN (
            SELECT h.id AS id, h.start, h.stop, s.host, s.user, s.uuid, s.tty, s.parents,
                   s.shell, s.level, h.type, h.code, h.path, h.cmd
            FROM history h JOIN sessions s ON s.id = h.session_id
        ) n ON n.id = l.id
        WHERE n.id IS NULL
           OR NOT (n.start IS l.start AND n.stop IS l.stop AND n.host IS l.host
               AND n.user IS l.user AND n.uuid IS l.uuid AND n.tty IS l.tty
               AND n.parents IS l.parents AND n.shell IS l.shell AND n.level IS l.level
               AND n.type IS l.type AND n.code IS l.code AND n.path IS l.path AND n.cmd IS l.cmd)
        """,
    ).fetchone()[0]
    conn.close()
    return mismatched
