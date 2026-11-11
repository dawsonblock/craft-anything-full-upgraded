# Craft Anything v0.4.0 — independent hardening upgrade

**Scope:** Complete source snapshot derived from the user-provided `craft-anything-main 2.zip`.
This is not an official ArtCraft release, a prebuilt application, or a claim that every original
feature is production-ready. All original crates, docs, assets, examples, lockfiles and licensing
files are retained. The original uploaded archive has not been changed.

## What was fixed

| Area | Previous behavior | New behavior |
| --- | --- | --- |
| Game core-loop verdict | Inverted the `not.is_empty()` condition: a failing or incomplete `--self-test` could be accepted while an all-passing report was rejected. | Passing reports return success; failed, missing, duplicate or unsupported-version steps fail explicitly. |
| Final Ready status | Trusted only the final check's `passed` bit. An earlier failure in the same round could be hidden. | Validates **all** checks from the last round before Ready; failures, unknown evidence and failed functional claims block Ready. |
| Quality thresholds | Could stop with Ready when requested comparison criteria were still unmet. | A working build below the configured threshold remains unverified and requests a fix or explicit user acceptance. |
| Picture evidence | An absent/unreadable original could produce `similarity: None` and pass despite a configured screenshot quality threshold. | Records unknown evidence and fails the required comparison. The general check predicate also refuses a missing score when the check has its own threshold. |
| Saved screenshots/tests | A program could create an output file and then crash/timeout; the file could be treated as successful output. A failed stale-file removal was ignored, and output size was unbounded. | `run_with` requires a successful process exit, fails on stale-file-removal errors, and reads at most 32 MiB of new output. |
| Plugin validators | A crashing/unavailable validator produced a passing check. | Plugin failure produces a failing check with an explanation; a configured check cannot certify a build it didn't evaluate. |
| Agent approvals | MCP/agent callers could call `project_inbox_answer` with `allow`/`always`, or mark their own build Ready through `project_build_finish`. | Both operations require a person-originated UI/CLI call; machine callers get `NoPermission`. |
| Data deletion | `project_delete` accepted a `confirm: true` argument even from an agent and wasn't marked destructive. | Only a person can delete; explicit `confirm: true` remains mandatory. The tool is marked as potentially data-discarding. |
| Spend/decision controls | Agent callers could raise asset budgets, enable paid assets, or change whether questions were answered automatically. | Mutation of these project controls requires UI/CLI; agents may still read the configuration. |
| Persistent security configuration | Agents could call `settings_set` and persist Deep/Auto mode changes; plugin and engine installation controls were also open to agent callers. | Persistent settings, plugin installation/removal and external engine installation require UI/CLI. |
| Large file reads | The 256 KiB *response* limit didn't bound reading and line-indexing an arbitrarily huge source file. | Reads reject files above 32 MiB and single-call edits reject files above 8 MiB, before loading their content. |

## New or extended regression coverage

- Game self-test: all three steps passed; missing/failed step; invalid JSON; rejected versions; duplicate steps.
- Final status: an earlier failure followed by a passing check; unknown evidence; failed functional claim; unmet configured visual threshold; empty checks.
- Project tools: both `Caller::Mcp` and `Caller::Agent` refused for deletion, approval, budget, asset enabling, question-policy changes and manual finish; read-only budget inspection remains available.
- Setup tools: both agent caller kinds refused for persistent settings, plugin installation/removal and engine installation.
- Screenshot evidence: quality-bar handling for unreadable/missing reference images.
- Generated screenshot/self-test output: bounded file reader and oversize-file rejection.
- Build tools: oversized sparse text files rejected before reading or editing.
- CheckResult::good: explicit comparison threshold without a similarity measurement is rejected.

**Verification distinction:** The Rust tests above are included as test *source*, but have
**not been executed** in the environment where this ZIP was assembled: it has no Rust compiler,
`cargo` or `rustfmt`. Do not use the inclusion of regression tests as evidence that they pass.

## Run the real checks before deployment

Install the Rust 1.97 (or newer compatible) toolchain, then from the root of this extracted project:

```bash
cargo fmt --all -- --check
cargo test -p craft-anything-engine
cargo test -p craft-anything-ai
cargo test -p craft-anything-mcp
cargo build -p craft-anything-cli
cargo clippy -p craft-anything-engine -p craft-anything-ai --all-targets -- -D warnings
cargo xtask layers
cargo xtask locks
cargo xtask secrets
```

These commands may need the system dependencies listed in the original README. Run cross-platform
builds and UI/CLI integration tests on macOS, Windows and Linux, not just a headless container.
When Rust is available, regenerate schemas and tool metadata with the repository's `xtask`
commands and confirm `cargo xtask wasm` if the changed engine is shipped to the browser.

## Important limits and next milestones

1. **Self-reported game tests are not independent gameplay proof.** A broken or dishonest
   executable can write `"passed": true`. Add externally driven input, game-state telemetry,
   screenshots/video and replayable interaction scripts before claiming behavioral equivalence.
2. **No live engine/UI regression runs here.** Run a multi-stack fixture matrix (basic website,
   browser interaction, desktop app, Godot/Rust game, malformed plugin, interrupted child process).
   Check that UI/CLI callers can still perform each human-only action with the appropriate dialog.
3. **The build's whole-result policy is stricter.** Builds below an explicit quality threshold
   now stop in NeedsYou rather than being silently called Ready. The person may explicitly
   accept a partial result with `project_build_finish`.
4. **No trusted review of third-party extensions.** Confirm checks operate with least privilege,
   read resource limits and subprocess isolation in the target deployment environment. A policy
   gate alone is not a sandbox; agents can still modify allowed project files and run approved tools.
5. **Large-file reading is bounded, not streamed.** The new 32 MiB source-file ceiling prevents
   accidental giant allocations; a later iteration should add bounded streaming with accurate
   line counts, cancellation and race-safe file metadata checks.
6. **Model-backed reconstruction is not exact reverse engineering.** Matching screenshots and
   generated behavior provides evidence, not guaranteed parity with proprietary programs.

## Provenance

Source baseline: user-provided `craft-anything-main 2.zip`, Rust workspace version `0.4.0`.
Modified code is limited to `crates/ai/src/rebuild/`, `crates/engine/src/workspace.rs`,
`crates/engine/src/tools/{build_tools,project_tools,setup_tools}.rs` and this documentation.
The original open-source license files remain intact. This source fork does not imply
endorsement by the original developers.
