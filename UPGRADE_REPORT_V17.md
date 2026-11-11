# Craft Anything v0.4.0 — Fail-closed CLI target cleanup V17

V17 is a complete, independent source snapshot upgraded from the verified V16
ZIP. It retains the upstream Rust workspace's `0.4.0` version; it is **not an
official build, verified native application, or signed release**.

## Real Rust behavior changed

V16's stateful MCP smoke showed that an explicit `core_target_close` call
could close a synthetic target and preserve evidence. That did **not** protect
the CLI's separate `with_target` helper used by `triage`, `report <path>`, and
path-taking tool subcommands. The helper made a best-effort close and threw
away its result:

```rust
let _ = engine.call("core_target_close", json!({}), call_options(cli, cancel));
```

V17 replaces the silent close with a checked, target-specific cleanup:

1. Open the supplied path with `core_target_open` and read its reported
   `target_id`. Refuse a malformed response and attempt to clean up.
2. Execute the requested inspection or tool operation.
3. Close **the exact target ID from step 1**, not the currently active target.
4. Attempt cleanup with an independent cancellation token even when the
   original operation was interrupted.
5. Return the original result if both operation and cleanup succeed.
6. Return the cleanup failure if inspection succeeded but closing failed.
7. If both fail, keep the original operation's error code/message and attach
   the cleanup error under `details.target_close_error`, preserving previous
   details as `details.operation_details`.

The target-close error is no longer invisible to callers. This is a lifecycle
correctness fix, **not** a claim that all inspections or arbitrary targets
are safe or supported. A process that is externally terminated before its
cleanup code runs cannot be guaranteed to release an in-memory target.

## Added regression coverage

Six Rust unit tests in `apps/craft-anything-cli/src/main.rs` cover:

- successful operation closes its opened target;
- failing operation also closes its target;
- a second close failure changes an otherwise successful CLI result to error;
- a newly opened second target survives closure of the first;
- cancellation of the inspection token does not stop cleanup; and
- simultaneous operation and cleanup errors preserve both error records.

The manual three-platform `.github/workflows/fork-native-cli-matrix.yml` now
runs the focused tests with `cargo test --locked -p craft-anything-cli --bin
craft-anything-cli target_lifecycle_tests` before the existing native CLI
matrix. The workflow will not export an unsigned executable if these tests
fail. Existing V1–V16 checks and documentation remain in the full archive.

## Verification limits

- The source and ZIP inventories are checked and all Python regression tests
  run in this packaging environment, including an independent extracted copy.
- **Rust and Cargo are not installed in this packaging environment.** New
  Rust tests have been authored, but not compiled or executed here.
- Native CI runs on Ubuntu, macOS, and Windows only after someone copies the
  source to an authorized repository and manually dispatches the workflow.
- No native application, game reconstruction, live target analysis, desktop
  GUI, device access, signing, notarization, or publication is verified here.

See [docs/native-validation.md](docs/native-validation.md) for how to run the
native tests and [UPGRADE_REPORT_V16.md](UPGRADE_REPORT_V16.md) for the prior
stateful MCP coverage.
