# -*- coding: utf-8 -*-

import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime

from flask import Flask, jsonify, render_template
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from sqlalchemy import desc, extract, func

from . import db

# Initialization and constants ------------------------------------------------
app = Flask(__name__)
app.secret_key = "2kQOLbr6NtfHV0wIItjHWzuwsgCUXA4CSSBWFE9yELqrkSZU"
db.create_tables()


# Flask Admin stuff -----------------------------------------------------------
class HistoryModelView(ModelView):
    can_create = False
    can_delete = True
    can_view_details = True
    create_modal = True
    edit_modal = True
    can_export = True
    page_size = 50

    list_template = "admin/history_list.html"

    column_exclude_list = ["parents"]
    column_searchable_list = [
        "id",
        "start",
        "stop",
        "duration",
        "host",
        "user",
        "uuid",
        "tty",
        "parents",
        "shell",
        "level",
        "type",
        "code",
        "path",
        "cmd",
    ]
    column_filters = ["host", "user", "uuid", "tty", "parents", "shell", "level", "type", "code", "path", "cmd"]
    column_editable_list = ["host", "user", "uuid", "tty", "shell", "level", "type", "code", "path", "cmd"]
    form_excluded_columns = ["start", "stop", "duration"]
    # form_widget_args = {
    #     'start': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'stop': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'duration': {'format': '%Y-%m-%d %H:%M:%S.%f'}
    # }


admin = Admin(app, name="Shell History", template_mode="bootstrap3")
admin.add_view(HistoryModelView(db.History, db.get_session()))


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.Session.remove()


# Utils -----------------------------------------------------------------------
def since_epoch(date):
    return time.mktime(date.timetuple())


def fractional_year(start, end):
    this_year = end.year
    this_year_start = datetime(year=this_year, month=1, day=1)
    next_year_start = datetime(year=this_year + 1, month=1, day=1)
    time_elapsed = since_epoch(end) - since_epoch(start)
    year_duration = since_epoch(next_year_start) - since_epoch(this_year_start)
    return time_elapsed / year_duration


# Special views ---------------------------------------------------------------
@app.route("/")
def home_view():
    return render_template("home.html")


@app.route("/update")
def update_call():
    data = {"message": None, "class": None}
    try:
        report = db.update()
    except Exception as e:
        data["class"] = "danger"
        data["message"] = "%s\n%s: %s" % (
            "Failed to import current history. The following exception occurred:",
            type(e),
            e,
        )
    else:
        if report.inserted:
            data["class"] = "success"
            data["message"] = (
                "Database successfully updated (%s new items), refresh the page to see the change." % report.inserted
            )
            if report.duplicates:
                data["class"] = "info"
                data["message"] += "\n%s duplicates were not imported." % report.duplicates

        else:
            data["class"] = "default"
            data["message"] = "Database already synchronized, nothing changed."

    return jsonify(data)


# Simple views rendering templates --------------------------------------------
@app.route("/codes")
def codes_view():
    return render_template("codes.html")


@app.route("/daily")
def daily_view():
    return render_template("daily.html")


@app.route("/daily_average")
def daily_average_view():
    return render_template("daily_average.html")


@app.route("/duration")
def duration_view():
    return render_template("duration.html")


@app.route("/hourly")
def hourly_view():
    return render_template("hourly.html")


@app.route("/hourly_average")
def hourly_average_view():
    return render_template("hourly_average.html")


@app.route("/length")
def length_view():
    return render_template("length.html")


@app.route("/markov")
def markov_view():
    return render_template("markov.html")


@app.route("/markov_full")
def markov_full_view():
    return render_template("markov_full.html")


@app.route("/monthly")
def monthly_view():
    return render_template("monthly.html")


@app.route("/monthly_average")
def monthly_average_view():
    return render_template("monthly_average.html")


@app.route("/over_time")
def over_time_view():
    return render_template("over_time.html")


@app.route("/top_commands_full")
def top_commands_full_view():
    return render_template("top_commands_full.html")


@app.route("/top_commands")
def top_commands_view():
    return render_template("top_commands.html")


@app.route("/trending")
def trending_view():
    return render_template("trending.html")


@app.route("/type")
def type_view():
    return render_template("type.html")


@app.route("/yearly")
def yearly_view():
    return render_template("yearly.html")


# Routes to return JSON contents ----------------------------------------------
@app.route("/codes_json")
def codes_json():
    session = db.Session()
    results = session.query(db.History.code, func.count(db.History.code)).group_by(db.History.code).all()
    # total = sum(r[1] for r in results)
    data = [{"name": r[0], "y": r[1]} for r in sorted(results, key=lambda x: x[1], reverse=True)]
    return jsonify(data)


@app.route("/daily_json")
def daily_json():
    session = db.Session()
    results = defaultdict(int)
    results.update(
        session.query(func.strftime("%w", db.History.start).label("day"), func.count("day")).group_by("day").all()
    )
    data = [results[str(day)] for day in range(1, 7)]
    # put sunday at the end
    data.append(results["0"])
    return jsonify(data)


@app.route("/daily_average_json")
def daily_average_json():
    session = db.Session()
    mintime = session.query(func.min(db.History.start)).first()[0]
    maxtime = session.query(func.max(db.History.start)).first()[0]
    number_of_weeks = (maxtime - mintime).days / 7 + 1
    results = defaultdict(int)
    results.update(
        session.query(func.strftime("%w", db.History.start).label("day"), func.count("day")).group_by("day").all()
    )
    data = [float("%.2f" % (results[str(day)] / number_of_weeks)) for day in range(1, 7)]
    # put sunday at the end
    data.append(float("%.2f" % (results["0"] / number_of_weeks)))
    return jsonify(data)


