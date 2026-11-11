# Craft Anything v0.4.0 — hardened full-source upgrade V3

This is a third **independent, uncompiled source fork** of the supplied
`craft-anything-main 2.zip`. Version 0.4.0 is the preserved Rust workspace
version; **V3** describes this packaging/hardening iteration and is not an
official upstream release. This archive retains the full previous source,
tests, assets and documentation instead of replacing the project with a
demonstration or a binary-only release.

## Changes relative to the V2 archive

1. **Explicit archive restore command.** Added the human-only
   `project_archive_restore` ToolSpec, handler, typed version-1 receipt
   and `Workspace::restore_archive`. Archive requests continue to be
   separate from irreversible `project_delete`. Restoring requires an
   explicit confirmation and the exact absolute original path recorded
   in `receipt.json`; the archive and destination must be siblings.
2. **No-clobber restore preflight.** Before restoring any project-owned
   entry, the engine verifies the receipt, manifest, archived location,
   symlink restrictions and every destination collision. It never silently
   merges or overwrites a project path. Personal files left in an
   in-place project remain untouched. The project manifest is restored
   last. The receipt is advanced from `archived` to `restoring`, then
   `restored`, with a timestamp after success.
3. **Failure-directed restore tests.** Rust unit tests cover a normal
   archived project, personal-file preservation, collision rejection
   before any moves, bad destination confirmation, refusal of an
   incomplete/restoring receipt, and an in-place edited app. Agent
   permission tests deny restore; a tool-level person path tests
   explicit confirmation and restoration.
4. **Fresher opt-in game evidence.** The independent standard-library
   Python replay probe now allocates a unique fresh screenshot directory
   per invocation, supplies a fresh nonce to the executable, requires
   the matching echo, checks PNG encoded size before decoding, and
   includes SHA-256 hashes of each accepted screenshot and the case
   in the output report. Regressions reject a stale screenshot set and
   incorrect challenge echo.
5. **Tool/UI/catalog/docs wiring.** The engine clean-room tool list,
   UI managed-result toast, all 16 localisation TSV files, documented
   tool inventory and source tool-count guard were updated. The
   completed restore is callable from the CLI or engine tools; it does
   **not** yet have a desktop Restore button. See
   [docs/project-archive.md](docs/project-archive.md) and
   [docs/independent-game-probe.md](docs/independent-game-probe.md).

## Verification performed for V3

- **Passed:** `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
  (7 synthetic Python tests, including deliberate failure fixtures).
- **Source review only:** Rust archive and tool regressions were added,
  but could not be compiled or run because `cargo`, `rustc`, and
  `rustfmt` were unavailable in the packaging environment.
- **Packaging:** Verify ZIP entry integrity, source retention relative
  to V2, and digest before distributing. The delivery response should
  report the measured results, not infer them from source code.

The synthetic replay fixture demonstrates that this checker rejects
particular bogus evidence. It does **not** demonstrate that Craft Anything
successfully builds or runs an actual game.

## Release gates for a Rust-equipped workstation

From the extracted project root:

    cargo fmt --all -- --check
    cargo xtask i18n-coverage
    cargo xtask tools --check
    cargo xtask schemas --check
    cargo xtask layers
    cargo test -p craft-anything-engine
    cargo test -p craft-anything-ui-egui
    cargo clippy --workspace --all-targets -- -D warnings
    cargo build -p craft-anything-cli
    python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v

Then execute real cross-platform manual tests: archive an ordinary
project, restore it, test deliberate destination collisions, confirm
personal files remain unmodified, recover a simulated interrupted
archive manually, and inspect the translated UI on macOS and Windows.
Run the independent probe against a genuine game adapter with inputs
and frames collected independently of the target.

## Unresolved risks, candidly

- **No compile/OS guarantee:** This archive is complete Rust source,
  not a verified working native executable. Upstream test counts and
  older status files do not apply to this V3 fork without rerunning.
- **Partial filesystem operations:** Archive and restore perform a
  sequence of file renames on a sibling volume, not one atomic
  transaction. A crash mid-restore leaves a `restoring` receipt;
  automatic retries are deliberately refused to avoid clobbering files.
  Filesystem races between preflight and rename still need adversarial
  testing and platform-specific handling.
- **Replay protocol trust:** A target executable can fabricate telemetry
  and screenshots and echo the nonce. The tool still executes a
  **trusted** program, not an isolated sandbox, and the Rust readiness
  gate does **not** launch it. Bounded subprocess output collection,
  independently injected input, independently captured frames and
  real timing remain for a subsequent iteration.
- **Existing broader security risks:** Third-party code execution,
  source provenance, dependency supply chain, native process
  isolation, and parity against reference games all require dedicated
  validation. V3 does not solve them.

This fork retains the original project's license files. No upstream
endorsement, completed production certification or independently
verified game compatibility is implied.
