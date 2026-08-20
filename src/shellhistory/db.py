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

"""Database engine and models.

The schema is split in two tables. Everything that stays constant for the whole
life of a shell -- who and where it is, its tty, its process ancestry -- lives
once in `sessions`; `history` holds only what changes from one command to the
next and points at its session.

That split is not cosmetic. The ancestry string is ~300 bytes and there are only
a couple of thousand distinct ones, so repeating it on every row made it half of
the database file on its own.
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UnicodeText,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

DEFAULT_DIR = Path.home() / ".shellhistory"

_db_env = os.getenv("SHELLHISTORY_DB")
_histfile_env = os.getenv("SHELLHISTORY_FILE")

if (_db_env is None or _histfile_env is None) and not DEFAULT_DIR.exists():
    DEFAULT_DIR.mkdir()

DB_PATH = Path(_db_env) if _db_env else DEFAULT_DIR / "db.sqlite3"
HISTFILE_PATH = Path(_histfile_env) if _histfile_env else DEFAULT_DIR / "history"

Base = declarative_base()
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _apply_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Configure SQLite for many small concurrent writers.

    Every recorded command is written by its own short-lived process, and any
    number of shells may be running at once. WAL keeps those writers from
    blocking readers, and `busy_timeout` makes a writer that loses the race wait
    its turn rather than raising SQLITE_BUSY -- which, unhandled, would silently
    drop the command.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


Session = scoped_session(sessionmaker(bind=engine))


def get_session() -> scoped_session:
    """Return the scoped session factory.

    Returns:
        The session factory shared by the application.
    """
    return Session


class ShellSession(Base):
    """One shell process, identified by everything that cannot change while it runs.

    The uuid alone is not enough: it is exported, so a subshell inherits it from
    its parent while having its own level and its own ancestry.
    """

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("uuid", "host", "user", "tty", "shell", "level", "parents"),)

    id = Column(Integer, primary_key=True)
    uuid = Column(String, index=True)
    host = Column(String)
    user = Column(String)
    tty = Column(String)
    shell = Column(String)
    level = Column(Integer)
    parents = Column(Text)

    def __repr__(self):
        return f"<ShellSession(uuid='{self.uuid}', tty='{self.tty}', level={self.level})>"


class History(Base):
    """One recorded command, pointing at the shell session it ran in."""

    __tablename__ = "history"
    __table_args__ = (UniqueConstraint("start", "session_id"),)

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    start = Column(DateTime, index=True)
    stop = Column(DateTime)
    type = Column(String)
    code = Column(Integer)
    path = Column(String)
    cmd = Column(UnicodeText)

    session = relationship("ShellSession", backref="commands", lazy="joined")

    @property
    def duration(self) -> timedelta | None:
        """Kept as a derived value: it is exactly stop - start, so storing it wasted a column."""
        if self.start is None or self.stop is None:
            return None
        return self.stop - self.start  # ty: ignore[invalid-return-type]

    def __repr__(self):
        return f"<History(path='{self.path}', cmd='{self.cmd}')>"


def is_legacy_schema() -> bool:
    """Tell whether the database still has the old single-table layout.

    Returns:
        True if the database predates the sessions/history split.
    """
    if not DB_PATH.exists():
        return False
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "history" not in tables:
        return False
    columns = {c["name"] for c in inspector.get_columns("history")}
    return "parents" in columns or "session_id" not in columns


def create_tables() -> None:
    """Create the tables if they are missing.

    Refuses to touch a legacy database: `create_all` would see the old `history`
    table, leave it exactly as it is, and the application would then fail against
    a schema it cannot read. Better to say so.
    """
    if is_legacy_schema():
        raise RuntimeError(
            f"{DB_PATH} uses the old single-table schema. "
            "Run `shellhistory-cli --migrate` to convert it (the original is kept as a backup).",
        )
    Base.metadata.create_all(engine)


class SessionCache:
    """Get-or-create for `sessions` rows, memoized for the life of the importer."""

    def __init__(self, sqla_session: Any) -> None:
        """Initialize the cache.

        Parameters:
            sqla_session: The SQLAlchemy session to create rows through.
        """
        self.sqla_session = sqla_session
        self._ids: dict[tuple, int] = {}

    def id_for(  # noqa: PLR0917 - these are the columns identifying a session
        self,
        uuid: str,
        host: str,
        user: str,
        tty: str,
        shell: str,
        level: int | None,
        parents: str,
    ) -> int:
        """Return the id of the session row for these values, creating it if needed.

        Parameters:
            uuid: The shell's uuid.
            host: The host name.
            user: The user name.
            tty: The terminal device.
            shell: The path of the running shell.
            level: The shell nesting level.
            parents: The process ancestry.

        Returns:
            The primary key of the matching session row.
        """
        key = (uuid, host, user, tty, shell, level, parents)
        if key in self._ids:
            return self._ids[key]
        row = (
            self.sqla_session.query(ShellSession)
            .filter_by(uuid=uuid, host=host, user=user, tty=tty, shell=shell, level=level, parents=parents)
            .one_or_none()
        )
        if row is None:
            row = ShellSession(
                uuid=uuid,
                host=host,
                user=user,
                tty=tty,
                shell=shell,
                level=level,
                parents=parents,
            )
            self.sqla_session.add(row)
            self.sqla_session.flush()
        # SQLAlchemy types columns as descriptors; on an instance this is the int key.
        row_id: int = row.id  # ty: ignore[invalid-assignment]
        self._ids[key] = row_id
        return row_id
