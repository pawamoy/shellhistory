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

# One filtering and splitting mechanism for every chart.
#
# Charts used to hardcode their query. They now describe what they count and let
# this module decide over which commands: the query string picks the machine, the
# user, the shell, the terminal, the time span, and whether the result comes back
# as one series or as one series per value of some dimension.
#
# Dimensions come in two kinds, and the difference is where the work happens.
# `host` is a column, so it can be filtered in SQL. `terminal` is read out of the
# process ancestry in Python, so it cannot -- instead the query groups by the
# ancestry itself (a couple of thousand distinct values, whatever the number of
# commands) and the grouping is folded down afterwards. Both end up as the same
# shape, and no chart has to know which kind it was given.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any

from flask import request
from sqlalchemy import func

from shellhistory._internal import _db as db
from shellhistory._internal import _env as env

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

ALL = "All"
"""Name of the single series returned when nothing is being split."""

UNKNOWN = env.UNKNOWN
"""Value standing in for a dimension a command says nothing about."""

TRUTHY = frozenset({"1", "true", "yes", "on"})
"""Query string spellings of "yes"."""

# Command types are recorded in the vocabulary of the shell that produced them:
# Zsh's `whence -w` says "command" and "reserved" where Bash's `type -t` says
# "file" and "keyword". Worth keeping in the database, but it splits one concept
# into two slices in a chart, so it is folded at display time.
NORMALIZED_TYPES = {"command": "file", "hashed": "file", "reserved": "keyword"}
"""Zsh type names, mapped onto the Bash names meaning the same thing."""


def normalize_type(name: str | None) -> str:
    """Fold a command type onto one shared vocabulary.

    Parameters:
        name: The type as the shell recorded it.

    Returns:
        The type under its Bash name.
    """
    return NORMALIZED_TYPES.get(name or "none", name or "none")


def _basename(path: str | None) -> str:
    """Return the program name in a path.

    Parameters:
        path: The path of a program, as recorded.

    Returns:
        The bare program name.
    """
    if not path:
        return UNKNOWN
    return path.rsplit("/", 1)[-1] or UNKNOWN


def _plain(value: Any) -> str:
    """Return a column value as a chart label.

    Parameters:
        value: The value as read from the database.

    Returns:
        The value as a string, or `unknown` where there was none.
    """
    if value is None or value == "":
        return UNKNOWN
    return str(value)


@dataclass(frozen=True)
class Facet:
    """One dimension a chart can be filtered or split by."""

    name: str
    """The query string key naming this dimension."""

    label: str
    """How the dimension is named in the interface."""

    column: Any
    """The column carrying it, or carrying what it is derived from."""

    resolve: Callable[[Any], str] = _plain
    """Turns a column value into the label the dimension shows."""

    session_level: bool = True
    """Whether the column belongs to the session rather than to the command."""

    splittable: bool = True
    """Whether a chart may be split by this dimension."""

    sql_filter: Callable[[str], Any] | None = None
    """Builds a WHERE clause for a wanted value, when one can be expressed."""

    @property
    def derived(self) -> bool:
        """Whether picking a value out needs Python rather than a WHERE clause."""
        return self.sql_filter is None


def _environment_facet(name: str, label: str) -> Facet:
    """Build the facet reading one axis out of the process ancestry.

    Parameters:
        name: The axis name, as listed in `_env.AXES`.
        label: How the axis is named in the interface.

    Returns:
        The facet for that axis.
    """
    return Facet(
        name=name,
        label=label,
        column=db.ShellSession.parents,
        resolve=lambda parents, axis=name: env.axis(parents, axis),
    )


