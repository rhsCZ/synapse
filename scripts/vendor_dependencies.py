#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_POETRY_VERSION = "2.1.1"
POETRY_PLUGIN_EXPORT_VERSION = "1.9.0"
DEFAULT_EXPORT_EXTRAS = ["all", "test", "systemd"]
TOOLING_WHEEL_PREFIXES = (
    "mock-",
    "pip-",
    "setuptools-",
    "wheel-",
    "poetry-",
    "poetry_core-",
    "poetry_plugin_export-",
)
BINARY_VENDOR_SUFFIXES = {
    ".a",
    ".bin",
    ".blb",
    ".crt",
    ".der",
    ".dll",
    ".dylib",
    ".exe",
    ".key",
    ".lib",
    ".o",
    ".p8",
    ".png",
    ".so",
}
CARGO_EXECUTABLE = Path("/usr/lib/rust-1.96/bin/cargo") if Path("/usr/lib/rust-1.96/bin/cargo").exists() else Path("cargo")
RISCV64_TARGET_LEXICON_ALIAS = (
    '            "riscv64gc" => Riscv64gc,\n'
    '            "riscv64a23" => Riscv64gc,\n'
)


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def run_result(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command))
    return subprocess.run(command, cwd=cwd, env=env, text=True)


def venv_executable(venv_dir: Path, name: str) -> Path:
    scripts_dir = venv_dir / "Scripts"
    if scripts_dir.exists():
        suffix = ".exe" if name == "python" else ".exe"
        candidate = scripts_dir / f"{name}{suffix}"
        if candidate.exists():
            return candidate

    bin_dir = venv_dir / "bin"
    return bin_dir / name


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "synapse-launchpad-packaging"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            return
        except Exception as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            if os.name != "nt":
                raise

    run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Invoke-WebRequest -Uri '{url}' -OutFile '{destination}'",
        ]
    )
    if last_error is not None and not destination.exists():
        raise last_error


def fetch_json(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": "synapse-launchpad-packaging"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            if os.name != "nt":
                return None

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_path = Path(temp_dir_name) / "payload.json"
        run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Invoke-WebRequest -Uri '{url}' -OutFile '{temp_path}'",
            ]
        )
        return json.loads(temp_path.read_text(encoding="utf-8"))


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


def extract_requirement_name(requirement: str) -> str:
    token = requirement.strip().split(";", 1)[0].strip()
    for separator in ("[", "<", ">", "=", "!", "~", " "):
        token = token.split(separator, 1)[0]
    return token.replace("_", "-").strip()


def is_universal_wheel(path: Path) -> bool:
    return path.name.endswith("none-any.whl")


def prune_platform_wheels_for_requirement(wheel_dir: Path, package_name: str, version: str) -> None:
    prefixes = (
        f"{package_name.replace('-', '_')}-{version.replace('-', '_')}-",
        f"{package_name.replace('-', '_')}-{version}-",
        f"{package_name}-{version.replace('-', '_')}-",
        f"{package_name}-{version}-",
    )
    for path in wheel_dir.iterdir():
        if not path.is_file() or path.suffix != ".whl":
            continue
        if not path.name.startswith(prefixes):
            continue
        if is_universal_wheel(path):
            continue
        path.unlink()


def download_sdist_from_pypi(package_name: str, version: str, wheel_dir: Path) -> Path | None:
    normalized = package_name.replace("_", "-")
    url = f"https://pypi.org/pypi/{normalized}/{version}/json"
    payload = fetch_json(url)
    if payload is None:
        return None

    urls = payload.get("urls", [])
    for artifact in urls:
        if artifact.get("packagetype") != "sdist":
            continue

        filename = artifact.get("filename")
        download_url = artifact.get("url")
        if not filename or not download_url:
            continue

        destination = wheel_dir / filename
        download_file(download_url, destination)
        return destination

    return None


