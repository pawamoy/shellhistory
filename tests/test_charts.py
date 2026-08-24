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

"""Tests for the charts, the dimensions they are split by, and the pages serving them."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from shellhistory._internal import _charts as charts
from shellhistory._internal import _db as db
from shellhistory._internal import _env as env
from shellhistory._internal._app import CHARTS, app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask.testing import FlaskClient

# Three shells, told apart by everything the charts can split on: two machines,
# two users, two shells, and three very different places to be typing.
QTILE = "/usr/bin/zsh \n/usr/bin/python /usr/bin/terminator \n/usr/bin/python /usr/bin/qtile start \n"
VSCODE = "/bin/bash \n/usr/share/code/code --no-sandbox \n/lib/systemd/systemd \n"
OVER_SSH = "-bash \nsshd: other@pts/2 \n/usr/sbin/sshd -D \n"

SESSIONS = (
    {
        "uuid": "u1",
        "host": "alpha",
        "user": "me",
        "tty": "/dev/pts/0",
        "shell": "/usr/bin/zsh",
        "level": 1,
        "parents": QTILE,
    },
    {
        "uuid": "u2",
        "host": "beta",
        "user": "me",
        "tty": "/dev/pts/1",
        "shell": "/bin/bash",
        "level": 2,
        "parents": VSCODE,
    },
    {
        "uuid": "u3",
        "host": "beta",
        "user": "other",
        "tty": "/dev/pts/2",
        "shell": "/bin/bash",
        "level": 1,
        "parents": OVER_SSH,
    },
)

# (session, start, seconds it took, exit code, type, directory, command line)
COMMANDS = (
    (0, "2024-01-02 09:00:00", 1, 0, "file", "/home/me/dev/thing", "git status"),
    (0, "2024-01-02 09:01:00", 1, 0, "alias", "/home/me/dev/thing", "ls -l"),
    (0, "2024-01-02 10:00:00", 20, 1, "file", "/home/me/dev/thing", "make test"),
    (1, "2024-02-03 14:00:00", 3, 0, "file", "/home/me/dev/other", "python run.py | head"),
    (1, "2024-02-03 14:05:00", 0, 127, "none", "/home/me/dev/other", "typo-command --wat"),
    (2, "2024-03-04 12:00:00", 100, 130, "file", "/srv/app", "sleep 100"),
)


def _release() -> None:
    """Hand back the connections the test held.

    The engine pools them, and a pooled connection collected later shows up as
    an unraisable exception in whichever unrelated test happens to be running.
    """
    db.Session.remove()
    db.engine.dispose()


def _clear_test_database() -> None:
    """Delete fixture data only when the database is inside pytest's temp root."""
    test_root = Path(os.environ["SHELLHISTORY_TEST_ROOT"]).resolve()
    database = db.DB_PATH.resolve()
    if not database.is_relative_to(test_root):
        raise RuntimeError(f"refusing to clear non-test database: {database}")
    session = db.Session()
    session.query(db.History).delete()
    session.query(db.ShellSession).delete()
    session.commit()


@pytest.fixture(name="client")
def _fixture_client() -> Iterator[FlaskClient]:
    db.create_tables()
    _clear_test_database()
    session = db.Session()

    rows = []
    for values in SESSIONS:
        row = db.ShellSession(**values)
        session.add(row)
        rows.append(row)
    session.flush()
    for index, start, seconds, code, kind, path, cmd in COMMANDS:
        moment = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
        session.add(
            db.History(
                session_id=rows[index].id,
                start=moment,
                stop=moment + timedelta(seconds=seconds),
                type=kind,
                code=code,
                path=path,
                cmd=cmd,
            ),
        )
    session.commit()

    app.config["TESTING"] = True
    yield app.test_client()

    _clear_test_database()
    _release()


@pytest.fixture(name="empty_client")
def _fixture_empty_client() -> Iterator[FlaskClient]:
    db.create_tables()
    _clear_test_database()
    app.config["TESTING"] = True
    yield app.test_client()
    _release()


