# Craft Anything v0.4.0 — cross-platform native CLI evidence V9

V9 is a complete independent source ZIP derived from V8. The Rust workspace
still identifies itself as 0.4.0. This is **not an official Craft Anything
release**, and this ZIP deliberately contains **no built native executables**.

## What changed since V8

1. Added a separate, manually dispatched GitHub Actions workflow:
   `.github/workflows/fork-native-cli-matrix.yml`. It requests native builds on
   hosted Ubuntu, macOS and Windows runners without signing credentials,
   release branches, deployment or user-specific secrets.
2. Added `scripts/native_cli_matrix.py`, a standalone Python 3.10+ CLI build
   runner. It verifies the source inventory **before executing the project**,
   checks the Rust minimum, builds with `cargo build --locked -p
   craft-anything-cli`, then runs the already-bounded V8 `--help` smoke test.
3. Before and after the native launch, the runner rechecks the source
   inventory. Compilation failure, a missing executable, bad help output,
   an unstable executable hash or any modified source prevents a green result.
4. A passing run copies the exact smoke-tested executable into a newly
   created output directory with `CLI_PROVENANCE.json`. Its size and SHA-256
   must match the smoke evidence. Existing artifact directories and report
   files are **not overwritten**. A failing/blocked run does not distribute
   an executable.
5. CI uploads a JSON receipt **even when validation fails** and uploads the
   **unsigned test executable** only when that platform job passes. These
   artifacts are temporary test evidence, not distributable signed releases.
6. Added synthetic regression tests of the full gate ordering, fail-closed
   states, executable copying and no-overwrite behavior.

## Verification performed while building this archive

- **64 Python regression tests passed**, including 13 tests added in V9.
- The source-inventory check passed for **900 files** (V8 contained 896).
- Both the new cross-platform CLI runner and the existing full native gate
  correctly returned **`blocked` / exit 2** when `cargo` and `rustc` were
  unavailable, while still passing source and Python validation.
- The final packaged archive is separately checked for ZIP CRC validity,
  manifest consistency, retained V8 contents and clean verified extraction.

## What V9 does *not* prove

- The native three-OS workflow is **provided but has not been run here**.
  Uploading these files to a GitHub repository does not automatically execute
  a manually dispatched workflow; an authorized user must trigger it.
- This packaging environment lacks `cargo` and `rustc`. We could run the
  Python gate and synthetic tests, but **could not compile or launch the real
  native Rust application**.
- Even green CLI matrix results do **not** test the desktop GUI, MCP server,
  authorization boundaries, integrations, game files, signing, Gatekeeper,
  installation, or a production release.
- A SHA-256 manifest records *consistency* only. It is unsigned and does
  not establish the source's publisher or trustworthiness.

## Next practical milestone

1. Upload the V9 extracted source folder as the repository root in an
   authorized GitHub fork, preserving the source inventory and folder layout.
2. Manually run **Independent Fork Native CLI Matrix** in GitHub Actions.
3. Read each platform's JSON receipt; inspect the failures and do not
   distribute executables that are merely unsigned test artifacts.
4. Run real UI and integration tests on all supported platforms, then follow
   the project's actual signing/notarization and release policy separately.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V8.md](UPGRADE_REPORT_V8.md) for existing gates and their limits.
