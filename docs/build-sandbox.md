# Confining a build's commands (Docker)

A builder's shell commands run on the person's computer unless they ask for confinement. With
`[build] sandbox = "docker"` in `config.toml`, every `build_command_run` runs inside a Docker
container instead, and so does the check's own build when the project's toolchain has an image
(Rust and Node.js today), so `sandbox_unconfined = "refuse"` can verify a build instead of
recording it as `unknown`.

This confines the command, not Docker. The daemon is still privileged and the person still approves
the command, so this is not a defence against a hostile person — it is what stops a build's
generated code from reading their home folder, SSH keys, browser profile or source checkouts, and
from reaching the network when they don't need it.

## What the container gets

| | |
|---|---|
| Mounts | The project folder (`app/`) at `/work`, read-write. The folder is resolved and must stay inside the project; a symlinked `app/` that leads outside is refused. Nothing else is mounted. |
| Working directory | `/work` |
| User | The host's numeric user and group on macOS and Linux; a fixed non-root id (`1000:1000`) on Windows, which has no host id to mirror. Any zero id, uid or gid, also falls back to `1000:1000`, so the container is never root (`0:20` is root: the kernel checks the uid). The project folder must be writable by that user: a folder whose permission bits don't allow it is refused before Docker starts. |
| Image | The tag in `sandbox_image`, or a pinned `sha256` digest when `sandbox_image_digest` is set |
| Storage | The writable layer is unlimited by default; `sandbox_disk_mb` adds Docker's `--storage-opt size`, which needs a storage driver with quota support (not Docker Desktop) |
| Project folder size | `/work` is a bind mount, so Docker can't quota it. `sandbox_project_mb` measures the folder before the build (a folder already past it is refused before Docker starts) and every 250 ms while it runs; a build that grows past it is stopped and the container removed. 0 is no ceiling. A folder with more than 100,000 entries is reported as unknown rather than measured forever, and is not stopped on a guess |
| Capabilities | `--cap-drop ALL`, `--security-opt no-new-privileges` |
| Network | `--network none` unless `sandbox_network` allows it (`bridge`) |
| Environment | Nothing from the host. A check's own build gets `HOME=/tmp` and `CARGO_HOME=/tmp/cargo`, inside the container, because the host id it runs as has no home there |
| Memory | `--memory` and `--memory-swap` at the same value, 4 GiB by default (256 MiB–64 GiB) |
| CPU | `--cpus`, 2 by default (0.1–64) |
| Processes | `--pids-limit`, 512 by default (16–32768) |
| Lifetime | `--rm`, a name and a label, and the container is stopped by name on timeout or cancel |
| Cleanup | A timeout or cancel kills the Docker client, so `--rm` alone would leave the container running; `docker rm --force <name>` stops it |

Out-of-range values are clamped, not ignored: a person who typed `0` gets the floor rather than an
unbounded container.

A folder the sandbox user can't write is refused with a remediation instead of failing inside the
build. That matters most on a root host: it falls back to `1000:1000`, and a root-owned `app/` is
not writable by that id. The permission bits are read, not ACLs, so a folder whose bits say no is
refused even if an ACL would allow it; the error names the folder and says how to change that.

## Settings

