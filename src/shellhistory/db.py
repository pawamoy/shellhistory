import codecs
import os
import sys
from base64 import b64decode
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Interval,
    String,
    Text,
    UnicodeText,
    UniqueConstraint,
    create_engine,
    exc,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from tqdm import tqdm

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


def create_tables():
    Base.metadata.create_all(engine)


Session = scoped_session(sessionmaker(bind=engine))


def get_session():
    return Session


class History(Base):
    __tablename__ = "history"
    __table_args__ = (UniqueConstraint("start", "uuid"), {"useexisting": True})

    Tuple = namedtuple("HT", "start stop uuid parents host user tty path shell level type code cmd")

    id = Column(Integer, primary_key=True)
    start = Column(DateTime)
    stop = Column(DateTime)
    duration = Column(Interval)
    host = Column(String)
    user = Column(String)
    uuid = Column(String)
    tty = Column(String)
    parents = Column(Text)
    shell = Column(String)
    level = Column(Integer)
    type = Column(String)
    code = Column(Integer)
    path = Column(String)
    cmd = Column(UnicodeText)

    def __repr__(self):
        return "<History(path='%s', cmd='%s')>" % (self.path, self.cmd)

    @staticmethod
    def line_to_tuple(line):
        return History.Tuple(*line.split(":", 12))

    @staticmethod
    def tuple_to_db_object(nt):
        start = datetime.fromtimestamp(float(nt.start) / 1000000.0)
        stop = datetime.fromtimestamp(float(nt.stop) / 1000000.0)
        duration = stop - start
        return History(
            start=start,
            stop=stop,
            duration=duration,
            host=nt.host,
            user=nt.user,
            path=b64decode(nt.path).decode().rstrip("\n"),
            uuid=nt.uuid,
            tty=nt.tty,
            parents=b64decode(nt.parents).decode().rstrip("\n"),
            shell=nt.shell,
            level=nt.level,
            type=nt.type,
            code=nt.code,
            cmd=nt.cmd,
        )

    @staticmethod
    def from_line(line):
        return History.tuple_to_db_object(History.line_to_tuple(line))


# Must run *after* History is declared, otherwise Base.metadata is still empty
# and create_all() silently creates a database without any table in it.
# create_all() is idempotent (checkfirst=True), so it is safe on every import.
create_tables()


def flush():
    session = Session()
    session.query(History).delete()
    session.commit()


def delete_table(table=History):
    table.__table__.drop(engine)


def yield_db_object_blocks(path, size=512):
    block = []

    with codecs.open(path, encoding="utf-8", errors="ignore") as stream:
        num_lines = sum(1 for _ in stream)

    with codecs.open(path, encoding="utf-8", errors="ignore") as stream:
        current_obj = None

        for i, line in enumerate(tqdm(stream, total=num_lines, unit="lines"), 1):
            first_char, line = line[0], line[1:].rstrip("\n")

            if first_char == ":":
                # new command
                if current_obj is not None:
                    block.append(current_obj)
                try:
                    current_obj = History.from_line(line)
                except Exception as e:
                    print(f"Line {i}: {e}\n{line}", file=sys.stderr)
            elif first_char == ";":
                # multi-line command
                if current_obj is None:
                    continue  # orphan line
                current_obj.cmd += "\n" + line
            else:
                # would only happen if file is corrupted
                print(f"Line {i}: invalid line starting with {first_char}\n{line}", file=sys.stderr)

            if len(block) == size:
                yield block
                block = []

        if current_obj is not None:
            block.append(current_obj)

    if block:
        yield block


InsertionReport = namedtuple("Report", "inserted duplicates")


def insert(obj_list, session, one_by_one=False):
    if obj_list:

        if one_by_one:
            duplicates, inserted = 0, 0

            for obj in obj_list:

                try:
                    session.add(obj)
                    session.commit()
                    inserted += 1
                except exc.IntegrityError:
                    session.rollback()
                    duplicates += 1

            return InsertionReport(inserted, duplicates)

        else:
            session.add_all(obj_list)
            session.commit()
            return InsertionReport(len(obj_list), 0)

    return InsertionReport(0, 0)


def import_file(path):
    session = Session()
    reports = []

    for obj_list in yield_db_object_blocks(path):

        try:
            reports.append(insert(obj_list, session))
        except exc.IntegrityError:
            session.rollback()
            reports.append(insert(obj_list, session, one_by_one=True))

    final_report = InsertionReport(sum([r.inserted for r in reports]), sum([r.duplicates for r in reports]))

    return final_report


def import_history():
    if not HISTFILE_PATH.exists():
        raise ValueError("%s: no such file" % HISTFILE_PATH)
    return import_file(HISTFILE_PATH)


def update():
    return import_history()
