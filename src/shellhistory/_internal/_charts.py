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

# What every chart counts, and how.
#
# One function per chart, each returning the data ready to be turned into JSON.
# None of them look at the request: they take the selection -- which commands,
# split by what -- from `_facets`, so filtering by host, by terminal or by time
# span is the same code for all of them and is written once.
#
# The rule followed throughout is to make SQLite do the grouping and to fold the
# groups in Python afterwards. Distributions are grouped at their full
# resolution (there are only ever a few thousand distinct command lengths or
# durations, whatever the number of commands) and only bucketed for display, so
# averages and medians stay exact without a single row leaving the database.

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func

from shellhistory._internal import _db as db
from shellhistory._internal import _env as env
from shellhistory._internal import _facets as facets

if TYPE_CHECKING:
    from collections.abc import Sequence

# Expressions -----------------------------------------------------------------
_TRIMMED = func.ltrim(db.History.cmd)

FIRST_WORD = func.substr(_TRIMMED, 1, func.instr(_TRIMMED + " ", " ") - 1)
"""The program a command line invokes, picked out by SQLite rather than by Python."""

DURATION_MS = (func.julianday(db.History.stop) - func.julianday(db.History.start)) * 86400000.0
"""How long a command took, in milliseconds."""

COMMAND_LENGTH = func.length(db.History.cmd)
"""How many characters a command line has."""

PATH_DEPTH = func.length(db.History.path) - func.length(func.replace(db.History.path, "/", ""))
"""How deep in the tree a command was run, counted in slashes."""

SIGINT = 130
"""Exit code of a command killed by Ctrl-C."""

NOT_FOUND = 127
"""Exit code of a command the shell could not find."""

CODE_MEANINGS = {
    0: "success",
    1: "general error",
    2: "misuse of a builtin",
    126: "found but not executable",
    127: "command not found",
    128: "invalid exit argument",
    129: "SIGHUP",
    130: "SIGINT (Ctrl-C)",
    131: "SIGQUIT",
    137: "SIGKILL",
    139: "SIGSEGV",
    141: "SIGPIPE",
    143: "SIGTERM",
    148: "SIGTSTP (Ctrl-Z)",
    255: "exit code out of range",
}
"""What the exit codes worth naming mean."""

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
"""Weekday names, starting on Monday like every chart here does."""

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
"""Month names."""

# SQLite's %w counts from Sunday; every chart here reads Monday first.
_SQLITE_WEEKDAYS = ("1", "2", "3", "4", "5", "6", "0")


def time_bucket(granularity: str) -> Any:
    """Return the expression bucketing commands into periods of time.

    Parameters:
        granularity: One of the keys of `_facets.GRANULARITIES`.

    Returns:
        The SQLite expression naming a command's period.
    """
    return func.strftime(facets.GRANULARITIES[granularity], db.History.start)


# Weighted statistics ----------------------------------------------------------
def _weighted_total(distribution: dict[Any, int]) -> int:
    """Return how many observations a distribution holds.

    Parameters:
        distribution: Value to number of observations.

    Returns:
        The number of observations.
    """
    return sum(distribution.values())


def _weighted_mean(distribution: dict[float, int]) -> float:
    """Return the mean of a distribution given as value to count.

    Parameters:
        distribution: Value to number of observations.

    Returns:
        The mean, or 0 for an empty distribution.
    """
    total = _weighted_total(distribution)
    if not total:
        return 0.0
    return sum(value * count for value, count in distribution.items()) / total


def _weighted_quantile(distribution: dict[float, int], quantile: float) -> float:
    """Return a quantile of a distribution given as value to count.

    Parameters:
        distribution: Value to number of observations.
        quantile: The wanted quantile, between 0 and 1.

    Returns:
        The value at that quantile, or 0 for an empty distribution.
    """
    total = _weighted_total(distribution)
    if not total:
        return 0.0
    wanted = quantile * total
    seen = 0
    for value in sorted(distribution):
        seen += distribution[value]
        if seen >= wanted:
            return float(value)
    return float(max(distribution))


def _summary(distribution: dict[float, int], *, scale: float = 1.0) -> dict[str, float]:
    """Describe a distribution with the numbers a chart puts in its subtitle.

    Parameters:
        distribution: Value to number of observations.
        scale: What to divide the values by before reporting them.

    Returns:
        The count, mean, median and quartiles.
    """
    return {
        "count": _weighted_total(distribution),
        "mean": round(_weighted_mean(distribution) / scale, 3),
        "median": round(_weighted_quantile(distribution, 0.5) / scale, 3),
        "p25": round(_weighted_quantile(distribution, 0.25) / scale, 3),
        "p75": round(_weighted_quantile(distribution, 0.75) / scale, 3),
        "p95": round(_weighted_quantile(distribution, 0.95) / scale, 3),
    }


