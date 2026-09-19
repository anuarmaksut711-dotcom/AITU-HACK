#!/usr/bin/env python3
"""Create an atomic PostgreSQL custom-format backup without exposing credentials."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
backup_dir = root / "backups"
backup_dir.mkdir(mode=0o700, exist_ok=True)
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
target = backup_dir / f"aimeet-{stamp}.dump"
temporary = target.with_suffix(".dump.partial")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, "wb") as stream:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "aimeet",
                "-d",
                "aimeet",
                "-Fc",
            ],
            cwd=root,
            stdout=stream,
            check=True,
        )
    if temporary.stat().st_size < 100:
        raise RuntimeError("Backup is unexpectedly empty")
    temporary.rename(target)
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
print(f"Backup created: {target.relative_to(root)}")
