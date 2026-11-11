# Craft Anything v0.4.0 — Truthful Deep Native Gate V25

This V25 archive is an **independent full-source snapshot** of Craft Anything,
continuing V24 and preserving all earlier source files and upgrade reports.
It is not a compiled application or an official release.

## Why V25 exists

V24 introduced processor-module fingerprinting, a manual Ghidra reanalysis
generation and desktop controls, but the packaging environment has **no Rust
toolchain or Ghidra**. Another feature-only ZIP would increase the amount of
unverified native code. V25 instead makes it easier to expose and resolve
that risk on an actual development machine.

## Upgrades

1. `scripts/native_validation.py` now accepts `--profile deep`. It keeps the
   verified source-inventory and Python-test gates, then runs Rust formatting,
   the Deep settings and lifecycle regressions, target reopen regressions and
   an egui desktop crate type check. The existing `--profile full` behavior
   remains the default and still requires a built CLI smoke test.
2. **Truthful statuses:** missing Cargo/rustc produces `blocked`, never a
   passing Rust result. A failed test produces `fail`, including when other
   checks are blocked. An invalid source manifest prevents bundled code from
   being executed. The report separately records `live_ghidra_validation`
   and `manual_validation` as `not_run` even if the automated checks pass.
3. **No destructive receipts:** new JSON reports use exclusive creation and
   are refused when an existing path would be overwritten. Reports inside
   the source folder are also refused because they would invalidate the
   source manifest.
4. `scripts/verify-deep.ps1` and `scripts/verify-deep.sh` start the focused
   gate on Windows and macOS/Linux respectively, with uniquely named receipts
   in the source tree's parent folder by default. Both accept an optional
   custom receipt path outside the source tree.
5. Six new Python regressions exercise blocked, passed and failed Deep
   profiles, integrity refusal, invalid profile names and receipt protection.

## How to run

From the extracted `craft-anything-full-upgraded` directory:

**Windows PowerShell** (requires Python 3 and Rust 1.97+):

```powershell
& .\scripts\verify-deep.ps1
```

**macOS/Linux** (requires Python 3 and Rust 1.97+):

```bash
bash scripts/verify-deep.sh
```

Or directly on any OS:

```bash
python3 scripts/native_validation.py --root . --profile deep --report ../deep-v25.json
```

Exit code `0` means automated checks passed; `1` means at least one check
failed; `2` means work is blocked by an unavailable toolchain. Even exit `0`
does **not** prove Ghidra launch, actual decompilation, permission policy or
desktop user-interface behavior. That requires separate live tests.

For the full existing gate, omit `--profile deep` or use `--profile full`.

## What can be verified in the packaging environment

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -q
python3 scripts/source_bundle_integrity.py verify-tree --root .
```

The native gate can also run here, but it should produce **blocked** because
`cargo`, `rustc` and `rustfmt` are not available. Do not mislabel a blocked
run as a passing native run. The release gate needs an actual Windows,
macOS or Linux Rust toolchain, real Ghidra integration checks and desktop
verification before production use.

## Not yet solved

- Ghidra module inventory scan cost in large installations and
  timestamp-restored changes in binaries.
- End-to-end Ghidra import/decompile tests with the actual desktop settings
  and user-selected compiler/language IDs.
- Permission, target identity, build acceptance and signing work listed in
  the earlier roadmap reports.