# Bucketing distributions for display ------------------------------------------
def _bucketize(
    grouped: dict[str, dict[Any, tuple]],
    edges: Sequence[float],
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    """Fold a full resolution distribution into the bins a chart shows.

    Parameters:
        grouped: Counts by series then by exact value.
        edges: The upper bound of every bin but the last.
        labels: The name of each bin, one more than there are edges.

    Returns:
        Counts by series then by bin name.
    """
    folded: dict[str, dict[str, int]] = {}
    for name, distribution in grouped.items():
        bins: dict[str, int] = dict.fromkeys(labels, 0)
        for value, count in distribution.items():
            if value is None:
                continue
            index = next((i for i, edge in enumerate(edges) if value < edge), len(edges))
            bins[labels[index]] += count[0] if isinstance(count, tuple) else count
        folded[name] = bins
    return folded


def _flatten(grouped: dict[str, dict[Any, tuple]]) -> dict[str, dict[Any, int]]:
    """Drop the aggregate tuples of a grouping down to their first value.

    Parameters:
        grouped: Aggregates by series then by bucket.

    Returns:
        The first aggregate of each bucket.
    """
    return {name: {key: values[0] for key, values in buckets.items()} for name, buckets in grouped.items()}


def _top_buckets(grouped: dict[str, dict[Any, Any]], limit: int) -> list[Any]:
    """Return the buckets holding the most, across every series.

    Parameters:
        grouped: Counts by series then by bucket.
        limit: How many buckets to keep.

    Returns:
        The busiest buckets, busiest first.
    """
    totals: Counter = Counter()
    for buckets in grouped.values():
        for key, value in buckets.items():
            totals[key] += value if isinstance(value, (int, float)) else value[0]
    return [key for key, _ in totals.most_common(limit)]


def _spans(select: facets.Selection) -> dict[str, tuple[datetime, datetime]]:
    """Return the time each series actually covers.

    Averages are per series on purpose: dividing a machine retired in 2019 by
    the whole nine years of history would say more about the other machines
    than about that one.

    Parameters:
        select: The selection being served.

    Returns:
        The first and last command of each series.
    """

    def combine(left: tuple, right: tuple) -> tuple:
        return (min(left[0], right[0]), max(left[1], right[1]))

    grouped = facets.aggregate(
        None,
        func.min(db.History.start),
        func.max(db.History.start),
        select=select,
        combine=combine,
    )
    spans = {}
    for name, buckets in grouped.items():
        first = min(values[0] for values in buckets.values())
        last = max(values[1] for values in buckets.values())
        if first is not None and last is not None:
            spans[name] = (first, last)
    return spans


def _per_period(
    grouped: dict[str, dict[Any, int]],
    select: facets.Selection,
    periods: str,
) -> dict[str, dict[Any, float]]:
    """Turn counts into averages per day, per week or per year of each series.

    Parameters:
        grouped: Counts by series then by bucket.
        select: The selection being served.
        periods: One of `day`, `week` or `year`.

    Returns:
        The averages, by series then by bucket.
    """
    divisors = {"day": 1, "week": 7, "year": 365.2425}
    spans = _spans(select)
    averaged: dict[str, dict[Any, float]] = {}
    for name, buckets in grouped.items():
        span = spans.get(name)
        days = ((span[1] - span[0]).days + 1) if span else 1
        divisor = max(days / divisors[periods], 1.0)
        averaged[name] = {key: round(value / divisor, 3) for key, value in buckets.items()}
    return averaged


# Charts that were already there ------------------------------------------------
def codes(limit: int = 15) -> dict[str, Any]:
    """Return how commands ended, by exit code.

    Parameters:
        limit: How many exit codes to report.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(db.History.code, select=select)
    buckets = _top_buckets(grouped, limit)
    return facets.payload(
        grouped,
        buckets,
        select=select,
        categories=[str(code) if code is not None else "none" for code in buckets],
        meanings=[CODE_MEANINGS.get(code, "") for code in buckets],
    )


def hourly(*, average: bool = False) -> dict[str, Any]:
    """Return when in the day commands are run.

    Parameters:
        average: Report a daily average rather than a total.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(func.strftime("%H", db.History.start), select=select)
    hours = [f"{hour:02d}" for hour in range(24)]
    if average:
        grouped = _per_period(grouped, select, "day")
    return facets.payload(grouped, hours, select=select)


def daily(*, average: bool = False) -> dict[str, Any]:
    """Return which days of the week commands are run on.

    Parameters:
        average: Report a weekly average rather than a total.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(func.strftime("%w", db.History.start), select=select)
    if average:
        grouped = _per_period(grouped, select, "week")
    return facets.payload(grouped, _SQLITE_WEEKDAYS, select=select, categories=DAY_NAMES)


def monthly(*, average: bool = False) -> dict[str, Any]:
    """Return which months of the year commands are run in.

    Parameters:
        average: Report a yearly average rather than a total.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(func.strftime("%m", db.History.start), select=select)
    if average:
        grouped = _per_period(grouped, select, "year")
    months = [f"{month:02d}" for month in range(1, 13)]
    return facets.payload(grouped, months, select=select, categories=MONTH_NAMES)


def yearly() -> dict[str, Any]:
    """Return how many commands were run each year.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(func.strftime("%Y", db.History.start), select=select)
    buckets = facets._bucket_order(grouped)
    if buckets:
        buckets = facets.time_buckets("year", buckets[0], buckets[-1])
    return facets.payload(grouped, buckets, select=select)


def over_time() -> dict[str, Any]:
    """Return how many commands were run over time.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(time_bucket(select.granularity), select=select)
    buckets = facets._bucket_order(grouped)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets
    return facets.payload(grouped, buckets, select=select, datetimes=True)


def length() -> dict[str, Any]:
    """Return how long command lines are.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.aggregate(COMMAND_LENGTH, select=select)
    exact = _flatten(grouped)
    edges = [10, 20, 30, 40, 50, 60, 80, 100, 150, 200, 300]
    labels = [
        "<10",
        "10-19",
        "20-29",
        "30-39",
        "40-49",
        "50-59",
        "60-79",
        "80-99",
        "100-149",
        "150-199",
        "200-299",
        "300+",
    ]
    return facets.payload(
        _bucketize(grouped, edges, labels),
        labels,
        select=select,
        summary={name: _summary(distribution) for name, distribution in exact.items()},
    )


DURATION_EDGES = [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 300000, 1800000]
"""Where one duration bin ends and the next begins, in milliseconds."""

DURATION_LABELS = [
    "<0.1s",
    "0.1-0.25s",
    "0.25-0.5s",
    "0.5-1s",
    "1-2s",
    "2-5s",
    "5-10s",
    "10-30s",
    "30s-1m",
    "1-5m",
    "5-30m",
    ">30m",
]
"""What each duration bin is called."""


def duration() -> dict[str, Any]:
    """Return how long commands take.

    Bucketed on a roughly logarithmic scale: almost everything a shell runs
    finishes in well under a second, so linear seconds put the whole history in
    the first bar and told nobody anything.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    # Rounded to a hundredth of a second before grouping, which is plenty for a
    # median and keeps the number of groups in the thousands.
    grouped = facets.aggregate(
        func.cast(DURATION_MS / 10.0, db.Integer),
        select=select,
        extra=(db.History.stop.isnot(None),),
    )
    exact = {name: {(key or 0) * 10.0: value[0] for key, value in buckets.items()} for name, buckets in grouped.items()}
    binned = _bucketize(exact, DURATION_EDGES, DURATION_LABELS)
    return facets.payload(
        binned,
        DURATION_LABELS,
        select=select,
        summary={name: _summary(distribution, scale=1000.0) for name, distribution in exact.items()},
    )


def top_commands(limit: int = 20, *, full: bool = False) -> dict[str, Any]:
    """Return the most used commands.

    Parameters:
        limit: How many commands to report.
        full: Count whole command lines rather than only the program invoked.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(db.History.cmd if full else FIRST_WORD, select=select)
    buckets = _top_buckets(grouped, limit)
    return facets.payload(grouped, buckets, select=select)


def types(*, raw: bool = False) -> dict[str, Any]:
    """Return what kind of thing the commands invoked were.

    Parameters:
        raw: Keep each shell's own vocabulary instead of folding onto one.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(db.History.type, select=select)
    if not raw:
        folded: dict[str, dict[Any, int]] = {}
        for name, buckets in grouped.items():
            target = folded.setdefault(name, defaultdict(int))
            for key, value in buckets.items():
                target[facets.normalize_type(key)] += value
        grouped = folded
    else:
        grouped = {name: {key or "none": value for key, value in buckets.items()} for name, buckets in grouped.items()}
    buckets = _top_buckets(grouped, 20)
    return facets.payload(grouped, buckets, select=select)


def wordcloud(limit: int = 150) -> dict[str, Any]:
    """Return the most used commands, for the cloud on the home page.

    Parameters:
        limit: How many words the cloud may hold.

    Returns:
        The chart's data.
    """
    select = facets.selection(splittable=False)
    grouped = facets.counts(FIRST_WORD, select=select)
    counted: Counter = Counter()
    for buckets in grouped.values():
        counted.update(buckets)
    return {"words": [{"name": word, "weight": count} for word, count in counted.most_common(limit) if word]}


def markov(limit: int = 40, *, full: bool = False) -> dict[str, Any]:
    """Return which commands follow which.

    Parameters:
        limit: How many transitions to report.
        full: Chain whole command lines rather than only the program invoked.

    Returns:
        The chart's data.
    """
    select = facets.selection(splittable=False)
    column = db.History.cmd if full else FIRST_WORD
    transitions: Counter = Counter()
    previous: str | None = None
    for _, (current,) in facets.labeled_rows(
        column,
        select=select,
        order_by=(db.History.start,),
    ):
        if previous is not None:
            transitions[(previous, current)] += 1
        previous = current

    common = transitions.most_common(limit)
    words: list[str] = []
    for (before, after), _ in common:
        for word in (before, after):
            if word not in words:
                words.append(word)
    return {
        "categories": words,
        "series": [[words.index(after), words.index(before), count] for (before, after), count in common],
    }


def share(select: facets.Selection | None = None) -> dict[str, Any]:
    """Return the total number of commands in each series.

    The one chart every dimension can be looked at through: split it by host, by
    terminal, by editor, and the same pie answers a different question each time.

    Parameters:
        select: The selection to honour. Defaults to the current request's.

    Returns:
        The chart's data.
    """
    select = facets.selection() if select is None else select
    grouped = facets.counts(None, select=select)
    slices = sorted(
        ((name, sum(buckets.values())) for name, buckets in grouped.items()),
        key=lambda entry: entry[1],
        reverse=True,
    )
    total = sum(count for _, count in slices)
    return {
        "split": select.split.name if select.split else None,
        "total": total,
        "slices": [{"name": name, "y": count} for name, count in slices],
    }


# Reliability -------------------------------------------------------------------
FAILED = case((db.History.code.is_(None), 0), (db.History.code != 0, 1), else_=0)
"""One for a command that failed, zero for one that worked or never said."""

KNOWN_CODE = case((db.History.code.is_(None), 0), else_=1)
"""One for a command whose exit code was recorded."""


def _rates(
    grouped: dict[str, dict[Any, tuple]],
    numerator: int = 1,
    denominator: int = 0,
) -> dict[str, dict[Any, float | None]]:
    """Turn pairs of totals into percentages.

    Parameters:
        grouped: Aggregates by series then by bucket.
        numerator: Which aggregate is the part.
        denominator: Which aggregate is the whole.

    Returns:
        The percentages, None where there was nothing to divide.
    """
    return {
        name: {
            key: (round(values[numerator] * 100 / values[denominator], 2) if values[denominator] else None)
            for key, values in buckets.items()
        }
        for name, buckets in grouped.items()
    }


def _unnormalized(select: facets.Selection) -> facets.Selection:
    """Return a selection that will not turn its series into shares.

    A rate is already a share of something, and scaling one to add up to a
    hundred would be meaningless.

    Parameters:
        select: The selection being served.

    Returns:
        The same selection with normalization switched off.
    """
    return replace(select, normalize=False)


FAILURE_MINIMUM = 20
"""How often a command must have run before its failure rate means anything."""


def failures(limit: int = 25, minimum: int = FAILURE_MINIMUM) -> dict[str, Any]:
    """Return how often commands fail, over time and by command.

    Parameters:
        limit: How many commands to rank.
        minimum: How many times a command must have run to be ranked at all.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    plain = _unnormalized(select)

    over = facets.aggregate(time_bucket(select.granularity), KNOWN_CODE, FAILED, select=select)
    buckets = facets._bucket_order(over)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets

    by_command = facets.aggregate(FIRST_WORD, KNOWN_CODE, FAILED, select=select)
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for series in by_command.values():
        for word, (ran, failed) in series.items():
            totals[word][0] += ran
            totals[word][1] += failed
    ranked = sorted(
        ((word, ran, failed) for word, (ran, failed) in totals.items() if word and ran >= minimum),
        key=lambda entry: (entry[2] / entry[1], entry[2]),
        reverse=True,
    )[:limit]
    words = [word for word, _, _ in ranked]

    overall = sum(counts[0] for series in by_command.values() for counts in series.values())
    broke = sum(counts[1] for series in by_command.values() for counts in series.values())

    return {
        "overTime": facets.payload(
            _rates(over),
            buckets,
            select=plain,
            datetimes=True,
            default=None,
        ),
        "byCommand": facets.payload(
            _rates(by_command),
            words,
            select=plain,
            default=None,
            runs=[ran for _, ran, _ in ranked],
            failures=[failed for _, _, failed in ranked],
        ),
        "minimum": minimum,
        "rate": round(broke * 100 / overall, 2) if overall else 0,
        "ran": overall,
        "broke": broke,
    }


def _ranked_by_code(code: int, limit: int) -> dict[str, Any]:
    """Return the commands most often ending with one particular exit code.

    Parameters:
        code: The exit code to look at.
        limit: How many commands to report.

    Returns:
        The chart's data, with how long each command ran before ending that way.
    """
    select = facets.selection()
    grouped = facets.aggregate(
        FIRST_WORD,
        func.count(),
        func.coalesce(func.sum(DURATION_MS), 0.0),
        select=select,
        extra=(db.History.code == code,),
    )
    words = [word for word in _top_buckets(grouped, limit) if word]
    spent: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for series in grouped.values():
        for word, (count, total) in series.items():
            spent[word][0] += count
            spent[word][1] += total or 0.0
    return facets.payload(
        _flatten(grouped),
        words,
        select=select,
        seconds=[round(spent[word][1] / spent[word][0] / 1000, 2) if spent[word][0] else 0 for word in words],
    )


def not_found(limit: int = 25) -> dict[str, Any]:
    """Return the commands the shell most often could not find.

    Typos, tools left behind on another machine, aliases never defined: the list
    doubles as a to-do list of things to install or spell differently.

    Parameters:
        limit: How many commands to report.

    Returns:
        The chart's data.
    """
    return _ranked_by_code(NOT_FOUND, limit)


def interrupted(limit: int = 25) -> dict[str, Any]:
    """Return the commands most often given up on with Ctrl-C.

    Parameters:
        limit: How many commands to report.

    Returns:
        The chart's data, with how long each was left running first.
    """
    return _ranked_by_code(SIGINT, limit)


def duration_by_code(limit: int = 10) -> dict[str, Any]:
    """Return how long commands run before ending with each exit code.

    Answers whether failures fail fast: a box per code, drawn from the whole
    distribution rather than from an average that a single overnight build would
    drag out of shape.

    Parameters:
        limit: How many exit codes to report.

    Returns:
        The chart's data.
    """
    select = facets.selection(splittable=False)
    # Tenths of a second: fine enough for a quartile, coarse enough to keep the
    # number of groups small.
    grouped = facets.aggregate(
        (db.History.code, func.cast(DURATION_MS / 100.0, db.Integer)),
        select=select,
        extra=(db.History.stop.isnot(None),),
    )
    distributions: dict[Any, dict[float, int]] = defaultdict(dict)
    for series in grouped.values():
        for (code, tenths), (count,) in series.items():
            key = (tenths or 0) / 10.0
            distributions[code][key] = distributions[code].get(key, 0) + count

    codes_seen = sorted(distributions, key=lambda code: _weighted_total(distributions[code]), reverse=True)[:limit]
    boxes = []
    for code in codes_seen:
        distribution = distributions[code]
        boxes.append(
            [
                round(_weighted_quantile(distribution, 0.05), 2),
                round(_weighted_quantile(distribution, 0.25), 2),
                round(_weighted_quantile(distribution, 0.5), 2),
                round(_weighted_quantile(distribution, 0.75), 2),
                round(_weighted_quantile(distribution, 0.95), 2),
            ],
        )
    return {
        "categories": [str(code) if code is not None else "none" for code in codes_seen],
        "meanings": [CODE_MEANINGS.get(code, "") for code in codes_seen],
        "counts": [_weighted_total(distributions[code]) for code in codes_seen],
        "boxes": boxes,
    }


RETRY_WINDOW = 300
"""How long after a failure a command still counts as another go at it, in seconds."""

RETRY_BUCKETS = ("1", "2", "3", "4", "5", "6-10", "11+")
"""How many goes at the same thing a chain took."""


def retries(limit: int = 20, window: int = RETRY_WINDOW) -> dict[str, Any]:
    """Return how many goes it takes to get a command right.

    A chain is a run of commands invoking the same program in the same shell,
    each following a failure of the one before by less than the window. Its
    length is how many attempts that took, and whether it ends on a zero says
    whether it ever worked.

    Parameters:
        limit: How many programs to rank.
        window: How long a failure stays worth retrying, in seconds.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    gap = timedelta(seconds=window)

    attempts: dict[str, Counter] = defaultdict(Counter)
    chains: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    open_chain: dict[str, Any] = {}

    def close() -> None:
        if not open_chain:
            return
        name = open_chain["series"]
        count = open_chain["attempts"]
        label = RETRY_BUCKETS[min(count, 5) - 1] if count <= 5 else ("6-10" if count <= 10 else "11+")  # noqa: PLR2004 - the bucket edges
        attempts[name][label] += 1
        record = chains[open_chain["word"]]
        record[0] += 1
        if count > 1:
            record[1] += 1

    for name, (session_id, start, code, word) in facets.labeled_rows(
        db.History.session_id,
        db.History.start,
        db.History.code,
        FIRST_WORD,
        select=select,
        order_by=(db.History.session_id, db.History.start),
    ):
        same = (
            open_chain
            and open_chain["session"] == session_id
            and open_chain["word"] == word
            and open_chain["failed"]
            and start is not None
            and open_chain["last"] is not None
            and start - open_chain["last"] <= gap
        )
        if same:
            open_chain["attempts"] += 1
        else:
            close()
            open_chain.clear()
            open_chain.update({"series": name, "session": session_id, "word": word, "attempts": 1})
        open_chain["last"] = start
        open_chain["failed"] = code is not None and code != 0
    close()

    ranked = sorted(
        ((word, total, retried) for word, (total, retried) in chains.items() if word and total >= 20),  # noqa: PLR2004 - too rare to rank below this
        key=lambda entry: (entry[2] / entry[1], entry[2]),
        reverse=True,
    )[:limit]

    return {
        "attempts": facets.payload(
            {name: dict(counted) for name, counted in attempts.items()},
            RETRY_BUCKETS,
            select=_unnormalized(select),
        ),
        "worst": {
            "categories": [word for word, _, _ in ranked],
            "rates": [round(retried * 100 / total, 2) for _, total, retried in ranked],
            "chains": [total for _, total, _ in ranked],
        },
        "window": window,
    }


# Sessions -----------------------------------------------------------------------
def _period_of(moment: datetime, granularity: str) -> str:
    """Return the time bucket an instant falls in, spelled the way SQLite spells it.

    Parameters:
        moment: The instant to place.
        granularity: One of the keys of `_facets.GRANULARITIES`.

    Returns:
        The bucket's name.
    """
    return moment.strftime(facets.GRANULARITIES[granularity])


def _session_rows(select: facets.Selection) -> list[tuple[str, datetime, datetime, int]]:
    """Return one row per selected shell, rather than one per command.

    Parameters:
        select: The selection being served.

    Returns:
        The series, first command, last moment and number of commands of each shell.
    """
    groups = facets.session_groups(select)
    session = db.Session()
    query = session.query(
        db.History.session_id,
        func.min(db.History.start),
        func.max(db.History.stop),
        func.max(db.History.start),
        func.count(),
    ).select_from(db.History)
    query = facets.command_selection(select).apply(query).group_by(db.History.session_id)

    rows = []
    for session_id, first, last_stop, last_start, count in query.all():
        name = groups.get(session_id)
        if name is None or first is None:
            continue
        last = max(value for value in (last_stop, last_start, first) if value is not None)
        rows.append((name, first, last, count))
    return rows


SESSION_LENGTH_EDGES = [1, 5, 15, 30, 60, 120, 240, 480]
"""Where one shell lifetime bin ends and the next begins, in minutes."""

SESSION_LENGTH_LABELS = ["<1m", "1-5m", "5-15m", "15-30m", "30-60m", "1-2h", "2-4h", "4-8h", "8h+"]
"""What each shell lifetime bin is called."""

SESSION_SIZE_EDGES = [2, 3, 4, 5, 6, 11, 21, 51, 101, 501]
"""Where one bin of commands per shell ends and the next begins."""

SESSION_SIZE_LABELS = ["1", "2", "3", "4", "5", "6-10", "11-20", "21-50", "51-100", "101-500", "500+"]
"""What each bin of commands per shell is called."""


def sessions() -> dict[str, Any]:
    """Return how shells are used: how long they live, how much they hold, how many at once.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    rows = _session_rows(select)

    minutes: dict[str, dict[float, int]] = defaultdict(dict)
    sizes: dict[str, dict[float, int]] = defaultdict(dict)
    for name, first, last, count in rows:
        lived = (last - first).total_seconds() / 60
        minutes[name][lived] = minutes[name].get(lived, 0) + 1
        sizes[name][count] = sizes[name].get(count, 0) + 1

    # How many shells were alive at once, sampled over time. Walking the opening
    # and closing of every shell in order costs one pass and gives the real peak
    # rather than a count of shells that merely overlapped the bucket.
    events: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for name, first, last, _ in rows:
        events[name].append((first, 1))
        events[name].append((last, -1))
    peaks: dict[str, dict[Any, int]] = defaultdict(dict)
    for name, timeline in events.items():
        timeline.sort()
        alive = 0
        previous: datetime | None = None
        for moment, change in timeline:
            if previous is not None and alive:
                # The level held from one event to the next, so every period in
                # between saw at least that many shells, not only the two ends.
                first = _period_of(previous, select.granularity)
                last = _period_of(moment, select.granularity)
                spanned = facets.time_buckets(select.granularity, first, last) or sorted({first, last})
                for bucket in spanned:
                    peaks[name][bucket] = max(peaks[name].get(bucket, 0), alive)
            alive += change
            previous = moment

    buckets = facets._bucket_order(peaks)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets

    plain = _unnormalized(select)
    return {
        "length": facets.payload(
            _bucketize(minutes, SESSION_LENGTH_EDGES, SESSION_LENGTH_LABELS),  # ty: ignore[invalid-argument-type]
            SESSION_LENGTH_LABELS,
            select=select,
            summary={name: _summary(distribution) for name, distribution in minutes.items()},
        ),
        "size": facets.payload(
            _bucketize(sizes, SESSION_SIZE_EDGES, SESSION_SIZE_LABELS),  # ty: ignore[invalid-argument-type]
            SESSION_SIZE_LABELS,
            select=select,
            summary={name: _summary(distribution) for name, distribution in sizes.items()},
        ),
        "concurrency": facets.payload(peaks, buckets, select=plain, datetimes=True),
        "shells": len(rows),
    }


def nesting() -> dict[str, Any]:
    """Return how deeply nested the shells commands run in are.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.counts(db.ShellSession.level, select=select, session_join=True)
    levels = sorted({level for buckets in grouped.values() for level in buckets}, key=lambda v: (v is None, v))
    return facets.payload(
        grouped,
        levels,
        select=select,
        categories=[str(level) if level is not None else "?" for level in levels],
    )


BURST_GAP = 1800
"""How long a pause has to be to end a stretch of work, in seconds."""

BURST_EDGES = [1, 5, 15, 30, 60, 120, 240]
"""Where one bin of working stretches ends and the next begins, in minutes."""

BURST_LABELS = ["<1m", "1-5m", "5-15m", "15-30m", "30-60m", "1-2h", "2-4h", "4h+"]
"""What each bin of working stretches is called."""

LONG_GAP = 86400
"""How long a pause has to be to be worth listing, in seconds."""


def rhythm(gap: int = BURST_GAP, limit: int = 20) -> dict[str, Any]:
    """Return the shape of a working day: stretches of commands, and the pauses between them.

    A stretch ends when nothing is typed for longer than the gap. What is left is
    a decent proxy for sitting down at the machine and getting up again.

    Parameters:
        gap: How long a pause has to be to end a stretch, in seconds.
        limit: How many of the longest pauses to list.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    threshold = timedelta(seconds=gap)
    long_enough = timedelta(seconds=LONG_GAP)

    lengths: dict[str, dict[float, int]] = defaultdict(dict)
    per_day: dict[str, Counter] = defaultdict(Counter)
    last: dict[str, datetime] = {}
    opened: dict[str, datetime] = {}
    longest: list[tuple[float, str, str]] = []

    def close(name: str) -> None:
        started, ended = opened.pop(name, None), last.get(name)
        if started is None or ended is None:
            return
        spent = (ended - started).total_seconds() / 60
        lengths[name][spent] = lengths[name].get(spent, 0) + 1
        per_day[name][started.date()] += 1

    for name, (start,) in facets.labeled_rows(db.History.start, select=select, order_by=(db.History.start,)):
        if start is None:
            continue
        previous = last.get(name)
        if previous is not None and start - previous > threshold:
            if start - previous >= long_enough:
                longest.append(
                    (
                        (start - previous).total_seconds() / 86400,
                        previous.isoformat(" ", "seconds"),
                        start.isoformat(" ", "seconds"),
                    ),
                )
            close(name)
        opened.setdefault(name, start)
        last[name] = start
    for name in list(opened):
        close(name)

    longest.sort(reverse=True)
    summary = {}
    for name, counted in per_day.items():
        days = len(counted)
        summary[name] = {
            "stretches": sum(counted.values()),
            "days": days,
            "perDay": round(sum(counted.values()) / days, 2) if days else 0,
            "longest": round(max(lengths[name], default=0), 1),
            **_summary(lengths[name]),
        }

    return {
        "length": facets.payload(
            _bucketize(lengths, BURST_EDGES, BURST_LABELS),  # ty: ignore[invalid-argument-type]
            BURST_LABELS,
            select=select,
            summary=summary,
        ),
        "gaps": [{"days": round(days, 2), "from": start, "to": end} for days, start, end in longest[:limit]],
        "gap": gap,
    }


# Directories and projects -------------------------------------------------------
CONTAINER_DIRS = frozenset(
    {
        "code",
        "codes",
        "data",
        "desktop",
        "dev",
        "devel",
        "documents",
        "git",
        "media",
        "mnt",
        "opt",
        "perso",
        "pro",
        "project",
        "projects",
        "repo",
        "repos",
        "run",
        "source",
        "sources",
        "src",
        "srv",
        "tmp",
        "var",
        "work",
        "workspace",
        "workspaces",
    },
)
"""Directories that hold projects rather than being one."""

HOME_DIRS = frozenset({"home", "users"})
"""Directories whose next segment is a user name rather than a project."""


HOME_DEPTH = 3
"""How far below /home a user directory is still looked for."""


def project_of(path: str | None, users: frozenset[str] = frozenset()) -> str:
    """Return the project a directory belongs to.

    Guessed rather than known: without reaching the filesystem there is no
    `.git` to look for, so the first segment that is neither a home nor one of
    the places projects are merely kept is taken to be the project. It is what
    makes `~/data/dev/griffe/src` and `/media/data/dev/griffe` one project.

    Parameters:
        path: The directory a command ran in.
        users: The user names seen in the history, which are directories rather
            than projects however deep under /home they sit.

    Returns:
        The project's name.
    """
    if not path:
        return facets.UNKNOWN
    segments = [segment for segment in path.split("/") if segment]
    index = 0
    home = 0
    while index < len(segments):
        segment = segments[index]
        lowered = segment.lower()
        if lowered in HOME_DIRS:
            # Everything down to the user's own directory is still the way in,
            # which on a machine with LDAP accounts can be /home/ldap/someone.
            home = HOME_DEPTH
            index += 1
            continue
        if segment in users:
            # A user name is a home, and just as often the directory somebody
            # groups their own repositories under: neither is a project.
            home = 0
            index += 1
            continue
        if home:
            home -= 1
            index += 1
            continue
        if lowered in CONTAINER_DIRS:
            index += 1
            continue
        return segment
    return "(home)"


def known_users() -> frozenset[str]:
    """Return the user names the history was recorded under.

    Returns:
        The user names.
    """
    session = db.Session()
    return frozenset(row[0] for row in session.query(db.ShellSession.user).distinct().all() if row[0])


def directories(limit: int = 25) -> dict[str, Any]:
    """Return where commands are run, and how deep in the tree.

    Parameters:
        limit: How many directories to report.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    by_path = facets.counts(db.History.path, select=select)
    paths = [path for path in _top_buckets(by_path, limit) if path]
    by_depth = facets.counts(PATH_DEPTH, select=select)
    depths = sorted({depth for buckets in by_depth.values() for depth in buckets}, key=lambda v: (v is None, v))
    return {
        "top": facets.payload(by_path, paths, select=select),
        "depth": facets.payload(
            by_depth,
            depths,
            select=select,
            categories=[str(depth) if depth is not None else "?" for depth in depths],
        ),
    }


def projects(limit: int = 15) -> dict[str, Any]:
    """Return which project was being worked on when.

    Parameters:
        limit: How many projects to report.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.aggregate((time_bucket(select.granularity), db.History.path), select=select)
    users = known_users()

    folded: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    totals: Counter = Counter()
    for name, buckets in grouped.items():
        for (period, path), (count,) in buckets.items():
            key = (period, project_of(path, users))
            folded[name][key] = folded[name].get(key, 0) + count
            totals[key[1]] += count

    keep = [project for project, _ in totals.most_common(limit)]
    periods = sorted({period for buckets in folded.values() for period, _ in buckets})
    if periods:
        periods = facets.time_buckets(select.granularity, periods[0], periods[-1]) or periods

    # One series per project: the split, if there is one, becomes the pie instead,
    # because a stacked area of hosts times projects reads as nothing at all.
    per_project: dict[str, dict[str, int]] = {project: {} for project in keep}
    for buckets in folded.values():
        for (period, project), count in buckets.items():
            if project in per_project:
                per_project[project][period] = per_project[project].get(period, 0) + count

    return {
        "timeline": facets.payload(per_project, periods, select=_unnormalized(select), datetimes=True),
        "totals": [{"name": project, "y": totals[project]} for project in keep],
    }


# What command lines are made of --------------------------------------------------
TOOL_PATTERN = re.compile(r"^[\w.+-]{1,40}$")
"""What a tool name may look like before it is allowed into a LIKE pattern."""


def tools(limit: int = 20) -> list[str]:
    """Return the most used programs, to choose between on the pages that need one.

    Parameters:
        limit: How many programs to offer.

    Returns:
        The programs, most used first.
    """
    grouped = facets.counts(FIRST_WORD)
    return [word for word in _top_buckets(grouped, limit) if word and TOOL_PATTERN.match(word)]


def _tool_rows(tool: str, select: facets.Selection) -> Any:
    """Walk the command lines invoking one program.

    Parameters:
        tool: The program the command lines start with.
        select: The selection being served.

    Yields:
        The series name and command line of each matching command.
    """
    return facets.labeled_rows(
        db.History.cmd,
        select=select,
        extra=(db.History.cmd.like(tool + " %"),),
    )


def subcommands(tool: str = "git", limit: int = 25) -> dict[str, Any]:
    """Return which subcommands of a program get used.

    `git` in the top commands chart is one bar saying nothing; broken up it is a
    portrait of how somebody works.

    Parameters:
        tool: The program to break up.
        limit: How many subcommands to report.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    if not TOOL_PATTERN.match(tool):
        return {"tool": tool, "series": [], "categories": [], "tools": tools()}

    grouped: dict[str, Counter] = defaultdict(Counter)
    for name, (cmd,) in _tool_rows(tool, select):
        for word in cmd.split()[1:]:
            # Skip the program's own options, and the values they take: a path
            # after `git -C` is not a subcommand.
            if word.startswith("-") or "=" in word or "/" in word:
                continue
            grouped[name][word] += 1
            break

    counted = {name: dict(counter) for name, counter in grouped.items()}
    found = _top_buckets(counted, limit)
    return facets.payload(counted, found, select=select, tool=tool, tools=tools())


MIN_FLAG = 2
"""How short a word can be and still be an option rather than a stray dash."""


def flags(tool: str = "", limit: int = 30) -> dict[str, Any]:
    """Return which options get passed, overall or to one program.

    Parameters:
        tool: The program to look at, or the empty string for all of them.
        limit: How many options to report.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    if tool and not TOOL_PATTERN.match(tool):
        tool = ""

    rows = _tool_rows(tool, select) if tool else facets.labeled_rows(db.History.cmd, select=select)
    grouped: dict[str, Counter] = defaultdict(Counter)
    for name, (cmd,) in rows:
        for word in cmd.split()[1:]:
            if len(word) < MIN_FLAG or not word.startswith("-") or word in ("--", "-"):
                continue
            # `--output=json` and `--output json` are the same option.
            grouped[name][word.split("=", 1)[0]] += 1

    counted = {name: dict(counter) for name, counter in grouped.items()}
    found = _top_buckets(counted, limit)
    return facets.payload(counted, found, select=select, tool=tool, tools=tools())


def _occurrences(needle: str) -> Any:
    """Return the expression counting how often a string appears in a command line.

    Parameters:
        needle: The string to look for.

    Returns:
        The SQLite expression counting it.
    """
    shrunk = func.length(db.History.cmd) - func.length(func.replace(db.History.cmd, needle, ""))
    return shrunk / len(needle)


COMPLEXITY_LABELS = ("0", "1", "2", "3", "4", "5+")
"""How many operators one command line strings together."""


def complexity() -> dict[str, Any]:
    """Return how elaborate command lines get, over time.

    Counts what strings commands together -- pipes, `&&`, `||`, `;` -- rather
    than characters, so that a long path does not read as a complicated command.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    double_pipes = _occurrences("||")
    double_amps = _occurrences("&&")
    single_pipes = _occurrences("|") - 2 * double_pipes
    semicolons = _occurrences(";")
    operators = single_pipes + double_pipes + double_amps + semicolons

    grouped = facets.aggregate(
        time_bucket(select.granularity),
        func.count(),
        func.sum(operators),
        func.sum(single_pipes),
        func.sum(double_pipes + double_amps),
        func.sum(semicolons),
        func.sum(COMMAND_LENGTH),
        select=select,
    )
    buckets = facets._bucket_order(grouped)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets

    averages = {
        name: {key: round((values[1] or 0) / values[0], 3) if values[0] else None for key, values in series.items()}
        for name, series in grouped.items()
    }
    lengths = {
        name: {key: round((values[5] or 0) / values[0], 1) if values[0] else None for key, values in series.items()}
        for name, series in grouped.items()
    }

    ran = sum(values[0] for series in grouped.values() for values in series.values())
    parts = {
        "pipes": sum(values[2] or 0 for series in grouped.values() for values in series.values()),
        "andOr": sum(values[3] or 0 for series in grouped.values() for values in series.values()),
        "semicolons": sum(values[4] or 0 for series in grouped.values() for values in series.values()),
    }

    distribution = facets.counts(
        case(
            (operators >= 5, "5+"),  # noqa: PLR2004 - the last bin
            else_=func.cast(func.cast(operators, db.Integer), db.String),
        ),
        select=select,
    )

    plain = _unnormalized(select)
    return {
        "overTime": facets.payload(averages, buckets, select=plain, datetimes=True, default=None),
        "lengths": facets.payload(lengths, buckets, select=plain, datetimes=True, default=None),
        "distribution": facets.payload(distribution, COMPLEXITY_LABELS, select=select),
        "parts": {name: round(total / ran, 4) if ran else 0 for name, total in parts.items()},
        "ran": ran,
    }


def sudo() -> dict[str, Any]:
    """Return how much of the history is run as somebody else.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    elevated = case((db.History.cmd.like("sudo %"), 1), (db.History.cmd == "sudo", 1), else_=0)
    grouped = facets.aggregate(time_bucket(select.granularity), func.count(), func.sum(elevated), select=select)
    buckets = facets._bucket_order(grouped)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets

    ran = sum(values[0] for series in grouped.values() for values in series.values())
    raised = sum(values[1] or 0 for series in grouped.values() for values in series.values())
    plain = _unnormalized(select)
    return {
        "share": facets.payload(_rates(grouped), buckets, select=plain, datetimes=True, default=None),
        "count": facets.payload(
            {name: {key: values[1] or 0 for key, values in series.items()} for name, series in grouped.items()},
            buckets,
            select=plain,
            datetimes=True,
        ),
        "ran": ran,
        "raised": raised,
    }


def vocabulary() -> dict[str, Any]:
    """Return how wide a vocabulary of programs is in use, and how it grows.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.aggregate((time_bucket(select.granularity), FIRST_WORD), select=select)

    words_by_period: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for name, buckets in grouped.items():
        for period, word in buckets:
            if word:
                words_by_period[name][period].add(word)

    distinct: dict[str, dict[str, int]] = {}
    fresh: dict[str, dict[str, int]] = {}
    for name, periods in words_by_period.items():
        seen: set[str] = set()
        distinct[name] = {}
        fresh[name] = {}
        for period in sorted(periods):
            words = periods[period]
            distinct[name][period] = len(words)
            fresh[name][period] = len(words - seen)
            seen |= words

    buckets = facets._bucket_order(distinct)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets

    plain = _unnormalized(select)
    return {
        "distinct": facets.payload(distinct, buckets, select=plain, datetimes=True),
        "new": facets.payload(fresh, buckets, select=plain, datetimes=True),
        "total": len({word for periods in words_by_period.values() for words in periods.values() for word in words}),
    }


# Time ----------------------------------------------------------------------------
def punchcard() -> dict[str, Any]:
    """Return the week as a grid: which hours of which days get typed in.

    Returns:
        The chart's data.
    """
    select = facets.selection(splittable=False)
    grouped = facets.counts(
        (func.strftime("%w", db.History.start), func.strftime("%H", db.History.start)),
        select=select,
    )
    cells: dict[tuple[str, str], int] = defaultdict(int)
    for buckets in grouped.values():
        for key, count in buckets.items():
            cells[key] += count
    data = [
        [hour, day, cells.get((weekday, f"{hour:02d}"), 0)]
        for day, weekday in enumerate(_SQLITE_WEEKDAYS)
        for hour in range(24)
    ]
    return {
        "hours": [f"{hour:02d}" for hour in range(24)],
        "days": list(DAY_NAMES),
        "data": data,
        "max": max((cell[2] for cell in data), default=0),
    }


def calendar() -> dict[str, Any]:
    """Return one number per day, for the year-by-year calendar.

    Returns:
        The chart's data.
    """
    select = facets.selection(splittable=False)
    grouped = facets.counts(func.strftime("%Y-%m-%d", db.History.start), select=select)
    days: dict[str, int] = defaultdict(int)
    for buckets in grouped.values():
        for day, count in buckets.items():
            if day:
                days[day] += count
    ordered = sorted(days.items())
    return {
        "days": [{"date": day, "count": count} for day, count in ordered],
        "max": max(days.values(), default=0),
        "total": sum(days.values()),
        "active": len(days),
    }


# The home page --------------------------------------------------------------------
def stats() -> dict[str, Any]:
    """Return the handful of numbers the home page opens with.

    Returns:
        The totals, and the records worth naming.
    """
    select = facets.selection(splittable=False)

    def combine(left: tuple, right: tuple) -> tuple:
        moments = [value for value in (left[1], right[1]) if value is not None]
        latest = [value for value in (left[2], right[2]) if value is not None]
        lengths = [value for value in (left[7], right[7]) if value is not None]
        return (
            left[0] + right[0],
            min(moments, default=None),
            max(latest, default=None),
            (left[3] or 0) + (right[3] or 0),
            (left[4] or 0) + (right[4] or 0),
            (left[5] or 0) + (right[5] or 0),
            left[6] + right[6],
            max(lengths, default=None),
        )

    grouped = facets.aggregate(
        None,
        func.count(),
        func.min(db.History.start),
        func.max(db.History.start),
        func.sum(KNOWN_CODE),
        func.sum(FAILED),
        func.coalesce(func.sum(DURATION_MS), 0.0),
        func.count(func.distinct(db.History.session_id)),
        func.max(COMMAND_LENGTH),
        select=select,
        combine=combine,
    )
    totals = next(iter(next(iter(grouped.values()), {}).values()), None)
    if totals is None:
        return {"commands": 0}

    ran, first, last, known, broke, spent, shells, longest = totals

    by_word = facets.counts(FIRST_WORD, select=select)
    words: Counter = Counter()
    for buckets in by_word.values():
        words.update({word: count for word, count in buckets.items() if word})

    by_day = facets.counts(func.strftime("%Y-%m-%d", db.History.start), select=select)
    per_day: Counter = Counter()
    for buckets in by_day.values():
        per_day.update(buckets)
    busiest = per_day.most_common(1)

    covered = (last - first).days + 1 if first and last else 0
    top = words.most_common(1)
    return {
        "commands": ran,
        "shells": shells,
        "programs": len(words),
        "first": first.isoformat(" ", "seconds") if first else None,
        "last": last.isoformat(" ", "seconds") if last else None,
        "days": covered,
        "activeDays": len(per_day),
        "perDay": round(ran / covered, 1) if covered else 0,
        "successRate": round((known - broke) * 100 / known, 2) if known else 0,
        "hours": round((spent or 0) / 3600000, 1),
        "longest": longest,
        "topCommand": {"name": top[0][0], "count": top[0][1]} if top else None,
        "busiestDay": {"date": busiest[0][0], "count": busiest[0][1]} if busiest else None,
    }


def environment() -> dict[str, Any]:
    """Return where the commands were typed, over time and in total.

    The same shape as any other split chart -- only the dimension it opens on is
    the one the process ancestry answers rather than one the shell recorded.

    Returns:
        The chart's data.
    """
    select = facets.selection(default_split="env")
    grouped = facets.counts(time_bucket(select.granularity), select=select)
    buckets = facets._bucket_order(grouped)
    if buckets:
        buckets = facets.time_buckets(select.granularity, buckets[0], buckets[-1]) or buckets
    return {
        "timeline": facets.payload(grouped, buckets, select=select, datetimes=True),
        "share": share(select),
        "axes": list(env.AXES),
    }


TRENDING_MINIMUM = 30
"""How often a program must have run in the span before its trend means anything."""


def trending(limit: int = 15, minimum: int = TRENDING_MINIMUM) -> dict[str, Any]:
    """Return which programs are on the way in, and which on the way out.

    The selected span is cut in half and each program's share of the commands is
    compared between the two. A share rather than a count, so that a period with
    simply more commands in it does not read as everything trending at once.

    Parameters:
        limit: How many programs to report at each end.
        minimum: How often a program must have run to be ranked at all.

    Returns:
        The chart's data.
    """
    select = facets.selection()
    grouped = facets.aggregate((time_bucket(select.granularity), FIRST_WORD), select=select)

    periods = sorted({period for buckets in grouped.values() for period, _ in buckets})
    if len(periods) < 2:  # noqa: PLR2004 - one period cannot be compared to anything
        return {"categories": [], "series": [], "early": None, "late": None, "minimum": minimum}
    middle = periods[len(periods) // 2]

    halves: dict[str, list[Counter]] = defaultdict(lambda: [Counter(), Counter()])
    for name, buckets in grouped.items():
        for (period, word), (count,) in buckets.items():
            if word:
                halves[name][0 if period < middle else 1][word] += count

    changes: dict[str, dict[str, float]] = {}
    overall: Counter = Counter()
    for name, (early, late) in halves.items():
        first, second = sum(early.values()), sum(late.values())
        moves = {}
        for word in set(early) | set(late):
            total = early[word] + late[word]
            if total < minimum:
                continue
            before = early[word] * 100 / first if first else 0.0
            after = late[word] * 100 / second if second else 0.0
            moves[word] = round(after - before, 3)
            overall[word] += late[word] - early[word]
        changes[name] = moves

    ranked = sorted(
        {word for moves in changes.values() for word in moves},
        key=lambda word: sum(moves.get(word, 0.0) for moves in changes.values()),
    )
    words = ranked[:limit] + [word for word in ranked[-limit:] if word not in ranked[:limit]]

    return {
        **facets.payload(changes, words, select=_unnormalized(select)),
        "early": f"{periods[0]} to {middle}",
        "late": f"{middle} to {periods[-1]}",
        "minimum": minimum,
    }
