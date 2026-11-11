# Craft Anything v0.4.0 — Native WebAssembly target smoke (V15)

V15 is a complete independent source-fork archive derived from the **verified
V14 ZIP**. It preserves all V14 files and the upstream Rust workspace version
0.4.0. It is **not** an official release, compiled desktop app, or security
assessment of the upstream project's many target-analysis tools.

## Change with practical value

Until V14 the manual native CLI build gate mostly checked help, metadata,
MCP negotiation, read-only session behavior and refusal of a nonexistent tool.
It did **not** analyze a target. V15 adds
`scripts/native_triage_smoke.py`, which exercises the real CLI's
`core_target_open` → `core_target_triage` → `core_target_close` path using the
actual Rust executable **once a native runner builds it**.

- Input: the exactly eight-byte WebAssembly 1.0 magic/header
  `00 61 73 6d 01 00 00 00`, created inside a disposable directory.
- Invocation: `craft-anything-cli --deep triage <synthetic-wasm>`; Deep mode is
  required to permit target inspection. `--allow-run` is **not** provided.
- Isolation: fresh private config/home, credential-filtered environment,
  no URL or network tool requested, no user-controlled target, 45-second execution ceiling
  and the shared 2 MB native-output bound.
- Evidence checks: full JSON evidence object, valid `ev_` identifier,
  `core_target_triage` operation, expected target name and SHA-256,
  `wasm` subject and result kind, eight-byte reported size, builtin engine,
  active Deep mode, explanatory WebAssembly summary, and the correct
  `formats_wasm_module_show` recommendation.
- Export gate: the `triage_smoke` receipt must match the built CLI's SHA-256
  and byte size and must contain all checked WASM evidence markers. Only then
  is an **unsigned test executable** copied for CI inspection.
- Privacy: raw evidence (including synthetic target details) and temporary
  local paths are removed before writing receipts. The temporary source
  fixture is destroyed automatically.

The smoke validates **one recognized format and a CLI execution path**. It
does not independently assert `core_target_close` succeeded: the current CLI
implementation discards close errors. It cannot validate all ~300 commands,
the native desktop UI, safety of inspecting arbitrary binaries, operating
system permissions, device effects or real projects.

## What was tested during packaging

The V15 Python regressions exercise valid and malformed evidence, forged
target identity, mismatched toolsets, unsupported output, failed native
launches, environment filtering, receipt redaction, missing binaries, and
fail-closed artifact export. The full Python regression suite, source
inventory and ZIP integrity checks should be re-run for each final archive.

**No Rust `cargo`/`rustc` toolchain is installed in the packaging
environment**, so it cannot be claimed that the actual Rust CLI built or
that the new native WASM analysis succeeded. For real evidence, extract the
ZIP into the `craft-anything-full-upgraded` folder of an authorized repository
and manually run `.github/workflows/fork-native-cli-matrix.yml` on Ubuntu,
macOS and Windows. Look for `cli_build`, `mcp_smoke`, `triage_smoke`,
`source_integrity_after_smoke` and `artifact_export` all marked `pass`.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V14.md](UPGRADE_REPORT_V14.md).
