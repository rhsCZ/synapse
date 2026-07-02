#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/test_pbuilder_offline.sh --version VERSION --series SERIES [options]

Required:
  --version VERSION           Upstream Synapse version, for example 1.155.0
  --series SERIES             Ubuntu series, for example noble or resolute

Optional:
  --tag TAG                   Git tag, defaults to v<VERSION>
  --tarball-url URL           Override upstream release tarball URL
  --architecture ARCH         Target architecture, defaults to host dpkg arch
  --work-dir DIR              Workspace root, defaults to work/pbuilder
  --mirror URL                Ubuntu mirror for base.tgz bootstrap/update
  --local-repo DIR            Local apt repo, defaults to /srv/local-apt-repo
  --skip-prepare              Reuse existing prepared source tree and skip vendoring
  --skip-vendoring            Prepare a fresh source tree, but reuse an existing vendor directory
  --refresh-base              Refresh existing base.tgz explicitly
  --skip-base-update          Deprecated alias for reusing existing base.tgz
  --preserve-buildplace       Keep pbuilder build directory after build
  --help                      Show this help

This script:
  1. optionally prepares a source tree with scripts/prepare_source.py
  2. optionally vendors Python and Cargo dependencies
  3. builds an unsigned source package locally
  4. creates or refreshes a pbuilder base.tgz from an Ubuntu mirror
  5. runs pbuilder fully offline against the local apt repo
EOF
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

default_mirror_for_arch() {
    case "$1" in
        amd64|i386)
            echo "http://archive.ubuntu.com/ubuntu"
            ;;
        *)
            echo "http://ports.ubuntu.com/ubuntu-ports"
            ;;
    esac
}

is_valid_basetgz() {
    local basetgz="$1"

    if [ ! -s "${basetgz}" ]; then
        return 1
    fi

    tar -tf "${basetgz}" >/dev/null 2>&1
}

create_basetgz_atomically() {
    local final_basetgz="$1"
    shift

    local temp_basetgz
    temp_basetgz="$(mktemp "${final_basetgz}.tmp.XXXXXX")"
    rm -f "${temp_basetgz}"

    if run_root pbuilder create --basetgz "${temp_basetgz}" "$@"; then
        mv -f "${temp_basetgz}" "${final_basetgz}"
    else
        rm -f "${temp_basetgz}"
        return 1
    fi
}

update_basetgz_atomically() {
    local final_basetgz="$1"
    shift

    local temp_basetgz
    temp_basetgz="$(mktemp "${final_basetgz}.tmp.XXXXXX")"
    cp "${final_basetgz}" "${temp_basetgz}"

    if run_root pbuilder update --basetgz "${temp_basetgz}" --override-config "$@"; then
        mv -f "${temp_basetgz}" "${final_basetgz}"
    else
        rm -f "${temp_basetgz}"
        return 1
    fi
}

write_apt_conf() {
    local aptconf_dir="$1"
    mkdir -p "${aptconf_dir}"
    cat >"${aptconf_dir}/apt.conf" <<'EOF'
Acquire::AllowInsecureRepositories "true";
Acquire::AllowDowngradeToInsecureRepositories "true";
APT::Get::AllowUnauthenticated "true";
EOF
}