def download_sdist_with_pip(python: str, wheel_dir: Path, package_name: str, version: str) -> Path | None:
    before = set(wheel_dir.iterdir())
    requirement = f"{package_name}=={version}"
    result = run_result(
        [
            python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "--no-deps",
            "--no-binary=:all:",
            requirement,
        ]
    )
    if result.returncode != 0:
        return None

    for path in wheel_dir.iterdir():
        if path in before or not path.is_file():
            continue
        if path.name.endswith(".tar.gz") or path.suffix == ".zip":
            return path

    return None


def parse_exported_requirements(requirements_path: Path) -> list[tuple[str, str]]:
    logical_lines: list[str] = []
    current = ""

    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.endswith("\\"):
            current += stripped[:-1].rstrip() + " "
            continue

        current += stripped
        logical_lines.append(current)
        current = ""

    if current:
        logical_lines.append(current)

    requirements: list[tuple[str, str]] = []
    for line in logical_lines:
        base = line.split(" --hash=", 1)[0].split(";", 1)[0].strip()
        if "==" not in base:
            continue

        package_name, version = base.split("==", 1)
        package_name = package_name.strip()
        version = version.strip()
        if package_name and version:
            requirements.append((package_name, version))

    return requirements


def download_exact_requirement(python: str, wheel_dir: Path, package_name: str, version: str) -> None:
    requirement = f"{package_name}=={version}"
    result = run_result(
        [
            python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "--no-deps",
            "--only-binary=:all:",
            requirement,
        ]
    )
    if result.returncode == 0:
        return

    source_artifact = download_sdist_from_pypi(package_name, version, wheel_dir)
    if source_artifact is None:
        source_artifact = download_sdist_with_pip(python, wheel_dir, package_name, version)
    if source_artifact is not None:
        return

    raise subprocess.CalledProcessError(result.returncode, result.args)


def download_exported_requirements(python: str, requirements_path: Path, wheel_dir: Path) -> None:
    for package_name, version in parse_exported_requirements(requirements_path):
        download_exact_requirement(python, wheel_dir, package_name, version)


def download_build_requirement(python: str, wheel_dir: Path, requirement: str) -> None:
    requirement_name = extract_requirement_name(requirement)
    before = set(wheel_dir.iterdir())
    binary_result = run_result(
        [
            python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "--no-deps",
            "--only-binary=:all:",
            requirement,
        ]
    )
    if binary_result.returncode == 0:
        downloaded = [path for path in wheel_dir.iterdir() if path not in before and path.is_file()]
        platform_wheels = [path for path in downloaded if path.suffix == ".whl" and not is_universal_wheel(path)]
        if platform_wheels:
            source_result = run_result(
                [
                    python,
                    "-m",
                    "pip",
                    "download",
                    "--dest",
                    str(wheel_dir),
                    "--no-deps",
                    "--no-binary=:all:",
                    requirement,
                ]
            )
            if source_result.returncode != 0:
                raise subprocess.CalledProcessError(source_result.returncode, source_result.args)
            for wheel_path in platform_wheels:
                wheel_path.unlink(missing_ok=True)
        return

    source_result = run_result(
        [
            python,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheel_dir),
            "--no-deps",
            "--no-binary=:all:",
            requirement,
        ]
    )
    if source_result.returncode == 0:
        return

    raise subprocess.CalledProcessError(source_result.returncode, source_result.args)


def read_pyproject_from_sdist(artifact_path: Path) -> dict | None:
    if artifact_path.name.endswith(".tar.gz"):
        with tarfile.open(artifact_path, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.name.endswith("/pyproject.toml") or member.name == "pyproject.toml"]
            if not members:
                return None
            fileobj = archive.extractfile(members[0])
            if fileobj is None:
                return None
            return tomllib.loads(fileobj.read().decode("utf-8"))

    if artifact_path.suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as archive:
            members = [name for name in archive.namelist() if name.endswith("/pyproject.toml") or name == "pyproject.toml"]
            if not members:
                return None
            return tomllib.loads(archive.read(members[0]).decode("utf-8"))

    return None


