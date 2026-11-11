# Craft Anything v0.4.0 — Target Identity and Deep Handoff V21

This is a **full independent source snapshot** extending V20, not an official
release or compiled application. All previous hardening and the original Cargo
workspace are retained.

## Three concrete V20 behaviors corrected

1. **Old path after opening identical content elsewhere.** A content-addressed
   target could be reopened from a moved or renamed copy, yet still point at
   the original path. Later `check_unchanged()` checks could fail against an
   obsolete file. V21 updates the verified path and name while retaining
   valid caches, cursor and unsaved annotations for identical bytes and
   interpretation.

2. **Old Deep decompiler after a language change.** `core_target_open` could
   change `language_id` without invalidating the target's cached Ghidra
   provider. V21 evicts that provider and resets the cursor when the explicit
   language pin changes. Repeating the same pin leaves the cache in place.
   Passing an explicit blank string clears the pin; omitting `language_id`
   preserves it. Language-only changes retain unrelated native caches and
   human annotations.

3. **Lost language selection in local-agent handoffs.** Target metadata passed
   to a local MCP child previously included path, hash and raw architecture,
   but not the per-target Deep `language_id`. V21 adds a backwards-compatible
   optional field to `TargetContext`, shares it from the desktop, and restores
   it in the child. Older JSON without the field still parses. Remote agents
   continue to receive no target metadata by design.

   Compatibility is **one-way**: an older installed local CLI that rejects
   unknown JSON keys may not accept a new handoff containing `language_id`.
   Use a matching V21 app/CLI pair, or test the installed CLI before handoff.

Reinterpreting a raw binary now also preserves its existing Deep pin when
the next open omits that field, while still resetting invalid raw-analysis
caches as in V20.

## Changed source and tests

| Path | Change |
|---|---|
| `crates/engine/src/tools/session_tools.rs` | Path refresh, language invalidation call, three regression tests. |
| `crates/engine/src/tools/deep.rs` | Targeted provider invalidation and one regression test. |
| `crates/engine/src/session.rs` | Preserve Deep pin on reinterpretation; serialize/restore local MCP pin. |
| `crates/engine/src/target.rs` | Optional backward-compatible target context field. |
| `crates/ai/src/cli_agents/mod.rs` | Updated context fixtures and local-versus-remote handoff test. |
| `.github/workflows/fork-native-cli-matrix.yml` | Both engine and AI target-handoff regression gates before unsigned CLI export. |

V21 adds **four engine tests and one AI test**. The `target_reopen_` engine
filter now matches eight tests including V20, and the `mcp_target_handoff_`
AI filter matches two tests.

## Validation commands

```bash
python3 scripts/source_bundle_integrity.py verify-tree --root .
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
cargo fmt --all -- --check
cargo test --locked -p craft-anything-engine target_reopen_
cargo test --locked -p craft-anything-ai mcp_target_handoff_
cargo test --locked -p craft-anything-engine annotation_load_
cargo test --locked -p craft-anything-engine target_close_
cargo test --locked -p craft-anything-cli --bin craft-anything-cli target_lifecycle_tests
```

The manual GitHub Actions workflow attempts these Rust regressions on Ubuntu,
macOS and Windows before retaining unsigned CLI binaries. That workflow was
**not run here**. Cargo/rustc are unavailable in this packaging environment:
the proposed Rust fixes and tests have been written, but **native behavior,
real Ghidra analysis and the desktop GUI are unverified**. Python source
integrity checks do not establish that the Rust code compiles.

## Remaining risk

1. Run the native Cargo gates and the full CLI smoke, then fix any failures.
2. Test switching between two real Ghidra processor choices in a live run.
3. Exercise moved-file reopening and local-agent transfer on three OSes.
4. Migrate 48-bit `t_` prefixes to longer IDs with saved-project compatibility.
5. Evaluate Deep cache invalidation when *global* Ghidra settings change in an
   open session. V21 handles explicit per-target changes only.

The archive is a testable source proposal, **not proof of production readiness**.
