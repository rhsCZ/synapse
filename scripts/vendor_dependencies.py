#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


POETRY_VERSION = "2.1.1"
POETRY_PLUGIN_EXPORT_VERSION = "1.9.0"


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


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

    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    wheel_dir.mkdir(parents=True, exist_ok=True)
    cargo_vendor_dir.mkdir(parents=True, exist_ok=True)

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
            f"poetry=={POETRY_VERSION}",
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
                f"poetry=={POETRY_VERSION}",
                f"poetry-plugin-export=={POETRY_PLUGIN_EXPORT_VERSION}",
            ]
        )
        run(
            [
                str(venv_dir / "bin" / "poetry"),
                "export",
                "--extras",
                "all",
                "--extras",
                "test",
                "--extras",
                "systemd",
                "-o",
                str(requirements_path),
            ],
            cwd=source_dir,
        )

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
