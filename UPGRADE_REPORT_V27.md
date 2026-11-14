# Craft Anything — Verified Source Release V27

This V27 archive is an **independent full-source snapshot** of Craft Anything,
continuing V26 and preserving all earlier source files, licenses, assets and
upgrade reports. It is not a compiled application or an official release.

V27 is a release-integrity and reliability upgrade. It merges the V26
packaging controls with the R5.1 published-release verifier, closes the two
confirmed release-check defects, and adds a pre-publication provenance gate.
It does not expand the reverse-engineering engine, and it does not claim
OS-level isolation for autonomous builders.

## Baseline and source-diff inventory

The tree is the `craft-anything-full-upgraded` checkout. Its last pre-R5.1
commit is `87bc84c`; the R5.1 work is `242b3d9` through `6a34848`, and V27 adds
the changes below. No source file was removed. The V26 packaging behaviour is
retained, not replaced:

| V26 control | V27 state |
|---|---|
| Complete source inventory | Retained; `SOURCE_INTEGRITY.json` covers every regular source file |
| Normalized ZIP timestamps and modes | Retained in `write_zip_member` |
| Exclusive destination creation | Retained with `O_CREAT \| O_EXCL` |
| Full extraction and rehash before publishing | Retained in `package_zip` |
| Strict release root | Retained; `package-zip` always requires it |

| File | Change | Purpose |
|---|---|---|
| `scripts/verify_release_source.py` | Added by R5.1, extended by V27 | Download, stage, verify and receipt a published source ZIP; bind it to a commit and attestation |
| `scripts/tests/test_verify_release_source.py` | Added by R5.1, extended by V27 | Release-check, race, provenance, attestation and workflow-input regressions |
| `.github/workflows/fork-release-source-check.yml` | Added by R5.1, hardened by V27 | Check the asset attached to a published release |
| `.github/workflows/fork-release-source-preflight.yml` | Added by V27 | Verify, package, extract, receipt and attest the source at the release commit |
| `.github/workflows/release.yml` | Changed by V27 | Gate the platform jobs on the preflight; check the draft asset before publication |
| `.github/workflows/fork-source-portability.yml` | Changed by R5.1/V27 | Run the packaging and release-verifier round trips on Ubuntu, macOS and Windows |
| `scripts/source_bundle_integrity.py` | Changed by R5.1/V27 | Strict/compatible root policy, reproducible ZIP, exclusive creation, visible policy |
| `scripts/tests/test_source_bundle_integrity.py` | Changed by R5.1/V27 | Packaging, root-policy and extraction regressions |
| `scripts/native_validation.py`, `scripts/native_cli_matrix.py` | Changed by R5.1 | `--locked` native commands and truthful blocked/fail statuses |
| `scripts/tests/test_native_validation.py` | Changed by R5.1 | Locked-command and status regressions |
| `xtask/src/main.rs` | Changed by V27 | Builds the CLI helper before the workspace tests so a clean CI checkout has it |
| `crates/platform/src/which.rs` | Changed by V27 | Finds the CLI at the Cargo profile root from `deps` or a build script's `out/` directory |
| `docs/source-bundle-integrity.md` | Changed by R5.1/V27 | Packaging, root modes, staged verification and provenance |
| `docs/releasing.md` | Changed by V27 | Preflight, draft check and post-publication check |
| `docs/ROADMAP.md`, `docs/STATUS.md` | Changed by R5.1/V27 | What landed and what still waits for a real release |
| `docs/native-validation.md`, `docs/tool-status.md`, `docs/pitfalls.md` | Changed by R5.1 | Native gate and the mistakes already recorded |
| `SOURCE_INTEGRITY.json` | Regenerated | Inventory of this tree |
| `UPGRADE_REPORT_V27.md` | Added | This report |

Function-level comparison of the integrity scripts:

