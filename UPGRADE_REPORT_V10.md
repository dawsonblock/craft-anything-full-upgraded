# Craft Anything v0.4.0 — native tool-registry smoke upgrade V10

V10 is an **independent, complete source ZIP** derived from V9, not a signed
release or a compiled desktop application. The Rust workspace version is
still 0.4.0. All V9 sources and earlier upgrade reports are retained.

## What is new

1. **Real CLI initialization smoke.** The manually dispatched Ubuntu, macOS
   and Windows native CLI workflow now attempts both `--help` (V8–V9) and
   `commands`. The latter creates the real application engine, loads its
   tool registry and emits the registered commands as JSON without invoking
   an analysis tool, opening a target or granting execution consent.
2. **Fail-closed registry contract.** `scripts/registry_smoke.py` requires a
   valid JSON array with at least 200 commands from at least 3 toolsets,
   nonempty typed metadata and unique tool IDs/command names. It records
   a canonical registry SHA-256 and observed counts, but excludes the raw
   command registry from the persisted CI receipt.
3. **Isolated run configuration.** The metadata launch uses a disposable
   configuration/home directory and an allowlisted subprocess environment.
   User API keys, GitHub tokens and unrelated environment variables are
   not forwarded to the executable. The process remains time- and
   output-bounded (up to 60 seconds and 2 MB, respectively).
4. **No export on partial success.** A green `--help` result no longer allows
   artifact export if real registry initialization fails, produces malformed
   data, times out or changes the executable. The binary digest must agree
   across both smokes and the final export copy.
5. **Build-to-registry provenance.** A passing binary artifact's
   `CLI_PROVENANCE.json` now records the validated command count, toolset
   count and registry digest alongside its own SHA-256 and the source
   manifest digest.
6. **Expanded synthetic checks.** Added parsers, process-envelope, process
   isolation, registry validation, binary-mutation and failure-gating tests;
   V9's previous source and ZIP checks remain.

## Verified here

- The Python regression suite and full extracted-archive replay are run during
  packaging, with test counts reported in the delivery response.
- Source SHA-256 inventory, ZIP CRCs and retention of all V9 files are checked.
- The native runner reports **blocked**, not passed, when `cargo`/`rustc` are
  unavailable. This environment has no Rust toolchain.

## Still unverified

- **No actual V10 native CLI compile or `commands` execution has run here.**
  The three-OS GitHub workflow must be triggered in an authorized repository.
- A registry listing is a broader smoke than `--help`, but is still **not**
  functional verification of the tools, MCP, GUI, targets, access controls,
  permission dialogs, game workflows or platform installation.
- Neither the source manifest nor the unsigned CI binaries authenticate the
  publisher. Review provenance and follow normal signing/notarization policy.

## How to finish native testing

1. Commit the complete extracted source to your authorized fork.
2. Run **Actions → Independent Fork Native CLI Matrix → Run workflow**.
3. Inspect each platform's `native-cli-receipt` artifact; it must show
   `cli_build`, `cli_smoke`, `registry_smoke` and `artifact_export` as passed.
4. Review the unsigned binary's provenance and run the separate desktop,
   integration, authorization and release tests before considering shipment.

See [docs/native-validation.md](docs/native-validation.md) for command-line
instructions and [UPGRADE_REPORT_V9.md](UPGRADE_REPORT_V9.md) for the previous
matrix architecture.
