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
from pathlib import Path

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
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, scoped_session, sessionmaker

DEFAULT_DIR = Path.home() / ".shellhistory"

DB_PATH = os.getenv("SHELLHISTORY_DB")
HISTFILE_PATH = os.getenv("SHELLHISTORY_FILE")

if (DB_PATH is None or HISTFILE_PATH is None) and not DEFAULT_DIR.exists():
    DEFAULT_DIR.mkdir()

if DB_PATH is None:
    DB_PATH = DEFAULT_DIR / "db.sqlite3"
else:
    DB_PATH = Path(DB_PATH)

if HISTFILE_PATH is None:
    HISTFILE_PATH = DEFAULT_DIR / "history"
else:
    HISTFILE_PATH = Path(HISTFILE_PATH)

Base = declarative_base()
engine = create_engine("sqlite:///%s" % DB_PATH, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _apply_pragmas(dbapi_connection, connection_record):
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


def get_session():
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
        return "<ShellSession(uuid='%s', tty='%s', level=%s)>" % (self.uuid, self.tty, self.level)


class History(Base):
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
    def duration(self):
        """Kept as a derived value: it is exactly stop - start, so storing it wasted a column."""
        if self.start is None or self.stop is None:
            return None
        return self.stop - self.start

    def __repr__(self):
        return "<History(path='%s', cmd='%s')>" % (self.path, self.cmd)


def is_legacy_schema():
    """True if the database still has the old single-table layout."""
    if not Path(DB_PATH).exists():
        return False
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "history" not in tables:
        return False
    columns = {c["name"] for c in inspector.get_columns("history")}
    return "parents" in columns or "session_id" not in columns


def create_tables():
    """Create the tables if they are missing.

    Refuses to touch a legacy database: `create_all` would see the old `history`
    table, leave it exactly as it is, and the application would then fail against
    a schema it cannot read. Better to say so.
    """
    if is_legacy_schema():
        raise RuntimeError(
            "%s uses the old single-table schema. "
            "Run `shellhistory-cli --migrate` to convert it (the original is kept as a backup)." % DB_PATH,
        )
    Base.metadata.create_all(engine)


class SessionCache:
    """Get-or-create for `sessions` rows, memoized for the life of the importer."""

    def __init__(self, sqla_session):
        self.sqla_session = sqla_session
        self._ids = {}

    def id_for(self, uuid, host, user, tty, shell, level, parents):
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
        self._ids[key] = row.id
        return row.id
