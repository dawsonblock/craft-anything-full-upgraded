# Craft Anything v0.4.0 — Deep-mode Global Settings and Cache Safety V22

This is an **independent, full-source snapshot** extending the V21 proposal.
It retains the original Cargo workspace, project resources, prior reports,
test harnesses, and all earlier hardening. It is **not** an official ArtCraft
release, a compiled executable, or evidence that native tests pass.

## Root cause and concrete fix

V21 invalidated a target's Ghidra provider when `core_target_open` changed
the *per-target* `language_id`. However, a different path could silently
serve an obsolete Ghidra session: `provider_for_active` returned a cached
`DeepSlot` **before** checking global Ghidra configuration. Editing the
global processor, compiler, installation path or Java settings could therefore
leave the prior result active; a cached startup failure was stale too.

V22 changes `DeepSlot` to include a `DeepKey`. On each Deep request, the
current per-target language override and the global Ghidra settings are
compared to the cached key. On a mismatch, the old provider/failure and
cursor address are discarded. A matching key avoids needless restarts;
clean-room mode still cannot launch Ghidra; native fallback is retained.

The on-disk project cache had a related issue: its settings hash reflected
the processor language, **not the compiler spec**. A project imported with
one compiler spec could be wrongly reused with another. Its v3 hash now
includes the effective processor, compiler spec, and resolved
`analyzeHeadless` launcher path. Changing resource limits need not re-run an
otherwise identical Ghidra analysis, but does refresh the live provider.
Existing v2 cache directories are left alone; they are not re-used by v3.

## Changed files

| File | Change |
|---|---|
| `crates/engine/src/tools/deep.rs` | Global-and-target Deep configuration key, stale live-provider eviction, cursor reset, versioned on-disk analysis hash. |
| `crates/engine/src/tools/deep.rs` tests | Three `deep_settings_` Rust regressions: key fields, hash fields, invalidation while an active fake Ghidra provider exists. Existing test fixtures updated for the keyed provider. |
| `.github/workflows/fork-native-cli-matrix.yml` | Additional Rust regression gate before unsigned CLI packaging. |
| `README.md`, `docs/STATUS.md`, `docs/native-validation.md`, `docs/ROADMAP.md` | V22 upgrade notes, reproduction and risk information. |

## Validation: required versus available

Source-package checks **can** run without Rust:

```bash
python3 scripts/source_bundle_integrity.py verify-tree --root .
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
python3 scripts/source_bundle_integrity.py verify-zip --archive ../Craft_Anything_v0.4.0_HARDENED_V22_FULL_SOURCE.zip
```

Native checks still required on Ubuntu, macOS and Windows:

```bash
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine deep_settings_
cargo test --locked -p craft-anything-engine target_reopen_
cargo test --locked -p craft-anything-ai mcp_target_handoff_
cargo test --locked --workspace
```

Then install real Ghidra and verify global language/`compiler_spec` changes
with an open target, compare actual decompilation addresses and signatures,
and inspect UI settings persistence. Also check failure-to-success recovery
after correcting a bad configured install path. The source-only test of a
fake provider does not replace those runs.

## Risks not solved

1. This environment has no `cargo`, `rustc`, or `rustfmt`; even a valid ZIP
   and passing Python tests **cannot prove this Rust change compiles**.
2. A Ghidra installation replaced *in place* at the same launcher path,
   without any settings edit, is not automatically detected by `DeepKey`.
   Relaunch the target or change the configured path after such an upgrade.
3. The analysis key includes the analyzer **path**, not a digest of all its
   program and architecture modules. In-place installation changes may still
   reuse a saved project; a future native implementation should fingerprint
   Ghidra version and module state if that guarantee is required.
4. The manual three-platform CI workflow is authored, not run from this
   offline packaging context; native compilation and desktop integration
   remain **pending**. V21's target path and agent handoff also require
   independent native validation.

Next highest-value step: run the manual Rust matrix, fix any compiler or
test failures it reports, then test a real Ghidra processor/compiler switch
against one unchanged target. Do not claim production readiness before it.
