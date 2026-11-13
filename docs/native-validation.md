# Native validation of the independent source fork (V6–V25)

This procedure checks the **source and optional native toolchain** without
claiming to have run a desktop application. It is independent of the upstream
shared CI workflow and uses only Python's standard library for orchestration.

The extracted folder should be named `craft-anything-full-upgraded`. The
native gate enforces that name; a renamed GitHub branch folder is for
inspection, not release qualification. Keep the generated JSON receipt
**outside** that folder: the source integrity manifest would otherwise become
stale.

## V25: Focused Deep test profile with protected receipts

Instead of waiting for the entire release pipeline, V25 lets a developer
verify the V22–V24 Ghidra settings and cache-lifecycle work with a focused,
fail-closed profile. It checks source integrity before any bundled code runs,
executes the Python suite and, when Rust 1.97+ is present, gates these native
commands:

```bash
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine deep_settings_
cargo test --locked -p craft-anything-engine deep_lifecycle_
cargo test --locked -p craft-anything-engine target_reopen_
cargo check --locked -p craft-anything-ui-egui
```

On Windows, run `& .\scripts\verify-deep.ps1` in PowerShell. On macOS/Linux,
run `bash scripts/verify-deep.sh`. The launchers create an external JSON
receipt automatically. To choose an output location yourself, use:

```bash
python3 scripts/native_validation.py --root . --profile deep --report ../deep-v25.json
```

Never reuse an existing receipt path: V25 refuses to overwrite it. Exit code
`2` means blocked (for example, no Cargo), not passed. A Rust test step that
reports zero passing tests fails the gate with `no_tests_selected`, so a
filter that no longer matches an authored test cannot look green. A passing
automated profile does **not** claim live Ghidra execution or visual UI
validation; both are explicitly `not_run` in the receipt. The full existing
release gate remains the default when `--profile` is omitted.

## V24: Bounded Ghidra module inventory and manual fresh analysis

V24 extends the Deep provider and saved analysis keys to track commonly changed
Ghidra processor, feature and framework modules, not just its launcher and
version file. The cache format advances to v5; older projects remain on disk
but are not reused by the new key. Small configuration/specification files are
hashed by content, and so are large JAR, SLA and native modules: a same-size
edit with a restored timestamp is still seen. The scan is bounded at 16,384
entries, 2,048 modules and 512 MiB of module content, so a module past the
budget keeps its metadata and can still evade detection; **Force fresh
analysis** covers that case.

The desktop's Engines tab now exposes global Ghidra processor language and
compiler specification, plus a **Force fresh analysis** button with confirmation. The latter
increments a backwards-compatible `analysis_generation` counter and chooses
a new saved analysis key without deleting previous projects. It is potentially
slow on first reanalysis. A per-target language pin still has priority.

New synthetic Rust tests check same-size processor specification mutations,
new binary module detection, a same-size module edit with a restored
timestamp (`inventory_tests`) and old settings compatibility. The Deep
profile and the manual CI matrix select them under `deep_settings_`,
`deep_lifecycle_` and `inventory_tests`:

```bash
cargo test --locked -p craft-anything-engine deep_settings_
cargo test --locked -p craft-anything-engine deep_lifecycle_
cargo test --locked -p craft-anything-engine inventory_tests
cargo check --locked -p craft-anything-ui-egui
```

**Still required:** run these tests with Cargo; profile the inventory on real
Ghidra installations; inspect the visible settings controls and persistence;
then run actual import/decompile and manual refresh on each supported OS.
Nothing in the Python source suite proves those native checks succeeded.

## V23: Clean-room provider teardown and in-place Ghidra version changes

V23 updates two separate cache lifecycles. When a desktop session changes
from effective Deep mode to Clean room, it releases all target Deep providers
(including cached failed startups) and resets their stale cursors. Original
target identities, unsaved annotations, and unrelated native caches remain.
The Deep hook also clears an active provider if called while already in Clean
room. The desktop uses `try_session`, so a currently running tool may delay
settings application and teardown until its session guard is available.

