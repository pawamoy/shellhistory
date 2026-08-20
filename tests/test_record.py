"""Tests for `record`, the standalone writer the shell calls."""

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

RECORD = Path(__file__).parent.parent / "src" / "shellhistory" / "record.py"


def normalize(sql):
    return re.sub(r"\s+", " ", sql).replace("IF NOT EXISTS ", "").strip()


def schema_of(path):
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return {
            name: normalize(sql)
            for kind, name, sql in connection.execute("SELECT type, name, sql FROM sqlite_master")
            if sql and not name.startswith("sqlite_")
        }
    finally:
        connection.close()


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "db.sqlite3")


def write(db_path, **overrides):
    from shellhistory import record

    values = {
        "start": "1787000000000000",
        "stop": "1787000000123456",
        "uuid": "uuid-1",
        "host": "corsair",
        "user": "pawamoy",
        "tty": "/dev/pts/3",
        "parents": "/usr/bin/zsh\n/usr/lib/systemd/systemd",
        "shell": "/usr/bin/zsh",
        "level": "2",
        "type": "builtin",
        "code": "0",
        "path": "/media/data/dev/x",
        "cmd": "echo hello",
    }
    values.update(overrides)
    record.record(values, db_path=db_path)
    return values


def rows(db_path):
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        return connection.execute(
            """SELECT s.uuid, s.host, s.user, s.tty, s.shell, s.level, s.parents,
                      h.start, h.stop, h.type, h.code, h.path, h.cmd
               FROM history h JOIN sessions s ON s.id = h.session_id ORDER BY h.id""",
        ).fetchall()
    finally:
        connection.close()


def test_writes_a_record_into_a_fresh_database(db_path):
    write(db_path)
    stored = rows(db_path)
    assert len(stored) == 1
    assert stored[0][0] == "uuid-1"
    assert stored[0][12] == "echo hello"


def test_command_punctuation_is_stored_verbatim(db_path):
    """The old text format needed base64 and ';' prefixes; parameters need neither."""
    nasty = "git commit -m \"a: colon\nand a newline\" && echo ';not a continuation'"
    write(db_path, cmd=nasty)
    assert rows(db_path)[0][12] == nasty


def test_quotes_cannot_break_out_of_the_statement(db_path):
    """A command that looks like SQL is data, not code."""
    write(db_path, cmd="'); DROP TABLE history; --")
    stored = rows(db_path)
    assert len(stored) == 1
    assert stored[0][12] == "'); DROP TABLE history; --"


def test_commands_from_one_shell_share_a_session_row(db_path):
    write(db_path, start="1787000000000000", cmd="first")
    write(db_path, start="1787000001000000", cmd="second")
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2
    finally:
        connection.close()


def test_a_subshell_gets_its_own_session(db_path):
    """The uuid is exported, so level and ancestry are what separate nested shells."""
    write(db_path, start="1787000000000000", level="2")
    write(db_path, start="1787000001000000", level="3")
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    finally:
        connection.close()


def test_replaying_the_same_record_is_a_no_op(db_path):
    write(db_path)
    write(db_path)
    assert len(rows(db_path)) == 1


def test_schema_matches_the_sqlalchemy_models(db_path, tmp_path):
    """record.py writes its own DDL, so it must not drift from the models."""
    write(db_path)

    from_models = str(tmp_path / "models.sqlite3")
    subprocess.run(
        [sys.executable, "-c",
         "from shellhistory import db; db.create_tables()"],
        env={**os.environ, "SHELLHISTORY_DB": from_models, "SHELLHISTORY_FILE": os.devnull},
        check=True,
    )
    assert schema_of(db_path) == schema_of(from_models)


def test_a_broken_database_parks_the_record_instead_of_losing_it(tmp_path, monkeypatch):
    from shellhistory import record

    unwritable = tmp_path / "db.sqlite3"
    unwritable.write_text("this is not a database")
    monkeypatch.setattr(record, "default_db_path", lambda: str(unwritable))

    assert record.main([
        "1787000000000000", "1787000000123456", "uuid-1", "corsair", "pawamoy",
        "/dev/pts/3", "parents", "/usr/bin/zsh", "2", "builtin", "0", "/tmp", "echo hi",
    ]) == 1
    parked = tmp_path / "unrecorded.jsonl"
    assert parked.exists()
    assert "echo hi" in parked.read_text()


def test_bad_argument_count_is_refused():
    from shellhistory import record

    assert record.main(["only", "two"]) == 2
