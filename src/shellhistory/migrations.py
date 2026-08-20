"""Migration tooling.

Two kinds of migration live here, both one-way and both preserving the original:

* `migrate_schema` converts a database from the old single-table layout to the
  `sessions` + `history` split.
* `import_file` reads the legacy colon-delimited text history that the shell
  used to append to, so archived history files can still be loaded.

Neither is needed in normal operation: the shell now writes to the database
directly (see `record.py`).
"""

import codecs
import os
import shutil
import sqlite3
import sys
from base64 import b64decode
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from . import db

# Legacy text format ----------------------------------------------------------
# A record was one line of colon-separated fields, with `path` and `parents`
# base64-encoded because they could contain the delimiter. Continuation lines of
# a multi-line command were prefixed with ';'.
LegacyTuple = namedtuple("LegacyTuple", "start stop uuid parents host user tty path shell level type code cmd")

InsertionReport = namedtuple("Report", "inserted duplicates")


def line_to_tuple(line):
    return LegacyTuple(*line.split(":", 12))


def tuple_to_row(nt):
    """Turn one legacy tuple into (session key, history values)."""
    start = datetime.fromtimestamp(float(nt.start) / 1000000.0)
    stop = datetime.fromtimestamp(float(nt.stop) / 1000000.0)
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


def yield_legacy_blocks(path, size=512):
    """Yield blocks of (session key, values) parsed from a legacy history file."""
    block = []

    with codecs.open(path, encoding="utf-8", errors="ignore") as stream:
        num_lines = sum(1 for _ in stream)

    with codecs.open(path, encoding="utf-8", errors="ignore") as stream:
        current = None

        for i, line in enumerate(tqdm(stream, total=num_lines, unit="lines"), 1):
            if not line:
                continue
            first_char, line = line[0], line[1:].rstrip("\n")

            if first_char == ":":
                if current is not None:
                    block.append(current)
                    current = None
                try:
                    current = tuple_to_row(line_to_tuple(line))
                except Exception as error:  # noqa: BLE001 - one bad line must not stop the import
                    print("Line %d: %s\n%s" % (i, error, line), file=sys.stderr)
                    current = None
            elif first_char == ";":
                if current is not None:
                    current[1]["cmd"] += "\n" + line
            else:
                print("Line %d: invalid line starting with %s\n%s" % (i, first_char, line), file=sys.stderr)

            if len(block) == size:
                yield block
                block = []

        if current is not None:
            block.append(current)

    if block:
        yield block


def import_file(path):
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


def import_history():
    if not Path(db.HISTFILE_PATH).exists():
        raise ValueError("%s: no such file" % db.HISTFILE_PATH)
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

INSERT_HISTORY = (
    "INSERT INTO history (id, session_id, start, stop, type, code, path, cmd) VALUES (?,?,?,?,?,?,?,?)"
)

LEGACY_COLUMNS = "id, start, stop, host, user, uuid, tty, parents, shell, level, type, code, path, cmd"


def migrate_schema(db_path=None, backup=True, verify=True, progress=True):
    """Convert a legacy single-table database to the sessions + history split.

    Builds the new database beside the old one and only swaps them once every
    row has been read back and compared, so a failure at any point leaves the
    original untouched.
    """
    db_path = Path(db_path or db.DB_PATH)
    if not db_path.exists():
        raise ValueError("%s: no such file" % db_path)

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
            "%s has uncommitted WAL content. Close every process using the database "
            "and try again, so nothing is lost when the file is replaced." % db_path,
        )

    source = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    columns = {row[1] for row in source.execute("PRAGMA table_info(history)")}
    if "parents" not in columns:
        source.close()
        raise ValueError("%s is not a legacy database (no `parents` column in `history`)" % db_path)

    total = source.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    new = sqlite3.connect(str(target))
    new.executescript(NEW_SCHEMA)

    session_ids = {}
    rows = source.execute("SELECT %s FROM history ORDER BY id" % LEGACY_COLUMNS)
    stream = tqdm(rows, total=total, unit="rows") if progress else rows

    batch = []
    for (row_id, start, stop, host, user, uuid, tty, parents, shell, level, type_, code, path, cmd) in stream:
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
        if len(batch) >= 10000:
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
        raise RuntimeError("migration produced %d rows from %d; aborted" % (migrated, total))

    if verify:
        mismatched = _verify(db_path, target)
        if mismatched:
            new.close()
            source.close()
            target.unlink()
            raise RuntimeError("migration verification failed on %d rows; aborted" % mismatched)

    new.close()
    source.close()

    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        keep = db_path.with_suffix(db_path.suffix + ".legacy-%s" % stamp)
        shutil.copy2(str(db_path), str(keep))
    else:
        keep = None

    os.replace(str(target), str(db_path))

    # Any -wal/-shm still lying around belongs to the file we just replaced.
    # Left in place, SQLite could try to replay a foreign write-ahead log onto
    # the new database; the backup keeps whatever they contained.
    for sidecar in (Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        if sidecar.exists():
            sidecar.unlink()

    return {"rows": migrated, "sessions": sessions, "backup": str(keep) if keep else None}


def _verify(legacy_path, new_path):
    """Re-join the new tables and compare every row against the legacy one."""
    conn = sqlite3.connect("file:%s?mode=ro" % new_path, uri=True)
    conn.execute("ATTACH DATABASE ? AS legacy", ("file:%s?mode=ro" % legacy_path,))
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
