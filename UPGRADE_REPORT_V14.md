# Craft Anything v0.4.0 — MCP safety-contract verification V14

V14 is a new **complete, independent source-fork archive** derived from the
verified V13 ZIP. It preserves all V13 files and retains the upstream Rust
workspace version (0.4.0). It is not an official release or a compiled app.

## What changed

1. **Read-only MCP descriptor check.** `scripts/mcp_protocol_smoke.py` now
   verifies that `core_session_show` is present in the full native MCP
   `tools/list` response, that its `inputSchema` accepts an empty object and
   no other properties, and that `readOnlyHint`, `idempotentHint`,
   `destructiveHint`, and `openWorldHint` match the read-only Rust tool
   declaration. Its annotation title must match its actual tool title.
   This catches a false safety label even when the tool name and description
   still match the CLI registry digest.
2. **Actual unknown-tool refusal path.** The bounded native protocol probe
   calls one deliberately nonexistent tool ID with empty arguments and
   demands an MCP `isError` response whose JSON text contains a `bad_input`
   code, the queried tool name, and nonempty remediation. This is distinct
   from the V12 negative *JSON-RPC method* test. A mistakenly accepted tool,
   malformed error, or success-looking failure is rejected.
3. **Strict reply envelopes.** Every expected JSON-RPC response must have
   exactly one of `result` or `error`, and each ID must appear once. Native
   `tools/call` work is concurrent, so response order is not assumed.
4. **Fail-closed export.** The manual three-platform native CLI workflow
   requires both descriptor verification and unknown-tool refusal evidence
   before it can retain an **unsigned test executable**. Its provenance
   receipt records the two Booleans, never raw tool output or user settings.
5. **Synthetic regression tests.** Added fixtures for false annotations,
   nonempty parameter schema, missing tool, nonexistent-tool success,
   malformed error content, incorrect error code, missing remediation,
   simultaneous result/error envelopes, and missing result/error envelopes.

The only registered tool invoked by this probe is `core_session_show`. It is
declared read-only and does not require a target. The second call requests an
ID that is not registered; no target path, URL, credentials, or project data
are supplied. The process has a bounded deadline/output and a temporary,
credential-free configuration.

## Verification and limits

- The package process runs the Python test suite, generates a new
  `SOURCE_INTEGRITY.json`, checks compressed ZIP integrity and retained V13
  members, and runs the tests from a fresh extracted copy.
- The actual source-of-truth Rust server handling `tools/call` and the Rust
  `Effects::READ_ONLY` and `empty_schema()` declarations were inspected for
  consistency with the expectations. **Source review is not native testing.**
- No `cargo` or `rustc` exists in the packaging environment. A real built
  CLI, MCP server run, desktop GUI, permission dialog, target analysis,
  network use, and production signing remain **unverified**. The Python
  test harness uses synthetic stand-ins.
- To get native evidence, manually dispatch the "Independent Fork Native
  CLI Matrix" workflow on an authorized repository with the source archive
  extracted as `craft-anything-full-upgraded`. The gate should produce a
  passing `mcp_smoke` and `artifact_export` on Ubuntu, macOS and Windows.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V13.md](UPGRADE_REPORT_V13.md).
