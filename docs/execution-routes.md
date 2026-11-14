# Execution routes

Every place Craft Anything starts a program, what it starts, and whether the strict build policy
(`[build] sandbox_unconfined = "refuse"`) stops it. This is the R3.0 PR 1 inventory; PR 2 replaces
the hand-kept checks with a mandatory execution context at the process boundary.

## Scope

"Generated code" means what a build made: the app, game or program under `app/`, a rebuilt website,
and anything a coding agent runs on its own. The policy is `crates/engine/src/execution.rs`:
`Route` declares what confines a route, `Policy::check` refuses a host-only route under `refuse`,
and `Policy::outside` gives the reason a caller shows under `allow`.

The platform runner is `crates/platform/src/process.rs`: stdin is closed or fed from bytes we own,
every run has a deadline, output is capped and drained, and the process group is killed on timeout,
cancel or drop. `crates/platform/src/sandbox.rs` adds Docker confinement. Neither knows what the
program is for; classification happens above them.

## How this was collected

```sh
rg -n "Process::new" crates apps -g '*.rs'
rg -n "Launch::new|start_watched|Launched" crates apps -g '*.rs'
rg -n "std::process::Command|Command::new" crates apps -g '*.rs'
rg -n "open_path\(|keep_awake\(" crates apps -g '*.rs'
```