def extract_sdist(artifact_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)

    if artifact_path.name.endswith(".tar.gz"):
        with tarfile.open(artifact_path, "r:gz") as archive:
            archive.extractall(destination)
    elif artifact_path.suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as archive:
            archive.extractall(destination)
    else:
        raise ValueError(f"Unsupported sdist format: {artifact_path}")

    extracted_dirs = [path for path in destination.iterdir() if path.is_dir()]
    if len(extracted_dirs) != 1:
        raise RuntimeError(f"Expected one extracted directory in {destination}, found {len(extracted_dirs)}")

    return extracted_dirs[0]


def discover_build_requirements(artifact_path: Path) -> list[str]:
    pyproject = read_pyproject_from_sdist(artifact_path)
    if pyproject is None:
        return ["setuptools", "wheel"]

    build_system = pyproject.get("build-system", {})
    requires = build_system.get("requires")
    if not requires:
        return ["setuptools", "wheel"]

    return [str(requirement) for requirement in requires]


def recursive_vendor_build_requirements(python: str, wheel_dir: Path, vendor_dir: Path) -> None:
    build_requirements: list[str] = []
    seen_requirements: set[str] = set()
    inspected_sdists: set[Path] = set()

    while True:
        sdists = [
            path for path in wheel_dir.iterdir()
            if path.is_file() and (path.name.endswith(".tar.gz") or path.suffix == ".zip")
        ]
        pending_sdists = [path for path in sdists if path not in inspected_sdists]
        if not pending_sdists:
            break

        new_requirements: list[str] = []
        for artifact_path in pending_sdists:
            inspected_sdists.add(artifact_path)
            for requirement in discover_build_requirements(artifact_path):
                if requirement in seen_requirements:
                    continue
                seen_requirements.add(requirement)
                build_requirements.append(requirement)
                new_requirements.append(requirement)

        if not new_requirements:
            continue

        temp_requirements = vendor_dir / ".build-requirements.in"
        temp_requirements.write_text(
            "".join(f"{requirement}\n" for requirement in new_requirements),
            encoding="utf-8",
            newline="\n",
        )
        for requirement in new_requirements:
            download_build_requirement(python, wheel_dir, requirement)
        newly_downloaded = [
            path
            for path in wheel_dir.iterdir()
            if path not in inspected_sdists and path.is_file()
        ]
        replace_platform_wheels_with_sdists(python, wheel_dir, newly_downloaded)
        temp_requirements.unlink(missing_ok=True)

    build_requirements_path = vendor_dir / "build-requirements.txt"
    build_requirements_path.write_text(
        "".join(f"{requirement}\n" for requirement in dict.fromkeys(build_requirements)),
        encoding="utf-8",
        newline="\n",
    )


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
        source_artifact = download_sdist_from_pypi(package_name, version, wheel_dir)
        if source_artifact is None:
            source_artifact = download_sdist_with_pip(python, wheel_dir, package_name, version)
        if source_artifact is not None:
            artifact_path.unlink(missing_ok=True)


