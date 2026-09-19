#!/usr/bin/env python3
"""Download local speaker models explicitly, without reading or uploading recordings."""

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION = (
    f"{BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING = f"{BASE}/speaker-recongition-models/nemo_en_titanet_small.onnx"


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "Aimeet-model-setup"})
    with (
        urllib.request.urlopen(request, timeout=120) as source,
        destination.open("wb") as target,
    ):
        shutil.copyfileobj(source, target)
    with destination.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target = args.output or root / "models" / "speaker-diarization"
    if target.exists():
        raise SystemExit(
            f"Target already exists: {target}. Use a new --output to update models."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="diarization-", dir=target.parent
    ) as temporary:
        staging = Path(temporary)
        archive = staging / "segmentation.tar.bz2"
        print("Downloading segmentation model...", flush=True)
        segmentation_hash = download(SEGMENTATION, archive)
        bundle = staging / "bundle"
        bundle.mkdir()
        # Copy only known regular members, never extract untrusted paths or links.
        with tarfile.open(archive, "r:bz2") as package:
            for name, output in (
                ("model.onnx", "segmentation.onnx"),
                ("LICENSE", "LICENSE.segmentation"),
            ):
                member = package.getmember(
                    f"sherpa-onnx-pyannote-segmentation-3-0/{name}"
                )
                if not member.isfile():
                    raise ValueError("Unexpected model archive member")
                with (
                    package.extractfile(member) as source,
                    (bundle / output).open("wb") as dest,
                ):
                    shutil.copyfileobj(source, dest)
        print("Downloading speaker embedding model...", flush=True)
        embedding_hash = download(EMBEDDING, bundle / "embedding.onnx")
        manifest = {
            "engine": "sherpa-onnx",
            "segmentation_url": SEGMENTATION,
            "segmentation_archive_sha256": segmentation_hash,
            "embedding_url": EMBEDDING,
            "embedding_sha256": embedding_hash,
        }
        (bundle / "aimeet-model.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        bundle.rename(target)
    print(f"Models ready: {target}")


if __name__ == "__main__":
    main()