The analysis cache key advances to v4. It fingerprints the resolved
`analyzeHeadless` path, small launcher contents, filesystem modification
metadata, and bounded contents of `Ghidra/application.properties` when
available. V22's `DeepKey` also includes that revision, allowing a provider
to restart after a typical Ghidra upgrade in the same directory. The key
does **not** hash every Ghidra module: a module modified without affecting
those identifiers can still escape detection. Old v3 projects are left
intact but are not reused as v4 projects.

Two additional Rust tests cover multi-target cleanup and in-place version
changes in synthetic installations. Manual CI also checks the desktop crate:

```bash
cargo test --locked -p craft-anything-engine deep_lifecycle_
cargo check --locked -p craft-anything-ui-egui
```

These are **authored checks, not successful native runs** until executed.
Live testing must include a Ghidra restart after a settings change, a
Deep-to-Clean-room transition while a target is open, and a genuine Ghidra
upgrade or reinstallation. A real Ghidra module fingerprint and complete
protection against in-place edits remain out of scope.

## V22: Global Ghidra settings and stale Deep-provider invalidation

V22 adds a live-provider configuration fingerprint for each open target.
Changing a global Ghidra language, compiler specification, installation,
Java runtime, timeout, memory allocation or managed engine store discards
the old provider (including a cached startup failure) at the next Deep
request; an unchanged configuration keeps it. A replacement provider may
have a different address map, so the stale cursor is reset.

The Ghidra on-disk project analysis key is bumped to v3 and includes the
effective language, compiler specification and analyzer launcher path. Older
v2 project caches are not deleted, but are not reused by v3. This protects
against inadvertently reusing a project imported with a different compiler.

Three new Rust regression tests run in the manual matrix:

```bash
cargo test --locked -p craft-anything-engine deep_settings_
```

The tests exercise the settings key, project-analysis key and a deterministic
fake Deep provider that must be evicted after a global setting changes. They
still need **actual native Rust execution**, and do **not** prove that real
Ghidra installations, binary analysis, and UI settings changes work on every
platform. An external Ghidra upgrade *in place* without any config edit is
not automatically detected by the live provider key.

## V21: Moved-file identity and Deep language handoff

V21 refreshes the actual target path when identical content is reopened from
another location, without erasing unsaved notes or reusable caches. Changing
an explicit `language_id` evicts the previous Ghidra provider; repeating an
unchanged choice retains it, a blank choice clears it, and omission preserves
the prior pin. The pin now crosses a local MCP handoff, while legacy handoffs
without the field are still accepted.

The manual three-platform workflow requires both Rust regression groups:

```bash
cargo test --locked -p craft-anything-engine target_reopen_
cargo test --locked -p craft-anything-ai mcp_target_handoff_
```

The engine group now covers eight V20–V21 tests; the AI group has two
target-handoff tests. **They have not been executed here.** A real Ghidra
switch and GUI checks on each platform are still required.


## V20: Keep unsaved notes when raw settings change; reject target ID collisions

Reopening the same content in `core_target_open` can intentionally change its
declared raw architecture and entry address. It replaces an existing
`OpenTarget` and resets interpretation-specific analysis caches and cursor.
Previously the replacement also destroyed *unsaved* names, comments, inline
comments and bookmarks. V20 copies only those annotations into the fresh
`OpenTarget`, so re-analysis can safely use new settings without destroying
the person's unpersisted notes. Saved project annotation loading remains
fail-closed as in V19.

Target IDs currently use just the first 12 SHA-256 hex digits. A different
full content hash that shares that prefix is now refused before it can
replace an open target. For MCP batch handoffs, the engine prevalidates both
existing targets and collisions among incoming targets before inserting any.
This guard does not lengthen IDs or solve all target identity design issues.