FACETS: dict[str, Facet] = {
    facet.name: facet
    for facet in (
        Facet("host", "Host", db.ShellSession.host, sql_filter=lambda v: db.ShellSession.host == v),
        Facet("user", "User", db.ShellSession.user, sql_filter=lambda v: db.ShellSession.user == v),
        Facet("shell", "Shell", db.ShellSession.shell, resolve=_basename),
        Facet("level", "Shell level", db.ShellSession.level, sql_filter=lambda v: db.ShellSession.level == int(v)),
        _environment_facet("env", "Where typed"),
        _environment_facet("terminal", "Terminal"),
        _environment_facet("editor", "Editor"),
        _environment_facet("wm", "Window manager"),
        _environment_facet("display_manager", "Session start"),
        _environment_facet("multiplexer", "Multiplexer"),
        _environment_facet("container", "Container"),
        _environment_facet("location", "Local or remote"),
        Facet(
            "status",
            "Outcome",
            db.History.code,
            resolve=lambda code: "success" if code == 0 else "failure",
            session_level=False,
            sql_filter=lambda v: db.History.code == 0 if v == "success" else db.History.code != 0,
        ),
        Facet(
            "type",
            "Command type",
            db.History.type,
            resolve=normalize_type,
            session_level=False,
        ),
    )
}
"""Every dimension charts can be filtered or split by, by query string key."""

PREFIXES = {
    "path": ("Directory", db.History.path),
    "cmd": ("Command line", db.History.cmd),
}
"""Free text dimensions matched on their beginning rather than chosen from a list."""

GRANULARITIES = {
    "day": "%Y-%m-%d",
    "week": "%Y-%W",
    "month": "%Y-%m",
    "year": "%Y",
}
"""Time buckets a series can be reported over, and how SQLite spells each one."""


def _parse_date(value: str | None) -> datetime | None:
    """Read a `YYYY-MM-DD` bound from the query string.

    Parameters:
        value: The value as given, if any.

    Returns:
        The date, or None when absent or unreadable.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")  # noqa: DTZ007 - local, like every stored timestamp
    except ValueError:
        return None


@dataclass
class Selection:
    """What the query string asked for: which commands, and split how."""

    split: Facet | None = None
    """The dimension to return one series per value of, if any."""

    filters: dict[str, str] = field(default_factory=dict)
    """The wanted value of each dimension being filtered on."""

    prefixes: dict[str, str] = field(default_factory=dict)
    """The wanted beginning of each free text dimension being filtered on."""

    since: datetime | None = None
    """Ignore anything older than this."""

    until: datetime | None = None
    """Ignore anything newer than this."""

    normalize: bool = False
    """Report each series as a share of its own total rather than as a count."""

    granularity: str = "month"
    """The time bucket of charts reporting a value over time."""

    @classmethod
    def from_request(cls, *, splittable: bool = True, default_split: str = "") -> Selection:
        """Read the selection out of the current request.

        Parameters:
            splittable: Whether the chart being served can be split at all.
            default_split: What to split by when the request does not say.

        Returns:
            What was asked for, with anything unrecognized left out.
        """
        args = request.args
        split_name = args.get("split", default_split) or ""
        split = FACETS.get(split_name) if splittable else None
        if split is not None and not split.splittable:
            split = None
        granularity = args.get("granularity", "month")
        return cls(
            split=split,
            filters={name: args[name] for name in FACETS if args.get(name)},
            prefixes={name: args[name] for name in PREFIXES if args.get(name)},
            since=_parse_date(args.get("since")),
            until=_parse_date(args.get("until")),
            normalize=args.get("normalize", "").lower() in TRUTHY,
            granularity=granularity if granularity in GRANULARITIES else "month",
        )

    # These three are read once per row of a grouping, which runs to tens of
    # thousands of rows, so they are worked out once instead.
    @cached_property
    def _derived_filters(self) -> list[Facet]:
        """The filtered dimensions that only Python can pick a value out of."""
        return [FACETS[name] for name in self.filters if FACETS[name].derived]

    @cached_property
    def group_facets(self) -> list[Facet]:
        """The dimensions whose column has to appear in the GROUP BY."""
        facets = [*self._derived_filters]
        if self.split is not None:
            facets.insert(0, self.split)
        return facets

    @cached_property
    def group_columns(self) -> list[Any]:
        """The columns to group by, each appearing once however many facets read it."""
        columns: list[Any] = []
        for facet in self.group_facets:
            if facet.column not in columns:
                columns.append(facet.column)
        return columns

    @cached_property
    def _column_indices(self) -> dict[str, int]:
        """Where each grouped facet's column sits in the grouped columns."""
        places = {str(column): index for index, column in enumerate(self.group_columns)}
        return {facet.name: places[str(facet.column)] for facet in self.group_facets}

    def needs_session(self) -> bool:
        """Tell whether serving this selection has to reach the session table."""
        touched = [*self.group_facets, *(FACETS[name] for name in self.filters)]
        return any(facet.session_level for facet in touched)

    def apply(self, query: Any, *, session_join: bool = False) -> Any:
        """Narrow a query to the selected commands.

        Only what SQL can decide is applied here: the dimensions read out of the
        process ancestry are dropped later, once the rows have been grouped.

        Parameters:
            query: The query to narrow.
            session_join: Join the session table even if no dimension needs it.

        Returns:
            The narrowed query.
        """
        if session_join or self.needs_session():
            query = query.join(db.ShellSession, db.History.session_id == db.ShellSession.id)
        if self.since is not None:
            query = query.filter(db.History.start >= self.since)
        if self.until is not None:
            query = query.filter(db.History.start <= self.until)
        for name, value in self.filters.items():
            facet = FACETS[name]
            if facet.sql_filter is not None:
                try:
                    query = query.filter(facet.sql_filter(value))
                except ValueError:
                    # A level that is not a number matches nothing rather than failing.
                    query = query.filter(db.History.id.is_(None))
        for name, value in self.prefixes.items():
            column = PREFIXES[name][1]
            query = query.filter(column.like(value.replace("%", r"\%") + "%", escape="\\"))
        return query

    def group_of(self, head: Sequence[Any]) -> str | None:
        """Turn the grouped column values of one row into its series name.

        Parameters:
            head: The grouped column values, in `group_columns` order.

        Returns:
            The name of the series the row belongs to, or None if the row was filtered out.
        """
        places = self._column_indices
        for facet in self._derived_filters:
            if facet.resolve(head[places[facet.name]]) != self.filters[facet.name]:
                return None
        if self.split is None:
            return ALL
        return self.split.resolve(head[places[self.split.name]])


