# Craft Anything v0.4.0 — portable verified-extraction upgrade V7

V7 is a complete independent full-source fork based on V6. The original
Cargo workspace remains at version 0.4.0. This is **not** an official
ArtCraft release, and it does not include compiled executables.

## What V7 changes

1. **Verified source installation.** The standard-library-only
   `scripts/source_bundle_integrity.py extract-zip` command now validates
   the ZIP inventory, unpacks into a private staging folder, independently
   rehashes the extracted source and renames the verified result into place.
   It refuses an already-existing destination root and cleans temporary
   files after failures. Nothing from the archive is executed.
2. **Portable archive paths.** ZIP verification, tree inventory generation
   and manifest validation now reject Windows-reserved device names,
   illegal Windows characters, trailing periods/spaces, control characters,
   non-NFC Unicode, oversized path components and case-insensitive filename
   or parent-directory spelling collisions.
3. **Archive structural conflict detection.** ZIP validation now refuses a
   regular file that is simultaneously used as a parent directory. This
   prevents a malformed archive from being declared installable.
4. **One-descriptor verification and extraction.** The extractor verifies
   and reads from the same open archive descriptor so replacing the archive
   *path* between the two operations does not silently switch its content.
5. **Cross-platform source test workflow.** Added a manually dispatched
   GitHub Actions workflow to run the independent source validator and its
   regression tests on Linux, macOS and Windows. It supplements the V6
   Linux native gate, which is unchanged.
6. **New regression coverage.** Synthetic tests exercise successful
   extraction, existing-file protection, tampered archives, rollback of
   incomplete staging, symlink destinations, forbidden names, directory
   aliases and file/directory conflicts.

## Evidence from this packaging environment

- Python test suite: **41 tests passed** after the V7 changes.
- The V6 ZIP remains verifiable under the tighter V7 rules.
- The final V7 ZIP is separately checked for CRC errors, file preservation
  and full SHA-256 source-manifest consistency as part of delivery.
- `cargo` and `rustc` are **not installed** here. Rust compilation,
  platform-specific application launch and real gameplay are **not verified**.
  Do not interpret a passing Python suite as a passing native application.

## Security limits

- The file inventory is **unsigned**. An attacker who controls both files
  and the manifest can still generate a consistent malicious archive.
  Use a verifier obtained independently from a trusted source, and compare
  the ZIP checksum to an independently authenticated value.
- The extraction destination should be a private directory. This code does
  not promise protection from an adversary concurrently racing directory
  names in a writable output parent.
- The Python validation does not detect malicious Rust logic, vulnerable
  dependencies or defects in the native app. Those require independent
  analysis and live platform tests.

See [docs/source-bundle-integrity.md](docs/source-bundle-integrity.md) for
commands and [UPGRADE_REPORT_V6.md](UPGRADE_REPORT_V6.md) for native-gate work.