The manual native CI workflow adds:

```bash
cargo test --locked -p craft-anything-engine target_reopen_
```

Four Rust regressions cover unsaved annotation preservation and refreshed
raw-binary analysis, project-note persistence on close, one-target collision
refusal, and atomic collision refusal for a restoration batch. These tests
are **authored but not executed** until the Rust workflow runs. Source
integrity and Python harness checks do not prove the Rust tests pass.

## V19: Fail closed on damaged saved annotations

The previous `Session::add_target` silently discarded *all* errors while
loading an existing annotation file. Invalid JSON or a read/permission error
could be interpreted as an empty note set, risking overwrite on a subsequent
save. `Project::load_annotations_or_default` now returns empty annotations
only if the individual file is genuinely absent in an existing notes
directory. Invalid JSON, a broken symlink, missing notes storage and other
I/O problems stop target opening with remediation, preserving the original
file for recovery.

`Session::add_target` now returns a `Result`, with its callers updated.
`Session::restore_targets` validates **every** target and its annotations
before inserting any of them, so one corrupt notes file cannot leave a
partially restored session. The manual native CI workflow now runs:

```bash
cargo test --locked -p craft-anything-engine annotation_load_
```

Five new Rust tests cover corrupt notes, valid-repair round trips, missing
notes storage, broken symlinks (Unix), refused target opening, and atomic
multi-target restoration. Failed tests block unsigned executable export.
These are **unexecuted Rust tests** until the native workflow runs.

## V18: Persist annotations for the target actually being closed

V17 made the CLI close a target by its returned ID. But the engine's
`core_target_close(target_id=...)` still called the active-target-only
annotation save, then removed the named target. When the two IDs differ,
the wrong target's annotations were persisted and the requested target's
last in-memory edits could be lost on close. V18 adds
`Session::save_target_annotations(&target_id)` and calls it before removal.
Unknown IDs now fail before any unrelated annotation write. The previous
`save_annotations()` entry point remains for tools editing the active target.

The manual native workflow runs three project-backed engine regressions before
exporting unsigned binaries:

```bash
cargo test --locked -p craft-anything-engine target_close_
```

They verify that closing an inactive target saves its own notes, preserves
the second active target's unsaved notes, and permits later reload; an
unknown target ID must leave the active target and its disk state unchanged;
and a note-write error must leave the target open for a later retry.
These tests are **authored but unexecuted** in the source packaging environment.

## V17: Verify the CLI's own target cleanup

The CLI wrapper used by `triage`, `report` with a path, and tool subcommands
previously discarded `core_target_close` errors. V17 now uses the ID reported
by `core_target_open` to close **that** target rather than whichever target
happens to be active at cleanup time. A successful inspection with failed
cleanup is an error. When both fail, the original operation error is returned
with a machine-readable `details.target_close_error` and the original details
kept under `details.operation_details`. The cleanup call receives a fresh
cancellation token so cancellation of the inspection does not skip cleanup.

The manual Ubuntu/macOS/Windows matrix runs these Rust unit regressions
**before** the native build and any unsigned export:

```bash
cargo test --locked -p craft-anything-cli --bin craft-anything-cli target_lifecycle_tests
```

The tests cover success, operation failure, failed close after apparent
success, nested target activity, cancellation, and simultaneous operation /
cleanup failure. A failing Rust regression stops the workflow; no unsigned
executable is uploaded as a passing result. These changes are source-level
until the Rust test command has actually run on a native toolchain.

## V9: Native CLI matrix on Ubuntu, macOS and Windows

