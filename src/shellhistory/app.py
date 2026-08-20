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

"""The Flask application serving the history charts."""

import os
import secrets
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import ClassVar

from flask import Flask, Response, jsonify, render_template, request
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from sqlalchemy import desc, extract, func

from shellhistory import db, migrations

# Initialization and constants ------------------------------------------------
app = Flask(__name__)
# Only used to sign the session cookie of the local admin UI. Generated per
# process unless one is supplied, so no usable key sits in the repository.
app.secret_key = os.environ.get("SHELLHISTORY_SECRET_KEY") or secrets.token_hex(32)
db.create_tables()


# Flask Admin stuff -----------------------------------------------------------
class HistoryModelView(ModelView):
    """Admin view over the recorded commands."""

    can_create = False
    can_delete = True
    can_view_details = True
    create_modal = True
    edit_modal = True
    can_export = True
    page_size = 50

    list_template = "admin/history_list.html"

    # host, user, uuid, tty, shell, level and parents belong to the session the
    # command ran in, so they are reached through the relationship rather than
    # repeated on every row.
    column_list: ClassVar[list] = [
        "id",
        "start",
        "stop",
        "type",
        "code",
        "path",
        "cmd",
        "session.host",
        "session.user",
        "session.uuid",
        "session.tty",
        "session.shell",
        "session.level",
    ]
    column_searchable_list: ClassVar[list] = [
        "type",
        "code",
        "path",
        "cmd",
        "session.host",
        "session.user",
        "session.uuid",
        "session.tty",
        "session.shell",
    ]

    column_filters = [  # noqa: RUF012
        "start",
        "type",
        "code",
        "path",
        "cmd",
        "session.host",
        "session.user",
        "session.uuid",
        "session.tty",
        "session.shell",
        "session.level",
        "session.parents",
    ]
    # Only the command's own columns are editable in place: a session row is
    # shared by every command that ran in that shell, so editing it here would
    # silently rewrite unrelated history.

    column_editable_list = ["type", "code", "path", "cmd"]  # noqa: RUF012
    form_excluded_columns: ClassVar[list] = ["start", "stop"]
    # form_widget_args = {
    #     'start': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'stop': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'duration': {'format': '%Y-%m-%d %H:%M:%S.%f'}
    # }


admin = Admin(app, name="Shell History", template_mode="bootstrap3")
admin.add_view(HistoryModelView(db.History, db.get_session()))


@app.teardown_appcontext
def shutdown_session(_exception: BaseException | None = None) -> None:
    """Remove the scoped session at the end of the request."""
    db.Session.remove()


# Utils -----------------------------------------------------------------------
def since_epoch(date: datetime) -> float:
    """Return a date as seconds since the epoch."""
    return time.mktime(date.timetuple())


def time_span() -> tuple[datetime | None, datetime | None]:
    """Return the first and last recorded times, or (None, None) on an empty database.

    Returns:
        The oldest and newest command start times.
    """
    session = db.Session()
    row = session.query(func.min(db.History.start), func.max(db.History.start)).first()
    if row is None:
        return None, None
    return row[0], row[1]


def plural(count: int, noun: str) -> str:
    """Return the count followed by the noun, pluralized when needed."""
    return "{} {}{}".format(count, noun, "" if count == 1 else "s")


def fractional_year(start: datetime, end: datetime) -> float:
    """Return the elapsed time between two dates as a fraction of a year."""
    this_year = end.year
    this_year_start = datetime(year=this_year, month=1, day=1)  # noqa: DTZ001 - local, pairs with mktime
    next_year_start = datetime(year=this_year + 1, month=1, day=1)  # noqa: DTZ001 - local, pairs with mktime
    time_elapsed = since_epoch(end) - since_epoch(start)
    year_duration = since_epoch(next_year_start) - since_epoch(this_year_start)
    return time_elapsed / year_duration


# Special views ---------------------------------------------------------------
@app.route("/")
def home_view() -> str:
    """Render the home page."""
    return render_template("home.html")


