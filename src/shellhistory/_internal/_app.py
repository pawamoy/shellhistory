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

# The Flask application serving the history charts.

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from flask import Flask, Response, jsonify, render_template, request
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Bootstrap4Theme
from markupsafe import Markup

from shellhistory._internal import _charts as charts
from shellhistory._internal import _db as db
from shellhistory._internal import _facets as facets
from shellhistory._internal import _migrations as migrations

# Initialization and constants ------------------------------------------------
app = Flask(__name__)
# Only used to sign the session cookie of the local admin UI. Generated per
# process unless one is supplied, so no usable key sits in the repository.
app.secret_key = os.environ.get("SHELLHISTORY_SECRET_KEY") or secrets.token_hex(32)
db.create_tables()


# Flask Admin stuff -----------------------------------------------------------
def format_parents(_view: ModelView, _context: Any, model: db.History, _name: str) -> Markup:
    """Render a session's process ancestry as one process per line.

    Parameters:
        _view: The admin view rendering the value.
        _context: The Jinja context, unused.
        model: The command being displayed.
        _name: The name of the field, unused.

    Returns:
        The ancestry, HTML-escaped, with one line per process.
    """
    parents = model.session.parents if model.session else None
    return Markup("<br>").join((parents or "").splitlines())


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
    column_list = [  # noqa: RUF012 - flask-admin sets this per instance
        "id",
        "start",
        "stop",
        "type",
        "code",
        "path",
        "cmd",
        "secret_scan.status",
        "session.host",
        "session.user",
        "session.uuid",
        "session.tty",
        "session.shell",
        "session.level",
    ]
    # The details view scaffolds the relationship itself otherwise, which only
    # gets us the repr of the session. Spelling the fields out shows them one by
    # one instead, `parents` included: the process ancestry is a ~300 byte,
    # multi-line string, readable here but far too wide for a table row. It
    # costs no extra query either way, since a command loads its session with
    # it (`lazy="joined"`).
    column_details_list = [  # noqa: RUF012 - flask-admin sets this per instance
        "id",
        "start",
        "stop",
        "duration",
        "type",
        "code",
        "path",
        "cmd",
        "secret_scan.status",
        "secret_scan.scanner",
        "secret_scan.scanned_at",
        "secret_scan.rules",
        "session.host",
        "session.user",
        "session.uuid",
        "session.tty",
        "session.shell",
        "session.level",
        "session.parents",
    ]
    column_formatters_detail = {"session.parents": format_parents}  # noqa: RUF012

    column_searchable_list = [  # noqa: RUF012
        "type",
        "code",
        "path",
        "cmd",
        "secret_scan.status",
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
    form_excluded_columns = ["start", "stop"]  # noqa: RUF012
    # form_widget_args = {
    #     'start': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'stop': {'format': '%Y-%m-%d %H:%M:%S.%f'},
    #     'duration': {'format': '%Y-%m-%d %H:%M:%S.%f'}
    # }


admin = Admin(app, name="Shell History", theme=Bootstrap4Theme(fluid=True))
admin.add_view(HistoryModelView(db.History, db.get_session()))


@app.teardown_appcontext
def shutdown_session(_exception: BaseException | None = None) -> None:
    """Remove the scoped session at the end of the request."""
    db.Session.remove()


# The chart catalogue -----------------------------------------------------------
# Every chart is one entry here: what it is called, what it counts, and how it is
# drawn. The routes, the navigation and the page itself are all generated from
# this, so adding a chart means adding a line and, for the ones a bar of numbers
# cannot express, a script of its own.
@dataclass(frozen=True)
class Chart:
    """One page of the application, and the data behind it."""

    slug: str
    """The chart's address, and the name of its JSON endpoint."""

    title: str
    """The chart's heading."""

    group: str
    """The section of the navigation the chart is listed under."""

    data: Callable[[], Any]
    """What to answer the JSON endpoint with."""

    spec: dict[str, Any] = field(default_factory=dict)
    """How the standard renderer should draw it, when it is drawn by that one."""

    script: str | None = None
    """The script drawing it, when the standard renderer will not do."""

    containers: tuple[str, ...] = ("container",)
    """The elements the page holds, for the charts that draw more than one."""

    splittable: bool = True
    """Whether the chart can be split into one series per value of a dimension."""

    granularity: bool = False
    """Whether the chart reports over time, and so takes a period."""

    about: str = ""
    """A sentence under the heading, for the charts that are guessing at something."""

    @property
    def options(self) -> dict[str, Any]:
        """What the page hands to the shared front end."""
        return {
            "endpoint": f"/{self.slug}_json",
            "splittable": self.splittable,
            "granularity": self.granularity,
            "spec": {"title": self.title, **self.spec},
            "containers": list(self.containers),
        }


def _tool() -> str:
    """Return the program the page is asking about.

    Returns:
        The program's name, as given in the query string.
    """
    return request.args.get("tool", "")


def _number(name: str, fallback: int) -> int:
    """Return a whole number from the query string.

    Parameters:
        name: The name of the parameter.
        fallback: What to use when it is absent or not a number.

    Returns:
        The number.
    """
    try:
        return int(request.args[name])
    except (KeyError, ValueError):
        return fallback


def _flag(name: str) -> bool:
    """Return whether a query string flag is set.

    Parameters:
        name: The name of the flag.

    Returns:
        Whether it was given a true value.
    """
    return request.args.get(name, "").lower() in facets.TRUTHY


CHARTS: tuple[Chart, ...] = (
    # Activity over time
    Chart(
        "over_time",
        "Commands over time",
        "Activity",
        charts.over_time,
        spec={"type": "area", "stacking": "auto"},
        granularity=True,
    ),
    Chart("yearly", "Commands per year", "Activity", charts.yearly, spec={"stacking": "auto"}),
    Chart("monthly", "Commands per month of the year", "Activity", charts.monthly),
    Chart(
        "monthly_average",
        "Commands per month of the year (average)",
        "Activity",
        lambda: charts.monthly(average=True),
        spec={"yTitle": "Commands per year", "valueDecimals": 1},
    ),
    Chart("daily", "Commands per day of the week", "Activity", charts.daily),
    Chart(
        "daily_average",
        "Commands per day of the week (average)",
        "Activity",
        lambda: charts.daily(average=True),
        spec={"yTitle": "Commands per week", "valueDecimals": 1},
    ),
    Chart("hourly", "Commands per hour of the day", "Activity", charts.hourly),
    Chart(
        "hourly_average",
        "Commands per hour of the day (average)",
        "Activity",
        lambda: charts.hourly(average=True),
        spec={"yTitle": "Commands per day", "valueDecimals": 2},
    ),
    Chart(
        "punchcard",
        "Punch card",
        "Activity",
        charts.punchcard,
        script="punchcard",
        splittable=False,
        about="The week as a grid. Every cell is one hour of one weekday, across the whole history.",
    ),
    Chart(
        "calendar",
        "Calendar",
        "Activity",
        charts.calendar,
        script="calendar",
        splittable=False,
        containers=("container",),
    ),
    Chart(
        "rhythm",
        "Working rhythm",
        "Activity",
        charts.rhythm,
        script="rhythm",
        containers=("container", "gaps"),
        about="A stretch of work is a run of commands with no pause longer than half an hour in it.",
    ),
    # Where the commands were typed
    Chart(
        "environment",
        "Where commands are typed",
        "Environment",
        charts.environment,
        script="environment",
        containers=("container", "share"),
        granularity=True,
        about=(
            "Read out of the process ancestry recorded with each shell: the terminal that opened it, "
            "the editor hosting that terminal, the window manager above it, and whether an sshd sits in between."
        ),
    ),
    Chart(
        "share",
        "Breakdown by dimension",
        "Environment",
        charts.share,
        script="share",
        about="The same pie for every dimension. Pick one to split by.",
    ),
    Chart("nesting", "Shell nesting", "Environment", charts.nesting, spec={"xTitle": "Shell level"}),
    Chart(
        "sessions",
        "Shell sessions",
        "Environment",
        charts.sessions,
        script="sessions",
        containers=("container", "size", "concurrency"),
        granularity=True,
    ),
    # How they ended
    Chart(
        "codes",
        "How commands ended",
        "Reliability",
        charts.codes,
        spec={"xTitle": "Exit code"},
    ),
    Chart(
        "failures",
        "Failure rate",
        "Reliability",
        lambda: charts.failures(minimum=_number("minimum", charts.FAILURE_MINIMUM)),
        script="failures",
        containers=("container", "commands"),
        granularity=True,
    ),
    Chart(
        "not_found",
        "Commands not found",
        "Reliability",
        charts.not_found,
        spec={"type": "bar", "yTitle": "Times not found"},
        about="Everything that exited 127: typos, tools left behind on another machine, aliases never defined.",
    ),
    Chart(
        "interrupted",
        "Commands given up on",
        "Reliability",
        charts.interrupted,
        spec={"type": "bar", "yTitle": "Times interrupted"},
        about="Everything that exited 130, which is what Ctrl-C leaves behind.",
    ),
    Chart(
        "retries",
        "Retries",
        "Reliability",
        charts.retries,
        script="retries",
        containers=("container", "worst"),
        about=(
            "A chain is a run of commands invoking the same program in the same shell, "
            "each following a failure of the one before by less than five minutes."
        ),
    ),
    Chart(
        "duration_by_code",
        "Duration by exit code",
        "Reliability",
        charts.duration_by_code,
        script="duration_by_code",
        splittable=False,
        about="Whether failures fail fast. Each box spans the quartiles, the whiskers the 5th and 95th percentiles.",
    ),
    # Where they ran
    Chart(
        "directories",
        "Directories",
        "Places",
        charts.directories,
        script="directories",
        containers=("container", "depth"),
    ),
    Chart(
        "projects",
        "Projects over time",
        "Places",
        charts.projects,
        script="projects",
        containers=("container", "totals"),
        granularity=True,
        about=(
            "The project is guessed from the path: the first segment that is neither a home "
            "nor one of the directories projects are merely kept in."
        ),
    ),
    # What the commands were
    Chart("top_commands", "Top commands", "Commands", charts.top_commands, spec={"type": "bar"}),
    Chart(
        "top_commands_full",
        "Top command lines",
        "Commands",
        lambda: charts.top_commands(full=True),
        spec={"type": "bar"},
    ),
    Chart("type", "Command types", "Commands", lambda: charts.types(raw=_flag("raw"))),
    Chart(
        "length",
        "Command lengths",
        "Commands",
        charts.length,
        spec={"xTitle": "Characters"},
    ),
    Chart(
        "duration",
        "Command durations",
        "Commands",
        charts.duration,
        spec={"xTitle": "How long it took"},
    ),
    Chart(
        "subcommands",
        "Subcommands",
        "Commands",
        lambda: charts.subcommands(_tool() or "git"),
        script="tooled",
        spec={"type": "bar", "yTitle": "Times used", "picker": "subcommands of"},
    ),
    Chart(
        "flags",
        "Options",
        "Commands",
        lambda: charts.flags(_tool()),
        script="tooled",
        spec={"type": "bar", "yTitle": "Times passed", "picker": "options of", "any": True},
    ),
    Chart(
        "complexity",
        "Command complexity",
        "Commands",
        charts.complexity,
        script="complexity",
        containers=("container", "distribution", "lengths"),
        granularity=True,
        about="Counting what strings commands together -- pipes, &&, ||, ; -- rather than characters.",
    ),
    Chart(
        "sudo",
        "Commands run as somebody else",
        "Commands",
        charts.sudo,
        script="sudo",
        containers=("container", "count"),
        granularity=True,
    ),
    Chart(
        "vocabulary",
        "Vocabulary",
        "Commands",
        charts.vocabulary,
        script="vocabulary",
        containers=("container", "new"),
        granularity=True,
        about="How many different programs get used in a period, and how many of them had never been used before.",
    ),
    Chart(
        "trending",
        "On the way in, on the way out",
        "Commands",
        lambda: charts.trending(minimum=_number("minimum", charts.TRENDING_MINIMUM)),
        spec={
            "type": "bar",
            "yTitle": "Change in share of commands, in points",
            "min": None,
            "valueSuffix": " pt",
            "valueDecimals": 2,
        },
        granularity=True,
        about=(
            "The selected span cut in half, and each program's share of the commands compared between the two halves."
        ),
    ),
    Chart(
        "markov",
        "What follows what",
        "Commands",
        charts.markov,
        script="markov",
        splittable=False,
    ),
    Chart(
        "markov_full",
        "What follows what (whole lines)",
        "Commands",
        lambda: charts.markov(full=True),
        script="markov",
        splittable=False,
    ),
)
"""Every chart the application serves."""

CHART_BY_SLUG = {chart.slug: chart for chart in CHARTS}
"""The charts, by address."""


def navigation() -> list[tuple[str, list[Chart]]]:
    """Return the charts grouped the way the sidebar lists them.

    Returns:
        Each section, with the charts in it.
    """
    groups: dict[str, list[Chart]] = {}
    for chart in CHARTS:
        groups.setdefault(chart.group, []).append(chart)
    return list(groups.items())


@app.context_processor
def _inject_navigation() -> dict[str, Any]:
    """Make the chart list available to every template."""
    return {"navigation": navigation()}


def _chart_page(chart: Chart) -> str:
    """Render a chart's page.

    Parameters:
        chart: The chart to render.

    Returns:
        The rendered page.
    """
    return render_template("chart.html", chart=chart)


def _chart_data(chart: Chart) -> Response:
    """Answer a chart's JSON endpoint.

    Parameters:
        chart: The chart being asked for.

    Returns:
        The chart's data.
    """
    return jsonify(chart.data())


for _chart in CHARTS:
    app.add_url_rule(f"/{_chart.slug}", _chart.slug, partial(_chart_page, _chart))
    app.add_url_rule(f"/{_chart.slug}_json", f"{_chart.slug}_json", partial(_chart_data, _chart))


# Pages and endpoints that are not charts ---------------------------------------
@app.route("/")
def home_view() -> str:
    """Render the home page."""
    return render_template("home.html")


@app.route("/facets_json")
def facets_json() -> Response:
    """Return every dimension the charts can be filtered or split by."""
    return jsonify(facets.available())


@app.route("/stats_json")
def stats_json() -> Response:
    """Return the numbers the home page opens with."""
    return jsonify(charts.stats())


@app.route("/wordcloud_json")
def wordcloud_json() -> Response:
    """Return the most used commands, for the cloud on the home page."""
    return jsonify(charts.wordcloud())


def plural(count: int, noun: str) -> str:
    """Return the count followed by the noun, pluralized when needed.

    Parameters:
        count: How many there are.
        noun: What there are that many of.

    Returns:
        The count and the noun.
    """
    return "{} {}{}".format(count, noun, "" if count == 1 else "s")


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