- `source_records`, `make_manifest`, `parse_manifest`: V26 behaviour retained
  (sorted rows, bounded sizes, portable-name checks, cache/target/log
  exclusions).
- `verify_tree`, `verify_zip`, `extract_verified_zip`: R5.1 added
  `--require-declared-root`; V27 reports `root_policy` as `strict` or
  `compatible` in every result.
- `write_zip_member`, `package_zip`: V26 normalized 1980 timestamps, mode
  `0644`, `ZIP_DEFLATED`, exclusive destination creation and extraction
  rehash retained; V27 reports the strict policy.
- `verify_release_source.py`: R5.1 provided download, single-descriptor
  verification, extraction and an exclusive receipt. V27 replaces the
  descriptor with an immutable staged copy, adds commit/tag resolution,
  provenance receipts, artifact-attestation verification, input validation
  through `run_gh`, and the staged-copy rehash invariant.

## The two confirmed release-check defects

### Workflow input injection

The R5.1 workflow inserted `${{ steps.release.outputs.repo }}`, tag and asset
directly into an inline shell script. V27 passes the values through `env`,
rejects characters outside a narrow allowlist before writing outputs, and the
checker validates the same identity before `gh` runs. A regression reads every
`run:` body in the release workflows and fails if it contains `${{`.

### Receipt detached from the verified bytes

R5.1 hashed the archive path, then reopened it for verification and extraction.
V27 copies the archive through a bounded stream into a private staging file,
hashes the copied bytes while copying, re-hashes the staged file before
verification, verifies and extracts that staged copy, and re-hashes it again
afterwards. A successful receipt therefore cannot describe different bytes
from those that were verified, even if the original path is replaced or
modified in place. Regressions cover:

- replacement during staging;
- replacement during ZIP verification, using two independently valid ZIPs;
- replacement during extraction;
- a staged ZIP corrupted after hashing;
- a staged file that is not a readable ZIP;
- an extraction interrupted by an error (an error receipt is written, no
  verified receipt);
- an existing receipt that must not be overwritten.

## Packaging and root policy

`package-zip` remains the one supported packaging entry point. It writes sorted
manifest members under the declared root, normalizes timestamps and modes,
creates the destination exclusively, verifies the staged ZIP, extracts and
rehashes it, then verifies and hashes the bytes actually written to the
destination descriptor. `verify-zip` and `verify-tree` accept any single safe
top-level folder by default (compatible mode) and refuse a renamed root with
`--require-declared-root` (strict mode). Both modes enforce the same traversal,
symlink, collision, file-count, size and manifest checks, and the result says
which policy ran.

ZIP bytes can still differ across Python or zlib versions. The release
workflows pin Python 3.12; byte-for-byte repeatability across other packaging
environments is not claimed.

## Pre-publication gate and provenance

`fork-release-source-preflight.yml` runs before the platform release jobs. It:

1. verifies the source tree with the strict root policy;
2. packages the canonical source ZIP;
3. verifies, extracts and rehashes the ZIP;
4. writes a receipt bound to `github.sha`;
5. attests the ZIP with GitHub's artifact attestation;
6. uploads the ZIP and receipt.

`release.yml` gates the external reusable release job on that preflight. After
the draft release is created, `source-verify` downloads the draft's source ZIP
by release ID (draft tags do not exist as Git refs until publication), checks
its SHA-256 and commit against the preflight receipt, and verifies the
attestation. A failure means the draft must not be published. The published
Release Source Artifact Check remains the backstop.

The checker can also bind a published asset directly:

    python3 scripts/verify_release_source.py \
      --repo OWNER/NAME --tag vX.Y.Z \
      --provenance release-source-preflight.json \
      --verify-attestation \
      --report /path/outside/the/checkout/receipt.json

`--provenance` requires the asset's SHA-256 to equal the preflight receipt's;
the tag's commit must equal the receipt's commit; the attestation must cover
the same digest and source commit. The attestation proves which repository and
workflow produced the ZIP and which commit it came from. It does not prove
code review, reproducibility, or that someone with release access could not
replace both the manifest and the source.