@app.route("/import_legacy")
def import_legacy_call() -> Response:
    """Load the legacy text history file.

    Commands reach the database directly now, so this is not how history gets
    in: it is here to pick up an archived file written by an older version, or
    the tail of one left behind by a shell that has not been re-sourced yet.
    """
    data = {"message": None, "class": None}
    try:
        report = migrations.import_history()
    except Exception as e:  # noqa: BLE001 - whatever went wrong is reported in the page
        data["class"] = "danger"
        data["message"] = "{}\n{}: {}".format(
            f"Failed to import {db.HISTFILE_PATH}. The following exception occurred:",
            type(e),
            e,
        )
    else:
        if report.inserted:
            data["class"] = "success"
            data["message"] = "Imported {} from {}, refresh the page to see the change.".format(
                plural(report.inserted, "new record"),
                db.HISTFILE_PATH,
            )
            if report.duplicates:
                data["class"] = "info"
                data["message"] += "\n{} already in the database.".format(plural(report.duplicates, "record"))

        else:
            data["class"] = "default"
            data["message"] = f"Nothing new in {db.HISTFILE_PATH}."

    return jsonify(data)


# Simple views rendering templates --------------------------------------------
@app.route("/codes")
def codes_view() -> str:
    """Render the page for commands by exit code."""
    return render_template("codes.html")


@app.route("/daily")
def daily_view() -> str:
    """Render the page for commands per day of the week."""
    return render_template("daily.html")


@app.route("/daily_average")
def daily_average_view() -> str:
    """Render the page for average commands per day of the week."""
    return render_template("daily_average.html")


@app.route("/duration")
def duration_view() -> str:
    """Render the page for commands by duration."""
    return render_template("duration.html")


@app.route("/hourly")
def hourly_view() -> str:
    """Render the page for commands per hour of the day."""
    return render_template("hourly.html")


@app.route("/hourly_average")
def hourly_average_view() -> str:
    """Render the page for average commands per hour of the day."""
    return render_template("hourly_average.html")


@app.route("/length")
def length_view() -> str:
    """Render the page for commands by length."""
    return render_template("length.html")


@app.route("/markov")
def markov_view() -> str:
    """Render the page for transitions between command words."""
    return render_template("markov.html")


@app.route("/markov_full")
def markov_full_view() -> str:
    """Render the page for transitions between full command lines."""
    return render_template("markov_full.html")


@app.route("/monthly")
def monthly_view() -> str:
    """Render the page for commands per month."""
    return render_template("monthly.html")


@app.route("/monthly_average")
def monthly_average_view() -> str:
    """Render the page for average commands per month."""
    return render_template("monthly_average.html")


@app.route("/over_time")
def over_time_view() -> str:
    """Render the page for commands over time."""
    return render_template("over_time.html")


@app.route("/top_commands_full")
def top_commands_full_view() -> str:
    """Render the page for the most used full command lines."""
    return render_template("top_commands_full.html")


@app.route("/top_commands")
def top_commands_view() -> str:
    """Render the page for the most used commands."""
    return render_template("top_commands.html")


@app.route("/trending")
def trending_view() -> str:
    """Render the page for trending commands."""
    return render_template("trending.html")


@app.route("/type")
def type_view() -> str:
    """Render the page for commands by type."""
    return render_template("type.html")


@app.route("/yearly")
def yearly_view() -> str:
    """Render the page for commands per year."""
    return render_template("yearly.html")


# Routes to return JSON contents ----------------------------------------------
@app.route("/codes_json")
def codes_json() -> Response:
    """Return commands by exit code as JSON."""
    session = db.Session()
    results = session.query(db.History.code, func.count(db.History.code)).group_by(db.History.code).all()
    # total = sum(r[1] for r in results)
    data = [{"name": r[0], "y": r[1]} for r in sorted(results, key=lambda x: x[1], reverse=True)]
    return jsonify(data)


@app.route("/daily_json")
def daily_json() -> Response:
    """Return commands per day of the week as JSON."""
    session = db.Session()
    results = defaultdict(int)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(func.strftime("%w", db.History.start).label("day"), func.count())
            .group_by("day")
            .all()
        },
    )
    data = [results[str(day)] for day in range(1, 7)]
    # put sunday at the end
    data.append(results["0"])
    return jsonify(data)


@app.route("/daily_average_json")
def daily_average_json() -> Response:
    """Return average commands per day of the week as JSON."""
    session = db.Session()
    mintime, maxtime = time_span()
    if mintime is None or maxtime is None:
        return jsonify([0.0] * 7)
    number_of_weeks = (maxtime - mintime).days / 7 + 1
    results: defaultdict = defaultdict(int)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(func.strftime("%w", db.History.start).label("day"), func.count())
            .group_by("day")
            .all()
        },
    )
    data = [float("%.2f" % (results[str(day)] / number_of_weeks)) for day in range(1, 7)]
    # put sunday at the end
    data.append(float("%.2f" % (results["0"] / number_of_weeks)))
    return jsonify(data)