```toml
[build]
sandbox = "docker"                  # "off" (default) or "docker"
sandbox_image = "node:22"           # default: debian:bookworm-slim
sandbox_image_digest = "sha256:…"   # optional pin; empty uses the tag
sandbox_disk_mb = 0                 # 0 = Docker's default; needs a quota-capable driver
sandbox_project_mb = 0              # 0 = no ceiling on the mounted project folder
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
| The check's own build (`cargo build`, `npm run build`) | Docker, when the toolchain has an image | Rust and Node.js do (`rust:1.99-slim-bookworm`, `node:22-bookworm`); the build is a program and argv, run without a shell. |
| The check's own build of anything else (an engine's build, a project's own `.venv`) | Nothing | An engine's editor needs the host, a project's own interpreter is not in the image, and the other toolchains have no image yet. |
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
become Ready. A build command and a Rust or Node.js check still run confined; the runs comparison,
an engine's build and a person's own project's build or tests do not run until they can be
confined too. That is the setting to use when confinement matters more than finishing the build.

## The check's own build

`BuildCheck` is the check that decides whether the project compiles. When the sandbox is on and
the project's toolchain has an image in `crates/engine/src/toolchains.toml`, it runs the same
command in the container, with only `app/` mounted at `/work`:

| Toolchain | Image | What runs |
|---|---|---|
| Rust (`Cargo.toml`) | `rust:1.99-slim-bookworm` | `cargo build --quiet` |
| Node.js (`package.json`) | `node:22-bookworm` | `npm run build` |

The image is a fixed tag, not `latest`; pinning it by digest is on the roadmap. A project's own
program comes first: a `.venv/bin/python` or a `mvnw` wrapper is not in the image, so a build that
would use one runs on this computer (and is refused under `refuse`). A static site has nothing to
run, so it passes without a container either way.

A build that ran records a receipt on its `CheckResult`: `runner` (`host` or `container`), the
image, the command, the exit code and a SHA-256 of the combined output. The note says where it ran
too ("It ran in a Docker container (`rust:1.99-slim-bookworm`)."), so an allowed unconfined check
is never mistaken for a confined one.

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
- It does not confine a **person's own project's** build or tests, or a toolchain without an image
  (Python, Go, .NET, Maven, Gradle, Make). Those run on this computer and are refused under
  `refuse`.
- It does not confine `native_window_capture`, screenshots, self-tests or the game probe, which
  launch the built program to look at it. Those need a display and a binary native to this computer,
  and are a separate decision. The policy refuses the screenshot, self-test and probe routes under
  `refuse`; a direct `native_window_capture` call is classified at its tool boundary too.
- It does not prove the image's toolchain is the same version as the person's; a project that needs
  a newer compiler than the image has fails the check, which says so in the build's output.
- It does not make the network safe when `sandbox_network` is on: a build can reach whatever the
  container can.
- It is not a substitute for approvals. Every command is still approved through
  `interaction_tools::approve` before it runs.

## Checking it

The argv construction is the security boundary and is unit-tested without Docker: exactly one
mount, the network flag, the capability and privilege flags, a non-root user, clamping, and the
command as the last argument (a shell line for a build command, a program and argv for a check). A
stopped daemon (the `docker` client exits non-zero with its connection message) is mapped to the
same plain error (`cargo test -p craft-anything-platform sandbox`). The user mapping and the folder
check are unit-tested too: `writable_by_reads_the_owner_group_and_other_bits` covers the owner,
group and other bits, and `a_folder_the_sandbox_user_cannot_write_is_refused` shows a read-only
folder is refused before Docker starts.

The policy and the check are tested without Docker too:

```sh
cargo test -p craft-anything-ai a_static_site_passes_under_strict_without_running_anything
cargo test -p craft-anything-ai a_toolchain_without_an_image_is_refused_under_strict
```

The first shows a page with nothing to build passes under `refuse` without running anything; the
second shows a Makefile project is recorded `unknown` rather than quietly built on this computer.

The cleanup path, the container's identity and the check's container build are tested live against
Docker when asked:

```sh
cargo test -p craft-anything-platform a_timed_out_command_leaves_no_container_running -- --ignored
cargo test -p craft-anything-platform the_container_runs_as_the_host_user_not_root -- --ignored
cargo test -p craft-anything-ai a_strict_check_builds_a_rust_project_in_the_container -- --ignored
```

The first runs `sleep 300` with a one-second deadline and then checks that no container carrying
the sandbox label is still running. The second asserts the container's uid and gid equal the
`--user` value (a root host's `1000:1000` fallback, not the host's id), that it isn't root, and
that it can write `/work`; it passed on macOS (Docker 29.9.3) as `uid=501 gid=20`, and a root-host
run is still owed. The third builds a Rust console app under
`sandbox_unconfined = "refuse"` and asserts the check passed with a `container` receipt, the image
and exit code 0, and that `target/` is in the project afterwards.
