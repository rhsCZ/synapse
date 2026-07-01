#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import tarfile
import tempfile
import tomllib
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path


PACKAGE_NAME = "matrix-synapse-py3"
MAINTAINER = "Synapse Packaging team <packages@matrix.org>"


def read_series_suffix_prefix(config_path: Path, series_name: str) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for item in config["series"]:
        if item["name"] == series_name:
            return item["version_suffix_prefix"]
    raise ValueError(f"Unknown series '{series_name}' in {config_path}")


def read_upload_revision(state_path: Path, version: str, series_name: str) -> int:
    if not state_path.exists():
        return 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    uploads = state.get("uploads", {})
    series_uploads = uploads.get(version, {})
    return int(series_uploads.get(series_name, 0)) + 1


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


def ensure_executable(path: Path) -> None:
    if not path.exists():
        return

    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_non_executable(path: Path) -> None:
    if not path.exists():
        return

    path.chmod(path.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def normalize_debian_permissions(debian_dir: Path) -> None:
    executable_files = {
        "build_virtualenv",
        "manage_debconf.pl",
        "matrix-synapse-py3.config",
        "matrix-synapse-py3.postinst",
        "rules",
    }

    for path in debian_dir.iterdir():
        if not path.is_file():
            continue

        if path.name in executable_files:
            ensure_executable(path)
        else:
            ensure_non_executable(path)


def extract_minimum_poetry_version(source_dir: Path) -> str:
    pyproject_path = source_dir / "pyproject.toml"
    if not pyproject_path.exists():
        pyproject_path = source_dir / "build" / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError("pyproject.toml was not found in the prepared source tree")

    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    poetry_requirement = pyproject.get("tool", {}).get("poetry", {}).get("requires-poetry")
    if not poetry_requirement:
        return "2.1.1"

    for pattern in (r">=\s*([0-9]+(?:\.[0-9]+){1,2})", r"==\s*([0-9]+(?:\.[0-9]+){1,2})", r">\s*([0-9]+(?:\.[0-9]+){1,2})"):
        match = re.search(pattern, poetry_requirement)
        if match:
            return match.group(1)

    raise ValueError(f"Unsupported requires-poetry constraint: {poetry_requirement}")


def read_pyproject(source_dir: Path) -> dict:
    pyproject_path = source_dir / "pyproject.toml"
    if not pyproject_path.exists():
        pyproject_path = source_dir / "build" / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError("pyproject.toml was not found in the prepared source tree")

    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def extract_export_extras(source_dir: Path) -> list[str]:
    pyproject = read_pyproject(source_dir)
    desired = ["all", "test", "systemd"]

    poetry_extras = pyproject.get("tool", {}).get("poetry", {}).get("extras", {})
    project_optional = pyproject.get("project", {}).get("optional-dependencies", {})
    available = set(poetry_extras.keys()) | set(project_optional.keys())

    return [extra for extra in desired if extra in available]


def write_metadata(
    path: Path,
    *,
    version: str,
    upstream_version: str,
    tag: str,
    series: str,
    source_dir: Path,
    revision: int,
    poetry_version: str,
    export_extras: list[str],
) -> None:
    metadata = {
        "package_name": PACKAGE_NAME,
        "package_version": version,
        "upstream_version": upstream_version,
        "series": series,
        "tag": tag,
        "revision": revision,
        "poetry_version": poetry_version,
        "export_extras": export_extras,
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
    parser.add_argument("--upload-state", default="versions/uploads.json")
    parser.add_argument("--tarball-url")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    template_dir = Path(args.template_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    upload_state_path = Path(args.upload_state).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix_prefix = read_series_suffix_prefix(config_path, args.series)
    revision = read_upload_revision(upload_state_path, args.version, args.series)
    upstream_version = args.version
    package_version = f"{upstream_version}-0{suffix_prefix}{revision}"
    source_dir = output_dir / f"{PACKAGE_NAME}-{upstream_version}"

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
    if debian_dir.exists():
        shutil.rmtree(debian_dir)
    shutil.copytree(template_dir, debian_dir)
    normalize_debian_permissions(debian_dir)

    generated_files = [
        debian_dir / "files",
        source_dir / ".cargo",
        source_dir / "vendor",
    ]
    for path in generated_files:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    poetry_version = extract_minimum_poetry_version(source_dir)
    export_extras = extract_export_extras(source_dir)
    write_changelog(debian_dir / "changelog", package_version, args.series, args.tag)
    write_metadata(
        debian_dir / ".packaging-info.json",
        version=package_version,
        upstream_version=upstream_version,
        tag=args.tag,
        series=args.series,
        source_dir=source_dir,
        revision=revision,
        poetry_version=poetry_version,
        export_extras=export_extras,
    )

    print(source_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