write_hook_script() {
    local hook_dir="$1"
    local local_repo="$2"
    mkdir -p "${hook_dir}"
    cat >"${hook_dir}/D05apt-update" <<EOF
#!/bin/sh
set -e
printf 'deb [trusted=yes] file:${local_repo} ./\n' > /etc/apt/sources.list
rm -f /etc/apt/sources.list.d/*
apt-get update
EOF
    chmod +x "${hook_dir}/D05apt-update"
}

write_pbuilderrc() {
    local rc_path="$1"
    local local_repo="$2"
    local aptconf_dir="$3"
    local hook_dir="$4"
    local series="$5"

    cat >"${rc_path}" <<EOF
MIRRORSITE="file://${local_repo}"
DISTRIBUTION="${series}"
COMPONENTS="main"
ALLOWUNTRUSTED="yes"
PBUILDERSATISFYDEPENDSCMD="/usr/lib/pbuilder/pbuilder-satisfydepends-apt"
BINDMOUNTS="${local_repo}"
APTCONFDIR="${aptconf_dir}"
HOOKDIR="${hook_dir}"
DEBOOTSTRAPOPTS=(
    '--variant=buildd'
    '--no-check-gpg'
)
USENETWORK="no"
EOF
}

write_pbuilder_base_rc() {
    local rc_path="$1"
    local mirror="$2"
    local series="$3"

    cat >"${rc_path}" <<EOF
MIRRORSITE="${mirror}"
DISTRIBUTION="${series}"
COMPONENTS="main restricted universe multiverse"
DEBOOTSTRAPOPTS=(
    '--variant=buildd'
)
EOF
}

check_host_prerequisites() {
    require_command python3
    require_command pbuilder
    require_command dh_virtualenv
    require_command dpkg-buildpackage

    if [ ! -f /usr/share/perl5/Debian/Debhelper/Sequence/python_virtualenv.pm ]; then
        cat >&2 <<'EOF'
Missing host packaging prerequisite: Debian::Debhelper::Sequence::python_virtualenv
Install the same packaging tools as in CI before running this script, for example:
  sudo apt-get install debhelper devscripts dh-virtualenv dpkg-dev fakeroot
EOF
        exit 1
    fi
}

restore_vendor_from_source_tarball() {
    local source_dir="$1"
    local source_dist_dir="$2"

    local source_tarball=""
    source_tarball="$(find "${source_dist_dir}" -maxdepth 1 -type f -name 'matrix-synapse-py3_*.tar.*' | sort | tail -n 1)"
    if [ -z "${source_tarball}" ] || [ ! -f "${source_tarball}" ]; then
        return 1
    fi

    local package_root
    package_root="$(tar -tf "${source_tarball}" | head -n 1 | cut -d/ -f1)"
    if [ -z "${package_root}" ]; then
        return 1
    fi

    local vendor_archive_path=""
    if tar -tf "${source_tarball}" "${package_root}/debian/vendor" >/dev/null 2>&1; then
        vendor_archive_path="${package_root}/debian/vendor"
        rm -rf "${source_dir}/vendor"
        mkdir -p "${source_dir}"
        tar -xf "${source_tarball}" -C "${source_dir}" \
            --strip-components=2 \
            "${vendor_archive_path}"
    elif tar -tf "${source_tarball}" "${package_root}/vendor" >/dev/null 2>&1; then
        vendor_archive_path="${package_root}/vendor"
        rm -rf "${source_dir}/vendor"
        mkdir -p "${source_dir}"
        tar -xf "${source_tarball}" -C "${source_dir}" \
            --strip-components=1 \
            "${vendor_archive_path}"
    else
        return 1
    fi
}

VERSION=""
TAG=""
SERIES=""
TARBALL_URL=""
ARCHITECTURE="$(dpkg --print-architecture)"
WORK_DIR="work/pbuilder"
MIRROR=""
LOCAL_REPO="/srv/local-apt-repo"
SKIP_PREPARE=0
SKIP_VENDORING=0
REFRESH_BASE=0
PRESERVE_BUILDPLACE=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:?missing value for --version}"
            shift 2
            ;;
        --tag)
            TAG="${2:?missing value for --tag}"
            shift 2
            ;;
        --series)
            SERIES="${2:?missing value for --series}"
            shift 2
            ;;
        --tarball-url)
            TARBALL_URL="${2:?missing value for --tarball-url}"
            shift 2
            ;;
        --architecture)
            ARCHITECTURE="${2:?missing value for --architecture}"
            shift 2
            ;;
        --work-dir)
            WORK_DIR="${2:?missing value for --work-dir}"
            shift 2
            ;;
        --mirror)
            MIRROR="${2:?missing value for --mirror}"
            shift 2
            ;;
        --local-repo)
            LOCAL_REPO="${2:?missing value for --local-repo}"
            shift 2
            ;;
        --skip-prepare)
            SKIP_PREPARE=1
            shift
            ;;
        --skip-vendoring)
            SKIP_VENDORING=1
            shift
            ;;
        --skip-base-update)
            REFRESH_BASE=0
            shift
            ;;
        --refresh-base)
            REFRESH_BASE=1
            shift
            ;;
        --preserve-buildplace)
            PRESERVE_BUILDPLACE=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "${VERSION}" ] || [ -z "${SERIES}" ]; then
    usage >&2
    exit 1
fi

if [ "${SKIP_PREPARE}" -eq 1 ] && [ "${SKIP_VENDORING}" -eq 1 ]; then
    echo "--skip-prepare and --skip-vendoring are mutually exclusive." >&2
    exit 1
fi

if [ -z "${TAG}" ]; then
    TAG="v${VERSION}"
fi
if [ -z "${MIRROR}" ]; then
    MIRROR="$(default_mirror_for_arch "${ARCHITECTURE}")"
fi

check_host_prerequisites

WORK_DIR="$(realpath -m "${WORK_DIR}")"
LOCAL_REPO="$(realpath -m "${LOCAL_REPO}")"
SERIES_WORK_DIR="${WORK_DIR}/${SERIES}-${ARCHITECTURE}"
PREPARED_DIR="${SERIES_WORK_DIR}/prepared"
SOURCE_DIST_DIR="${SERIES_WORK_DIR}/source"
RESULT_DIR="${SERIES_WORK_DIR}/result"
APT_CACHE_DIR="${SERIES_WORK_DIR}/aptcache"
BUILDPLACE_DIR="${SERIES_WORK_DIR}/buildplace"
BASE_TGZ="${WORK_DIR}/base-${SERIES}-${ARCHITECTURE}.tgz"
LOG_DIR="${SERIES_WORK_DIR}/logs"
APTCONF_DIR="${SERIES_WORK_DIR}/aptconf"
HOOK_DIR="${SERIES_WORK_DIR}/hooks"
PBUILDER_RC="${SERIES_WORK_DIR}/pbuilder-offline.rc"
PBUILDER_BASE_RC="${SERIES_WORK_DIR}/pbuilder-base.rc"
REUSED_VENDOR_STASH=""

if [ ! -d "${LOCAL_REPO}" ]; then
    echo "Local apt repo does not exist: ${LOCAL_REPO}" >&2
    exit 1
fi
if [ ! -f "${LOCAL_REPO}/Packages" ] && [ ! -f "${LOCAL_REPO}/Packages.gz" ]; then
    echo "Local apt repo is missing Packages or Packages.gz: ${LOCAL_REPO}" >&2
    exit 1
fi

mkdir -p "${PREPARED_DIR}" "${SOURCE_DIST_DIR}" "${RESULT_DIR}" "${APT_CACHE_DIR}" "${BUILDPLACE_DIR}" "${LOG_DIR}"
write_apt_conf "${APTCONF_DIR}"
write_hook_script "${HOOK_DIR}" "${LOCAL_REPO}"
write_pbuilderrc "${PBUILDER_RC}" "${LOCAL_REPO}" "${APTCONF_DIR}" "${HOOK_DIR}" "${SERIES}"
write_pbuilder_base_rc "${PBUILDER_BASE_RC}" "${MIRROR}" "${SERIES}"

PBUILDER_CREATE_ARGS=(
    --distribution "${SERIES}"
    --architecture "${ARCHITECTURE}"
    --configfile "${PBUILDER_BASE_RC}"
    --aptcache "${APT_CACHE_DIR}"
    --buildplace "${BUILDPLACE_DIR}"
)

if [ -f "${BASE_TGZ}" ] && ! is_valid_basetgz "${BASE_TGZ}"; then
    echo "Discarding invalid pbuilder base tarball: ${BASE_TGZ}" >&2
    rm -f "${BASE_TGZ}"
fi

if [ ! -f "${BASE_TGZ}" ]; then
    create_basetgz_atomically "${BASE_TGZ}" "${PBUILDER_CREATE_ARGS[@]}"
elif [ "${REFRESH_BASE}" -eq 1 ]; then
    update_basetgz_atomically "${BASE_TGZ}" "${PBUILDER_CREATE_ARGS[@]}"
fi

if [ "${SKIP_PREPARE}" -eq 1 ]; then
    SOURCE_DIR="$(find "${PREPARED_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'matrix-synapse-py3-*' | sort | tail -n 1)"
    if [ -z "${SOURCE_DIR}" ] || [ ! -d "${SOURCE_DIR}" ]; then
        echo "No prepared source tree found in ${PREPARED_DIR}; cannot use --skip-prepare." >&2
        exit 1
    fi
    echo "Reusing prepared source tree: ${SOURCE_DIR}"
else
    if [ "${SKIP_VENDORING}" -eq 1 ]; then
        EXISTING_VENDOR_DIR="$(
            find "${PREPARED_DIR}" -mindepth 2 -maxdepth 3 -type d \( -path '*/vendor' -o -path '*/debian/vendor' \) \
                | sort | tail -n 1
        )"
        if [ -n "${EXISTING_VENDOR_DIR}" ] && [ -d "${EXISTING_VENDOR_DIR}" ]; then
            REUSED_VENDOR_STASH="$(mktemp -d)"
            cp -a "${EXISTING_VENDOR_DIR}" "${REUSED_VENDOR_STASH}/vendor"
        elif REUSED_VENDOR_STASH="$(mktemp -d)" && restore_vendor_from_source_tarball "${REUSED_VENDOR_STASH}" "${SOURCE_DIST_DIR}"; then
            :
        else
            rm -rf "${REUSED_VENDOR_STASH}"
            echo "No existing vendor directory found in ${PREPARED_DIR} or ${SOURCE_DIST_DIR}; cannot use --skip-vendoring." >&2
            exit 1
        fi
    fi

    prepare_args=(
        --version "${VERSION}"
        --tag "${TAG}"
        --series "${SERIES}"
        --output-dir "${PREPARED_DIR}"
    )
    if [ -n "${TARBALL_URL}" ]; then
        prepare_args+=(--tarball-url "${TARBALL_URL}")
    fi

    SOURCE_DIR="$(python3 scripts/prepare_source.py "${prepare_args[@]}")"
    if [ "${SKIP_VENDORING}" -eq 1 ]; then
        rm -rf "${SOURCE_DIR}/vendor"
        cp -a "${REUSED_VENDOR_STASH}/vendor" "${SOURCE_DIR}/vendor"
        rm -rf "${REUSED_VENDOR_STASH}"
        python3 scripts/vendor_dependencies.py --source-dir "${SOURCE_DIR}" --refresh-include-binaries-only
        echo "Reused vendored dependencies via temporary stash."
    else
        python3 scripts/vendor_dependencies.py --source-dir "${SOURCE_DIR}"
    fi
