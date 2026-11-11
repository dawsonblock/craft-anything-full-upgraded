# Craft Anything v0.4.0 — native MCP protocol qualification V12

V12 is a **complete independently packaged source fork**, derived from the
verified V11 archive. It retains V11's sources and upgrade history, and does
not claim official authorship, signed releases or demonstrated app behavior.
The Rust workspace version stays `0.4.0`.

## Why this step matters

V11 verified only native CLI help and two metadata listings *when run by CI*.
A working command registry does not establish that a separate MCP client can
initialize a server, parse its JSON-RPC stream, enumerate the same tools or
receive protocol errors correctly. V12 adds a real native MCP server smoke
to the future CI gate, while avoiding actual tool execution.

## Changes

1. **Actual MCP JSON-RPC process probe.** New
   `scripts/mcp_protocol_smoke.py` launches the compiled CLI's
   `--deep mcp --all-tools` mode with a disposable home/config directory,
   allowlisted environment and closed-after-request stdin. `--deep` is
   used only to enumerate all registered tools. It sends initialization,
   an initialized notification, `tools/list`, `ping` and an intentionally
   unsupported method. It never sends `tools/call` or opens a target.
2. **Protocol checks.** Response envelopes, distinct IDs, negotiated
   protocol version, server name/version, supported tool capability, ping
   and the required `-32601` method-not-found response are checked. A
   notification must not produce a response.
3. **CLI/MCP contract reconciliation.** V11's registry validator now
   also computes a canonical SHA-256 over sorted tuples of tool ID,
   title and summary. The MCP validator checks count, unique names,
   nonempty descriptions, schemas and typed annotations, then requires
   the exact same contract digest from `tools/list`.
4. **Bounded subprocess and privacy.** MCP stdout and stderr are drained
   independently to keep diagnostics from corrupting JSON-RPC output.
   One tool-list reply can occupy multiple megabytes on a single line,
   so the smoke accepts up to 24 MB stdout and 256 KB stderr. Both streams
   are bounded while running; output overflow, timeout, failure to launch,
   bad JSON or inconsistency fail closed. Each native run has a maximum
   60-second deadline. No raw output, tool schemas or local configuration
   is stored in the CI receipt.
5. **Export gate and provenance.** The manual Ubuntu/macOS/Windows workflow
   now requires `mcp_smoke` after the help/registry/capabilities checks,
   followed by another source-integrity verification. All successful
   receipts must refer to the **same unmodified executable**. The unsigned
   artifact's `CLI_PROVENANCE.json` records the cross-CLI/MCP contract
   digest, tool count and negotiated MCP protocol version. A failing
   MCP check blocks binary export.
6. **Adversarial synthetic tests.** Parser tests cover malformed or
   mismatched JSON-RPC, protocol negotiation, duplicate/unknown IDs,
   incorrect tool metadata, invalid schemas, failed pings and missing
   negative-method rejection. Process tests exercise concurrent pipe
   draining, multi-megabyte single-line responses, timeout, output caps,
   environment isolation, redaction and binary mutation. CI gate tests
   confirm MCP failures prevent unsigned binary export.

## Verification and outstanding work

The packaging response records the Python test count, source manifest,
archive CRC, V11 file retention and extracted-ZIP test replay results.

**The packaging host has no Rust compiler or Cargo.** No compiled MCP
server was launched here. The subprocess fixtures are controlled Python
programs, and they cannot substitute for actual Rust initialization or
MCP interoperability on Windows/macOS/Linux. To perform those checks,
unpack the source into an authorized fork and manually dispatch
**Actions → Independent Fork Native CLI Matrix**. Inspect every OS receipt
for `cli_build`, `cli_smoke`, `registry_smoke`, `mcp_smoke` and
`artifact_export` passing. Do not ship unsigned CI test executables as
releases; GUI, consent, integration, device and real target behavior still
need separate testing.

See [docs/native-validation.md](docs/native-validation.md) for the exact
standalone command and [UPGRADE_REPORT_V11.md](UPGRADE_REPORT_V11.md) for
the earlier cross-view metadata checks.