def command_selection(select: Selection) -> Selection:
    """Return the part of a selection that the command table alone can answer.

    Session level filters are carried by `session_groups`, so repeating them as a
    join would only make the query walk wider rows for nothing.

    Parameters:
        select: The selection to strip down.

    Returns:
        The same selection, without its session level filters and without its split.
    """
    return Selection(
        filters={name: value for name, value in select.filters.items() if not FACETS[name].session_level},
        prefixes=select.prefixes,
        since=select.since,
        until=select.until,
        granularity=select.granularity,
    )


def selection(*, splittable: bool = True, default_split: str = "") -> Selection:
    """Return what the current request asked for.

    Parameters:
        splittable: Whether the chart being served can be split at all.
        default_split: What to split by when the request does not say.

    Returns:
        The selection.
    """
    return Selection.from_request(splittable=splittable, default_split=default_split)


def _sum(left: tuple, right: tuple) -> tuple:
    """Add two rows of aggregates together, tolerating missing values.

    Parameters:
        left: The aggregates accumulated so far.
        right: The aggregates of another row folding onto the same bucket.

    Returns:
        The combined aggregates.
    """
    return tuple((a or 0) + (b or 0) for a, b in zip(left, right, strict=True))


def aggregate(
    bucket: Any,
    *values: Any,
    select: Selection | None = None,
    extra: Sequence[Any] = (),
    having: Any | None = None,
    combine: Callable[[tuple, tuple], tuple] = _sum,
    session_join: bool = False,
) -> dict[str, dict[Any, tuple]]:
    """Count something per bucket, once per series.

    Parameters:
        bucket: The expression the commands are bucketed by, a sequence of them for a composite
            bucket, or None for one bucket per series.
        values: The aggregates to compute per bucket. Defaults to a plain count.
        select: The selection to honour. Defaults to the current request's.
        extra: Conditions the chart itself imposes, on top of the selection.
        having: An optional HAVING clause.
        combine: How to fold two rows landing in the same bucket of the same series.
        session_join: Join the session table even if no dimension needs it.

    Returns:
        The aggregates, by series name then by bucket.
    """
    select = selection() if select is None else select
    values = values or (func.count(),)
    session = db.Session()
    columns = select.group_columns
    # A chart with nothing to bucket by -- a pie, a total -- asks for one bucket
    # per series rather than for a constant expression SQLite would misread as a
    # column number.
    if bucket is None:
        expressions: list[Any] = []
    elif isinstance(bucket, (list, tuple)):
        expressions = list(bucket)
    else:
        expressions = [bucket]
    query = session.query(*columns, *expressions, *values).select_from(db.History)
    query = select.apply(query, session_join=session_join)
    for condition in extra:
        query = query.filter(condition)
    query = query.group_by(*columns, *expressions)
    if having is not None:
        query = query.having(having)

    width = len(columns)
    depth = len(expressions)
    grouped: dict[str, dict[Any, tuple]] = {}
    for row in query.all():
        name = select.group_of(row[:width])
        if name is None:
            continue
        if depth == 0:
            key = None
        elif depth == 1:
            key = row[width]
        else:
            key = tuple(row[width : width + depth])
        series = grouped.setdefault(name, {})
        previous = series.get(key)
        current = tuple(row[width + depth :])
        # Several ancestries can fold onto one series, so their rows are combined.
        series[key] = current if previous is None else combine(previous, current)
    return grouped


