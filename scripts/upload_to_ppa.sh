#!/usr/bin/env bash

set -euo pipefail

CHANGES_FILE="${1:?changes file is required}"
PPA_TARGET="${2:?ppa target is required}"

dput "${PPA_TARGET}" "${CHANGES_FILE}"
