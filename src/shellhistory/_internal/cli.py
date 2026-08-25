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

# Why does this file exist, and why not put this in `__main__`?
#
# You might be tempted to import things from `__main__` later,
# but that will cause problems: the code will get executed twice:
#
# - When you run `python -m shellhistory` python will execute
#   `__main__.py` as a script. That means there won't be any
#   `shellhistory.__main__` in `sys.modules`.
# - When you import `__main__` it will get executed again (as a module) because
#   there's no `shellhistory.__main__` in `sys.modules`.

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from shellhistory._internal import debug


class _DebugInfo(argparse.Action):
    def __init__(self, nargs: int | str | None = 0, **kwargs: Any) -> None:
        super().__init__(nargs=nargs, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        debug._print_debug_info()
        sys.exit(0)


def get_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser.

    Returns:
        An argparse parser.
    """
    parser = argparse.ArgumentParser(
        prog="shellhistory",
        epilog="subcommands: secrets (scan command history for credentials)",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {debug._get_version()}")
    parser.add_argument("--debug-info", action=_DebugInfo, help="Print debug information.")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--location",
        dest="location",
        action="store_true",
        help="Print the path of the shell script to source.",
    )
    group.add_argument("--web", dest="web", action="store_true", help="Run the web application.")
    group.add_argument(
        "--import",
        dest="import_file",
        action="store_true",
        help="Import a legacy text history file into the database.",
    )
    group.add_argument(
        "--migrate",
        dest="migrate",
        action="store_true",
        help="Convert a legacy single-table database to the current schema.",
    )
    parser.add_argument("file", nargs="?", help="History file to import (defaults to $SHELLHISTORY_FILE).")

    return parser


def main(args: list[str] | None = None) -> int:
    """Run the main program.

    This function is executed when you type `shellhistory` or `python -m shellhistory`.

    Parameters:
        args: Arguments passed from the command line.

    Returns:
        An exit code.
    """
    args = sys.argv[1:] if args is None else args
    if args and args[0] == "secrets":
        return _secrets_command(args[1:])

    parser = get_parser()
    opts = parser.parse_args(args=args)

    if opts.location:
        return location()
    if opts.web:
        return web()
    if opts.migrate:
        return migrate()
    if opts.import_file:
        return import_legacy(opts.file)

    parser.print_help()
    return 0


def _secrets_command(args: list[str] | None = None) -> int:
    """Scan commands with Gitleaks and optionally redact detected credentials.

    Parameters:
        args: Arguments after the `secrets` subcommand.

    Returns:
        An exit code.
    """
    parser = argparse.ArgumentParser(
        prog="shellhistory secrets",
        description="Scan command history locally with Gitleaks and review detected credentials.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Redact all findings without prompting, save scan states, VACUUM the database, and truncate its WAL.",
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="Scan rows that already have a clean or redacted scan state.",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Commands sent to each Gitleaks process.")
    parser.add_argument("--gitleaks", default="gitleaks", help="Path to the Gitleaks executable.")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds allowed for each batch.")
    opts = parser.parse_args(args=args)

    from shellhistory._internal import _db as db  # noqa: PLC0415
    from shellhistory._internal import _secrets  # noqa: PLC0415

    db.engine.dispose()

    def review(scan: _secrets._RowScan) -> bool | str | None:
        """Print one finding and obtain its redaction decision."""
        rules = ", ".join(scan.rules) or "unknown rule"
        print(f"\nCommand {scan.row.id}: {scan.findings} finding(s) ({rules})")
        print("Recorded command:")
        print(scan.row.cmd)
        print("Redacted command:")
        print(scan.redacted)
        while True:
            try:
                answer = input("Choose [a]pply, [r]edact, [k]eep, or [s]kip: ").strip().lower()
            except EOFError as error:
                raise _secrets._SecretsError("interactive review ended before all findings were decided") from error
            if answer in {"a", "apply"}:
                return True
            if answer in {"r", "redact"}:
                try:
                    return input("Write the redacted command: ")
                except EOFError as error:
                    raise _secrets._SecretsError("interactive review ended before all findings were decided") from error
            if answer in {"k", "keep"}:
                return False
            if answer in {"s", "skip"}:
                return None
            print("Please enter a (apply), r (redact), k (keep), or s (skip).")

    try:
        report = _secrets._scan_database(
            db.DB_PATH,
            apply=opts.apply,
            rescan=opts.rescan,
            batch_size=opts.batch_size,
            executable=opts.gitleaks,
            timeout=opts.timeout,
            review=None if opts.apply else review,
        )
    except (OSError, ValueError, _secrets._SecretsError) as error:
        print(f"secret scan failed: {error}", file=sys.stderr)
        return 1

    print(f"scanned {report.scanned} commands; found {report.findings} findings in {report.flagged} commands")
    for rule, count in report.rule_counts.items():
        print(f"  {rule}: {count} commands")
    if opts.apply:
        print(f"redacted {report.redacted} commands")
    elif report.flagged:
        print(f"redacted {report.redacted} commands; kept {report.kept} commands; skipped {report.skipped} commands")
    return 0


def location() -> int:
    """Print the path of the shell script to source.

    Returns:
        An exit code.
    """
    print(Path(__file__).parent.parent / "shellhistory.sh")
    return 0


def web() -> int:
    """Run the web application.

    Returns:
        An exit code.
    """
    # Imported here, not at module level: `shellhistory-location` runs from every
    # shell's startup file, and it must not pay for importing Flask.
    from shellhistory._internal._app import app  # noqa: PLC0415

    app.run()
    return 0


def migrate() -> int:
    """Convert a legacy single-table database to the sessions + history split.

    Returns:
        An exit code.
    """
    from shellhistory._internal import _db as db  # noqa: PLC0415
    from shellhistory._internal import _migrations as migrations  # noqa: PLC0415

    if not db.is_legacy_schema():
        print(f"{db.DB_PATH} is already on the current schema, nothing to do.")
        return 0
    result = migrations.migrate_schema()
    print(
        f"migrated {result['rows']} records into {result['sessions']} sessions\noriginal kept at {result['backup']}",
    )
    return 0


def import_legacy(path: str | None = None) -> int:
    """Import a legacy text history file into the database.

    Parameters:
        path: The file to import. Defaults to `$SHELLHISTORY_FILE`.

    Returns:
        An exit code.
    """
    from shellhistory._internal import _migrations as migrations  # noqa: PLC0415

    report = migrations.import_file(path) if path else migrations.import_history()
    print(f"imported {report.inserted} records ({report.duplicates} already present)")
    return 0
