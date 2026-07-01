#!/usr/bin/env bash

set -euo pipefail

SOURCE_DIR="${1:?source dir is required}"
GPG_KEY_ID="${2:?gpg key id or fingerprint is required}"
OUTPUT_DIR="${3:-dist}"

mkdir -p "${OUTPUT_DIR}"

pushd "${SOURCE_DIR}" >/dev/null

SOURCE_NAME="$(dpkg-parsechangelog -S Source)"
SOURCE_VERSION="$(dpkg-parsechangelog -S Version)"
UPSTREAM_VERSION="${SOURCE_VERSION%-*}"
SOURCE_BASENAME="${SOURCE_NAME}-${UPSTREAM_VERSION}"

ORIG_TARBALL="../${SOURCE_NAME}_${UPSTREAM_VERSION}.orig.tar.xz"
ORIG_VENDOR_TARBALL="../${SOURCE_NAME}_${UPSTREAM_VERSION}.orig-vendor.tar.xz"

rm -f "${ORIG_TARBALL}" "${ORIG_VENDOR_TARBALL}"

tar --exclude='./debian' --exclude='./vendor' --exclude='./.pc' -cJf "${ORIG_TARBALL}" \
    --transform "s,^\\.,${SOURCE_BASENAME}," .
tar -C vendor -cJf "${ORIG_VENDOR_TARBALL}" .

if [ -f "debian/rules" ]; then
    chmod +x debian/rules
fi

export DEB_SIGN_KEYID="${GPG_KEY_ID}"
dpkg-buildpackage -d -S -sa -k"${GPG_KEY_ID}"

popd >/dev/null

PARENT_DIR="$(dirname "${SOURCE_DIR}")"
shopt -s nullglob
for artifact in \
    "${PARENT_DIR}/${SOURCE_NAME}_${UPSTREAM_VERSION}.orig.tar."* \
    "${PARENT_DIR}/${SOURCE_NAME}_${UPSTREAM_VERSION}.orig-vendor.tar."* \
    "${PARENT_DIR}/${SOURCE_NAME}_${SOURCE_VERSION}.debian.tar."* \
    "${PARENT_DIR}/${SOURCE_NAME}_${SOURCE_VERSION}.dsc" \
    "${PARENT_DIR}/${SOURCE_NAME}_${SOURCE_VERSION}_source.buildinfo" \
    "${PARENT_DIR}/${SOURCE_NAME}_${SOURCE_VERSION}_source.changes"
do
    mv "${artifact}" "${OUTPUT_DIR}/"
done