def test_cleanup_refuses_a_database_outside_the_test_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never let fixture cleanup reach a developer's configured database."""
    monkeypatch.setenv("SHELLHISTORY_TEST_ROOT", str(tmp_path / "test-root"))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "real-history.sqlite3")

    with pytest.raises(RuntimeError, match="refusing to clear non-test database"):
        _clear_test_database()


def series_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a payload's series by name.

    Parameters:
        payload: What a chart endpoint answered.

    Returns:
        Each series, by its name.
    """
    return {entry["name"]: entry for entry in payload["series"]}


# Reading the process ancestry -------------------------------------------------
@pytest.mark.parametrize(
    ("parents", "axis", "expected"),
    [
        (QTILE, "env", "Terminator"),
        (QTILE, "terminal", "Terminator"),
        (QTILE, "wm", "qtile"),
        (QTILE, "location", "local"),
        (VSCODE, "env", "VS Code"),
        (VSCODE, "editor", "VS Code"),
        (VSCODE, "location", "editor"),
        (OVER_SSH, "env", "SSH"),
        (OVER_SSH, "location", "remote"),
        ("", "env", env.UNKNOWN),
        (None, "location", env.UNKNOWN),
    ],
)
def test_reads_the_environment_out_of_the_ancestry(parents: str | None, axis: str, expected: str) -> None:
    assert env.axis(parents, axis) == expected


def test_prefers_the_nearest_ancestor_that_explains_the_shell() -> None:
    # An editor terminal opened from a real terminal is the editor's, not the terminal's.
    nested = "/bin/bash \n/usr/share/code/code \n/usr/bin/terminator \n"
    assert env.classify(nested).context == "VS Code"
    assert env.classify(nested).terminal == "Terminator"


def test_being_reached_over_the_network_outranks_the_window() -> None:
    remote_editor = "/bin/bash \n/usr/share/code/code \n/usr/sbin/sshd -D \n"
    assert env.classify(remote_editor).location == "remote"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/me/data/dev/griffe/src", "griffe"),
        ("/media/data/dev/griffe", "griffe"),
        ("/home/ldap/me/data/dev/pytb/valib", "pytb"),
        ("/home/me/data/dev/me/aria2p", "aria2p"),
        ("/home/me", "(home)"),
        ("/", "(home)"),
        (None, env.UNKNOWN),
    ],
)
def test_guesses_the_project_from_the_path(path: str | None, expected: str) -> None:
    assert charts.project_of(path, frozenset({"me"})) == expected


# The pages ---------------------------------------------------------------------
@pytest.mark.parametrize("chart", CHARTS, ids=lambda chart: chart.slug)
def test_every_chart_serves_a_page_and_its_data(client: FlaskClient, chart: Any) -> None:
    assert client.get(f"/{chart.slug}").status_code == 200
    assert client.get(f"/{chart.slug}_json").status_code == 200


@pytest.mark.parametrize("chart", CHARTS, ids=lambda chart: chart.slug)
def test_every_chart_survives_an_empty_database(empty_client: FlaskClient, chart: Any) -> None:
    assert empty_client.get(f"/{chart.slug}").status_code == 200
    assert empty_client.get(f"/{chart.slug}_json").status_code == 200


@pytest.mark.parametrize("path", ["/", "/facets_json", "/stats_json", "/wordcloud_json"])
def test_the_pages_that_are_not_charts(client: FlaskClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_chart_library_version_is_pinned(client: FlaskClient) -> None:
    """Do not let a breaking CDN release change the app without a code change."""
    page = client.get("/yearly").get_data(as_text=True)

    assert "code.highcharts.com/13.0.1/highcharts.js" in page
    assert page.count("code.highcharts.com/13.0.1/") == 6
    assert 'src="https://code.highcharts.com/highcharts.js"' not in page


def test_charts_use_a_readable_type_scale(client: FlaskClient) -> None:
    shared = client.get("/static/js/sh.js").get_data(as_text=True)
    calendar = client.get("/static/js/calendar.js").get_data(as_text=True)

    assert 'fontSize: "1.125rem"' in shared
    assert shared.count('fontSize: "1rem"') == 3
    assert 'fontSize: "16px"' in calendar
    assert 'fontSize: "11px"' not in calendar


def test_calendar_logarithmic_color_axis_starts_above_zero(client: FlaskClient) -> None:
    script = client.get("/static/js/calendar.js").get_data(as_text=True)

    assert 'colorAxis: { min: 1, max: data.max' in script
    assert 'type: "logarithmic"' in script


def test_markov_low_counts_remain_visible(client: FlaskClient) -> None:
    script = client.get("/static/js/markov.js").get_data(as_text=True)

    assert "min: 1" in script
    assert 'minColor: "#dbeaf7"' in script
    assert 'type: "logarithmic"' in script


@pytest.mark.parametrize(
    "query",
    [
        "split=host",
        "split=env",
        "split=status",
        "split=terminal&normalize=1",
        "host=alpha",
        "location=remote",
        "level=1",
        "type=file",
        "path=/home/me",
        "cmd=git",
        "since=2024-02-01&until=2024-03-01",
        "granularity=year",
        "split=nonsense&host=nonsense&granularity=nonsense",
    ],
)
@pytest.mark.parametrize("slug", ["over_time", "codes", "sessions", "failures", "projects", "environment"])
def test_every_dimension_narrows_every_chart(client: FlaskClient, slug: str, query: str) -> None:
    assert client.get(f"/{slug}_json?{query}").status_code == 200


# Splitting and filtering --------------------------------------------------------
def test_splitting_gives_one_series_per_value(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?split=host").get_json()
    series = series_of(payload)
    assert set(series) == {"alpha", "beta"}
    assert series["alpha"]["total"] == 3
    assert series["beta"]["total"] == 3


def test_splitting_by_a_dimension_read_from_the_ancestry(client: FlaskClient) -> None:
    series = series_of(client.get("/over_time_json?split=location").get_json())
    assert {name: entry["total"] for name, entry in series.items()} == {"local": 3, "editor": 2, "remote": 1}


def test_not_splitting_gives_one_series(client: FlaskClient) -> None:
    payload = client.get("/over_time_json").get_json()
    assert [entry["name"] for entry in payload["series"]] == ["All"]
    assert payload["series"][0]["total"] == 6


def test_filtering_on_a_column(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?host=alpha").get_json()
    assert payload["series"][0]["total"] == 3


def test_filtering_on_something_read_from_the_ancestry(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?editor=VS%20Code").get_json()
    assert payload["series"][0]["total"] == 2


def test_filtering_and_splitting_at_once(client: FlaskClient) -> None:
    series = series_of(client.get("/over_time_json?host=beta&split=location").get_json())
    assert {name: entry["total"] for name, entry in series.items()} == {"editor": 2, "remote": 1}


def test_filtering_on_a_directory(client: FlaskClient) -> None:
    assert client.get("/over_time_json?path=/home/me/dev/thing").get_json()["series"][0]["total"] == 3
    assert client.get("/over_time_json?path=/home/me").get_json()["series"][0]["total"] == 5


def test_filtering_on_the_beginning_of_a_command(client: FlaskClient) -> None:
    assert client.get("/over_time_json?cmd=git").get_json()["series"][0]["total"] == 1


def test_filtering_on_a_time_span(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?since=2024-02-01&until=2024-02-28").get_json()
    assert payload["series"][0]["total"] == 2


def test_a_filter_matching_nothing_says_so_rather_than_failing(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?host=nowhere").get_json()
    assert payload["series"] == []


def test_normalizing_reports_shares_of_each_series(client: FlaskClient) -> None:
    payload = client.get("/hourly_json?split=host&normalize=1").get_json()
    assert payload["normalize"] is True
    for entry in payload["series"]:
        assert round(sum(entry["data"]), 2) == 100.0


def test_time_series_fill_the_periods_nothing_happened_in(client: FlaskClient) -> None:
    payload = client.get("/over_time_json?granularity=month").get_json()
    # January to March, the empty month between them included.
    assert len(payload["series"][0]["data"]) == 3
    assert [point[1] for point in payload["series"][0]["data"]] == [3, 2, 1]


# What the charts count ----------------------------------------------------------
def test_exit_codes_are_counted(client: FlaskClient) -> None:
    payload = client.get("/codes_json").get_json()
    counted = dict(zip(payload["categories"], payload["series"][0]["data"], strict=True))
    assert counted == {"0": 3, "1": 1, "127": 1, "130": 1}
    meanings = dict(zip(payload["categories"], payload["meanings"], strict=True))
    assert meanings["127"] == "command not found"
    assert meanings["130"] == "SIGINT (Ctrl-C)"


def test_commands_not_found_are_ranked(client: FlaskClient) -> None:
    payload = client.get("/not_found_json").get_json()
    assert payload["categories"] == ["typo-command"]


def test_interrupted_commands_carry_how_long_they_ran(client: FlaskClient) -> None:
    payload = client.get("/interrupted_json").get_json()
    assert payload["categories"] == ["sleep"]
    assert payload["seconds"][0] == pytest.approx(100, abs=1)


def test_the_home_page_numbers(client: FlaskClient) -> None:
    stats = client.get("/stats_json").get_json()
    assert stats["commands"] == 6
    assert stats["shells"] == 3
    assert stats["successRate"] == 50.0
    assert stats["topCommand"]["count"] == 1


def test_sessions_are_measured_one_shell_at_a_time(client: FlaskClient) -> None:
    payload = client.get("/sessions_json").get_json()
    assert payload["shells"] == 3
    assert payload["size"]["summary"]["All"]["count"] == 3


def test_trending_compares_the_two_halves_of_the_span(client: FlaskClient) -> None:
    payload = client.get("/trending_json?minimum=0").get_json()
    # Three months of history, so there is an earlier half and a later one.
    assert payload["early"] == "2024-01 to 2024-02"
    assert payload["late"] == "2024-02 to 2024-03"


def test_trending_says_nothing_when_there_is_only_one_period(client: FlaskClient) -> None:
    payload = client.get("/trending_json?granularity=year").get_json()
    assert payload["categories"] == []
    assert payload["series"] == []


def test_the_dimensions_on_offer_are_the_ones_the_database_has(client: FlaskClient) -> None:
    available = client.get("/facets_json").get_json()
    offered = {facet["name"]: facet["values"] for facet in available["facets"]}
    assert offered["host"] == ["alpha", "beta"]
    assert offered["location"] == ["editor", "local", "remote"]
    assert "container" not in offered  # nothing ran in one, so there is nothing to pick


def test_every_chart_page_names_a_script_that_exists() -> None:
    scripts = {chart.script or "chart" for chart in CHARTS}
    folder = __import__("pathlib").Path(app.root_path) / "static" / "js"
    missing = sorted(name for name in scripts if not (folder / f"{name}.js").is_file())
    assert not missing


@pytest.mark.parametrize("slug", ["markov", "markov_full"])
def test_markov_charts_use_their_square_container(client: FlaskClient, slug: str) -> None:
    page = client.get(f"/{slug}").get_data(as_text=True)
    css = client.get("/static/css/home.css").get_data(as_text=True)
    script = client.get("/static/js/markov.js").get_data(as_text=True)

    assert 'class="sh-chart sh-markov-chart"' in page
    assert "aspect-ratio: 1 / 1" in css
    assert "height: auto" in css
    assert "max-width: 800px" in css
    assert "data.categories.length * 22" not in script