The searches find 58 `Process::new` call sites outside the platform runner, sandbox and capture
service (a few are in test modules); 16 `Launch`/`Launched` uses; and 11 `open_path`/`keep_awake`
calls.
Production code reaches `std::process::Command` only through `platform::process`; the direct uses
found by the third search are tests (`apple_runtime.rs`'s Windows fixture and the Grok live check).

## Covered by `engine::execution::Policy`

| Route | Entry point | What runs | Confinement | Checked by |
|---|---|---|---|---|
| `build_command_run` | `tools/build_tools.rs` | A shell command the builder proposed | Docker (`platform::sandbox`) | `TOOL_ROUTES` in `Engine::call` |
| `build_probe_run` | `tools/build_tools.rs` | The independent replay probe and the built game | Host-only | `TOOL_ROUTES` in `Engine::call` |
| `BuildCheck` | `rebuild/kinds/mod.rs` | `cargo build`, `npm run build`, an engine's build | Host-only | `Policy::check` in the check |
| `RunsCheck` | `rebuild/kinds/runs.rs` | The built console program with the original's inputs | Host-only | `Policy::check` in the check |
| `OwnBuildCheck`, `OwnTestsCheck` | `rebuild/kinds/own.rs` | A person's own project's build and tests | Host-only | `Policy::check` in the checks |
| `ScreenshotCheck`, `SelfTestCheck` | `rebuild/kinds/screenshot.rs`, `self_test.rs` | The built program with `--screenshot` or `--self-test` | Host-only | `Policy::check` in `run_with` |
| `Route::Agent` | `rebuild/mod.rs` | A coding agent that builds the project | Host-only | `Policy::check` in `RebuildRun::start` |

Under `refuse` a covered check records `unknown` with the reason; a covered tool call returns
`FeatureMissing` with `details.reason = "sandbox_unconfined"`; a covered agent build does not start.

## Uncovered generated-code routes

These start generated code but do not consult the policy. The first two have ignored regression
tests that assert the desired refusal; run them to see the gap.

| Route | Code | What it starts | Why it is a gap | Test |
|---|---|---|---|---|
| `runtime_walkthrough_record` | `tools/process_tools.rs` | A program under a PTY with scripted input | Not in `TOOL_ROUTES`; clean-room allowed; a builder agent can call it directly | `a_walkthrough_is_refused_when_the_sandbox_refuses_unconfined_runs` (ignored) |
| `project_play` | `tools/project_tools.rs` → `pin_tools::start` → `pins::RunList`/`Launch` | The built app or game | Not in `TOOL_ROUTES`; starts the program through `Launch`, which the policy does not see | `playing_a_build_is_refused_when_the_sandbox_refuses_unconfined_runs` (ignored) |
| `project_pin_open` | `tools/pin_tools.rs` → `pins::RunList`/`Launch` | A pinned copy of the built program | Same as `project_play` | none (needs a pin) |
| `native_window_capture` | `tools/window_tools.rs` | A program, to picture its window | Not in `TOOL_ROUTES`; used by Study and the screenshot check, and callable directly | none (needs a GUI) |
| `web_site_open`, `web_site_capture`, `web_page_states_capture`, `web_page_screenshot_take`, `web_walkthrough_check` | `tools/browser_tools.rs`, `web_common.rs` | A browser on the built website | Not in `TOOL_ROUTES`; the site's JavaScript runs | none (needs Chrome) |
| `electron_app_*` | `tools/electron_runtime_tools.rs` → `web_common::Browser` | The built Electron app | Not in `TOOL_ROUTES`; starts the app's own executable | none (needs Electron) |
| A chat or question with a coding agent | `AiHub::start`, `start_role`, `cli_agents` | The agent CLI | Only `RebuildRun::start` checks `Route::Agent`; a chat/question does not | none (needs an agent) |
| Any tool above, called directly by a builder agent | `Engine::call` | Whatever the tool starts | The check is per-tool, not at the process boundary; an agent can name `app/`'s program | the two ignored tests |

`project_preview` is not in this table: it serves the built site from an in-process static server,
and the browser that opens the URL is the person's. The built site's JavaScript still runs in that
browser, so the browser route above is the one that matters.

`project_open_folder` opens the OS file manager; it does not run the build's code.

## Other process launches (not generated code)

These start external programs, not the build's code. They still need classification so a new tool
cannot launch a program without anyone deciding what it is.

| Area | Routes | What runs | Current handling |
|---|---|---|---|
| Target analysis | `apple_calls_observe`, `gdb_*`, `rizin_*`, `frida_*`, `adb_*`, `packet_capture_*`, `native_disassemble_external`, the Ghidra bridge, jadx/apktool and the MCP client | lldb, gdb, rizin, Frida, adb, tshark, objdump and friends, Ghidra, jadx, an MCP server | Effects and consent; no sandbox policy |
| Installers | `install_tools`, `engines_install`, `engines::game` | Vendor installers, `msiexec`, PowerShell | Effects and consent; no sandbox policy |
| Agents | `cli_agents` adapters, `ssh.rs` | The agent CLI, over SSH when configured | `Route::Agent` only in `RebuildRun::start`; chat/question uncovered |
| Housekeeping | `keep_awake`, `open_path`, `setup::doctor`, `cli_agents` version/model checks, `window_tools::process_tree` | caffeinate/PowerShell, the file manager, `--version`, `ps`/PowerShell | No policy; not generated code |
| Platform services | `platform::capture` (used by `runtime_walkthrough_record` and `native_window_capture`), `platform::process` (the runner), `platform::sandbox` (Docker) | The program the caller names | The caller classifies; capture and process do not |

## The central controller gap

The policy is checked where a tool, check or run starts, not where a program starts:

- `TOOL_ROUTES` in `execution.rs` is a hand-kept list of two tools. The checks call
  `Policy::check` by hand. A new tool, or a direct call from an agent, can miss both.
- `Process::new` and `Launch::new` are called directly from more than 70 places. There is no
  execution context argument, so the runner cannot tell a build command from a window capture.
- `native_window_capture` and `runtime_walkthrough_record` are Clean-room tools that run a program
  the agent names. `project_play` and pins start the built program. A chat with an agent starts the
  agent. Under `refuse`, none of them is stopped today.

PR 2 closes this with one mandatory `ExecutionContext` at the process boundary:

1. Every launch names a route and what it starts (generated code, a target tool, an installer, an
   agent, housekeeping).
2. The context asks `Policy` before `spawn`. Under `refuse`, an unclassified or host-only generated
   route fails with the same `FeatureMissing` error, before the program starts.
3. The route inventory above is the acceptance list: each uncovered generated-code row either moves
   into the controller or is declared host-only with a reason and a test.
4. The two ignored tests become ordinary passing tests; a new launch without a route fails a test
   or a compile-time check.

## Regression tests

```sh
cargo test -p craft-anything-engine a_walkthrough_is_refused_when_the_sandbox_refuses_unconfined_runs -- --ignored
cargo test -p craft-anything-engine playing_a_build_is_refused_when_the_sandbox_refuses_unconfined_runs -- --ignored
```

Both assert the secure behavior and fail today, which is the point: they demonstrate that
`sandbox_unconfined = "refuse"` does not reach `runtime_walkthrough_record` or `project_play`.
PR 2 removes the `#[ignore]` once the controller checks them.
