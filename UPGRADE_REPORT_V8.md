# Craft Anything v0.4.0 — native CLI smoke-test upgrade V8

V8 is a complete independent source ZIP derived from V7. Its original
Rust workspace remains at version 0.4.0; it is **not** an official ArtCraft
release, and no precompiled application is included.

## Changes over V7

1. **Mandatory executable smoke test after CLI compilation.** The native
   validation gate now runs the built `craft-anything-cli --help` with a
   bounded deadline and output, requires usage text, and treats any launch
   or output failure as an actual gate failure. Previously a successful
   `cargo build` was the last available CLI check.
2. **SHA-256 executable evidence.** The new `cli_smoke` receipt includes the
   executable's byte size and SHA-256 digest. It hashes the file both before
   and after the smoke test and fails if they differ. Binary paths are
   redacted from the recorded command to avoid revealing local usernames.
3. **Cargo configuration support.** The smoke check resolves standard
   `CARGO_TARGET_DIR` and `CARGO_BUILD_TARGET` variables, with strict
   validation of the target triple. It refuses missing, empty, oversized
   or symbolic-link executables.
4. **Fail-closed dependencies.** A missing Rust toolchain blocks both CLI
   compilation and its smoke test. A failed compilation blocks only the
   dependent smoke test while keeping the gate status `fail`; a successful
   compile without successful smoke can no longer make the gate green.
5. **Repeatable inventory checks after compilation.** The V5–V7 inventory
   verifier now excludes Cargo's root `target/` directory, but still rejects
   unlisted source files elsewhere. This fixes false tampering alerts after
   a normal local build.
6. **Regression and documentation updates.** Added Python tests for actual
   help execution via a controlled executable stand-in, target resolution,
   missing and changed binaries, required help text, and CI gating behavior.

## Evidence and limits

- **51 Python regression tests passed** in the packaging environment,
  including the new V8 executable smoke-test stand-in.
- The final archive is checked for V7 file retention, ZIP CRC consistency,
  and its full SHA-256 source inventory.
- Rust `cargo` and `rustc` are **not installed** in this environment. The
  real Craft Anything CLI has **not been compiled or launched here**.
  The release runner's actual `cli_smoke` remains **blocked**, not passed.
- A CLI `--help` smoke run does not prove the desktop app, MCP server,
  external tools, archive restoration, games, permissions, signing or
  software supply chain is safe or working. Test those separately.

## How to complete the native proof

With a Rust 1.97+ toolchain and platform build requirements installed,
run `python3 scripts/native_validation.py --root . --report
../craft_native_validation.json` inside the extracted source directory.
The manual Linux GitHub workflow from V6 runs the same gate, now including
the V8 `cli_smoke` check. A passing CLI smoke receipt contains the binary
hash that was actually observed, not a build claim inferred from source.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V7.md](UPGRADE_REPORT_V7.md) for prior hardening.
