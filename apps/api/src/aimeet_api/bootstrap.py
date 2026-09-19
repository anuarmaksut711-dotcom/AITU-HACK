"""Create the initial workspace owner explicitly; never seed known credentials."""

import os
import sys

from pydantic import BaseModel, EmailStr, Field, ValidationError
from sqlalchemy import select, text

from aimeet_api.core.config import Settings
from aimeet_api.core.security import hash_password, hash_token
from aimeet_api.db.models import User, Workspace
from aimeet_api.db.session import create_engine_and_session


class BootstrapInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=14, max_length=1024)
    display_name: str = Field(default="Workspace owner", min_length=1, max_length=200)
    workspace_name: str = Field(default="My workspace", min_length=1, max_length=200)


def main() -> None:
    try:
        payload = BootstrapInput(
            email=os.environ.get("BOOTSTRAP_EMAIL", ""),
            password=os.environ.get("BOOTSTRAP_PASSWORD", ""),
            display_name=os.environ.get("BOOTSTRAP_DISPLAY_NAME", "Workspace owner"),
            workspace_name=os.environ.get("BOOTSTRAP_WORKSPACE_NAME", "My workspace"),
        )
    except ValidationError:
        print(
            "Set a valid BOOTSTRAP_EMAIL and BOOTSTRAP_PASSWORD (14–1024 characters).",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    engine, factory = create_engine_and_session(Settings())
    try:
        with factory.begin() as db:
            normalized_email = str(payload.email).casefold()
            if engine.dialect.name == "postgresql":
                # Serialize only bootstrap calls for this email; released on commit/rollback.
                # A transaction prevents an abandoned workspace if account creation fails.
                db.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": int(hash_token(normalized_email)[:15], 16)},
                )
            if db.scalar(select(User).where(User.email == normalized_email)):
                print("Account already exists; no credentials changed.")
                return
            workspace = Workspace(name=payload.workspace_name)
            db.add(workspace)
            db.flush()
            db.add(
                User(
                    workspace_id=workspace.id,
                    email=normalized_email,
                    display_name=payload.display_name,
                    password_hash=hash_password(payload.password),
                )
            )
        print("Workspace and owner account created.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