def vendor_cargo_dependencies(source_dir: Path, cargo_vendor_dir: Path, wheel_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        command = [str(CARGO_EXECUTABLE), "vendor", "--locked", "--versioned-dirs"]

        for artifact_path in sorted(wheel_dir.iterdir()):
            if not artifact_path.is_file():
                continue
            if not (artifact_path.name.endswith(".tar.gz") or artifact_path.suffix == ".zip"):
                continue

            extract_dir = temp_dir / artifact_path.name.removesuffix(".tar.gz").removesuffix(".zip")
            extracted_root = extract_sdist(artifact_path, extract_dir)
            for cargo_toml in sorted(extracted_root.rglob("Cargo.toml")):
                cargo_lock = cargo_toml.with_name("Cargo.lock")
                if not cargo_lock.exists():
                    continue
                command.extend(["--sync", str(cargo_toml)])

        command.append(str(cargo_vendor_dir))
        run(command, cwd=source_dir)


def patch_target_lexicon_riscv64_alias(cargo_vendor_dir: Path) -> None:
    for crate_dir in sorted(cargo_vendor_dir.glob("target-lexicon-0.13.2")):
        targets_path = crate_dir / "src" / "targets.rs"
        checksum_path = crate_dir / ".cargo-checksum.json"
        if not targets_path.exists() or not checksum_path.exists():
            continue

        original = targets_path.read_text(encoding="utf-8")
        if '"riscv64a23"' in original:
            continue

        needle = '            "riscv64gc" => Riscv64gc,\n'
        if needle not in original:
            raise RuntimeError(f"Unable to patch {targets_path}: expected riscv64gc match not found")

        updated = original.replace(needle, RISCV64_TARGET_LEXICON_ALIAS, 1)
        targets_path.write_text(updated, encoding="utf-8", newline="\n")

        checksum_data = json.loads(checksum_path.read_text(encoding="utf-8"))
        checksum_data["files"]["src/targets.rs"] = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        checksum_path.write_text(
            json.dumps(checksum_data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
            newline="\n",
        )


def read_poetry_version(source_dir: Path) -> str:
    metadata_path = source_dir / "debian" / ".packaging-info.json"
    if not metadata_path.exists():
        return DEFAULT_POETRY_VERSION

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata.get("poetry_version", DEFAULT_POETRY_VERSION)


def read_export_extras(source_dir: Path) -> list[str]:
    metadata_path = source_dir / "debian" / ".packaging-info.json"
    if not metadata_path.exists():
        return DEFAULT_EXPORT_EXTRAS

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    extras = metadata.get("export_extras", DEFAULT_EXPORT_EXTRAS)
    return [str(extra) for extra in extras]


def looks_binary(path: Path) -> bool:
    if path.suffix in BINARY_VENDOR_SUFFIXES:
        return True

    with path.open("rb") as handle:
        return b"\0" in handle.read(4096)


def update_include_binaries(source_dir: Path) -> None:
    cargo_vendor_dir = source_dir / "vendor" / "cargo"
    include_binaries_path = source_dir / "debian" / "source" / "include-binaries"

    binary_paths: list[str] = []
    if cargo_vendor_dir.exists():
        for path in sorted(cargo_vendor_dir.rglob("*")):
            if not path.is_file():
                continue
            if looks_binary(path):
                binary_paths.append(path.relative_to(source_dir).as_posix())

    if binary_paths:
        include_binaries_path.parent.mkdir(parents=True, exist_ok=True)
        include_binaries_path.write_text(
            "".join(f"{relative_path}\n" for relative_path in binary_paths),
            encoding="utf-8",
            newline="\n",
        )
    else:
        include_binaries_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--python", default="python3")
    parser.add_argument("--skip-cargo", action="store_true")
    parser.add_argument("--refresh-include-binaries-only", action="store_true")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    vendor_dir = source_dir / "vendor"
    wheel_dir = vendor_dir / "wheels"
    cargo_vendor_dir = vendor_dir / "cargo"
    requirements_path = vendor_dir / "exported_requirements.txt"
    poetry_version = read_poetry_version(source_dir)
    export_extras = read_export_extras(source_dir)

    if args.refresh_include_binaries_only:
        update_include_binaries(source_dir)
        print(source_dir)
        return 0

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
            "mock",
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

        venv_python = venv_executable(venv_dir, "python")
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
        export_command = [str(venv_executable(venv_dir, "poetry")), "export"]
        for extra in export_extras:
            export_command.extend(["--extras", extra])
        export_command.extend(["-o", str(requirements_path)])
        run(export_command, cwd=source_dir)

    existing_artifacts = set(wheel_dir.iterdir())
    download_exported_requirements(args.python, requirements_path, wheel_dir)
    downloaded_artifacts = [path for path in wheel_dir.iterdir() if path not in existing_artifacts]
    replace_platform_wheels_with_sdists(args.python, wheel_dir, downloaded_artifacts)
    recursive_vendor_build_requirements(args.python, wheel_dir, vendor_dir)

    cargo_lock = source_dir / "Cargo.lock"
    if args.skip_cargo:
        print("Skipping cargo vendor as requested.")
    elif cargo_lock.exists():
        vendor_cargo_dependencies(source_dir, cargo_vendor_dir, wheel_dir)
        patch_target_lexicon_riscv64_alias(cargo_vendor_dir)
    else:
        print("Cargo.lock not found, skipping cargo vendor.")

    update_include_binaries(source_dir)

    print(source_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