@app.route("/duration_json")
def duration_json():
    session = db.Session()
    results = session.query(db.History.duration).all()

    flat_values = [r[0].seconds + round(r[0].microseconds / 1000) for r in results]
    counter = Counter(flat_values)

    data = {
        "average": float("%.2f" % statistics.mean(flat_values)),
        "median": statistics.median(flat_values),
        "series": [counter[duration] for duration in range(1, max(counter.keys()) + 1)],
    }
    return jsonify(data)


@app.route("/hourly_json")
def hourly_json():
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        session.query(extract("hour", db.History.start).label("hour"), func.count("hour")).group_by("hour").all()
    )
    data = [results[hour] for hour in range(0, 24)]
    return jsonify(data)


@app.route("/hourly_average_json")
def hourly_average_json():
    session = db.Session()
    mintime = session.query(func.min(db.History.start)).first()[0]
    maxtime = session.query(func.max(db.History.start)).first()[0]
    number_of_days = (maxtime - mintime).days + 1
    results = defaultdict(lambda: 0)
    results.update(
        session.query(extract("hour", db.History.start).label("hour"), func.count("hour")).group_by("hour").all()
    )
    data = [float("%.2f" % (results[hour] / number_of_days)) for hour in range(0, 24)]
    return jsonify(data)


@app.route("/length_json")
def length_json():
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        session.query(func.char_length(db.History.cmd).label("length"), func.count("length")).group_by("length").all()
    )

    if not results:
        return jsonify({})

    flat_values = []
    for length, number in results.items():
        flat_values.extend([length] * number)

    data = {
        "average": float("%.2f" % statistics.mean(flat_values)),
        "median": statistics.median(flat_values),
        "series": [results[length] for length in range(1, max(results.keys()) + 1)],
    }
    return jsonify(data)


@app.route("/markov_json")
def markov_json():
    session = db.Session()
    words_2 = []
    w2 = None
    words = session.query(db.History.cmd).order_by(db.History.start).all()
    for word in words:
        w1, w2 = w2, word[0].split(" ")[0]
        words_2.append((w1, w2))
    counter = Counter(words_2).most_common(40)
    unique_words = set()
    for (w1, w2), count in counter:
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
def markov_full_json():
    session = db.Session()
    words_2 = []
    w2 = None
    words = session.query(db.History.cmd).order_by(db.History.start).all()
    for word in words:
        w1, w2 = w2, word[0]
        words_2.append((w1, w2))
    counter = Counter(words_2).most_common(40)
    unique_words = set()
    for (w1, w2), count in counter:
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
def monthly_json():
    session = db.Session()
    results = defaultdict(lambda: 0)
    results.update(
        session.query(extract("month", db.History.start).label("month"), func.count("month")).group_by("month").all()
    )
    data = [results[month] for month in range(1, 13)]
    return jsonify(data)


@app.route("/monthly_average_json")
def monthly_average_json():
    session = db.Session()
    mintime = session.query(func.min(db.History.start)).first()[0]
    maxtime = session.query(func.max(db.History.start)).first()[0]
    number_of_years = fractional_year(mintime, maxtime) + 1
    results = defaultdict(lambda: 0)
    results.update(
        session.query(extract("month", db.History.start).label("month"), func.count("month")).group_by("month").all()
    )
    data = [float("%.2f" % (results[month] / number_of_years)) for month in range(1, 13)]
    return jsonify(data)


@app.route("/over_time_json")
def over_time_json():
    session = db.Session()
    results = session.query(db.History.start).order_by(db.History.start).all()

    def datetime_to_milliseconds(dt):
        return int(datetime(dt.year, dt.month, dt.day).timestamp() * 1000)

    counter = Counter([datetime_to_milliseconds(r[0]) for r in results])
    data = [(k, v) for k, v in counter.items()]
    return jsonify(data)


@app.route("/top_commands_full_json")
def top_commands_full_json():
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
def top_commands_json():
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
def trending_json():
    session = db.Session()
    data = None
    return jsonify(data)


@app.route("/type_json")
def type_json():
    session = db.Session()
    results = session.query(db.History.type, func.count(db.History.type)).group_by(db.History.type).all()
    # total = sum(r[1] for r in results)
    data = [{"name": r[0] or "none", "y": r[1]} for r in sorted(results, key=lambda x: x[1], reverse=True)]
    return jsonify(data)


@app.route("/wordcloud_json")
def wordcloud_json():
    session = db.Session()
    results = session.query(db.History.cmd).order_by(func.random()).limit(100)
    text = " ".join(r[0] for r in results.all())
    return jsonify(text)


@app.route("/yearly_json")
def yearly_json():
    session = db.Session()
    minyear = session.query(extract("year", func.min(db.History.start))).first()[0]
    maxyear = session.query(extract("year", func.max(db.History.start))).first()[0]
    results = defaultdict(lambda: 0)
    results.update(
        session.query(extract("year", db.History.start).label("year"), func.count("year")).group_by("year").all()
    )
    data = [(year, results[year]) for year in range(minyear, maxyear + 1)]
    return jsonify(data)
