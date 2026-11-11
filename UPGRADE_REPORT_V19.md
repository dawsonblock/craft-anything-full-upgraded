# Craft Anything v0.4.0 — Fail-closed annotation loading V19

V19 is the complete independent source snapshot upgraded from the verified
V18 ZIP. It retains the full source tree and all previous V1–V18 changes.
The Cargo workspace version is still `0.4.0`. This is **not** an official,
signed or production-validated native release.

## Failure mode corrected

Before V19, session target insertion loaded notes using:

```rust
let annotations = self.project.as_ref()
    .and_then(|p| p.load_annotations(&target.sha256).ok())
    .unwrap_or_default();
```

Converting every `load_annotations` error into `None` had a serious side
effect: corrupt JSON, inaccessible project data or a missing annotations
folder was silently interpreted as "this target has no notes." A later
annotation edit or target close could write empty/default annotations and
replace an existing damaged file, eliminating a chance to recover its data.

## V19 changes

1. `Project::load_annotations_or_default` returns empty annotations only
   when the individual notes file is absent from an existing annotation
   directory. Invalid JSON, broken links and unexpected I/O errors are
   returned with repair guidance; the source file is not modified.
2. `Session::add_target` returns `Result<TargetId, ToolError>` and loads
   annotations **before** inserting or activating the target. The CLI and
   other internal callers are updated for the fallible API.
3. `Session::restore_targets` opens and prepares all requested targets first,
   then commits them together. A damaged notes file in target B does not
   leave target A accidentally restored.
4. Previous V17/V18 changes remain: target-ID-based closing, checked cleanup,
   accurate target-specific annotation saves and save failures that retain
   an open target.

There is no claim of synchronizing external concurrent editors or preventing
all filesystem races; these changes address the deterministic failure path.

## New regression coverage

Five Rust tests were added to the engine:

- Missing note file defaults to empty; malformed existing JSON fails without
  overwriting the file, and a repaired annotation file loads correctly.
- Missing annotations directory fails rather than being silently recreated.
- Broken annotation symlink is treated as an error on Unix.
- The public `core_target_open` API refuses corrupt notes, keeps the session
  empty and accepts a repaired file with the saved comments intact.
- Restoring two targets is all-or-nothing when one notes file is malformed;
  once repaired, both restore in order.

The manual Ubuntu/macOS/Windows workflow now runs `cargo test --locked -p
craft-anything-engine annotation_load_` before the V18 target-close tests,
V17 CLI tests and existing native CLI smoke gates. A Rust test failure
blocks unsigned binary export.

## Validation and limits

- Source tree and ZIP integrity are verified when creating this archive.
- The existing Python regression suite is run and repeated after independent
  extraction of the final ZIP.
- **Cargo and rustc are not available in this packaging environment.** The
  five Rust regressions and native app paths have not been compiled or run
  here. The native CI matrix has not been dispatched in this task.
- This is source code only; there is no compiled desktop app, Windows or
  macOS binary, signed release or proven production behavior in this ZIP.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V18.md](UPGRADE_REPORT_V18.md).