The new manual `.github/workflows/fork-native-cli-matrix.yml` is a separate
cross-platform check. It installs Rust 1.97, attempts `cargo build --locked -p
craft-anything-cli`, and runs the **compiled** CLI with `--help` (V9),
`commands` (V10), `capabilities` (V11), an MCP protocol
handshake and tool listing (V12), and a single read-only MCP session query (V13).
V14 also checks the queried tool's MCP input schema and effect annotations,
then requires a typed refusal for an intentionally nonexistent MCP tool.
V15 additionally launches the compiled CLI against a disposable eight-byte
WebAssembly module to exercise actual target opening and triage. The output
must be a typed evidence record with exact fixture SHA-256, WASM kind/size,
Deep mode, and a WASM-specific recommendation. The CLI's `with_target` code
attempts closing the target after triage, but this smoke does not independently
check closure or other formats.
V16 adds a stronger **stateful MCP target lifecycle** smoke. Unlike separately
launched CLI commands, all its requests share one native MCP process. A
strictly ordered sequence checks the initial empty session, opens the same
disposable WASM fixture, runs `formats_wasm_module_show` with summary detail,
verifies the parsed module is valid and empty (zero sections/imports/exports),
confirms the target is active and evidence has been recorded, closes that
specific target ID, and re-queries the session to confirm it is empty while
the evidence ledger remains intact. Each state-changing request waits for
its response before the next is sent, preventing native MCP worker races.
It has a bounded runtime/output and temporary credential-free configuration.
The new `mcp_target_smoke` check is required for unsigned binary export;
failure or missing evidence blocks the artifact. This still does not validate
the desktop app, arbitrary binaries, connected devices, or user permissions.
It does not
need signing keys, publish a release, or trigger automatically on commits.

After transferring the complete source into an authorized GitHub repository,
open **Actions → Independent Fork Native CLI Matrix → Run workflow**.
Each platform job uploads `native-cli-receipt-<OS>` if the validator ran, even
if that validation fails, and on
success an `unsigned-native-cli-<OS>` artifact containing the executable and
`CLI_PROVENANCE.json`. CI artifacts are unsigned, temporary and provided for
inspection only. In V11, a green job requires source consistency, actual CLI
compilation, a `--help` launch and an isolated `commands` listing that parses
into at least **200 distinct tools** across at least **three toolsets**. The
registry fields and unique command names are checked before the binary is
exported. It still says nothing about the GUI or live tool functionality.

V10 executes `commands` with a disposable home/configuration directory and a
small allowlist of system environment variables. Personal configuration and
credentials are not forwarded to the subprocess. It records registry counts
and a canonical SHA-256 digest rather than dumping the full JSON registry
into CI receipts. The registry launch has its own 60-second ceiling, and
the executable must retain its original hash. A missing or invalid registry
blocks successful artifact export even if the CLI's `--help` works.

V11 runs `capabilities` immediately after `commands` with the **same private
environment and temporary configuration**, and it cross-checks the total
tool count, each tool ID, toolset association and `needs_target` flag. It
also checks typed availability metadata, engine IDs and CLI metadata. The
raw capabilities JSON may contain engine locations, so it is never stored
in the CI receipt; only a canonical SHA-256, CLI version and observed counts
are retained. Process output is capped at 2 MB and each metadata subprocess
has a 60-second ceiling. Either metadata view failing or contradicting
the other prevents unsigned binary export. The artifact provenance file
requires both metadata digests and the CLI version. This does not execute
registered tools, inspect a real project, or verify a desktop app.

V12 also launches `craft-anything-cli --deep mcp --all-tools` in an isolated
temporary home/config directory. `--deep` is used **only for exhaustive tool
enumeration**, not a target inspection or a tool call. The test sends a finite
newline-delimited JSON-RPC session: `initialize`,
`notifications/initialized`, `tools/list`, `ping` and one intentionally
unknown method, then closes stdin. It expects the server to negotiate the
pinned protocol version, announce the expected server identity/version,
return the full tool list, answer ping and reject the unknown method.
The names, titles and descriptions must agree with the CLI `commands`
contract digest and command count. The server's output is drained with
separate stdout/stderr readers; it has a 60-second deadline, a 24 MB stdout
cap and a 256 KB stderr cap to accommodate multi-megabyte JSON-RPC lines.
No raw tool schemas, stderr or local paths enter the saved receipt.
The tool listing does **not** prove that any individual tool works or that
`--deep` mode is safe to use on untrusted targets. No `tools/call` was sent
by the original V12 probe.
The test binary is exported only if this new `mcp_smoke` also passes and its
binary hash agrees with the previous checks.

