#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def load_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "synapse-launchpad-packaging",
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"latest_version": None, "latest_tag": None, "updated_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def write_github_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="element-hq/synapse")
    parser.add_argument("--state", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    state = load_state(Path(args.state))
    release = load_json(f"https://api.github.com/repos/{args.repo}/releases/latest")

    tag = release["tag_name"]
    version = tag.removeprefix("v")
    is_new = version != state.get("latest_version")

    outputs = {
        "is_new": "true" if is_new else "false",
        "version": version,
        "tag": tag,
        "tarball_url": release["tarball_url"],
    }

    print(json.dumps(outputs, indent=2))

    if args.github_output:
        write_github_output(Path(args.github_output), outputs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
