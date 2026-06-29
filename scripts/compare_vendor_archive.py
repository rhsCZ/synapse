#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path


def list_source_vendor_files(source_dir: Path) -> set[str]:
    vendor_dir = source_dir / "vendor"
    if not vendor_dir.is_dir():
        raise FileNotFoundError(f"Vendor directory not found: {vendor_dir}")

    return {
        path.relative_to(source_dir).as_posix()
        for path in vendor_dir.rglob("*")
        if path.is_file()
    }


def list_archive_vendor_files(archive_path: Path) -> set[str]:
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(roots) != 1:
            raise RuntimeError(f"Expected exactly one archive root in {archive_path}, found: {sorted(roots)}")

        root = next(iter(roots))
        prefix = f"{root}/vendor/"
        result: set[str] = set()
        for member in members:
            if not member.isfile():
                continue
            name = member.name
            if not name.startswith(prefix):
                continue
            result.add(name[len(root) + 1 :])
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--archive", required=True)
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    archive_path = Path(args.archive).resolve()

    source_files = list_source_vendor_files(source_dir)
    archive_files = list_archive_vendor_files(archive_path)

    missing_in_archive = sorted(source_files - archive_files)
    extra_in_archive = sorted(archive_files - source_files)

    print(f"source vendor files:  {len(source_files)}")
    print(f"archive vendor files: {len(archive_files)}")
    print(f"missing in archive:   {len(missing_in_archive)}")
    print(f"extra in archive:     {len(extra_in_archive)}")

    if missing_in_archive:
        print("\nMissing in archive:")
        for path in missing_in_archive[:200]:
            print(path)
        if len(missing_in_archive) > 200:
            print(f"... truncated, {len(missing_in_archive) - 200} more")

    if extra_in_archive:
        print("\nExtra in archive:")
        for path in extra_in_archive[:200]:
            print(path)
        if len(extra_in_archive) > 200:
            print(f"... truncated, {len(extra_in_archive) - 200} more")

    return 1 if missing_in_archive or extra_in_archive else 0


if __name__ == "__main__":
    sys.exit(main())
