# Craft Anything v0.4.0 — read-only MCP tool integration V13

V13 is an independently packaged **full source fork**, derived from the
verified V12 ZIP. It retains all V12 source files, earlier upgrade reports
and the original Rust workspace version (`0.4.0`). This is not an official
release or a claim that the desktop application runs.

## What changed

1. **One real tool-invocation path in native CI:** V12 already compiled the
   CLI (when an external CI runner is available) and validated MCP
   initialization, listing, ping and protocol errors. V13 additionally
   sends exactly one JSON-RPC `tools/call` to `core_session_show` with `{}`
   arguments. The underlying Rust tool is declared read-only, does not
   require a target, and only reports session metadata.
2. **Strict result/state invariants:** The new validator requires a
   successful result, JSON `structuredContent` matching its text content,
   no open or active target, an empty target list, no project, empty evidence
   and unknown counters, consistent Deep-mode flags, correct server version
   and a nonempty host identifier. These checks make a false success fail
   the gate. Other tools are never called.
3. **Asynchronous response handling:** The MCP server dispatches tool
   calls on worker threads. The protocol parser accepts replies in any
   order but requires exactly one typed response for each request ID and
   no response to the initialized notification.
4. **Export remains fail-closed:** The independent Ubuntu/macOS/Windows
   native CLI matrix now refuses unsigned artifact export without the
   validated read-only call evidence. Passing artifacts record the public
   tool ID and a Boolean that the isolated session was empty, alongside
   V12's protocol, registry and executable integrity metadata.
5. **Negative-case regression coverage:** Added synthetic test mutations
   for a tool error, missing or malformed JSON text, mismatched text/typed
   output, unexpected targets or projects, nonzero session counters,
   conflicting mode, incorrect server identity or invalid host. Matrix
   tests reject missing or forged read-only tool evidence.

## Verification and limitations

The packaging process reruns Python unit tests, produces a fresh source
inventory, verifies ZIP CRCs and V12 member retention, then extracts the
ZIP to a new directory and runs the tests again. The delivery response
reports the exact counts and any failures.

The packaging environment still has **no Cargo/rustc**. None of the above
native Rust/MCP executions was run here; subprocess fixture tests use
controlled Python programs. To obtain real evidence, transfer the source
into an authorized repository and manually dispatch **Independent Fork
Native CLI Matrix** on Ubuntu, macOS and Windows. Every platform must
report a passing `mcp_smoke` and `artifact_export`. The broader GUI,
reverse-engineering tools, approvals, game reconstruction, device access
and production signing remain unverified and require separate work.

See [docs/native-validation.md](docs/native-validation.md) and
[UPGRADE_REPORT_V12.md](UPGRADE_REPORT_V12.md).