@app.route("/duration_json")
def duration_json() -> Response:
    """Return commands by duration as JSON."""
    session = db.Session()
    results = session.query(db.History.start, db.History.stop).all()

    flat_values = []
    for start, stop in results:
        if start is None or stop is None:
            continue
        delta = stop - start
        flat_values.append(delta.seconds + round(delta.microseconds / 1000))
    if not flat_values:
        return jsonify({"average": 0, "median": 0, "series": []})
    counter = Counter(flat_values)

    data = {
        "average": float(f"{statistics.mean(flat_values):.2f}"),
        "median": statistics.median(flat_values),
        "series": [counter[duration] for duration in range(1, max(counter.keys()) + 1)],
    }
    return jsonify(data)


@app.route("/hourly_json")
def hourly_json() -> Response:
    """Return commands per hour of the day as JSON."""
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(extract("hour", db.History.start).label("hour"), func.count())
            .group_by("hour")
            .all()
        },
    )
    data = [results[hour] for hour in range(24)]
    return jsonify(data)


@app.route("/hourly_average_json")
def hourly_average_json() -> Response:
    """Return average commands per hour of the day as JSON."""
    session = db.Session()
    mintime, maxtime = time_span()
    if mintime is None or maxtime is None:
        return jsonify([0.0] * 24)
    number_of_days = (maxtime - mintime).days + 1
    results: defaultdict = defaultdict(int)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(extract("hour", db.History.start).label("hour"), func.count())
            .group_by("hour")
            .all()
        },
    )
    data = [float("%.2f" % (results[hour] / number_of_days)) for hour in range(24)]
    return jsonify(data)


@app.route("/length_json")
def length_json() -> Response:
    """Return commands by length as JSON."""
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(func.char_length(db.History.cmd).label("length"), func.count())
            .group_by("length")
            .all()
        },
    )

    if not results:
        return jsonify({})

    flat_values = []
    for length, number in results.items():
        flat_values.extend([length] * number)

    data = {
        "average": float(f"{statistics.mean(flat_values):.2f}"),
        "median": statistics.median(flat_values),
        "series": [results[length] for length in range(1, max(results.keys()) + 1)],
    }
    return jsonify(data)


@app.route("/markov_json")
def markov_json() -> Response:
    """Return transitions between command words as JSON."""
    session = db.Session()
    words_2 = []
    w2 = None
    words = session.query(db.History.cmd).order_by(db.History.start).all()
    for word in words:
        w1, w2 = w2, word[0].split(" ")[0]
        words_2.append((w1, w2))
    counter = Counter(words_2).most_common(40)
    unique_words = set()
    for (w1, w2), _count in counter:
        unique_words.add(w1)
        unique_words.add(w2)
    unique_words = list(unique_words)
    data = {
        "xCategories": unique_words,
        "yCategories": unique_words,
        "series": [[unique_words.index(w2), unique_words.index(w1), count] for (w1, w2), count in counter],
    }
    return jsonify(data)


@app.route("/markov_full_json")
def markov_full_json() -> Response:
    """Return transitions between full command lines as JSON."""
    session = db.Session()
    words_2 = []
    w2 = None
    words = session.query(db.History.cmd).order_by(db.History.start).all()
    for word in words:
        w1, w2 = w2, word[0]
        words_2.append((w1, w2))
    counter = Counter(words_2).most_common(40)
    unique_words = set()
    for (w1, w2), _count in counter:
        unique_words.add(w1)
        unique_words.add(w2)
    unique_words = list(unique_words)
    data = {
        "xCategories": unique_words,
        "yCategories": unique_words,
        "series": [[unique_words.index(w2), unique_words.index(w1), count] for (w1, w2), count in counter],
    }
    return jsonify(data)


@app.route("/monthly_json")
def monthly_json() -> Response:
    """Return commands per month as JSON."""
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(extract("month", db.History.start).label("month"), func.count())
            .group_by("month")
            .all()
        },
    )
    data = [results[month] for month in range(1, 13)]
    return jsonify(data)