def counts(
    bucket: Any,
    *,
    select: Selection | None = None,
    extra: Sequence[Any] = (),
    session_join: bool = False,
) -> dict[str, dict[Any, int]]:
    """Count commands per bucket, once per series.

    Parameters:
        bucket: The expression the commands are bucketed by, or None for one bucket per series.
        select: The selection to honour. Defaults to the current request's.
        extra: Conditions the chart itself imposes, on top of the selection.
        session_join: Join the session table even if no dimension needs it.

    Returns:
        The counts, by series name then by bucket.
    """
    grouped = aggregate(bucket, select=select, extra=extra, session_join=session_join)
    return {name: {key: value[0] for key, value in buckets.items()} for name, buckets in grouped.items()}


def session_groups(select: Selection | None = None) -> dict[int, str]:
    """Map every selected session to the series it belongs to.

    Reads the whole session table -- which stays small however long the history
    grows -- so that charts walking commands one by one can name each one's
    series with a dictionary lookup instead of a join.

    Parameters:
        select: The selection to honour. Defaults to the current request's.

    Returns:
        The series name of each session that passes the session level filters.
    """
    select = selection() if select is None else select
    session = db.Session()
    facets = [*select.group_facets, *(FACETS[name] for name in select.filters)]
    session_facets = [facet for facet in facets if facet.session_level]
    columns: list[Any] = [db.ShellSession.id]
    seen: dict[str, int] = {}
    for facet in session_facets:
        key = str(facet.column)
        if key not in seen:
            seen[key] = len(columns)
            columns.append(facet.column)

    groups: dict[int, str] = {}
    for row in session.query(*columns).all():
        keep = True
        for facet in session_facets:
            if (
                facet.name in select.filters
                and facet.resolve(row[seen[str(facet.column)]]) != select.filters[facet.name]
            ):
                keep = False
                break
        if not keep:
            continue
        split = select.split
        if split is not None and split.session_level:
            groups[row[0]] = split.resolve(row[seen[str(split.column)]])
        else:
            groups[row[0]] = ALL
    return groups


