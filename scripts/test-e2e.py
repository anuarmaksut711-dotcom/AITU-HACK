#!/usr/bin/env python3
"""Run the real browser suite, loading local credentials without shell expansion."""

import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
settings = {}
env_file = root / ".env"
if env_file.is_file():
    for line in env_file.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"BOOTSTRAP_EMAIL", "BOOTSTRAP_PASSWORD", "WEB_PORT"}:
            settings[key] = value
env = os.environ.copy()
for target, source in (("E2E_EMAIL", "BOOTSTRAP_EMAIL"), ("E2E_PASSWORD", "BOOTSTRAP_PASSWORD")):
    if not env.get(target) and settings.get(source):
        env[target] = settings[source]
if not env.get("E2E_EMAIL") or not env.get("E2E_PASSWORD"):
    raise SystemExit("Set E2E_EMAIL/E2E_PASSWORD or initialize the local .env first.")
env.setdefault("PLAYWRIGHT_BASE_URL", f"http://127.0.0.1:{settings.get('WEB_PORT', '8787')}")
raise SystemExit(
    subprocess.run(["npm", "run", "test:e2e"], cwd=root / "apps/web", env=env).returncode
)
