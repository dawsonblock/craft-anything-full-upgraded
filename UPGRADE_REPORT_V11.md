# Craft Anything v0.4.0 — native capabilities/registry consistency V11

V11 is a **complete, independently packaged source fork** of the V10 archive.
It does not claim upstream authorship, a published binary, platform support
or a verified desktop app. The workspace Rust version remains `0.4.0`.
All V10 source files and previous upgrade reports are retained.

## New engineering work

1. **Cross-view live metadata smoke:** After the real native CLI passes
   `--help` and `commands` in the manual Ubuntu/macOS/Windows CI matrix,
   `scripts/registry_smoke.py` now also invokes the compiled CLI's
   `capabilities` command. Both commands share the same private disposable
   configuration directory and strictly allowlisted environment. No
   registered analysis tool is invoked and no target file is opened.
2. **Cross-view tool invariants:** The validator rejects a mismatched total
   number of tools, missing or duplicate tool IDs, extra IDs, assignments
   to the wrong toolset, contradictory `needs_target` flags, malformed
   availability metadata, invalid toolset records and duplicate engine IDs.
   Typed CLI version, host, analysis mode, toolsets and engine data are also
   required. Empty published toolsets are allowed; every actual tool must
   belong to exactly one toolset and appear in both metadata views.
3. **Bounded and private execution:** Each metadata subprocess has a
   60-second ceiling and 2 MB combined output ceiling. Both execute with
   a private home/config directory, receive no project credentials, and
   use closed stdin. The raw command/capability JSON and output excerpts
   are discarded rather than written to CI receipts. Capabilities can
   contain local engine paths, so retaining the raw JSON would be unsafe.
4. **Additional evidence:** The report now includes `registry_sha256`,
   `capabilities_sha256`, number of commands, registry and capabilities
   toolset counts, capability engine count and CLI version. SHA-256 digests
   are computed from canonical JSON. The binary is hashed before and after
   metadata execution, and a mismatch fails the smoke.
5. **Fail-closed unsigned artifact export:** `scripts/native_cli_matrix.py`
   now rejects missing/malformed capability digests, invalid counts or a
   missing CLI version before copying an executable. The unsigned test
   artifact's `CLI_PROVENANCE.json` includes both metadata digests and counts,
   the CLI version, source manifest hash and binary hash.
6. **More adversarial synthetic tests:** Added checks for valid/canonical
   capability output, malformed payloads, missing/duplicate/extra tools,
   wrong toolsets, target-flag disagreements, malformed availability or
   engine metadata, second-command timeout, JSON parsing failure, shared
   environment, redaction and missing export evidence.

## How to validate this fork

1. Extract the ZIP with `scripts/source_bundle_integrity.py extract-zip`
   as described in [docs/source-bundle-integrity.md](docs/source-bundle-integrity.md).
2. Run `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`.
3. On a machine with Rust 1.97+, run
   `python3 scripts/native_cli_matrix.py --root . --timeout 1800 --report ../native_cli_receipt.json --artifact-dir ../native_cli_artifact`.
4. In an authorized fork, manually dispatch
   **Actions → Independent Fork Native CLI Matrix** to run the check on
   Ubuntu, macOS and Windows. The CI uploads receipts even if checks fail;
   unsigned binaries appear only after all gates pass.

## Evidence vs. remaining work

The ZIP is verifiable through the source inventory, its CRCs, replayed
Python unit tests and extracted-tree integrity check. The final packaging
receipt accompanying this archive records those results.

**This environment has no `cargo` or `rustc`.** The new CLI `capabilities`
check has *synthetic Python coverage* but has **not been exercised against a
compiled CLI** on any OS here. The three-platform matrix remains a task for
the user or an authorized CI runner. No actual tool invocation, MCP,
desktop GUI, authorization prompt, target analysis, game behavior or
integration testing is established by this metadata check.

The source inventory and unsigned test executables are not publisher
signatures, and a green metadata smoke is not a release qualification.
