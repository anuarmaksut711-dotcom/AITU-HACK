#!/usr/bin/env python3
"""Restore a dump into a uniquely named temporary database, validate, then remove it."""

import subprocess
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if len(sys.argv) != 2:
    raise SystemExit("Usage: python3 scripts/verify-backup.py backups/NAME.dump")
dump = Path(sys.argv[1]).resolve()
if not dump.is_file() or dump.suffix != ".dump":
    raise SystemExit("Expected an existing PostgreSQL .dump file")
name = "aimeet_restore_" + uuid.uuid4().hex
prefix = ["docker", "compose", "exec", "-T", "postgres"]
subprocess.run(prefix + ["createdb", "-U", "aimeet", name], cwd=root, check=True)
try:
    with dump.open("rb") as source:
        subprocess.run(
            prefix + ["pg_restore", "-U", "aimeet", "-d", name, "--no-owner", "--exit-on-error"],
            cwd=root,
            stdin=source,
            check=True,
        )
    result = subprocess.run(
        prefix
        + [
            "psql",
            "-U",
            "aimeet",
            "-d",
            name,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT count(*) FROM users; SELECT count(*) FROM meetings; "
            "SELECT version_num FROM alembic_version;",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    rows = result.stdout.strip().splitlines()
    if len(rows) != 3 or not rows[0].isdigit() or not rows[1].isdigit():
        raise RuntimeError("Unexpected restored database structure")
    print(
        f"PASS: restored users={rows[0]}, meetings={rows[1]}, "
        f"migration={rows[2]} into isolated database."
    )
finally:
    # Only the unique database successfully created by this invocation is removed.
    subprocess.run(prefix + ["dropdb", "-U", "aimeet", name], cwd=root, check=True)
