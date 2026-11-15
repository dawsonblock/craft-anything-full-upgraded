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

## Generated-code routes the controller classifies

These start generated code and were outside the check/build loop before PR 2. The central
execution controller now classes each one at its tool or agent boundary, and `Process` checks the
policy before spawning; under `refuse` they do not start.

| Route | Code | What it starts | Class | Test |
|---|---|---|---|---|
| `runtime_walkthrough_record` | `tools/process_tools.rs` | A program under a PTY with scripted input | `Generated("runtime-walkthrough")` | `a_walkthrough_is_refused_when_the_sandbox_refuses_unconfined_runs` |
| `project_play` | `tools/project_tools.rs` → `pin_tools::start` → `pins::RunList`/`Launch` | The built app or game | `Generated("project-play")` | `playing_a_build_is_refused_when_the_sandbox_refuses_unconfined_runs` |
| `project_pin_open` | `tools/pin_tools.rs` → `pins::RunList`/`Launch` | A pinned copy of the built program | `Generated("project-play")` | none (needs a pin) |
| `native_window_capture` | `tools/window_tools.rs` | A program, to picture its window | `Generated("native-window-capture")` | none (needs a GUI) |
| `web_site_open`, `web_site_capture`, `web_page_show`, `web_page_states_capture`, `web_page_screenshot_take`, `web_walkthrough_check` | `tools/browser_tools.rs`, `web_common.rs` | A browser on the built website | `Generated("website")` | none (needs Chrome) |
| `electron_app_*` | `tools/electron_runtime_tools.rs` → `web_common::Browser` | The built Electron app | `Generated("electron")` | none (needs Electron) |
| A chat or question with a coding agent | `AiHub::start`, `start_role`, `cli_agents` | The agent CLI | `Agent` | none (needs an agent) |
| Any tool above, called directly by a builder agent | `Engine::call` | Whatever the tool starts | `tool_class` | the two tests above |

`project_preview` is not in this table: it serves the built site from an in-process static server,
and the browser that opens the URL is the person's. The built site's JavaScript still runs in that
browser, so the browser route above is the one that matters.

`project_open_folder` opens the OS file manager; it does not run the build's code.

## Other process launches (not generated code)

These start external programs, not the build's code. They are classified too, so a new tool cannot
launch a program without anyone deciding what it is.

| Area | Routes | What runs | Class |
|---|---|---|---|
| Target analysis | `apple_calls_observe`, `gdb_*`, `rizin_*`, `frida_*`, `adb_*`, `packet_capture_*`, `native_disassemble_external`, the Ghidra bridge, jadx/apktool and the MCP client | lldb, gdb, rizin, Frida, adb, tshark, objdump and friends, Ghidra, jadx, an MCP server | `TargetTool` |
| Installers | `install_tools`, `engines_install`, `engines::game` | Vendor installers, `msiexec`, PowerShell | `Installer` |
| Agents | `cli_agents` adapters, `ssh.rs` | The agent CLI, over SSH when configured | `Agent` |
| Housekeeping | `keep_awake`, `open_path`, `setup::doctor`, `cli_agents` version/model checks, `window_tools::process_tree` | caffeinate/PowerShell, the file manager, `--version`, `ps`/PowerShell | `Housekeeping` |
| Platform services | `platform::capture` (used by `runtime_walkthrough_record` and `native_window_capture`), `platform::process` (the runner), `platform::sandbox` (Docker) | The program the caller names | The caller's class; the runner checks it |

## The central controller

PR 2 landed the controller:

- `platform::execution` holds the thread-local context: a `Policy` and a default `Class`.
  `with_policy` installs it and restores the previous one when the call returns, so one tool's
  class cannot leak into the next.
- `Process::spawn` and `Process::start_watched` call `execution::check()` before they create the
  child. A launch with no context is allowed (tests, setup); under a policy, an `Unclassified` or
  host-only generated class fails with `FeatureMissing` before the program starts.
- `Engine::call` installs a `BuildPolicy` around every tool with `tool_class`, and pre-checks a
  `Generated` route before the tool runs, so a refusal does not show an approval card first.
- `RebuildRun::start` installs the policy on the run's worker with `Class::Agent`; `run_checks`
  narrows it per check with `check_class` (an own project's `build` is `own-check`).
- `AiHub::start` installs it around an agent chat, so a chat or question is held to `Route::Agent`.
- `TOOL_ROUTES` is gone. `tool_class` is the one table at the tool boundary; a tool not in it is
  `Unclassified`, and a program it starts fails under `refuse`.

## Regression tests

```sh
cargo test -p craft-anything-engine a_walkthrough_is_refused_when_the_sandbox_refuses_unconfined_runs
cargo test -p craft-anything-engine playing_a_build_is_refused_when_the_sandbox_refuses_unconfined_runs
```

Both assert the secure behavior and pass: `sandbox_unconfined = "refuse"` stops
`runtime_walkthrough_record` and `project_play` before the program starts.
