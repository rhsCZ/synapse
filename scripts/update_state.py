#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--uploads-state")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--series", action="append", default=[])
    args = parser.parse_args()

    state_path = Path(args.state)
    state = {
        "latest_version": args.version,
        "latest_tag": args.tag,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")

    if args.uploads_state:
        uploads_path = Path(args.uploads_state)
        uploads_state = load_json(uploads_path, {"uploads": {}})
        version_uploads = uploads_state.setdefault("uploads", {}).setdefault(args.version, {})
        for series in args.series:
            version_uploads[series] = int(version_uploads.get(series, 0)) + 1
        uploads_path.write_text(json.dumps(uploads_state, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
