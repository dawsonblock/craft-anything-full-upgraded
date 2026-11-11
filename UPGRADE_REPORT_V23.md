# Craft Anything v0.4.0 — Deep Lifecycle and Analyzer Identity V23

This archive is a **full independent source snapshot** continuing V22. It
contains the original Cargo workspace and all previous V1–V22 reports,
features and tests. It is **not** an official release or a compiled program.

## Two source-level issues addressed

1. **Retaining Deep providers after switching to Clean room.** In V22,
   `provider_for_active()` returned `Err(None)` in Clean room but did not
   remove its cached `DeepSlot`. The desktop's settings update also left the
   providers attached. V23 adds `Session::evict_deep_providers()` for *all*
   open targets; `CraftApp::flush_settings()` calls it when the effective
   session transitions to Clean room. The Deep hook also removes a provider
   if another caller reaches it in Clean room. Eviction drops the provider
   or remembered startup error and clears a stale cursor, retaining target
   identity, user annotations and unrelated analysis caches. The desktop's
   session lock may delay teardown while another tool is already running;
   this is **not** a synchronous interruption of in-flight analysis.

2. **Reusing a Ghidra cache after an in-place installation upgrade.** V22
   distinguished analyzer launcher paths but not revisions *at the same
   path*. V23 adds `analyzer_revision` to the live `DeepKey` and upgrades
   the project analysis hash to v4. It uses a canonical launcher path,
   bounded launcher content (up to 256 KiB), launcher file metadata and
   the bounded contents of the installation's `Ghidra/application.properties`
   file (up to 64 KiB) when available. This identifies typical in-place
   launcher or application-version changes without loading arbitrary large
   files. V3 cache directories are retained for recovery but are not reused.

   **Limit:** This is a *best-effort* fingerprint, not a cryptographic digest
   of every Ghidra module. A modified module with unchanged launcher,
   version file and relevant metadata is still not detected automatically.

## Source files changed

| Path | Change |
|---|---|
| `crates/engine/src/tools/deep.rs` | Bounded analyzer revision, v4 project cache key, clean-room eviction helper and hook, two synthetic Rust tests. |
| `crates/engine/src/session.rs` | Multi-target Deep-provider eviction while retaining notes and other caches. |
| `crates/ui-egui/src/lib.rs` | Evict cached Deep providers when effective settings switch to Clean room. |
| `.github/workflows/fork-native-cli-matrix.yml` | Two regression tests and desktop crate type check before unsigned CLI export. |
| `README.md`, `docs/STATUS.md`, `docs/native-validation.md`, `docs/ROADMAP.md` | Clear V23 behavior and unverified dependencies. |

## Validation

The following source-level checks are runnable without Rust:

```bash
python3 scripts/source_bundle_integrity.py verify-tree --root .
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
```

Native validation required on Windows, macOS and Linux:

```bash
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine deep_lifecycle_
cargo test --locked -p craft-anything-engine deep_settings_
cargo test --locked -p craft-anything-engine target_reopen_
cargo test --locked -p craft-anything-ai mcp_target_handoff_
cargo check --locked -p craft-anything-ui-egui
cargo test --locked --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

Also run the CLI's real binary smoke, Ghidra with a real executable, and
the desktop's Deep/clean-room transition with two simultaneously open
targets. Check that no stale process survives after the session guard becomes
available. These checks were **not run in the source-packaging environment**
because `cargo`, `rustc` and `rustfmt` are unavailable. Python and checksum
success, if obtained, cannot establish native correctness.

## Deferred risks

- Ghidra's complete executable/extension module state is not fingerprinted.
- Global processor/compiler-spec UI controls remain absent; the engine
  supports them through configuration and per-target language options.
- The original target ID is still a 48-bit SHA-256 prefix with fail-closed
  collisions; backwards-compatible ID migration remains open.
- Agent permission bypasses, standalone build acceptance, release signing,
  and live Windows/macOS/Linux checks remain separate, higher-level roadmap
  work. No source ZIP alone resolves them.

**Next gate:** run native tests before adding further source features.
