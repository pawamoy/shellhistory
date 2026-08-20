"""Configuration for the pytest test suite."""

import os
import tempfile

# `db` resolves the database path at import time, so this has to happen before
# any test module imports the package. Without it a test run would open -- and
# `create_tables` would write to -- the developer's own history database.
_TMP = tempfile.mkdtemp(prefix="shellhistory-tests-")
os.environ.setdefault("SHELLHISTORY_DB", os.path.join(_TMP, "db.sqlite3"))
os.environ.setdefault("SHELLHISTORY_FILE", os.path.join(_TMP, "history"))
