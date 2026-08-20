"""Tests for the schema migration and the legacy text-file importer."""

import sqlite3
import subprocess
import sys
from base64 import b64encode

import pytest

LEGACY_SCHEMA = """
CREATE TABLE history (
    id INTEGER NOT NULL,
    start DATETIME, stop DATETIME, duration DATETIME,
    host VARCHAR, user VARCHAR, uuid VARCHAR, tty VARCHAR, parents TEXT,
    shell VARCHAR, level INTEGER, type VARCHAR, code INTEGER,
    path VARCHAR, cmd TEXT,
    PRIMARY KEY (id), UNIQUE (start, uuid)
);
"""

PARENTS = "/usr/bin/zsh\n/usr/lib/systemd/systemd --switched-root"


def make_legacy(path, rows):
    connection = sqlite3.connect(str(path))
    connection.executescript(LEGACY_SCHEMA)
    connection.executemany(
        """INSERT INTO history
           (id, start, stop, duration, host, user, uuid, tty, parents, shell, level, type, code, path, cmd)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    connection.commit()
    connection.close()


def legacy_row(row_id, start, uuid="u1", level=2, cmd="echo hello"):
    return (
        row_id, start, start, "1970-01-01 00:00:00.001000",
        "corsair", "pawamoy", uuid, "/dev/pts/3", PARENTS,
        "/usr/bin/zsh", level, "builtin", 0, "/home/pawamoy", cmd,
    )


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    make_legacy(path, [
        legacy_row(1, "2026-01-01 10:00:00.000000"),
        legacy_row(2, "2026-01-01 10:00:01.000000"),
        legacy_row(3, "2026-01-01 10:00:02.000000", level=3),
        legacy_row(4, "2026-01-01 10:00:03.000000", uuid="u2"),
    ])
    return path


def test_migration_splits_sessions_and_keeps_every_row(legacy_db):
    from shellhistory import migrations

    result = migrations.migrate_schema(db_path=legacy_db, progress=False)
    assert result["rows"] == 4
    # same uuid but a different level is a different shell; so is a different uuid
    assert result["sessions"] == 3

    connection = sqlite3.connect("file:%s?mode=ro" % legacy_db, uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3
        # the ancestry string is stored once per session, not once per command
        assert connection.execute("SELECT COUNT(DISTINCT parents) FROM sessions").fetchone()[0] == 1
    finally:
        connection.close()


def test_migration_preserves_row_ids_and_values(legacy_db):
    from shellhistory import migrations

    migrations.migrate_schema(db_path=legacy_db, progress=False)
    connection = sqlite3.connect("file:%s?mode=ro" % legacy_db, uri=True)
    try:
        rows = connection.execute(
            """SELECT h.id, h.start, s.uuid, s.level, s.parents, h.type, h.code, h.path, h.cmd
               FROM history h JOIN sessions s ON s.id = h.session_id ORDER BY h.id""",
        ).fetchall()
    finally:
        connection.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4]
    assert rows[0][1] == "2026-01-01 10:00:00.000000"
    assert rows[2][3] == 3
    assert rows[3][2] == "u2"
    assert all(r[4] == PARENTS for r in rows)


def test_migration_keeps_the_original_as_a_backup(legacy_db):
    from shellhistory import migrations

    result = migrations.migrate_schema(db_path=legacy_db, progress=False)
    backup = sqlite3.connect("file:%s?mode=ro" % result["backup"], uri=True)
    try:
        columns = {row[1] for row in backup.execute("PRAGMA table_info(history)")}
        assert "parents" in columns  # untouched legacy layout
        assert backup.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 4
    finally:
        backup.close()


def test_migration_refuses_an_already_migrated_database(legacy_db):
    from shellhistory import migrations

    migrations.migrate_schema(db_path=legacy_db, progress=False)
    with pytest.raises(ValueError, match="not a legacy database"):
        migrations.migrate_schema(db_path=legacy_db, progress=False)


def test_migration_refuses_when_wal_content_is_pending(legacy_db):
    from shellhistory import migrations

    wal = legacy_db.parent / (legacy_db.name + "-wal")
    wal.write_bytes(b"pending")
    with pytest.raises(RuntimeError, match="WAL"):
        migrations.migrate_schema(db_path=legacy_db, progress=False)
    # the original is still there, untouched
    assert legacy_db.exists()


def test_legacy_text_import(tmp_path, monkeypatch):
    """The colon-delimited format is gone from the shell but archives still use it."""
    monkeypatch.setenv("SHELLHISTORY_DB", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("SHELLHISTORY_FILE", str(tmp_path / "history"))

    path = tmp_path / "history"
    parents_b64 = b64encode(PARENTS.encode()).decode()
    path_b64 = b64encode(b"/home/pawamoy").decode()
    path.write_text(
        ":1787000000000000:1787000000123456:u1:{p}:corsair:pawamoy:/dev/pts/3:{d}"
        ":/usr/bin/zsh:2:builtin:0:echo one\n"
        ";echo two\n".format(p=parents_b64, d=path_b64),
    )

    code = subprocess.run(
        [sys.executable, "-c",
         "from shellhistory import migrations; r = migrations.import_file(%r); print(r.inserted)" % str(path)],
        capture_output=True, text=True,
    )
    assert code.returncode == 0, code.stderr
    assert code.stdout.strip().endswith("1")

    connection = sqlite3.connect("file:%s?mode=ro" % (tmp_path / "db.sqlite3"), uri=True)
    try:
        row = connection.execute(
            "SELECT s.parents, h.path, h.cmd FROM history h JOIN sessions s ON s.id=h.session_id",
        ).fetchone()
    finally:
        connection.close()
    assert row[0] == PARENTS          # base64 decoded
    assert row[1] == "/home/pawamoy"  # base64 decoded
    assert row[2] == "echo one\necho two"  # ';' continuation rejoined


def test_migration_removes_sidecar_files_of_the_replaced_database(legacy_db):
    """A -wal left behind belongs to the old file and must not survive the swap."""
    from shellhistory import migrations

    shm = legacy_db.parent / (legacy_db.name + "-shm")
    shm.write_bytes(b"stale")
    migrations.migrate_schema(db_path=legacy_db, progress=False)

    assert not shm.exists()
    assert not (legacy_db.parent / (legacy_db.name + "-wal")).exists()
    connection = sqlite3.connect("file:%s?mode=ro" % legacy_db, uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
