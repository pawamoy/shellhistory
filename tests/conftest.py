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

"""Configuration for the pytest test suite."""

import os
import tempfile
from pathlib import Path

# `db` resolves the database path at import time, so this has to happen before
# any test module imports the package. Without it a test run would open -- and
# `create_tables` would write to -- the developer's own history database.
_TMP = Path(tempfile.mkdtemp(prefix="shellhistory-tests-"))
os.environ.setdefault("SHELLHISTORY_DB", str(_TMP / "db.sqlite3"))
os.environ.setdefault("SHELLHISTORY_FILE", str(_TMP / "history"))