## Verification performed for this snapshot

On macOS with Python 3.12 and Rust 1.99.1 (the workspace minimum is 1.97.0):

| Check | Result |
|---|---|
| Python regressions | 181 passed |
| Source manifest | Strict root verified; exact count and bytes are in `SOURCE_INTEGRITY.json` |
| `cargo fmt --all -- --check` | Passed |
| `cargo xtask i18n-coverage` | 0 missing in every catalog |
| `cargo xtask tools --check` | 304 tools, up to date |
| `cargo xtask schemas --check` | Up to date |
| `cargo xtask layers` | Passed |
| `cargo xtask locks` | 380 files, passed |
| `cargo xtask secrets` | Passed |
| Engine tests | 603 passed |
| UI tests | 208 passed |
| Focused Deep profile | 4 settings, 3 lifecycle, 1 inventory and 8 target-reopen tests passed; UI typecheck passed |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passed |
| `cargo xtask ci` (the CI gate) | Passed locally: workspace clippy and tests, CLI helper build, wasm check |
| CLI build | Passed |
| CLI `--help` smoke test | Passed |
| Package, verify and extract round trip | Passed |

The machine-readable native receipt is `craft_native_validation_v27.json`; the
CI gate log is `v27_xtask_ci.log`; the packaging receipt is
`CraftAnything_V27_FULL_SOURCE_VERIFIED.receipt.json`. `find_cli` now checks
the Cargo profile root, so the CLI helper is found from both
`target/<profile>/deps` and `target/<profile>/build/<crate>/<hash>/out`; the
clean CI workspace test previously failed the two `crates/ai` rebuild tests in
the second layout. The final archive SHA-256 is recorded in the packaging
receipt, not in this file (the file is inside the archive).

## Readiness gates

- **Gate A — verified source release:** Python regressions, strict manifest,
  deterministic packaging, exclusive creation, staged receipt verification,
  pre-publication workflow definition and provenance binding are in place.
  The GitHub workflows have not run against a real release; the first live run
  is still owed.
- **Gate B — native application release:** the automated macOS gate passes
  (engine, UI, focused Deep profile, clippy, format, CLI build and smoke).
  Windows and Linux native runs, live Ghidra integration and desktop behaviour
  were not run.
- **Gate C — autonomous execution readiness:** not established. Craft
  Anything's approval and process supervision do not provide an OS-level
  filesystem and network boundary for coding agents. This remains a separate
  engineering program.

## Not run, or still blocked

- A real GitHub release, the reusable external release workflow, and a real
  artifact attestation (`gh attestation verify` is unit-tested with synthetic
  JSON only).
- Windows and Linux native builds; live Ghidra import/decompilation; desktop
  UI behaviour.
- Byte-for-byte ZIP reproduction across Python/zlib versions.
- OS-level sandboxing for autonomous builders (Gate C).

## How to reproduce

From the extracted `craft-anything-full-upgraded` directory:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
python3 scripts/source_bundle_integrity.py verify-tree --root . --require-declared-root
python3 scripts/source_bundle_integrity.py package-zip \
  --root . --archive ../CraftAnything_V27_FULL_SOURCE_VERIFIED.zip
python3 scripts/verify_release_source.py \
  --archive ../CraftAnything_V27_FULL_SOURCE_VERIFIED.zip \
  --report ../CraftAnything_V27_FULL_SOURCE_VERIFIED.receipt.json --root .
```

With a Rust toolchain of 1.97.0 or newer:

```bash
python3 scripts/native_validation.py --root . --timeout 1800 \
  --report /tmp/craft_native_validation_v27.json
```

Exit code `0` means the automated gate passed; `1` means a check failed; `2`
means work is blocked by an unavailable toolchain. A green automated gate is
not a production certification.