V13 extends the same finite read-only protocol session with exactly one
`tools/call` for `core_session_show` and empty arguments. This tool is
declared `Effects::READ_ONLY` and does not require a target. The probe
verifies a successful `isError: false` result, a well-formed `structuredContent`,
the text content as matching JSON, an empty target/project/evidence state,
Deep-mode status, matching server/version, and a valid host identifier.
It rejects a changed or nonempty session and stores only a Boolean success
and the tool's public identifier in the receipt. Nothing else in the
registry is invoked. **This still does not exercise actual reverse
engineering, device access, consent, GUI or platform integration.**

V15 requires an additional `triage_smoke` step *after* metadata and MCP
validation and *before* unsigned binary export. It constructs the WASM sample
in a disposable temporary directory, provides a credential-filtered home and
configuration, launches `craft-anything-cli --deep triage <synthetic-wasm>`
with a 45-second ceiling and the standard 2 MB output limit, and deletes the
sample. `--deep` is necessary because target inspection is not a Clean-room
operation. No arbitrary or user-supplied target is used. A failed native
command, wrong evidence, mutated binary or malformed output fails the gate;
only a SHA-256 of the known synthetic fixture and typed summary fields enter
the report and executable provenance. This smoke does **not** test malware,
arbitrary binaries, runtime instrumentation, GUI behavior, authorization,
target cleanup or code signing. It has synthetic regression tests locally,
but becomes native evidence only after a real Cargo build and run on each OS.
All V14 and earlier checks remain required.

The same targeted gate can be run on a properly configured local machine:

```bash
python3 scripts/native_cli_matrix.py --root . --timeout 1800 \
  --report ../native_cli_receipt.json --artifact-dir ../native_cli_artifact
```

On PowerShell use `python` (or `py -3`) and put the arguments on one line.
Neither `../native_cli_receipt.json` nor `../native_cli_artifact` may already
exist, and both paths must be outside the source tree. The artifact directory
is only created after an actual passing smoke. Exit codes: 0 `pass`, 1 `fail`,
2 `blocked` (such as missing Rust). Compilation and launch time and captured
output are bounded; receipts redact executable command paths. The source
inventory is checked both before and after the native run.

The `macos-latest` and `windows-latest` runners rely on their hosted SDK and
C++ toolchains. A failing CI job is an engineering result to diagnose; do not
claim platform support until the actual job is green and further app tests
are done. The original Linux full quality gate described below remains.

## Run on Linux or macOS

From inside `craft-anything-full-upgraded`:

```bash
python3 scripts/native_validation.py --root . --report ../craft_native_validation.json
```

Prerequisites for native checks:

- Python 3.10 or newer.
- Cargo and Rust `rustc` 1.97 or newer, with `rustfmt` and `clippy` installed.
- Platform-specific libraries and SDKs required by the Cargo workspace.

If Rust is missing, the integrity and Python tests still run, but **every
Rust check is recorded as `blocked`**, and the overall exit code is 2.
On Linux you can trigger the manually dispatched GitHub Actions workflow
**Independent Fork Native Validation** (`.github/workflows/fork-native-validation.yml`)
after pushing the fork to your own repository. It installs a pinned Rust
toolchain, runs the full gate and saves the JSON receipt as a CI artifact.
This workflow validates Linux only.

## Run on Windows

From PowerShell, with the same working directory:

```powershell
py -3 scripts/native_validation.py --root . --report ../craft_native_validation.json
```

