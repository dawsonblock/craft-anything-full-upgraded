# Craft Anything v0.4.0 — hardened source upgrade V2

This is a second, **independent source fork** of the user-provided
craft-anything-main 2.zip. The underlying Rust workspace version remains
0.4.0; V2 identifies the hardening bundle, not an official upstream release.
It preserves the complete earlier hardened source and does not ship a compiled
desktop binary.

## New changes since the previous hardening ZIP

1. **Recoverable project removal:** Added Workspace::archive_for_deletion
   and the explicit human-only project_archive tool. It moves only
   Craft Anything-owned project files into a collision-safe sibling
   CraftAnything-Trash-.../project/, leaving any personal files in place.
   Every archive contains a versioned receipt.json with its original folder
   and final status. A still-valid manifest travels last so an interrupted
   operation remains identifiable. The archived project can be reopened at
   archive/project/; restoring it to the original folder remains a manual
   procedure (see docs/project-archive.md). The old project_delete API remains available **separately**
   for explicit, confirmed, irreversible deletion by a person.
2. **Safer desktop default:** The project's former Delete affordance now
   invokes project_archive after showing a confirmation and recoverability
   explanation. Success identifies the recovery folder. An ongoing build
   still blocks the action.
3. **Independent opt-in gameplay probe:** Added
   scripts/independent_game_probe.py, a black-box subprocess replay checker
   that independently decodes actual screenshot pixels, verifies CRCs,
   checks movement, action counter and HUD, and rejects missing or unsafe
   image paths. It is **not yet integrated into the Rust rebuild gate**.
4. **Failure-focused regression coverage:** Added Rust tests for archive
   collision protection, personal-file preservation, in-place edits and
   caller permissions. Added Python tests with a known-good replay fixture
   and deliberately broken movement, action, HUD, image and path evidence.

## What was actually verified here

- Python standard-library regression suite:
  python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v.
- Archive extraction/integrity, source presence and packaging checks
  (see the final delivery notes for the executed results).

**Unavailable:** Rust, Cargo and rustfmt were not installed in the
packaging environment. None of the Rust tests—including the new archive tests—
could be executed here. No native UI launch, real game-engine run or macOS /
Windows integration test was performed. The new replay fixture is synthetic
and is not evidence of working gameplay in the full application.

## Required validation before production use

From the extracted source on a computer with a compatible Rust toolchain:

    cargo fmt --all -- --check
    cargo xtask i18n-coverage
    cargo xtask tools --check
    cargo xtask schemas --check
    cargo xtask layers
    cargo test -p craft-anything-engine
    cargo test -p craft-anything-ui-egui
    cargo clippy --workspace --all-targets -- -D warnings
    cargo build -p craft-anything-cli
    python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v

Rebuild the UI, exercise archive and permanent delete paths manually, and
make sure the archive is outside the active-project list on macOS, Windows and
Linux. Generated tool schemas and metadata must be refreshed if xtask reports
a mismatch.

## Remaining material risks

- The Python replay probe **trusts** the program to supply correct telemetry
  and image content. Independent input injection and video capture are not
  implemented, and this standalone tool is **not a security sandbox**.
- Archiving moves filesystem entries with rename and is designed for
  sibling directories on one volume. A partial move or an unusual filesystem
  error requires manual inspection of the archive and original project;
  full atomic transactions or one-click restoration are not present.
- Other weaknesses listed in UPGRADE_REPORT.md—third-party
  extension isolation, cross-platform process handling, full reproducibility
  and target/game-specific behavioral parity—still require real tests.

The modified fork is governed by the original source license files; no official
upstream endorsement is implied.
