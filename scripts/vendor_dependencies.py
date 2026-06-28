#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


DEFAULT_POETRY_VERSION = "2.1.1"
POETRY_PLUGIN_EXPORT_VERSION = "1.9.0"
DEFAULT_EXPORT_EXTRAS = ["all", "test", "systemd"]
TOOLING_WHEEL_PREFIXES = (
    "pip-",
    "setuptools-",
    "wheel-",
    "poetry-",
    "poetry_core-",
    "poetry_plugin_export-",
)


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def parse_wheel_requirement(filename: str) -> tuple[str, str] | None:
    if not filename.endswith(".whl"):
        return None

    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None

    distribution = "-".join(parts[:-4]).replace("_", "-")
    version = parts[-4].replace("_", "-")
    if not distribution or not version:
        return None

    return distribution, version


def replace_platform_wheels_with_sdists(python: str, wheel_dir: Path, artifact_paths: list[Path]) -> None:
    for artifact_path in artifact_paths:
        if artifact_path.suffix != ".whl":
            continue
        if artifact_path.name.endswith("none-any.whl"):
            continue
        if artifact_path.name.startswith(TOOLING_WHEEL_PREFIXES):
            continue

        requirement = parse_wheel_requirement(artifact_path.name)
        if requirement is None:
            continue

        package_name, version = requirement
        before_download = set(wheel_dir.iterdir())
        run(
            [
                python,
                "-m",
                "pip",
                "download",
                "--dest",
                str(wheel_dir),
                "--no-deps",
                "--no-binary=:all:",
                f"{package_name}=={version}",
            ]
        )
        new_sources = [
            path
            for path in wheel_dir.iterdir()
            if path not in before_download and (path.name.endswith(".tar.gz") or path.suffix == ".zip")
        ]
        if new_sources:
            artifact_path.unlink()


def read_poetry_version(source_dir: Path) -> str:
    metadata_path = source_dir / ".packaging-info.json"
    if not metadata_path.exists():
        return DEFAULT_POETRY_VERSION

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata.get("poetry_version", DEFAULT_POETRY_VERSION)


def read_export_extras(source_dir: Path) -> list[str]:
    metadata_path = source_dir / ".packaging-info.json"
    if not metadata_path.exists():
        return DEFAULT_EXPORT_EXTRAS

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    extras = metadata.get("export_extras", DEFAULT_EXPORT_EXTRAS)
    return [str(extra) for extra in extras]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--python", default="python3")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    debian_dir = source_dir / "debian"
    vendor_dir = debian_dir / "vendor"
    wheel_dir = vendor_dir / "wheels"
    cargo_vendor_dir = vendor_dir / "cargo"
    requirements_path = vendor_dir / "exported_requirements.txt"
    poetry_version = read_poetry_version(source_dir)
    export_extras = read_export_extras(source_dir)

    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    wheel_dir.mkdir(parents=True, exist_ok=True)
    cargo_vendor_dir.mkdir(parents=True, exist_ok=True)

    (vendor_dir / "poetry-version.txt").write_text(poetry_version + "\n", encoding="utf-8", newline="\n")
    (vendor_dir / "export-extras.json").write_text(json.dumps(export_extras) + "\n", encoding="utf-8", newline="\n")

    run(
        [
            args.python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "pip",
            "setuptools",
            "wheel",
            f"poetry=={poetry_version}",
            f"poetry-plugin-export=={POETRY_PLUGIN_EXPORT_VERSION}",
        ]
    )

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        venv_dir = temp_dir / "poetry-venv"

        run([args.python, "-m", "venv", str(venv_dir)])

        venv_python = venv_dir / "bin" / "python"
        run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheel_dir),
                f"poetry=={poetry_version}",
                f"poetry-plugin-export=={POETRY_PLUGIN_EXPORT_VERSION}",
            ]
        )
        export_command = [str(venv_dir / "bin" / "poetry"), "export"]
        for extra in export_extras:
            export_command.extend(["--extras", extra])
        export_command.extend(["-o", str(requirements_path)])
        run(export_command, cwd=source_dir)

    existing_artifacts = set(wheel_dir.iterdir())
    run(
        [
            args.python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "--requirement",
            str(requirements_path),
        ]
    )
    downloaded_artifacts = [path for path in wheel_dir.iterdir() if path not in existing_artifacts]
    replace_platform_wheels_with_sdists(args.python, wheel_dir, downloaded_artifacts)

    cargo_lock = source_dir / "Cargo.lock"
    if cargo_lock.exists():
        run(["cargo", "vendor", "--locked", str(cargo_vendor_dir)], cwd=source_dir)
        cargo_config_dir = source_dir / ".cargo"
        cargo_config_dir.mkdir(parents=True, exist_ok=True)
        (cargo_config_dir / "config.toml").write_text(
            (
                "[source.crates-io]\n"
                'replace-with = "vendored-sources"\n\n'
                "[source.vendored-sources]\n"
                'directory = "debian/vendor/cargo"\n'
            ),
            encoding="utf-8",
            newline="\n",
        )
    else:
        print("Cargo.lock not found, skipping cargo vendor.")

    print(source_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
