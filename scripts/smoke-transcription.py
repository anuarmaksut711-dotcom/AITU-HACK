#!/usr/bin/env python3
"""Real ASR smoke against a temporary database; does not modify the running app."""

import argparse
import json
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--diarization-model-path", type=Path, default=Path("models/speaker-diarization"))
    parser.add_argument("--min-speakers", type=int, default=0)
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument(
        "--language", choices=["auto", "ru", "kk", "en"], default="auto"
    )
    parser.add_argument("--expect", action="append", default=[])
    args = parser.parse_args()

    from aimeet_api.core.config import Settings
    from aimeet_api.core.security import hash_password
    from aimeet_api.db.models import Base, User, Workspace
    from aimeet_api.main import create_app
    from aimeet_api.modules.transcription.jobs import claim_next
    from aimeet_api.modules.transcription.worker import run_job
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(prefix="aimeet-stt-smoke-") as temporary:
        config = Settings(
            database_url=f"sqlite:///{temporary}/smoke.db",
            audio_dir=Path(temporary) / "audio",
            app_env="development",
            stt_model_path=args.model_path.resolve(),
            stt_diarization_model_path=args.diarization_model_path.resolve(),
            allowed_origins="http://testserver",
            cookie_secure=False,
        )
        app = create_app(config)
        Base.metadata.create_all(app.state.engine)
        credentials = {
            "email": "stt-smoke@example.com",
            "password": "Local-smoke-only-739!",
        }
        with app.state.session_factory() as db:
            workspace = Workspace(name="Isolated transcription smoke")
            db.add(workspace)
            db.flush()
            db.add(
                User(
                    workspace_id=workspace.id,
                    email=credentials["email"],
                    display_name="Smoke",
                    password_hash=hash_password(credentials["password"]),
                )
            )
            db.commit()
        with TestClient(app, headers={"X-Requested-With": "aimeet"}) as client:
            assert (
                client.post("/api/v1/auth/login", json=credentials).status_code == 200
            )
            for source in args.files:
                started = time.monotonic()
                with source.open("rb") as stream:
                    response = client.post(
                        "/api/v1/meetings/audio",
                        params={
                            "title": "Local ASR verification",
                            "language": args.language,
                            "filename": source.name,
                            **({"num_speakers": args.num_speakers} if args.num_speakers else {}),
                        },
                        content=iter(lambda: stream.read(64 * 1024), b""),
                        headers={"Content-Type": "application/octet-stream"},
                    )
                assert response.status_code == 202, response.text
                identifier = response.json()["id"]
                claim = claim_next(app.state.session_factory, config)
                assert claim is not None
                run_job(app.state.session_factory, config, claim)
                result = client.get(f"/api/v1/meetings/{identifier}").json()
                assert result["transcription"]["status"] == "succeeded", result[
                    "transcription"
                ]
                assert result["transcript"].strip()
                speakers = {row["speaker"] for row in result["segments"] if row.get("speaker")}
                assert len(speakers) >= args.min_speakers, speakers
                if args.num_speakers:
                    assert len(speakers) <= args.num_speakers, speakers
                for phrase in args.expect:
                    assert phrase.lower() in result["transcript"].lower(), (
                        f"Missing: {phrase}"
                    )
                for segment in result["segments"]:
                    assert (
                        0
                        <= segment["start"]
                        <= segment["end"]
                        <= result["transcription"]["duration_seconds"] + 0.001
                    )
                assert (
                    client.delete(f"/api/v1/meetings/{identifier}").status_code == 204
                )
                assert not list(config.audio_dir.iterdir())
                print(
                    json.dumps(
                        {
                            "format": source.suffix,
                            "status": "passed",
                            "transcript_chars": len(result["transcript"]),
                            "speakers": len(speakers),
                            "audio_seconds": result["transcription"][
                                "duration_seconds"
                            ],
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        app.state.engine.dispose()


if __name__ == "__main__":
    main()
