# Craft Anything v0.4.0 — Target Reopen State Safety V20

This is a **full independent source snapshot** upgraded from the verified
V19 archive, not an upstream release. The Cargo workspace remains at
`0.4.0`; no compiled, signed or production-validated executable is included.
All original source files and the V1–V19 hardening changes are retained.

## Concrete bugs addressed

### 1. Unsaved notes lost on changing raw interpretation

`core_target_open` correctly reopens identical bytes when their declared
raw architecture or load/entry address changes. The fresh `OpenTarget` has
fresh analysis caches and a default cursor, which is appropriate: an
instruction found at an old base address may be meaningless at the new one.
However, `Session::insert_target` previously replaced **the entire old
target**, silently discarding unsaved names, comments, inline comments and
bookmarks. If a project was attached, a later target close could overwrite
its saved notes with a stale on-disk version, losing newer edits.

V20 carries forward existing in-memory annotations **only when the same
full content hash is reopened**. It intentionally does not reuse the old
cursor or analysis cache. The V19 behavior—refusing unreadable/corrupt
saved annotation files—still applies before the replacement.

### 2. Different inputs sharing a short target ID

The target ID uses only 12 hex digits (48 bits) from the SHA-256 value.
Two inputs with different complete hashes can therefore share an ID. The
previous map insertion would replace the first target and its session
state without warning. This is unlikely in a small honest workload but is
not impossible, and short prefixes should not be treated as full hashes.

V20 checks the **complete hash** against an existing target before adding
another with the same ID. The operation returns an actionable error and
leaves the existing target active and unchanged. `restore_targets` also
preflights collisions within an incoming handoff batch and against existing
open targets, before inserting any part of the batch. These checks do not
change the legacy target-ID format; a longer-ID migration is separate work.

## Code and gate changes

| File | Change |
|---|---|
| `crates/engine/src/session.rs` | Keep unsaved annotations when reinterpreting identical bytes; reject full-hash mismatch on a reused short ID; preflight all handoff IDs atomically. |
| `crates/engine/src/tools/session_tools.rs` | Extend the raw-architecture reopen regression to cover all annotation types and cursor reset; add a project-backed save-on-close regression. |
| `.github/workflows/fork-native-cli-matrix.yml` | Require the new `target_reopen_` engine Rust tests before any unsigned native CLI build or artifact export. |
| `docs/STATUS.md`, `docs/native-validation.md`, `README.md` | Explain V20 behavior, validation steps and limitations. |
| `docs/ROADMAP.md` | Record the unfinished longer-ID migration as a separate task. |

Four Rust tests (two tool-path, two session-path) are named with the common
`target_reopen_` prefix. They cover both ordinary reopening and intentionally
forged prefix collisions in a controlled test fixture, without brute force
or loading any real person's files.

## How to validate on a Rust-enabled machine

From the extracted archive root:

```bash
python3 scripts/source_bundle_integrity.py verify-tree --root .
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine target_reopen_
cargo test --locked -p craft-anything-engine annotation_load_
cargo test --locked -p craft-anything-engine target_close_
cargo test --locked -p craft-anything-cli --bin craft-anything-cli target_lifecycle_tests
python3 scripts/native_cli_matrix.py --root . --timeout 2400 --report ../native_cli_smoke.json --artifact-dir ../native_cli_unsigned
```

The separately dispatched GitHub Actions workflow runs the new and older
Rust gates on Ubuntu, macOS and Windows. A native failure blocks export of
the unsigned smoke-tested CLI; that workflow does **not** sign, publish or
validate the desktop UI.

## Evidence and limits

- The full source tree and ZIP integrity are checked while packaging.
- Python source-bundle/harness tests are run here and rerun from an
  independently extracted ZIP.
- **Cargo/rustc is not present in the packaging environment.** Rust tests,
  compilation, native CLI operation and UI behavior cannot be claimed as
  passing until run on an actual Rust toolchain.
- No universal proof of cache correctness, concurrent editor conflict
  resolution, adversarial hash-collision resistance or backwards-compatible
  target-ID migration is claimed.

See [UPGRADE_REPORT_V19.md](UPGRADE_REPORT_V19.md) for the immediately prior
annotation-load hardening and [docs/native-validation.md](docs/native-validation.md)
for the complete cross-platform test procedure.
