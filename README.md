# Synapse Launchpad Packaging

This repository tracks upstream releases from `element-hq/synapse`, prepares an
offline-capable Debian source package for `matrix-synapse-py3`, and uploads the
signed source package to a Launchpad PPA.

## Workflow

1. A scheduled GitHub Actions workflow checks the latest upstream release.
2. For each configured Ubuntu series, the workflow prepares a source tree with:
   - upstream Synapse source
   - `debian/` packaging from `debian-template/`
   - vendored Python wheels
   - vendored Cargo crates
3. GitHub builds a signed source package.
4. The source package is uploaded to the configured Launchpad PPA.
5. Launchpad builds binary packages for the architectures available in that PPA.

## Repository layout

- `debian-template/` Debian packaging template copied into each prepared source tree
- `config/series.json` target Ubuntu series and version suffix prefixes
- `scripts/` release detection, source preparation, vendoring, source build, and upload helpers
- `.github/workflows/` scheduled and manual GitHub Actions workflows
- `versions/state.json` last successfully uploaded upstream release
- `versions/uploads.json` upload counters used to increment suffixes such as `noble1`, `noble2`, ...

## Required GitHub secrets

- `LAUNCHPAD_GPG_PRIVATE_KEY` ASCII-armored private key used to sign source uploads
- `LAUNCHPAD_GPG_PASSPHRASE` passphrase for the private key
- `LAUNCHPAD_GPG_KEY_ID` signing key identifier used by `dpkg-buildpackage`
- `LAUNCHPAD_PPA` target upload shortcut, for example `ppa:example/ubuntu/synapse`

Example values:

```text
LAUNCHPAD_GPG_PRIVATE_KEY
-----BEGIN PGP PRIVATE KEY BLOCK-----

lQPGBGjExampleBCAC7W...
...
=abcd
-----END PGP PRIVATE KEY BLOCK-----
```

```text
LAUNCHPAD_GPG_PASSPHRASE
correct-horse-battery-staple
```

```text
LAUNCHPAD_GPG_KEY_ID
040E32ED181FA56A8FD7451A6E31AE9E1202A960
```

```text
LAUNCHPAD_PPA
ppa:your-launchpad-team/ubuntu/synapse
```

Notes:

- `LAUNCHPAD_GPG_PRIVATE_KEY` must be the full ASCII-armored secret key exported with `gpg --armor --export-secret-keys <KEY_ID>`.
- `LAUNCHPAD_GPG_PASSPHRASE` is the passphrase protecting that private key. If you create an unprotected CI-only key, this secret can be left empty, but using a passphrase is safer.
- `LAUNCHPAD_GPG_KEY_ID` should match the signing key imported into Launchpad. Use the full fingerprint to avoid `dpkg-buildpackage` warnings about long key IDs.
- `LAUNCHPAD_PPA` is the `dput` target in Launchpad shortcut form. Typical values look like `ppa:your-user/ubuntu/synapse` or `ppa:your-team/ubuntu/packages`.

## Required Launchpad setup

1. Import the public GPG key into the Launchpad account that owns or can upload to the PPA.
2. Ensure the PPA exposes the required build dependencies for each target series.
3. Upload auxiliary toolchain packages such as `rustc-1.96` and `cargo-1.96` before uploading Synapse.

## Notes

- The current template keeps Debian source format `3.0 (native)` to match the packaging you already have.
- Vendoring is done during the GitHub workflow because Launchpad builders cannot fetch from PyPI or crates.io during package builds.
- Upload suffixes are tracked per upstream version and Ubuntu series. Re-uploading `1.155.0` for `noble` increments the package version from `1.155.0+noble1` to `1.155.0+noble2`.