def labeled_rows(
    *columns: Any,
    select: Selection | None = None,
    extra: Sequence[Any] = (),
    order_by: Sequence[Any] = (),
) -> Iterator[tuple[str, tuple]]:
    """Walk the selected commands, each tagged with the series it belongs to.

    For charts that cannot be expressed as a GROUP BY -- anything looking at what
    follows what, or at a whole session at a time.

    Parameters:
        columns: The command columns to read.
        select: The selection to honour. Defaults to the current request's.
        extra: Conditions the chart itself imposes, on top of the selection.
        order_by: How to order the rows.

    Yields:
        The series name and the requested columns, for each selected command.
    """
    select = selection() if select is None else select
    groups = session_groups(select)
    session = db.Session()

    split = select.split
    history_split = split if split is not None and not split.session_level else None
    selected: list[Any] = [db.History.session_id, *columns]
    if history_split is not None:
        selected.append(history_split.column)

    query = session.query(*selected).select_from(db.History)
    query = command_selection(select).apply(query)
    for condition in extra:
        query = query.filter(condition)
    if order_by:
        query = query.order_by(*order_by)

    width = len(columns)
    for row in query.yield_per(2000):
        name = groups.get(row[0])
        if name is None:
            continue
        if history_split is not None:
            name = history_split.resolve(row[width + 1])
        yield name, tuple(row[1 : width + 1])


# Shaping the answer ----------------------------------------------------------
def _bucket_order(grouped: dict[str, dict[Any, Any]]) -> list[Any]:
    """Return every bucket appearing in any series, in order.

    Parameters:
        grouped: The aggregates, by series then by bucket.

    Returns:
        The sorted buckets.
    """
    buckets: set[Any] = set()
    for series in grouped.values():
        buckets.update(series)
    return sorted(buckets)


def time_buckets(granularity: str, first: str, last: str) -> list[str]:
    """Return every time bucket between two of them, including the empty ones.

    A stacked area with holes in it is a lie, so the gaps are filled rather than
    left out.

    Parameters:
        granularity: One of the keys of `GRANULARITIES`.
        first: The earliest bucket, as SQLite formatted it.
        last: The latest bucket.

    Returns:
        The buckets from the first to the last.
    """
    if granularity == "year":
        return [str(year) for year in range(int(first), int(last) + 1)]
    if granularity == "month":
        buckets = []
        year, month = (int(part) for part in first.split("-"))
        last_year, last_month = (int(part) for part in last.split("-"))
        while (year, month) <= (last_year, last_month):
            buckets.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004 - December
        return buckets
    if granularity == "day":
        buckets = []
        day = datetime.strptime(first, "%Y-%m-%d")  # noqa: DTZ007 - local, like every stored timestamp
        end = datetime.strptime(last, "%Y-%m-%d")  # noqa: DTZ007 - local, like every stored timestamp
        while day <= end:
            buckets.append(day.strftime("%Y-%m-%d"))
            day += timedelta(days=1)
        return buckets
    # Weeks are left as they come: %W numbering makes arithmetic on them a trap.
    return []


def _milliseconds(granularity: str, bucket: str) -> int:
    """Return the start of a time bucket, as milliseconds since the epoch.

    Parameters:
        granularity: One of the keys of `GRANULARITIES`.
        bucket: The bucket, as SQLite formatted it.

    Returns:
        The bucket's first instant.
    """
    if granularity == "year":
        moment = datetime(int(bucket), 1, 1)  # noqa: DTZ001 - local, like every stored timestamp
    elif granularity == "month":
        year, month = (int(part) for part in bucket.split("-"))
        moment = datetime(year, month, 1)  # noqa: DTZ001 - local, like every stored timestamp
    elif granularity == "day":
        moment = datetime.strptime(bucket, "%Y-%m-%d")  # noqa: DTZ007 - local, like every stored timestamp
    else:
        year, week = (int(part) for part in bucket.split("-"))
        moment = datetime.strptime(f"{year}-{week}-1", "%Y-%W-%w")  # noqa: DTZ007 - local, like every stored timestamp
    return int(moment.timestamp() * 1000)


def _shares(data: list[float]) -> list[float]:
    """Return a series as percentages of its own total.

    Parameters:
        data: The values of one series.

    Returns:
        The same values, as shares of their total.
    """
    total = sum(data)
    if not total:
        return [0.0] * len(data)
    return [round(value * 100 / total, 3) for value in data]


