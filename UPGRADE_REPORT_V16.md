# Craft Anything v0.4.0 — Stateful native MCP target lifecycle V16

V16 is the **full independent source snapshot** derived from the verified V15
archive, retaining every V15 member. The upstream Rust source workspace stays
at version `0.4.0`. This is not an official or signed release.

## Problem addressed

V15 added a native CLI triage smoke for an eight-byte valid WebAssembly module.
It launched a separate CLI process and checked the returned evidence.
That was useful, but it **could not prove the target was closed**: the CLI's
`with_target` function discards target-close errors and the process exits.
It also never invoked the deeper `formats_wasm_module_show` parser.

V16 adds `scripts/native_mcp_target_smoke.py`, a fail-closed native MCP
integration smoke that performs the following operations **in one process**:

1. Initialize the MCP session and confirm the expected protocol/server.
2. Query `core_session_show` and require an empty Deep-mode session.
3. Call `core_target_open` with a freshly created, disposable WASM 1.0 module;
   verify its target ID, hash, format and eight-byte size.
4. Call `formats_wasm_module_show` on the active target with summary detail.
   Verify evidence identity, original-file hash, parser name, valid WASM module
   version 1, absence of parse errors and zero structural counts.
5. Query `core_session_show` to confirm the target is active and at least
   one parser evidence record has been retained.
6. Call `core_target_close` explicitly on the target ID and verify success.
7. Query `core_session_show` again; require **no open targets or active target**,
   while the recorded evidence count remains unchanged.

MCP `tools/call` workers are concurrent in the Rust server. V16 therefore
waits for and verifies every request's response before sending the next.
The probe uses a private temporary configuration, credential-filtered
environment, no user-supplied targets, a 75-second deadline, bounded pipes,
an eight-byte fixture, and a strict maximum-output guard. Receipts retain
only fixed status fields, the known synthetic fixture's SHA-256, operation
count and the binary hash—not raw target evidence or ephemeral local paths.

This new `mcp_target_smoke` gate is mandatory **after** V15's `triage_smoke`
and **before** source re-verification and unsigned executable export. Any
failed native command, bad evidence, parser error, incomplete cleanup, changed
binary or malformed transcript blocks the artifact. Existing V1–V15 checks
remain mandatory, as does the original GitHub Actions manual dispatch.

## Validation and honest limits

- Python unit tests cover the ordered lifecycle state machine, forged
  evidence, failed target close, inconsistent response payloads, binary
  identity checks, leak-free receipts and the unsigned artifact export gate.
- A real Python child process is used to test the newline-delimited JSON-RPC
  transport, request ordering and out-of-order response refusal. This is a
  **test of the Python transport**, not a substitute for native Rust execution.
- The source manifest, ZIP integrity, full Python suite, retained V15 members
  and fresh-extraction test suite are verified when the archive is packaged.
- **Rust `cargo` and `rustc` are unavailable in the packaging workspace.**
  Neither real native MCP parsing nor desktop app operation has been proven.

To get actual native evidence, copy the complete extracted source into an
authorized repository and manually run **Independent Fork Native CLI Matrix**
on Ubuntu, macOS and Windows. Check for green `cli_build`, `triage_smoke`,
`mcp_target_smoke` and `artifact_export` receipts on every system.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V15.md](UPGRADE_REPORT_V15.md).
