# Craft Anything v0.4.0 — hardened full-source upgrade V4

This is an **independent, uncompiled V4 source package** derived from the
V3 full-source ZIP, which itself derives from the user's uploaded
`craft-anything-main 2.zip`. The Rust workspace retains its original
version 0.4.0. V4 is an audit/packaging version, not an official upstream
release and not a compiled application.

## What actually changed since V3

1. **Bounded gameplay-probe process output.** The optional Python
   `scripts/independent_game_probe.py` no longer uses
   `subprocess.run(capture_output=True)`, which buffered arbitrary
   child output until exit. It continuously drains stdout and stderr
   on separate threads with caps of 1,000,000 bytes and 256,000 bytes.
   It terminates the adapter if it exceeds either cap or the configured
   timeout. On Unix-like systems it attempts to kill the whole process
   group. The request is supplied via a private temporary file instead
   of a potentially blocking stdin pipe.
2. **Reproducible local evidence recheck.** The generated replay report
   now preserves the challenge nonce and observed response object.
   `scripts/verify_probe_report.py` revalidates the saved report
   against the original replay case and the original captured PNG
   files. It recomputes movement, action, HUD, pixel-difference metrics
   and exact screenshot hashes, then refuses mismatches. This is a
   **consistency check**, not an authenticity signature.
3. **Negative regression coverage.** Added synthetic fixtures and tests
   for oversized stdout, oversized stderr, a hung adapter, changed
   replay requirements and a changed screenshot, plus an end-to-end
   probe-to-verifier CLI run. Both use Python's standard library.
4. **Documentation.** Updated the replay protocol guide and the
   fork's status caveat. The new modules are additive; all V3 source
   files and previous hardening changes remain in the V4 ZIP.

## Verification in the packaging environment

- `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`:
  **11 tests passed**, including all new failure cases.
- ZIP integrity and retention of the V3 source tree must be
  checked on the finished archive; these are reported separately
  in delivery.
- `cargo`, `rustc`, `rustfmt`, and Docker are **not installed**
  here. The Rust source, including V3's new project restore
  functionality, could not be compiled or validated natively.
- No live target game, UI run, OS sandbox, or cross-platform game
  adapter was tested. Synthetic fixtures are not evidence of
  gameplay fidelity.

## Still necessary before production

1. Install a compatible Rust toolchain and run the checks in
   `UPGRADE_REPORT_V3.md` on Windows, macOS and Linux as applicable.
2. Test the new restore path against real project folders, including
   collisions, symlinks and simulated interruption.
3. Build an independent game adapter that injects inputs and captures
   frames **outside** the target executable, and connect the resulting
   checks to a bounded Rust readiness gate.
4. Provide controlled process-tree termination on Windows, safely
   isolate untrusted programs, cap captured screenshot disk usage
   and ensure temporary capture folders can be cleaned without
   deleting user-owned files.
5. Sign or separately anchor immutable report evidence if forensic
   provenance is required. The current nonce, hashes and recheck
   detect accidental or partial tampering, but an adversary
   controlling all the files can fabricate a self-consistent report.

This source retains the project's license files and previous fork reports.
