# Craft Anything v0.4.0 — native-validation upgrade V6

V6 is a new full-source archive derived from V5. It retains the entire
V5 tree and original Cargo workspace. This is an **independent fork**, not
an official ArtCraft release. No precompiled app is included.

## Changes from V5

1. **Portable release gate.** Added `scripts/native_validation.py` to
   verify the V5/V6 SHA-256 source inventory, run the Python regression
   suite and invoke 10 specific Cargo checks, with explicit time budgets,
   bounded log capture and a machine-readable JSON receipt.
2. **Honest blocked state.** Cargo checks cannot be marked passing when
   Rust is absent or older than the workspace's required 1.97 version.
   Receipts distinguish `pass`, `fail` and `blocked`; a real failure
   takes priority over a blocked prerequisite.
3. **Source integrity fail-closed.** A mismatched manifest stops the
   native gate before executing code from the extracted source tree.
   `.git` checkout metadata is excluded from source hashing so a checked-out
   copy can be tested in CI; additional unlisted source files still fail.
4. **Independent manual CI path.** Added
   `.github/workflows/fork-native-validation.yml`, a manually dispatched
   Linux workflow that installs Rust 1.97, runs the entire local gate and
   retains its report even on failure. The existing upstream workflow
   is not replaced or modified.
5. **Adversarial tests and instructions.** New
   `scripts/tests/test_native_validation.py` tests success, nonzero exits,
   missing binaries, timeouts, excessive output, old and missing Rust,
   source-tampering precedence and report location. Added
   `docs/native-validation.md` with macOS, Windows and Linux commands.

## Packaging-environment evidence

- The Python standard-library suite ran on this V6 source tree:
  **32 tests passed**, including the original V4 probe, V5 integrity and
  12 V6/compatibility regressions.
- The delivery process regenerates `SOURCE_INTEGRITY.json`, checks the
  extracted inventory, tests the final ZIP CRCs and independently checks
  its manifest. These checks verify distribution completeness, not code
  correctness or trusted publisher identity.
- A full Rust/native run is **blocked in this packaging environment**:
  neither `cargo` nor `rustc` is installed. The standalone gate records
  this as `blocked` with no native successes asserted.
- The native GUI, real gameplay input and platform permission behavior
  remain **unverified**, not implicitly passing.

## Work not completed by V6

1. Run the gate on a real Rust-equipped Linux CI runner and resolve any
   compiler, test, formatting, lint, schema or locking failures.
2. Independently run it on macOS and Windows, then exercise application
   launch, archive restoration, GUI capture, permissions and shutdown.
3. Validate actual gameplay interactions with authorized input and fresh,
   externally captured screenshots; synthetic probe tests alone are not
   evidence of gameplay fidelity.
4. After platform validation, create reproducible and signed release
   artifacts. The source manifest is unsigned and cannot prove origin.

See [UPGRADE_REPORT_V5.md](UPGRADE_REPORT_V5.md) for the earlier source
inventory and [docs/native-validation.md](docs/native-validation.md) for
command-by-command usage.
