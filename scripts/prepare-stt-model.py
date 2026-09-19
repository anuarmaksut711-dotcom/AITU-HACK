#!/usr/bin/env python3
"""Explicit online provisioning. No meeting data is read or uploaded."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        choices=["small", "medium", "large-v3", "large-v3-turbo"],
        default="large-v3-turbo",
    )
    parser.add_argument("--revision", help="Optional immutable Hugging Face commit SHA")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target = args.output or root / "models" / f"whisper-{args.model}"
    if target.exists():
        raise SystemExit(
            f"Target already exists: {target}. Use a new --output for a model update."
        )
    # Provisioning is deliberately separate from the network-disabled runtime.
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HOME", str(root / ".local" / "huggingface"))
    from huggingface_hub import HfApi, snapshot_download

    repo = (
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
        if args.model == "large-v3-turbo"
        else f"Systran/faster-whisper-{args.model}"
    )
    revision = HfApi().model_info(repo, revision=args.revision).sha
    temporary = target.with_name(target.name + ".partial")
    snapshot_download(
        repo,
        revision=revision,
        local_dir=temporary,
        allow_patterns=["*.json", "model.bin", "vocabulary.*", "README.md"],
    )
    required = ["model.bin", "config.json", "tokenizer.json"]
    if any(not (temporary / name).is_file() for name in required):
        raise SystemExit("Model is incomplete; runtime model path was not changed.")
    (temporary / "aimeet-model.json").write_text(
        json.dumps({"source": repo, "revision": revision}, indent=2) + "\n"
    )
    temporary.rename(target)
    print(f"Model prepared at {target}; revision={revision}")


if __name__ == "__main__":
    main()
