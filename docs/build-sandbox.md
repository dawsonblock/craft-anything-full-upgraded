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
| Mounts | The project folder (`app/`) at `/work`, read-write. Nothing else is mounted. |
| Working directory | `/work` |
| User | The host's numeric user and group, so files the build writes stay the person's, not root's |
| Capabilities | `--cap-drop ALL`, `--security-opt no-new-privileges` |
| Network | `--network none` unless `sandbox_network` allows it (`bridge`) |
| Memory | `--memory` and `--memory-swap` at the same value, 4 GiB by default (256 MiB–64 GiB) |
| CPU | `--cpus`, 2 by default (0.1–64) |
| Processes | `--pids-limit`, 512 by default (16–32768) |
| Lifetime | `--rm`, and the run is killed on timeout or cancel like any other external program |

Out-of-range values are clamped, not ignored: a person who typed `0` gets the floor rather than an
unbounded container.

## Settings

```toml
[build]
sandbox = "docker"                  # "off" (default) or "docker"
sandbox_image = "node:22"           # default: debian:bookworm-slim
sandbox_network = true              # default: true; package installs need it
```

`debian:bookworm-slim` has a POSIX shell and nothing else, so a build that needs Node, Rust or
Python must name an image that has that toolchain. `sandbox_network = false` is for builds that
only compile what the project already contains.

## When Docker isn't there

The run fails with a plain error and a remediation:

> Docker isn't running, so the build can't run sandboxed. Start Docker Desktop (or install Docker)
> and try again, or turn the sandbox off in Settings → Build.

A bad image name, or a project path containing `:` (which Docker reads as a volume separator), is
refused before anything starts.

## What this does not do

- It does not sandbox the **coding agent** itself. Claude Code, Codex, Gemini CLI and the others
  run as their own processes with their own tools; this only wraps the shell commands our builder
  hands to `build_command_run`.
- It does not confine `native_window_capture`, screenshots, self-tests or the game probe, which
  launch the built program to look at it. Those need a display and are a separate decision.
- It does not make the network safe when `sandbox_network` is on: a build can reach whatever the
  container can.
- It is not a substitute for approvals. Every command is still approved through
  `interaction_tools::approve` before it runs.

## Checking it

The argv construction is the security boundary and is unit-tested without Docker: exactly one
mount, the network flag, the capability and privilege flags, clamping, and the command as the last
argument (`cargo test -p craft-anything-platform sandbox`).
