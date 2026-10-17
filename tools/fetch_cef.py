"""Download a pinned official CEF minimal SDK; never runs downloaded code.

Use --platform windows64/linux64. Downloads stay in .research.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=("windows64", "linux64"))
    parser.add_argument("--version", default="152.0.14+g8a0b5b9+chromium-152.0.7977.167")
    parser.add_argument("--destination", type=Path, default=Path(".research/cef"))
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://cef-builds.spotifycdn.com/index.json", timeout=60) as stream:
        index = json.load(stream)
    version = next(v for v in index[args.platform]["versions"] if v["cef_version"] == args.version)
    entry = next(f for f in version["files"] if f["type"] == "minimal")
    archive = args.destination / entry["name"]
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        with urllib.request.urlopen("https://cef-builds.spotifycdn.com/" +
                                    urllib.parse.quote(entry["name"]), timeout=120) as source:
            with partial.open("wb") as out:
                while data := source.read(4 * 1024 * 1024):
                    out.write(data)
        partial.replace(archive)
    digest = hashlib.sha1()
    with archive.open("rb") as source:
        while data := source.read(4 * 1024 * 1024):
            digest.update(data)
    if archive.stat().st_size != entry["size"] or digest.hexdigest() != entry["sha1"]:
        raise RuntimeError(f"Official index checksum/size mismatch: {archive}")
    root = args.destination / entry["name"].removesuffix(".tar.bz2")
    if not (root / "include/cef_app.h").is_file():
        # Extraction rejects links and anything outside the intended directory.
        destination = args.destination.resolve()
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            for member in members:
                target = (destination / member.name).resolve()
                if not target.is_relative_to(destination) or member.issym() or member.islnk():
                    raise RuntimeError(f"Unsafe archive member: {member.name}")
            bundle.extractall(destination, members=members, filter="data")
    print(root.resolve(), flush=True)


if __name__ == "__main__":
    main()