fi

bash scripts/build_unsigned_source_package.sh "${SOURCE_DIR}" "${SOURCE_DIST_DIR}"

DSC_PATH="$(find "${SOURCE_DIST_DIR}" -maxdepth 1 -name '*.dsc' -print -quit)"
if [ -z "${DSC_PATH}" ]; then
    echo "Failed to find built .dsc in ${SOURCE_DIST_DIR}" >&2
    exit 1
fi

PBUILDER_BUILD_ARGS=(
    --configfile "${PBUILDER_RC}"
    --basetgz "${BASE_TGZ}"
    --distribution "${SERIES}"
    --architecture "${ARCHITECTURE}"
    --override-config
    --aptcache "${APT_CACHE_DIR}"
    --buildplace "${BUILDPLACE_DIR}"
    --buildresult "${RESULT_DIR}"
    --logfile "${LOG_DIR}/pbuilder-build.log"
    --debbuildopts "-uc -us"
    --use-network no
)

if [ "${PRESERVE_BUILDPLACE}" -eq 1 ]; then
    PBUILDER_BUILD_ARGS+=(--preserve-buildplace)
fi

run_root pbuilder build "${PBUILDER_BUILD_ARGS[@]}" "${DSC_PATH}"

echo
echo "Prepared source dir: ${SOURCE_DIR}"
echo "Source package dir: ${SOURCE_DIST_DIR}"
echo "Local apt repo: ${LOCAL_REPO}"
echo "Build results: ${RESULT_DIR}"
echo "pbuilder log: ${LOG_DIR}/pbuilder-build.log"
