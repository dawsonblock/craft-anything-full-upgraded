# Craft Anything v0.4.0 — Target-specific annotation persistence V18

V18 is an independent, **full-source ZIP** built on the verified V17 source.
It preserves the complete original workspace and all V17 files. The upstream
Cargo workspace version remains `0.4.0`; the archive is not a signed or
official release.

## The bug

The CLI's V17 `with_target` wrapper now correctly closes the target ID that
it opened, even if a nested operation selects a different active target.
However, the underlying engine's `core_target_close` still performed:

```rust
ctx.session.save_annotations()?;
let closed = ctx.session.close_target(&id)
    .ok_or_else(|| ToolError::new(ErrorCode::NotFound, format!("No open target {id}")))?;
```

`save_annotations()` intentionally saves **the active target** for ordinary
annotation tools. But `core_target_close` accepts any open target ID. If
target B is active and an explicit close requests target A, this sequence
persisted B's annotations before removing A. If A had only in-memory edits,
those edits were not saved as intended. The bug was in the engine, so
fixing the CLI target ID alone was insufficient.

## V18 behavior change

- `Session::save_target_annotations(&id)` validates and selects a particular
  open target, then writes **that target's** names, comments, inline comments
  and bookmarks to the project using its own file hash.
- `core_target_close` saves the requested target's annotations before
  removing it from the session. If persistence fails, it does not close the
  target; if the ID is missing, it does not save an unrelated target.
- `Session::save_annotations()` keeps its active-target semantics for existing
  naming/comment/bookmark tools, and delegates the actual save to the same
  target-specific helper.
- The V17 CLI target-ID cleanup and previous hardening work remain intact.

## New Rust tests

Three tests in `crates/engine/src/tools/session_tools.rs` exercise the public
engine API with disposable projects and separate target files:

1. Open A, then B; add different in-memory notes; close A by ID while B is
   active; verify only A's notes were persisted, B remains active, then close
   B and check both notes survive project reload.
2. Open a target with unsaved annotations; request close of an unknown ID;
   verify a typed `not_found`, unchanged active target, and no unrelated note
   file; subsequently close the real target and verify its notes are saved.
3. Block the annotation file's atomic replacement; verify close fails and
   retains the target and its notes, then remove the blocker and retry the
   close successfully.

The manual CI workflow now runs:

```bash
cargo test --locked -p craft-anything-engine target_close_
cargo test --locked -p craft-anything-cli --bin craft-anything-cli target_lifecycle_tests
```

Both test commands must succeed before the later native CLI checks or any
unsigned executable export can proceed.

## Evidence and limitations

- Source-tree and ZIP checks and the Python regression suite are run when
  packaging V18, including a second run from a freshly extracted archive.
- Rust `cargo` and `rustc` are absent here, so **none of the new native
  tests have been compiled or executed** in this environment.
- The manual multi-OS CI workflow must be run from an authorized repository
  to establish native results. No desktop app, arbitrary binary inspection,
  device operation, game recreation, signing or release is claimed.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V17.md](UPGRADE_REPORT_V17.md).
