"""Install Sherpa 1.13.8's matching native ORT build, only during image construction."""

import hashlib
import io
import platform
import urllib.request
import zipfile
from pathlib import Path

BUILDS = {
    "x86_64": ("x64", "c4f8994d56191d9d2c92a961b39fe790459f2c5d155f912b239506ea31359534"),
    "aarch64": ("aarch64", "28083273643f40fa6477ef9d53986fe216304710c4b1c743b217a4effd016339"),
}


def main():
    architecture, checksum = BUILDS[platform.machine()]
    name = f"onnxruntime-linux-{architecture}-glibc2_17-Release-1.28.2"
    url = f"https://github.com/csukuangfj/onnxruntime-libs/releases/download/v1.28.2/{name}.zip"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Sherpa runtime checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        Path("/usr/local/lib/libonnxruntime.so").write_bytes(
            archive.read(f"{name}/lib/libonnxruntime.so")
        )
        Path("/usr/local/share/onnxruntime-LICENSE").write_bytes(archive.read(f"{name}/LICENSE"))


if __name__ == "__main__":
    main()
