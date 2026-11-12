# Craft Anything v0.4.0 — Ghidra Module Awareness and Desktop Controls V24

This is a **full independent source snapshot** continuing V23. It contains
the complete upstream-derived Cargo workspace and all earlier independent
upgrade reports. It is **not** an official release or a compiled application.

## Changes

1. **Detect common Ghidra module-only updates.** The v5 on-disk project key
   and per-target live `DeepKey` now include a bounded inventory digest of
   `Ghidra/Processors`, `Ghidra/Features` and `Ghidra/Framework`. It records
   relative names, lengths and timestamps for known analysis-related files,
   hashes the contents of a bounded number of small compiler/processor
   specifications, and identifies symlinks without following them. There are
   explicit limits on directory visits, recursion depth and content hashing.
   The launcher and version-file checks introduced in V23 remain.
   **Amended after V24:** a later change (`#16`) also content-hashes large
   JAR, SLA and native modules, streamed within a budget of 2,048 modules and
   512 MiB; only modules past that budget keep metadata alone. The V24 text
   above describes the behaviour at the time and is kept for the record.

2. **Manual fresh-analysis generation.** `GhidraSettings::analysis_generation`
   defaults to zero when loading an older `config.toml`. Incrementing it
   changes the saved project key and live provider key even when the Ghidra
   installation and other settings appear identical. Existing disk analyses
   are **not deleted**; a fresh analysis may take hours. This is an escape
   hatch for mutations the bounded inventory does not detect.

3. **Desktop configuration.** Settings → Engines now has editable global
   Ghidra processor-language and compiler-spec overrides, plus a clearly
   labelled **Force fresh analysis** action with a second confirmation. A per-target language selected
   on open still overrides the global language. Empty fields restore Ghidra
   defaults. The desktop applies these changes using its existing settings
   propagation and persistence mechanism.

4. **Avoid duplicate module scanning.** On an ordinary Deep request, the
   revision calculated for the live provider key is reused when deriving the
   disk cache key. The pre-analysis entry point computes it once separately.

## What the revision cannot guarantee

- At V24, large JAR, native and SLA modules were identified by name, size
  and modification timestamp, **not** by reading every byte, so a same-size
  edit with a restored timestamp could evade detection. Change `#16` now
  streams their contents within the same bounded budget (2,048 modules /
  512 MiB); modules past that budget are still metadata-only and can still
  evade detection.
- Small text file hashing has aggregate and per-file limits. Very large
  installations or directories can exceed the bounded inventory, in which
  case not every module is covered. Check the installation and explicitly
  request fresh analysis when in doubt.
- The inventory scans filesystem metadata on a Deep request. Its latency
  needs benchmarking with a real Ghidra installation; there is no claim
  yet that it meets interactive response-time budgets.
- Switching to clean-room mode cannot forcibly interrupt a Deep job already
  holding the session lock. V23's provider teardown runs when the session
  lock becomes available.

## Files changed in V24

| Source path | Reason |
| --- | --- |
| `crates/engine/src/tools/deep.rs` | Bounded module revision, v5 project key, generation-aware key, reuse of computed revision, synthetic regression tests. |
| `crates/engine/src/settings.rs` | Backwards-compatible `analysis_generation` field. |
| `crates/ui-egui/src/settings_ui.rs` | Editable global processor/compiler fields and manual reanalysis button. |
| `README.md`, `docs/STATUS.md`, `docs/native-validation.md`, `docs/ROADMAP.md` | New behavior and remaining risks. |

## Checks and release gates

The complete source folder and the independently extracted ZIP should each
pass the source integrity commands:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
python3 scripts/source_bundle_integrity.py verify-tree --root .
```

Run the following in a Rust 1.97 development environment **before release**:

```bash
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine deep_settings_
cargo test --locked -p craft-anything-engine deep_lifecycle_
cargo test --locked -p craft-anything-engine inventory_tests
cargo check --locked -p craft-anything-ui-egui
cargo test --locked --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

The manual three-OS CI workflow already calls the focused `deep_settings_`,
`deep_lifecycle_`, `inventory_tests` and desktop check commands. Test additionally with a real
Ghidra installation: replace one processor spec while keeping the launcher
unchanged; confirm the old analysis is not reopened; then use **Force fresh
analysis** and verify an explicit new analysis generation. Check settings
round-tripping across restart and confirm the explicit target language pin
still wins over the global language field.

**Validation limit in this packaging environment:** the Python and archive
checks can run, but no `cargo`, `rustc`, `rustfmt`, live Ghidra, desktop UI or
cross-platform native execution is available. Source success is not native
runtime evidence.

## Still open beyond V24

- Profile the module revision scan in actual Ghidra installs; consider
  invalidation based on a maintained module manifest for stronger guarantees.
- Verify the GUI controls, Ghidra bridge and language/compiler combinations
  on Windows, macOS and Linux.
- Migrate 48-bit short target IDs compatibly; audit agent permissions and
  verify standalone build acceptance and release signing. These are separate
  roadmap work, not addressed by a cache upgrade.
