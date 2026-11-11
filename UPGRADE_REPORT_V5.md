# Craft Anything v0.4.0 — verified-source upgrade V5

V5 is a new full-source package derived from the prior V4 ZIP, itself an
independent fork of the user's `craft-anything-main 2.zip`.
The underlying Cargo workspace remains version 0.4.0.
No binary application is included and no official upstream release or
endorsement is implied.

## What V5 adds over V4

1. **File-by-file source integrity inventory.**
   `SOURCE_INTEGRITY.json` contains the size and SHA-256 digest of every
   non-transient regular source file except the manifest itself. It
   includes the Rust crates, UI, assets, tests, configuration, licenses,
   upgrade reports and new verifier. All V4 archive entries are retained.
2. **Independent source and ZIP verifier.**
   `scripts/source_bundle_integrity.py` can regenerate the inventory,
   validate an extracted source folder, and verify the complete ZIP
   *without extracting or executing its code*. The ZIP mode rejects
   missing, extra or modified files, duplicate archive members,
   unsafe paths, symbolic links, wrong package roots and excessive
   uncompressed data.
3. **Adversarial regression fixtures.**
   Added `scripts/tests/test_source_bundle_integrity.py` with
   synthetic valid and deliberately malformed trees/archives: modified
   contents, missing entries, unexpected additions, traversal names,
   duplicate entries, symbolic links, an external directory and
   inconsistent manifest metadata.
4. **Release-check guidance.** Added
   [docs/source-bundle-integrity.md](docs/source-bundle-integrity.md)
   with repeatable verification commands, the threat model and
   explicit limitations.

## Evidence and limitations

- The Python standard-library regression suite is expected to contain
  20 tests, combining the earlier V4 replay-probe coverage and V5
  source-integrity cases. The delivery notes must report the *executed*
  test count and results; this statement is not a substitute for a run.
- Verify the final archive's ZIP CRCs, compare its contents against V4
  for missing entries and run both the extracted-tree and ZIP integrity
  verifier before delivery.
- This **does not** prove that the archive came from a trusted author:
  an attacker could replace source files and regenerate the unsigned
  manifest. An external trusted checksum or signed release is required
  for publisher authentication.
- `cargo`, `rustc` and `rustfmt` were unavailable in the prior
  packaging environment. The new Python verification does not
  establish Rust compile success or native application behaviour.

## Next release-blocking work

1. Run `cargo fmt`, `cargo xtask tools --check`,
   `cargo test`, `cargo clippy` and native UI builds using an
   installed Rust toolchain. Investigate and fix real failures.
2. Exercise project-archive restore through the CLI and app on actual
   macOS and Windows project folders. Add desktop restoration UI
   if needed.
3. Integrate an independent, consented game adapter with true input
   injection and externally captured screenshots. The V4 synthetic
   replay probe is not itself a proof of gameplay.
4. Use reproducible builds and signed release artifacts once the
   native functionality is verified. The V5 checksum inventory is
   only the first link in that chain.

See [UPGRADE_REPORT_V4.md](UPGRADE_REPORT_V4.md) and
[UPGRADE_REPORT_V3.md](UPGRADE_REPORT_V3.md) for prior work.