Windows process cancellation is best-effort: an indirectly spawned child
may survive a timeout. Run untrusted source in a disposable VM or a
restricted CI environment, not as an administrator. For a full Windows
gate, use an environment with Visual Studio's required C++ build tools,
the relevant SDK, Rust 1.97+, Cargo, rustfmt and clippy installed.

## Checks executed

| Name | Command | What it verifies |
|---|---|---|
| `source_integrity` | Built-in Python SHA-256 inventory | Extracted source matches the unsigned source manifest |
| `python_tests` | `python -m unittest discover -s scripts/tests -p 'test_*.py' -v` | Synthetic Python regression tests |
| `fmt` | `cargo fmt --all -- --check` | Rust formatting |
| `i18n` | `cargo xtask i18n-coverage` | Localization checks |
| `tools` | `cargo xtask tools --check` | Generated tool contract |
| `schemas` | `cargo xtask schemas --check` | Generated schema contract |
| `layers` | `cargo xtask layers` | Internal crate layering rules |
| `locks` | `cargo xtask locks` | Workspace lock/consistency rules |
| `engine_tests` | `cargo test -p craft-anything-engine` | Engine test target |
| `ui_tests` | `cargo test -p craft-anything-ui-egui` | Desktop UI test target |
| `clippy` | `cargo clippy --workspace --all-targets -- -D warnings` | Compiler lints for workspace targets |
| `cli_build` | `cargo build -p craft-anything-cli` | Native CLI compilation |
| `cli_smoke` | Built CLI executable `--help` | V8: actually launches the CLI, requires a usage message, and hashes the executable before and after |

The V8 `cli_smoke` check is **blocked** if the CLI compilation is blocked or
failed. If compilation succeeds but its executable is missing, empty, too
large, changes while being tested, does not launch or does not show CLI
usage, the gate **fails**. It records the binary size and SHA-256 only on
a successful read. The launch is limited to 20 seconds. That is a genuine
headless CLI smoke test, **not** a desktop GUI launch or gameplay test.

Cargo normally writes `target/debug/craft-anything-cli` (or the `.exe` on
Windows). V8 also recognizes `CARGO_TARGET_DIR` and `CARGO_BUILD_TARGET`.
If you set a custom target directory inside the source tree, place it under
the root-level `target/` folder or relocate it outside the source folder;
other generated directories invalidate the source inventory.

The gate stops before executing any project code if the source inventory
fails, but its hash list is **not signed**: it does not authenticate the
author or make an untrusted ZIP safe to execute. The gate limits each
subprocess to a configurable runtime (default 30 minutes, maximum one
hour) and 2 MB of captured stdout/stderr. A verbose command exceeding
that limit fails instead of silently truncating a passing report.

## Interpret the JSON receipt and exit code

- `pass` / exit 0: all automated checks completed with a passing result.
- `fail` / exit 1: at least one check actually failed, timed out or could
  not run as configured. Failures have priority over blocked checks.
- `blocked` / exit 2: prerequisites are missing, most commonly Rust.
  **Blocked is not a success.**

Each command contains its status, duration, return code, bounded output
excerpt and failure reason when applicable. The global `manual_validation`
field is always `not_run`: the gate does not test live gameplay, Windows or
macOS UI capture, permission dialogs or a real external game adapter.

To change the per-command timeout:

```bash
python3 scripts/native_validation.py --root . --timeout 900 --report ../craft_native_validation.json
```

## Packaging and authenticity

Source inventory and ZIP checking from V5 remain available:

```bash
python3 scripts/source_bundle_integrity.py verify-tree --root .
python3 scripts/source_bundle_integrity.py verify-zip --archive ../Craft_Anything_v0.4.0_HARDENED_V21_FULL_SOURCE.zip
```

The ZIP command checks the delivered archive without unpacking or running
its files. Neither verifier is a substitute for a publisher signature,
native build, gameplay test or security audit.