def series(
    grouped: dict[str, dict[Any, Any]],
    buckets: Sequence[Any],
    *,
    select: Selection | None = None,
    default: Any = 0,
) -> list[dict[str, Any]]:
    """Turn grouped aggregates into chart series, biggest first.

    Parameters:
        grouped: The aggregates, by series then by bucket.
        buckets: The buckets to report, in order.
        select: The selection being served, which says whether to normalize.
        default: The value to report for a bucket a series has nothing in.

    Returns:
        One entry per series, ready for the chart.
    """
    select = selection() if select is None else select
    out = []
    for name, values in grouped.items():
        data = [values.get(bucket, default) for bucket in buckets]
        total = sum(value for value in data if isinstance(value, (int, float)))
        if select.normalize:
            data = _shares(data)
        out.append({"name": name, "data": data, "total": total})
    # Biggest first, so the legend reads in the order the eye meets the bands.
    out.sort(key=lambda entry: entry["total"], reverse=True)
    return out


def payload(
    grouped: dict[str, dict[Any, Any]],
    buckets: Sequence[Any] | None = None,
    *,
    select: Selection | None = None,
    categories: Sequence[Any] | None = None,
    datetimes: bool = False,
    default: Any = 0,
    **extra: Any,
) -> dict[str, Any]:
    """Assemble the answer a faceted chart endpoint returns.

    Parameters:
        grouped: The aggregates, by series then by bucket.
        buckets: The buckets to report, in order. Defaults to every one seen.
        select: The selection being served. Defaults to the current request's.
        categories: The labels of the buckets, when they differ from the buckets.
        datetimes: Whether to report the buckets as instants rather than as labels.
        default: The value to report for a bucket a series has nothing in.
        extra: Anything else the chart wants alongside its series.

    Returns:
        The chart's data, its series named after the dimension it was split by.
    """
    select = selection() if select is None else select
    buckets = _bucket_order(grouped) if buckets is None else buckets
    built = series(grouped, buckets, select=select, default=default)
    answer: dict[str, Any] = {
        "split": select.split.name if select.split else None,
        "normalize": select.normalize,
        "granularity": select.granularity,
        "series": built,
        **extra,
    }
    if datetimes:
        stamps = [_milliseconds(select.granularity, bucket) for bucket in buckets]
        for entry in built:
            entry["data"] = list(zip(stamps, entry["data"], strict=True))
        answer["xType"] = "datetime"
    else:
        answer["categories"] = list(categories if categories is not None else buckets)
    return answer


def available() -> dict[str, Any]:
    """Return every dimension and the values it actually takes in this database.

    Parameters:
        None.

    Returns:
        The dimensions, each with the values worth offering, plus the free text ones.
    """
    session = db.Session()
    values: dict[str, list[str]] = {}

    rows = session.query(
        db.ShellSession.host,
        db.ShellSession.user,
        db.ShellSession.shell,
        db.ShellSession.level,
        db.ShellSession.parents,
    ).all()
    seen: dict[str, set[str]] = {name: set() for name in FACETS}
    for host, user, shell, level, parents in rows:
        seen["host"].add(_plain(host))
        seen["user"].add(_plain(user))
        seen["shell"].add(_basename(shell))
        seen["level"].add(_plain(level))
        environment = env.classify(parents)
        for name, attribute in env.AXES.items():
            seen[name].add(getattr(environment, attribute))

    for name, found in seen.items():
        if found:
            values[name] = sorted(found)
    values["status"] = ["success", "failure"]
    values["type"] = sorted({normalize_type(row[0]) for row in session.query(db.History.type).distinct().all()})

    return {
        "facets": [
            {"name": name, "label": facet.label, "splittable": facet.splittable, "values": values.get(name, [])}
            for name, facet in FACETS.items()
            if values.get(name) and len(values[name]) > 1
        ],
        "prefixes": [{"name": name, "label": label} for name, (label, _) in PREFIXES.items()],
        "granularities": list(GRANULARITIES),
    }
