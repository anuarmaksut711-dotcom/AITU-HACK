#!/usr/bin/env python3
"""Initialize unique local credentials; never overwrite an existing environment."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
target = root / ".env"
content = "\n".join(
    [
        "# Local-only environment. Keep this file private and out of version control.",
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
        "BOOTSTRAP_EMAIL=admin@example.com",
        f"BOOTSTRAP_PASSWORD={secrets.token_urlsafe(24)}",
        "WEB_PORT=8787",
        "APP_ENV=development",
        "COOKIE_SECURE=false",
        "ALLOWED_ORIGINS=http://localhost:8787,http://127.0.0.1:8787",
        "LIVEKIT_API_KEY=soyle-local",
        f"LIVEKIT_API_SECRET={secrets.token_hex(32)}",
        "",
    ]
)
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    # Add only newly required local credentials; preserve existing values exactly.
    import fcntl
    with target.open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        existing = file.read()
        keys = {line.partition("=")[0] for line in existing.splitlines()}
        additions = []
        if "LIVEKIT_API_KEY" not in keys:
            additions.append("LIVEKIT_API_KEY=soyle-local")
        if "LIVEKIT_API_SECRET" not in keys:
            additions.append(f"LIVEKIT_API_SECRET={secrets.token_hex(32)}")
        if additions:
            file.write(("" if existing.endswith("\n") else "\n") + "\n".join(additions) + "\n")
            file.flush()
            os.fsync(file.fileno())
    print("Existing credentials preserved; any missing voice-room credentials initialized.")
else:
    with os.fdopen(fd, "w") as file:
        file.write(content)
    print("Created private .env with unique database and administrator credentials.")