@app.route("/monthly_average_json")
def monthly_average_json() -> Response:
    """Return average commands per month as JSON."""
    session = db.Session()
    mintime, maxtime = time_span()
    if mintime is None or maxtime is None:
        return jsonify([0.0] * 12)
    number_of_years = fractional_year(mintime, maxtime) + 1
    results: defaultdict = defaultdict(int)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(extract("month", db.History.start).label("month"), func.count())
            .group_by("month")
            .all()
        },
    )
    data = [float("%.2f" % (results[month] / number_of_years)) for month in range(1, 13)]
    return jsonify(data)


@app.route("/over_time_json")
def over_time_json() -> Response:
    """Return commands over time as JSON."""
    session = db.Session()
    results = session.query(db.History.start).order_by(db.History.start).all()

    def datetime_to_milliseconds(dt: datetime) -> int:
        """Return a datetime as milliseconds since the epoch."""
        return int(datetime(dt.year, dt.month, dt.day).timestamp() * 1000)  # noqa: DTZ001 - local midnight

    counter = Counter([datetime_to_milliseconds(r[0]) for r in results])
    data = [(k, v) for k, v in counter.items()]
    return jsonify(data)


@app.route("/top_commands_full_json")
def top_commands_full_json() -> Response:
    """Return the most used full command lines as JSON."""
    session = db.Session()
    results = (
        session.query(db.History.cmd, func.count(db.History.cmd).label("count"))
        .group_by(db.History.cmd)
        .order_by(desc("count"))
        .limit(20)
        .all()
    )
    data = {"categories": [r[0] for r in results], "series": [r[1] for r in results]}
    return jsonify(data)


@app.route("/top_commands_json")
def top_commands_json() -> Response:
    """Return the most used commands as JSON."""
    session = db.Session()
    # Tried to do this with SQL only. Failed. POSITION is not a function.
    # results = (
    #     session.query(
    #         sqlfunc.substr(db.History.cmd, 1, sqlfunc.position(" ", db.History.cmd)),
    #         func.count(db.History.cmd).label("count"),
    #     )
    #     .group_by(db.History.cmd)
    #     .order_by(desc("count"))
    #     .limit(20)
    #     .all()
    # )
    results = session.query(db.History.cmd).all()
    counter = Counter([r[0].split(" ")[0] for r in results]).most_common(20)
    data = {"categories": [c[0] for c in counter], "series": [c[1] for c in counter]}
    return jsonify(data)


@app.route("/trending_json")
def trending_json() -> Response:
    """Return trending commands as JSON."""
    # Not implemented yet: see https://github.com/pawamoy/shell-history/issues/9
    return jsonify(None)


# Command types are recorded in the vocabulary of the shell that produced them:
# Zsh's `whence -w` says "command" and "reserved" where Bash's `type -t` says
# "file" and "keyword". That distinction is worth keeping in the database, but
# it splits one concept into two slices in a chart, so callers can ask for the
# names to be folded onto Bash's vocabulary at display time.
NORMALIZED_TYPES = {"command": "file", "hashed": "file", "reserved": "keyword"}


@app.route("/type_json")
def type_json() -> Response:
    """Return commands by type as JSON."""
    normalize = request.args.get("normalize", "").lower() in ("1", "true", "yes", "on")
    session = db.Session()
    results = session.query(db.History.type, func.count(db.History.type)).group_by(db.History.type).all()
    counts = defaultdict(int)
    for type_name, count in results:
        name = type_name or "none"
        if normalize:
            name = NORMALIZED_TYPES.get(name, name)
        counts[name] += count
    data = [{"name": name, "y": count} for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)]
    return jsonify(data)


@app.route("/wordcloud_json")
def wordcloud_json() -> Response:
    """Return a sample of commands for the word cloud as JSON."""
    session = db.Session()
    results = session.query(db.History.cmd).order_by(func.random()).limit(100)
    text = " ".join(r[0] for r in results.all())
    return jsonify(text)


@app.route("/yearly_json")
def yearly_json() -> Response:
    """Return commands per year as JSON."""
    session = db.Session()
    mintime, maxtime = time_span()
    if mintime is None or maxtime is None:
        return jsonify([])
    minyear, maxyear = mintime.year, maxtime.year
    results: defaultdict = defaultdict(int)
    results.update(
        {
            row[0]: row[1]
            for row in session.query(extract("year", db.History.start).label("year"), func.count())
            .group_by("year")
            .all()
        },
    )
    data = [(year, results[year]) for year in range(minyear, maxyear + 1)]
    return jsonify(data)
