"""Extract one checksum-pinned conda-forge MinIO rebuild and its licenses.

The package contains a static Linux amd64 executable. It is used by the local
integration demo and the Docker image so those paths run the same storage server.
"""
import argparse
from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

PACKAGE_URL = "https://conda.anaconda.org/conda-forge/linux-64/minio-server-2025.10.15.17.29.55-hbcca054_0.conda"
SHA256 = "5be30a904d2f5d5defa3be69c9f8fa519fde68f907049e351028727c90a5068e"


@contextmanager
def decompressed(stream):
    try:
        from compression.zstd import ZstdFile
    except ImportError:
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(stream) as raw:
            yield raw
    else:
        with ZstdFile(stream, "rb") as raw:
            yield raw


def extract(package: Path, output: Path):
    with package.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != SHA256:
        raise ValueError("MinIO package checksum did not match the pinned value")
    with zipfile.ZipFile(package) as archive:
        members = [name for name in archive.namelist() if name.endswith(".tar.zst")]
        if len(members) != 2:
            raise ValueError("Unexpected conda package layout")
        for name in members:
            with archive.open(name) as compressed, decompressed(compressed) as raw:
                with tarfile.open(fileobj=raw, mode="r|") as tar:
                    for entry in tar:
                        relative = None
                        if entry.name == "bin/minio":
                            relative = Path("bin/minio")
                        elif entry.name.startswith("info/licenses/"):
                            relative = Path("licenses") / entry.name.removeprefix("info/licenses/")
                        if relative is None or not entry.isfile():
                            continue
                        if ".." in relative.parts or relative.is_absolute():
                            raise ValueError("Unsafe package path")
                        target = output / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(entry) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination)
    binary = output / "bin/minio"
    if not binary.is_file() or not (output / "licenses/LICENSE").is_file():
        raise ValueError("MinIO executable or license was missing")
    binary.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, help="Existing downloaded package; still checksum-verified")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="minio-package-") as temporary:
        package = args.package or Path(temporary) / "minio.conda"
        if args.package is None:
            with urllib.request.urlopen(PACKAGE_URL, timeout=60) as response, package.open("wb") as target:
                shutil.copyfileobj(response, target)
        extract(package, args.output)
    print("Pinned MinIO executable and licenses extracted; package checksum verified.")


if __name__ == "__main__":
    main()
