# Confining a build's commands (Docker)

A builder's shell commands run on the person's computer unless they ask for confinement. With
`[build] sandbox = "docker"` in `config.toml`, every `build_command_run` runs inside a Docker
container instead.

This confines the command, not Docker. The daemon is still privileged and the person still approves
the command, so this is not a defence against a hostile person — it is what stops a build's
generated code from reading their home folder, SSH keys, browser profile or source checkouts, and
from reaching the network when they don't need it.

## What the container gets

| | |
|---|---|
| Mounts | The project folder (`app/`) at `/work`, read-write. The folder is resolved and must stay inside the project; a symlinked `app/` that leads outside is refused. Nothing else is mounted. |
| Working directory | `/work` |
| User | The host's numeric user and group on macOS and Linux; a fixed non-root id (`1000:1000`) on Windows, which has no host id to mirror. A root host (`0:0`) also falls back to `1000:1000`, so the container is never root. |
| Image | The tag in `sandbox_image`, or a pinned `sha256` digest when `sandbox_image_digest` is set |
| Storage | The writable layer is unlimited by default; `sandbox_disk_mb` adds Docker's `--storage-opt size`, which needs a storage driver with quota support (not Docker Desktop) |
| Capabilities | `--cap-drop ALL`, `--security-opt no-new-privileges` |
| Network | `--network none` unless `sandbox_network` allows it (`bridge`) |
| Memory | `--memory` and `--memory-swap` at the same value, 4 GiB by default (256 MiB–64 GiB) |
| CPU | `--cpus`, 2 by default (0.1–64) |
| Processes | `--pids-limit`, 512 by default (16–32768) |
| Lifetime | `--rm`, a name and a label, and the container is stopped by name on timeout or cancel |
| Cleanup | A timeout or cancel kills the Docker client, so `--rm` alone would leave the container running; `docker rm --force <name>` stops it |

Out-of-range values are clamped, not ignored: a person who typed `0` gets the floor rather than an
unbounded container.

## Settings

```toml
[build]
sandbox = "docker"                  # "off" (default) or "docker"
sandbox_image = "node:22"           # default: debian:bookworm-slim
sandbox_image_digest = "sha256:…"   # optional pin; empty uses the tag
sandbox_disk_mb = 0                 # 0 = Docker's default; needs a quota-capable driver
sandbox_network = true              # default: true; package installs need it
sandbox_unconfined = "allow"        # default: "allow"; "refuse" stops routes that can't be confined
```

`debian:bookworm-slim` has a POSIX shell and nothing else, so a build that needs Node, Rust or
Python must name an image that has that toolchain. `sandbox_network = false` is for builds that
only compile what the project already contains.

## One boundary for every route

`build_command_run` is not the only way a build's code runs. Each route declares what confines it
(`crates/engine/src/execution.rs`), and the policy decides whether it may run:

| Route | Confined by | Why |
|---|---|---|
| `build_command_run` | Docker | It is a shell command and sees only the project folder. |
| The check's own build (`cargo build`, `npm run build`, an engine's build) | Nothing | It builds with the project's own toolchain on this computer; the image may not have it, and an engine's editor needs the host. |
| The runs comparison (`app-runs`) | Nothing | It starts the built console program on this computer. |
| A person's own project's build or tests (`OwnBuildCheck`/`OwnTestsCheck`) | Nothing | Its own toolchain runs on this computer. |
| `build_probe_run` | Nothing | The built game is native to this computer, so it can't run in a Linux container. |
| A build's screenshot or self-test | Nothing | The built program is native to this computer and opens a window. |
| A coding agent (Claude Code, Codex, …) | Nothing | The agent runs its own tools and signs in as the person. |

With `sandbox_unconfined = "allow"` (the default), a route that can't be confined still runs, and
its approval card or result says it runs on this computer, outside the Docker sandbox: a check's
note, the probe's approval card, and `rebuild_start`'s result (the CLI prints it too). The UI shows
it as a feed note; the sandbox is `config.toml` only, and putting it in Settings → Build is on the
roadmap.

With `sandbox_unconfined = "refuse"`, those routes are refused before they start. A tool call gets a
plain error that names the setting; a check records `unknown` with the reason, so the build cannot
become Ready. A build command still runs confined, but the check's own build, the runs comparison
and a person's own project's build or tests do not run until they can be confined too. That is the
setting to use when confinement matters more than finishing the build.

## When Docker isn't there

The run fails with a plain error and a remediation:

> Docker isn't running, so the build can't run sandboxed. Start Docker Desktop (or install Docker)
> and try again, or turn the sandbox off in Settings → Build.

A bad image name, or a project path containing `:` (which Docker reads as a volume separator), is
refused before anything starts.

## What this does not do

- It does not sandbox the **coding agent** itself. Claude Code, Codex, Gemini CLI and the others
  run as their own processes with their own tools; `sandbox_unconfined = "refuse"` stops a build
  with one instead of pretending to confine it.
- It does not confine `native_window_capture`, screenshots, self-tests or the game probe, which
  launch the built program to look at it. Those need a display and a binary native to this computer,
  and are a separate decision. The policy refuses the screenshot, self-test and probe routes under
  `refuse`; a direct `native_window_capture` call is not covered.
- It does not make the network safe when `sandbox_network` is on: a build can reach whatever the
  container can.
- It is not a substitute for approvals. Every command is still approved through
  `interaction_tools::approve` before it runs.

## Checking it

The argv construction is the security boundary and is unit-tested without Docker: exactly one
mount, the network flag, the capability and privilege flags, a non-root user, clamping, and the
command as the last argument. A stopped daemon (the `docker` client exits non-zero with its
connection message) is mapped to the same plain error (`cargo test -p craft-anything-platform
sandbox`).

The cleanup path is tested live against Docker when asked:

```sh
cargo test -p craft-anything-platform a_timed_out_command_leaves_no_container_running -- --ignored
```

It runs `sleep 300` with a one-second deadline and then checks that no container carrying the
sandbox label is still running.
