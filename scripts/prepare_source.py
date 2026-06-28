#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path


PACKAGE_NAME = "matrix-synapse-py3"
MAINTAINER = "Synapse Packaging team <packages@matrix.org>"


def read_series_suffix(config_path: Path, series_name: str) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for item in config["series"]:
        if item["name"] == series_name:
            return item["version_suffix"]
    raise ValueError(f"Unknown series '{series_name}' in {config_path}")


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "synapse-launchpad-packaging"},
    )
    with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def extract_tarball(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, "r:gz") as archive:
        try:
            archive.extractall(destination, filter="data")
        except TypeError:
            archive.extractall(destination)

    extracted = [path for path in destination.iterdir() if path.is_dir()]
    if len(extracted) != 1:
        raise RuntimeError(f"Expected one extracted directory in {destination}, found {len(extracted)}")
    return extracted[0]


def write_changelog(path: Path, version: str, series: str, tag: str) -> None:
    timestamp = format_datetime(datetime.now(timezone.utc))
    content = (
        f"{PACKAGE_NAME} ({version}) {series}; urgency=medium\n\n"
        f"  * Package upstream Synapse release {tag}.\n\n"
        f" -- {MAINTAINER}  {timestamp}\n"
    )
    path.write_text(content, encoding="utf-8", newline="\n")


def write_metadata(path: Path, *, version: str, tag: str, series: str, source_dir: Path) -> None:
    metadata = {
        "package_name": PACKAGE_NAME,
        "package_version": version,
        "series": series,
        "tag": tag,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
    }
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--series", required=True)
    parser.add_argument("--config", default="config/series.json")
    parser.add_argument("--template-dir", default="debian-template")
    parser.add_argument("--output-dir", default="work/prepared")
    parser.add_argument("--tarball-url")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    template_dir = Path(args.template_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = read_series_suffix(config_path, args.series)
    package_version = f"{args.version}+{suffix}"
    source_dir = output_dir / f"{PACKAGE_NAME}-{package_version}"

    if source_dir.exists():
        shutil.rmtree(source_dir)

    tarball_url = args.tarball_url or f"https://github.com/element-hq/synapse/archive/refs/tags/{args.tag}.tar.gz"

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        archive_path = temp_dir / f"{args.tag}.tar.gz"
        download_file(tarball_url, archive_path)
        extracted_root = extract_tarball(archive_path, temp_dir / "src")
        shutil.copytree(extracted_root, source_dir)

    debian_dir = source_dir / "debian"
    shutil.copytree(template_dir, debian_dir)

    generated_files = [
        debian_dir / "files",
        debian_dir / "vendor",
    ]
    for path in generated_files:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    write_changelog(debian_dir / "changelog", package_version, args.series, args.tag)
    write_metadata(source_dir / ".packaging-info.json", version=package_version, tag=args.tag, series=args.series, source_dir=source_dir)

    print(source_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
