<p align="center">
  <a href="https://getartcraft.com/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/brand/artcraft-logo-white.svg">
      <img alt="ArtCraft" src="docs/brand/artcraft-logo.svg" width="200">
    </picture>
  </a>
</p>

<p align="center">
  <img src="assets/app-icon/hicolor/256x256/apps/ai.storyteller.craft-anything.png" width="128" height="128" alt="Craft Anything icon: an engraved raven on a blue tile">
</p>

<h1 align="center">Craft Anything</h1>

> [!IMPORTANT]
> This archive is an **independent hardened source snapshot**, not an official ArtCraft release.
> See [UPGRADE_REPORT_V25.md](UPGRADE_REPORT_V25.md), [UPGRADE_REPORT_V24.md](UPGRADE_REPORT_V24.md), [UPGRADE_REPORT_V23.md](UPGRADE_REPORT_V23.md), [UPGRADE_REPORT_V22.md](UPGRADE_REPORT_V22.md), [UPGRADE_REPORT_V21.md](UPGRADE_REPORT_V21.md), [UPGRADE_REPORT_V20.md](UPGRADE_REPORT_V20.md), [UPGRADE_REPORT_V19.md](UPGRADE_REPORT_V19.md), [UPGRADE_REPORT_V18.md](UPGRADE_REPORT_V18.md), [UPGRADE_REPORT_V17.md](UPGRADE_REPORT_V17.md), [UPGRADE_REPORT_V16.md](UPGRADE_REPORT_V16.md), [UPGRADE_REPORT_V15.md](UPGRADE_REPORT_V15.md), [UPGRADE_REPORT_V14.md](UPGRADE_REPORT_V14.md), [UPGRADE_REPORT_V13.md](UPGRADE_REPORT_V13.md), [UPGRADE_REPORT_V12.md](UPGRADE_REPORT_V12.md), [UPGRADE_REPORT_V11.md](UPGRADE_REPORT_V11.md), [UPGRADE_REPORT_V10.md](UPGRADE_REPORT_V10.md), [UPGRADE_REPORT_V9.md](UPGRADE_REPORT_V9.md), [UPGRADE_REPORT_V8.md](UPGRADE_REPORT_V8.md), [UPGRADE_REPORT_V7.md](UPGRADE_REPORT_V7.md), [UPGRADE_REPORT_V6.md](UPGRADE_REPORT_V6.md), [UPGRADE_REPORT_V5.md](UPGRADE_REPORT_V5.md), [UPGRADE_REPORT_V4.md](UPGRADE_REPORT_V4.md), [UPGRADE_REPORT_V3.md](UPGRADE_REPORT_V3.md), [UPGRADE_REPORT_V2.md](UPGRADE_REPORT_V2.md) and [UPGRADE_REPORT.md](UPGRADE_REPORT.md)
> for the exact changes, test limitations and
> build instructions. It includes the full original source tree; no compiled application is
> bundled, and Rust compilation was not available in the packaging environment.
> V6 adds a local, fail-closed release gate (`python3 scripts/native_validation.py --root .`),
> with installation notes in [docs/native-validation.md](docs/native-validation.md).
> V7 adds a verified ZIP extraction command and stronger portable filename checks;
> see [docs/source-bundle-integrity.md](docs/source-bundle-integrity.md).
> V8 makes a successful native CLI build require a real `--help` smoke test
> with a binary SHA-256 receipt; see [docs/native-validation.md](docs/native-validation.md).
> V9 adds manually dispatched Ubuntu/macOS/Windows native CLI build-and-smoke
> checks with downloadable JSON receipts and unsigned test executables. The
> workflow has not been run in this packaging environment.
> V10 extends the native gate to initialize the actual tool registry in an
> isolated process, validate its JSON structure and require both smokes to
> pass before an unsigned test binary can be retained. Native execution
> remains unverified in the packaging environment.
> V11 also invokes the real CLI's `capabilities` metadata command inside the
> same isolated environment and checks that its tool IDs, toolsets and
> target requirements agree with `commands`. The native CI artifact now
> requires both metadata digests and retains capability counts and CLI version.
> This additional check is **not** a native test result until its workflow runs.
> V12 adds a bounded, isolated MCP JSON-RPC protocol smoke to that same
> manual cross-platform CI gate: initialize, notification, full tool listing,
> ping and a rejected unknown method. It checks the MCP tool names, titles
> and descriptions against a canonical CLI registry digest, and refuses an
> unsigned executable if MCP initialization or correspondence fails. The
> protocol probe has synthetic regression coverage only in this environment.
> V13 adds exactly one read-only MCP `core_session_show` call after the
> protocol checks. The native CI gate verifies the tool's JSON and text
> response agree and that the isolated session has no open target, project,
> evidence or unknowns. Any error, unexpected state or identity mismatch
> prevents binary artifact export. Rust execution remains pending here.
> V14 additionally verifies that MCP exposes `core_session_show` with a
> parameter-free schema and genuine read-only, non-destructive annotations,
> and sends a call to a deliberately nonexistent tool. The server must return
> a structured `bad_input` refusal with a useful remediation before CI may
> retain an unsigned CLI test executable. These checks have Python regression
> coverage but require the native CI job for real Rust execution.
> V15 adds an isolated **native target-analysis smoke** to the manual
> cross-platform CLI matrix. A compiled CLI must open an eight-byte WebAssembly
> fixture with `--deep triage` and return a typed, fixture-hash-bound evidence
> record recommending the WebAssembly toolset. The harness strips target output
> and temporary paths from saved receipts, rejects inconsistent evidence,
> and refuses unsigned CLI export unless the new gate passes. The Python
> harness is tested; no native Rust run was possible in this environment.
> V16 adds a second native-target gate using a **single long-lived MCP process**:
> open the fixture, parse it with the built-in WebAssembly parser, inspect
> the active session, explicitly close the target, and verify that the session
> contains no open targets afterward while preserving its evidence ledger.
> Each request waits for its response before proceeding, since native MCP
> tool calls may otherwise race. This is still a synthetic Python-validated
> source package until its actual Rust CLI workflow is run on each OS.
> V17 fixes the real Rust CLI's target lifecycle wrapper: a close failure
> can no longer be ignored; cleanup closes the target ID the wrapper opened,
> even if a different target became active; and cleanup is still attempted
> after an interrupted operation. If inspection and cleanup both fail, the
> original error code is preserved and the cleanup error is included in
> structured details. Six Rust regression tests now gate the manual native
> CLI matrix. Those Rust tests still require a real toolchain to be run.
> V18 fixes a related engine bug: when `core_target_close` is given a specific
> target ID, it previously saved annotations belonging to the **active**
> target, which may be different. The engine now saves the annotations of the
> named target before closing it, checks unknown IDs before any save, and
> preserves the active-target annotation API used by other tools. Three engine
> regression tests are included in the native CI gate. Rust compilation has
> not been run in this source packaging environment.
> V19 prevents a saved-project data-loss path: the engine previously treated
> corrupt or unreadable annotation files as if no notes existed, then could
> overwrite those files with empty annotations. Opening a target now returns
> an actionable error on damaged or inaccessible notes, while an actually
> absent annotation file still starts empty. Restoring multiple targets
> validates all annotations before changing the session, avoiding partial
> restoration. Five Rust tests are included in the native CI gate, but native
> compilation remains unverified in this packaging environment.
> V20 closes a second note-loss path: reopening the same bytes under different
> raw-binary interpretation settings now carries forward unsaved names,
> comments and bookmarks, while resetting interpretation-dependent caches
> and cursor state. A short target-ID collision between *different* full
> SHA-256 hashes is rejected rather than overwriting the existing target;
> restoration batches reject such collisions before changing session state.
> Four Rust regressions are added to the manual CI gate. They require a real
> Cargo/rustc toolchain; source-only checks here cannot validate Rust runtime.
> V21 refreshes the verified file path when identical bytes are reopened from
> another location, invalidates a cached Ghidra provider when the explicit
> Deep language pin changes, and carries that optional pin to a local agent's
> MCP session. An empty pin clears the override; omission preserves it.
> Four new engine and one AI regression tests have been added to the native
> CI gate, but native execution remains unverified here.
> V22 invalidates cached live Ghidra providers *and failed-start decisions*
> when global Ghidra configuration changes, even if a target's language pin
> stays the same. Its on-disk analysis cache now distinguishes compiler spec
> and analyzer installation path as well as language. Three additional Rust
> regressions are gated in the manual CLI matrix but are not verified here
> without Cargo and a live Ghidra installation.
> V23 discards cached Deep providers on the desktop's transition to Clean room,
> preserving human notes and unrelated native caches. It also fingerprints
> Ghidra's bounded launcher and application-version metadata to detect common
> upgrades *at the same path*. Two new Rust regressions and a desktop UI
> type-check gate are included, but native execution remains unverified.
> V24 tracks ordinary module-only Ghidra updates using bounded metadata and
> small-specification hashes, and adds a manual fresh-analysis generation.
> The desktop Engines page now exposes global processor-language and compiler
> settings. Large module content changes with restored metadata can still
> evade automatic detection; native Ghidra and GUI tests remain pending.
> V25 adds a focused, fail-closed Deep native validation profile, runnable
> from Windows PowerShell or macOS/Linux shell. It produces a receipt showing
> passed, failed or blocked Rust checks, without claiming live Ghidra or GUI
> tests ran. See [UPGRADE_REPORT_V25.md](UPGRADE_REPORT_V25.md).

<p align="center">
  <b>English</b> · <a href="README.ja.md">日本語</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-hant.md">繁體中文</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.pt.md">Português</a>
</p>

<p align="center">
  <b>See how any website, app or game works, then have AI build your own version; an open-source, clean-room engine in pure Rust.</b><br>
  Point it at a target, say what you want, and it studies the original with real inspection tools,<br>
  has a coding agent build a new project, checks the result against the original and refines it.
</p>

<p align="center">
  <img alt="100% Rust" src="https://img.shields.io/badge/100%25-Rust-b7410e?style=flat-square&logo=rust">
  <img alt="macOS · Windows · Linux · FreeBSD · Web" src="https://img.shields.io/badge/macOS%20%C2%B7%20Windows%20%C2%B7%20Linux%20%C2%B7%20FreeBSD%20%C2%B7%20Web-native-2d81ff?style=flat-square">
  <img alt="License: MIT OR Apache-2.0" src="https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-3a3a3a?style=flat-square">
  <img alt="Status: early alpha" src="https://img.shields.io/badge/status-early%20alpha-d69e2e?style=flat-square">
</p>

<p align="center">
  <a href="https://discord.gg/artcraft"><img alt="Join the ArtCraft community on Discord" src="https://img.shields.io/badge/Join%20us%20on%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" height="40"></a>
</p>

<p align="center">
  <a href="#downloads"><b>Download Craft Anything</b></a> ·
  <a href="https://getartcraft.com/">ArtCraft</a> ·
  <a href="https://getartcraft.com/apps">All Crafting Apps</a>
</p>

<br>

<p align="center">
  <img src="docs/images/project-website.png" alt="A website project in Craft Anything: the check lists what doesn't match yet and how alike each page is, the builder says its version is ready, an approval card asks before running a command, and the plan shows two of four steps done" width="100%">
  <br>
  <sub>A website project: each check says how alike the build is, you approve anything outside the rules, and the plan shows what's left.</sub>
</p>

> [!NOTE]
> **ArtCraft is a community of artists from all walks of life.** Painters, photographers,
> filmmakers, illustrators, designers, animators, hobbyists, and people who picked up a pencil
> last week. If you make things, you're one of us. **[Come say hi on Discord](https://discord.gg/artcraft).**

<p align="center">
  <a href="#how-it-works">How it works</a> ·
  <a href="#two-ways-to-use-it">Two ways to use it</a> ·
  <a href="#ai">AI</a> ·
  <a href="#built-for-agents">Agents</a> ·
  <a href="#under-the-hood">Under the hood</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#status">Status</a> ·
  <a href="#downloads">Downloads</a> ·
  <a href="https://discord.gg/artcraft">Discord</a>
</p>

<br>

<table>
  <tr>
    <td width="25%" valign="top">
      <h3>🔍 Study anything</h3>
      Websites, desktop apps, phone apps, games and files: real inspection tools find out how they're built and what they do, with the evidence behind every answer.
    </td>
    <td width="25%" valign="top">
      <h3>🛠️ Build your own</h3>
      Say what you want ("Recreate this website", "Recreate this game's gameplay in Rust") and a coding agent builds a new project in the stack you ask for.
    </td>
    <td width="25%" valign="top">
      <h3>✅ Checked, not guessed</h3>
      Every build is compared with the original (pages at three screen widths, a game's own self-test, an app's runs) and refined until it's close.
    </td>
    <td width="25%" valign="top">
      <h3>🤖 Agent-ready</h3>
      322 tools, each one a CLI command and an MCP tool, so any agent can drive the same engine the app uses. No AI is needed to inspect.
    </td>
  </tr>
</table>

<br>

## How it works

Point Craft Anything at a target, say what you want, and press Start. It works in four steps,
asking you in plain words when a decision is yours:

1. **Study:** inspect the original: its pages at every width, its code, its files, how it runs.
2. **Build:** a coding agent builds a new project, in the language, framework or engine your
   request names (or a sensible default).
3. **Check:** compare the build with the original and list what doesn't match yet.
4. **Ready:** open, play or preview the result, or ask for changes and it refines it.

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/make.png" alt="The Make screen: pick a website, an app or a game to recreate, type the address, choose Clean room or Deep, say what you want and press Start" width="100%"><br>
      <sub><b>Make:</b> pick what to recreate, say what you want, press Start.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/project-game.png" alt="A finished snake game project with its steps all done and a Play the game button" width="100%"><br>
      <sub><b>Ready:</b> play the game, or ask for changes below.</sub>
    </td>
  </tr>
</table>

## Two ways to use it

- **Simple** (default): "What do you want to make?" Pick a website, app or game, describe it,
  press Start. A project page shows the four steps (Study → Build → Check → Ready), what the
  agent is doing in plain words, question and approval cards, how alike the result is, and
  buttons to open the result or its folder. Ask for changes in the box at the bottom.
  "Just look inside" opens a target for questions without building anything (in Deep mode).
- **Advanced**: the full workbench: every tool (Ctrl/Cmd+K), functions, strings, pseudo-C,
  disassembly, hex, call graphs, evidence, unknowns, jobs, logs and the agent console.

Both modes drive the same engine.

## Clean room or Deep

- **Clean room** (the default): nothing inside the original is read, not its code, files or page
  source. Craft Anything works from what you can see: pictures you add, the website as a visitor
  sees it (screenshots, visible text, measured layout, colours, fonts and spacing, menus opened)
  and, when you allow it, a picture of the app's window. Tools that would look inside are refused
  and say how to turn on Deep mode.
- **Deep**: every tool is used, for your private use. A program's code and files are read with the
  built-in engines (and Ghidra when it's installed), a website's JavaScript is read and its start
  page watched, and the builder may use all of them.

Clean room is a rule about what Craft Anything offers and refuses. It is not an operating-system
sandbox: it doesn't isolate your computer. A coding agent you run still acts as you, and what it can
read, write or run depends on its own permissions. Which of an agent's own tools Craft Anything can
refuse differs by agent; for Claude Code's tools in chats and questions, see
[`docs/claude-code.md`](docs/claude-code.md).

Switch in the title bar or Settings, with `settings_set deep_mode true`, or for one CLI run with
`--deep` (or `--clean-room`).

<p align="center">
  <img src="docs/images/workbench.png" alt="The Advanced workbench: a function list, pseudo-C of the selected function, the agent answering a question with a tool call, and the evidence ledger" width="100%">
  <br>
  <sub><b>Advanced:</b> functions, pseudo-C, the agent and the evidence behind every answer.</sub>
</p>

## AI

Building needs a coding agent on your computer. **Claude Code** is supported first: Craft
Anything runs your own, unmodified `claude` program, which uses your own sign-in; Craft Anything
never sees or handles your account. Your use of it falls under your own agreement with
Anthropic. See [docs/claude-code.md](docs/claude-code.md) for how and why. A Codex adapter
exists but hasn't been verified on a real install yet. **GitHub Copilot** can use your installed
Copilot CLI through ACP: sign in separately with `copilot login`. **Grok Build** can use your
installed `grok` program through ACP (`grok agent stdio`): sign in separately with `grok login`.
See [docs/grok.md](docs/grok.md). Other agents that speak ACP (Gemini CLI, Qwen Code, opencode,
goose, Hermes, Maki, Devin) can be switched to it per backend; of those, Maki and Devin have been
run live (Devin authenticates with the backend's stored key — its own CLI sign-in doesn't count
over ACP). Anthropic, OpenAI, OpenRouter, xAI (Grok, when `XAI_API_KEY` is set) and local
OpenAI-compatible servers are available for questions and analysis (Settings → AI).

## Built for agents

Every tool is also a CLI subcommand and an MCP tool, so an agent can study a target or run a
whole rebuild without the app:

```sh
craft-anything-cli commands                                   # every tool, by toolset
craft-anything-cli mcp                                        # serve them to any MCP client
craft-anything-cli rebuild https://example.com "Recreate this website"
```

Results are evidence records with their confidence, coverage and limitations; big ones come back
as a summary first and are paged with `core_evidence_browse`. `craft-anything-cli setup`
connects Craft Anything to coding-agent apps.

## Under the hood

A pure-Rust reverse-engineering engine with 322 tools (304 in the engine, the rest from the AI and
setup layers) for native programs, .NET, Java, Android, Apple, Electron/JavaScript, websites,
firmware, packet captures, databases and game files. Its own decoders, disassembler and
decompiler work without any outside program; Deep mode uses them all, and Ghidra when it's
installed. It powers the desktop app (egui), the CLI and the MCP server, and parts of it run in
the browser as WebAssembly. The full list is in [docs/tools.md](docs/tools.md).

## Get started

Requires Rust **1.97 or newer** (a dependency needs it; `rustup update`). On Debian or Ubuntu,
the desktop app also needs:

```sh
sudo apt-get install libxkbcommon-dev libwayland-dev libx11-dev libxrandr-dev libxi-dev libgl1-mesa-dev libgtk-3-dev
```

Then:

```sh
git clone https://github.com/storytold/craft-anything && cd craft-anything
cargo run --release -p craft-anything                # the desktop app
cargo install --path apps/craft-anything-cli         # the CLI, as `craft-anything-cli`
craft-anything-cli doctor                            # checks the install, coding agents and engines
```

Building needs a coding agent signed in on your computer (see [AI](#ai)); `doctor` says what's
missing. Rebuild from the command line (questions are asked in the terminal):

```sh
craft-anything-cli rebuild https://example.com "Recreate this website"
craft-anything-cli rebuild - "A snake game in Rust" --rounds 1
craft-anything-cli rebuild https://example.com "Make the header blue" --continue-in "<project folder>"
craft-anything-cli rebuild - "Add a contact form" --project-folder "<your folder>" --existing edit
```

New projects go in `Documents/Craft Anything` (change it in Settings → General). Each project
folder holds `craft.json` (the request, target, status and history), `capture/` (what was
recorded from the target), `app/` (the generated project), `checks/` (each comparison against
the original), `investigation.cany/` (evidence) and `inbox/` (open questions).

A project can also start in a folder you pick (Choose a folder… under Start, or `--project-folder`).
If it already has files, you say what they are: the app itself, which the builder edits in place,
or material to start from, with the app built in `app/` beside them (Settings → General can
remember the answer). Settings → General also lists projects from more folders; removing one
only takes it off the list.

## Status

> [!IMPORTANT]
> **Status:** Craft Anything is in early alpha. What works today, what is partial and what's next:
>
> - **Works today:** the desktop app (Simple and Advanced), the CLI and the MCP server; the
>   Study → Build → Check → Refine workflow for websites, apps and games with Claude Code as the
>   builder; website checks at three screen widths; game rebuilds in Unity, Unreal or Godot; Deep
>   mode with Ghidra; generated pictures and 3D models (opt-in, your own fal.ai key); plugins (MCP
>   servers, and WASM components in builds with the `wasm-plugins` option).
> - **Partly there:** building with API keys and other coding agents (built, not yet run live),
>   behaviour checks for apps and games, a few engine tools tested only against stand-ins.
> - **Released:** public releases with installers for Windows, macOS, Linux and FreeBSD are
>   published on the [Releases page](https://github.com/storytold/craft-anything/releases); they are
>   still alpha.
> - **Next:** the managed tier.
>
> [docs/STATUS.md](docs/STATUS.md) has the detail and how each part was checked,
> [docs/tool-status.md](docs/tool-status.md) the tool-by-tool checklist, and
> [docs/ROADMAP.md](docs/ROADMAP.md) the plan, item by item. Please file issues, or tell us what
> broke on [Discord](https://discord.gg/artcraft).

**Responsible use.** Use Craft Anything on software you own or are allowed to examine. It never
bypasses DRM, logins or anti-cheat. Rebuilds recreate layout, style and behaviour from evidence;
the agent is told not to copy logos, photos or text unless you confirm you own them or have
permission.

**Installing dependencies runs code.** When a builder installs a project's dependencies, the commands
run on your computer, and the packages can run code while they install. npm runs each package's
install scripts by default, and Rust build scripts and Python source packages run their own setup
code. Craft Anything asks before a command of a kind it hasn't seen in this project (`npm`, `cargo`…).
Choosing "always allow" for `npm` lets every later npm command run without asking, so only do that
for a project whose dependencies you trust. To install npm packages without their install scripts,
run `npm install --ignore-scripts`.

## Downloads

On GitHub, the [latest release](https://github.com/storytold/craft-anything/releases/latest) has every build listed below, and [all releases](https://github.com/storytold/craft-anything/releases) has earlier versions and their notes. `<ver>` in the file names is the version number, and `SHA256SUMS.txt` lists a checksum for every file.

### Windows

| Build | Installer | Portable |
|---|---|---|
| x64 (64-bit Intel/AMD) | `craft-anything-<ver>-windows-x64.msi` | `craft-anything-<ver>-windows-x64-portable.zip` |
| arm64 (Snapdragon and other ARM PCs) | `craft-anything-<ver>-windows-arm64.msi` | `craft-anything-<ver>-windows-arm64-portable.zip` |
| x86 (32-bit) | `craft-anything-<ver>-windows-x86.msi` | `craft-anything-<ver>-windows-x86-portable.zip` |

Installers and executables are code-signed. The portable zip keeps its settings next to the app.

### macOS

| Build | File | Notes |
|---|---|---|
| App, universal (Apple silicon + Intel) | `craft-anything-<ver>-macos-universal.dmg` | Signed and notarized |
| Command-line tool, universal | `craft-anything-cli-<ver>-macos-universal.zip` | Signed and notarized; see below if macOS blocks it |

Versions after 0.2.0 include the command-line tool in the app, so builds don't need the separate
zip. You only need it to use the command line without the app.

A browser download of the zip can stop with "Apple could not verify craft-anything-cli" (a lone
program can't carry its notarization with it, so macOS has to look it up online). A download from
the terminal isn't checked this way, so use this instead:

```sh
ver=0.2.0   # change this to the version you want
curl -fLO "https://github.com/storytold/craft-anything/releases/download/v$ver/craft-anything-cli-$ver-macos-universal.zip"
ditto -x -k "craft-anything-cli-$ver-macos-universal.zip" .
mkdir -p ~/.local/bin
mv "craft-anything-cli-$ver-macos-universal/craft-anything-cli" ~/.local/bin/
~/.local/bin/craft-anything-cli --version
```

To run it as just `craft-anything-cli`, add `~/.local/bin` to your `PATH`.

If you already have the blocked copy, press Done (not Move to Bin), then run
`xattr -d com.apple.quarantine <path to craft-anything-cli>`, or allow it in System Settings →
Privacy & Security → Open Anyway.

#### Checking that a Mac download is signed and notarized

Each Mac release is signed with a Developer ID certificate and notarized by Apple. To check a download
yourself, run these in Terminal (replace `<ver>` with the version and the paths with yours):

```sh
# The disk image: "accepted" and "source=Notarized Developer ID" mean it passed
spctl --assess --type open --context context:primary-signature -v "craft-anything-<ver>-macos-universal.dmg"
# The notarization ticket is attached: prints "The validate action worked!"
xcrun stapler validate "craft-anything-<ver>-macos-universal.dmg"

# The app, after you open the disk image: the signature must verify
codesign --verify --deep --strict --verbose=2 "/Volumes/<disk image name>/Craft Anything.app"
spctl --assess --type execute --verbose "/Volumes/<disk image name>/Craft Anything.app"

# The command-line tool: a bare program can't carry a ticket, so macOS checks it online
codesign --verify --strict --verbose=2 ~/.local/bin/craft-anything-cli
codesign -dv --verbose=4 ~/.local/bin/craft-anything-cli 2>&1 | grep Authority
```

The `Authority` lines should name a "Developer ID Application" certificate. If a check fails, don't
run the file; report it with the output and the release tag.

### Linux

| Format | x86_64 | aarch64 (ARM64) | Notes |
|---|---|---|---|
| AppImage | `craft-anything-<ver>-linux-x86_64.AppImage` | `craft-anything-<ver>-linux-aarch64.AppImage` | Runs anywhere; updates itself with [AppImageUpdate](https://github.com/AppImageCommunity/AppImageUpdate) (`.zsync` files) |
| Flatpak | `craft-anything-<ver>-linux-x86_64.flatpak` | `craft-anything-<ver>-linux-aarch64.flatpak` | Sandboxed, so it can't start a browser on your computer, which studying websites needs (use another package for that); `flatpak install --user <file>` |
| Debian/Ubuntu | `craft-anything-<ver>-linux-x86_64.deb` | `craft-anything-<ver>-linux-aarch64.deb` | |
| Fedora/RHEL/openSUSE | `craft-anything-<ver>-linux-x86_64.rpm` | `craft-anything-<ver>-linux-aarch64.rpm` | |
| Tarball | `craft-anything-<ver>-linux-x86_64.tar.gz` | `craft-anything-<ver>-linux-aarch64.tar.gz` | Unpack anywhere |

RISC-V (riscv64): `craft-anything-<ver>-linux-riscv64.tar.gz`, a tarball only.

The AppImage carries the CLI too: run `<AppImage> --cli <command>` (for example
`<AppImage> --cli mcp` for an MCP client), or make a link to the AppImage named
`craft-anything-cli` and run that.

### FreeBSD

| Build | File |
|---|---|
| x86_64 | `craft-anything-<ver>-freebsd-x86_64.tar.gz` |

### Web (WebAssembly)

| Build | File | Notes |
|---|---|---|
| Browser viewer | `craft-anything-web-<ver>.zip` | Says what a file is, in the browser (the file never leaves it); host the folder on any static web server ([packaging/web/README.md](packaging/web/README.md)) |

<br>

<p align="center">
  <a href="https://discord.gg/artcraft"><img alt="Join the ArtCraft community on Discord" src="https://img.shields.io/badge/Join%20us%20on%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" height="40"></a>
</p>

<h3 align="center">Come make things with us</h3>

<p align="center">
  Our Discord is where artists of every kind hang out: people who paint, shoot, draw, cut film,
  set type, and people still figuring out what they like to make. Share what you're working on,
  ask for help, tell us what's broken, or tell us what you wish these tools could do.
  Whatever your medium and however long you've been at it, you're welcome here.
</p>

<p align="center">
  <a href="https://discord.gg/artcraft"><b>discord.gg/artcraft</b></a> ·
  <a href="https://getartcraft.com/">getartcraft.com</a> ·
  <a href="https://getartcraft.com/apps">The Crafting Apps</a> ·
  <a href="https://github.com/storytold/craft-anything">Craft Anything</a>
</p>

---

## The Craft App Suite

| | App | What it's for | Code | Learn more |
|:-:|---|---|---|---|
| <img src="https://raw.githubusercontent.com/storytold/photocraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.photocraft.png" alt="" width="32" height="32"> | **PhotoCraft** | Image editing: layers, masks, type and real PSD files | [GitHub](https://github.com/storytold/photocraft) | [Website](https://getartcraft.com/apps/photocraft) |
| <img src="https://raw.githubusercontent.com/storytold/vectorcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.vectorcraft.png" alt="" width="32" height="32"> | **VectorCraft** | Vector illustration | [GitHub](https://github.com/storytold/vectorcraft) | [Website](https://getartcraft.com/apps/vectorcraft) |
| <img src="https://raw.githubusercontent.com/storytold/filmcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.filmcraft.png" alt="" width="32" height="32"> | **FilmCraft** | Video editing, color and sound | [GitHub](https://github.com/storytold/filmcraft) | [Website](https://getartcraft.com/apps/filmcraft) |
| <img src="https://raw.githubusercontent.com/storytold/lightcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.lightcraft.png" alt="" width="32" height="32"> | **LightCraft** | Photo library and raw development | [GitHub](https://github.com/storytold/lightcraft) | [Website](https://getartcraft.com/apps/lightcraft) |
| <img src="https://raw.githubusercontent.com/storytold/pdfcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.pdfcraft.png" alt="" width="32" height="32"> | **PdfCraft** | Reading, organizing and protecting PDFs | [GitHub](https://github.com/storytold/pdfcraft) | [Website](https://getartcraft.com/apps/pdfcraft) |
| <img src="https://raw.githubusercontent.com/storytold/effectcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.effectcraft.png" alt="" width="32" height="32"> | **EffectCraft** | Motion graphics and visual effects | [GitHub](https://github.com/storytold/effectcraft) | [Website](https://getartcraft.com/apps/effectcraft) |
| <img src="https://raw.githubusercontent.com/storytold/designcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.designcraft.png" alt="" width="32" height="32"> | **DesignCraft** | Page layout and publishing | [GitHub](https://github.com/storytold/designcraft) | [Website](https://getartcraft.com/apps/designcraft) |
| <img src="https://raw.githubusercontent.com/storytold/figcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.figcraft.png" alt="" width="32" height="32"> | **FigCraft** | Interface design and prototyping | [GitHub](https://github.com/storytold/figcraft) | [Website](https://getartcraft.com/apps/figcraft) |
| <img src="https://raw.githubusercontent.com/storytold/wordcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.wordcraft.png" alt="" width="32" height="32"> | **WordCraft** | Word processing and documents | [GitHub](https://github.com/storytold/wordcraft) | [Website](https://getartcraft.com/apps/wordcraft) |
| <img src="https://raw.githubusercontent.com/storytold/soundcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.soundcraft.png" alt="" width="32" height="32"> | **SoundCraft** | Audio recording and editing | [GitHub](https://github.com/storytold/soundcraft) | [Website](https://getartcraft.com/apps/soundcraft) |
| <img src="https://raw.githubusercontent.com/storytold/deckcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.deckcraft.png" alt="" width="32" height="32"> | **DeckCraft** | Presentations and slide decks | [GitHub](https://github.com/storytold/deckcraft) | [Website](https://getartcraft.com/apps/deckcraft) |
| <img src="https://raw.githubusercontent.com/storytold/cadcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.cadcraft.png" alt="" width="32" height="32"> | **CADCraft** | Computer-aided design | [GitHub](https://github.com/storytold/cadcraft) | [Website](https://getartcraft.com/apps/cadcraft) |
| <img src="https://raw.githubusercontent.com/storytold/gridcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.gridcraft.png" alt="" width="32" height="32"> | **GridCraft** | Spreadsheets and data | [GitHub](https://github.com/storytold/gridcraft) | [Website](https://getartcraft.com/apps/gridcraft) |
| <img src="https://raw.githubusercontent.com/storytold/texturecraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.texturecraft.png" alt="" width="32" height="32"> | **TextureCraft** | 3D texture painting | [GitHub](https://github.com/storytold/texturecraft) | [Website](https://getartcraft.com/apps/texturecraft) |
| <img src="https://raw.githubusercontent.com/storytold/materialcraft/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.materialcraft.png" alt="" width="32" height="32"> | **MaterialCraft** | Node-based procedural materials | [GitHub](https://github.com/storytold/materialcraft) | [Website](https://getartcraft.com/apps/materialcraft) |
| <img src="https://raw.githubusercontent.com/storytold/craft-anything/main/assets/app-icon/hicolor/64x64/apps/ai.storyteller.craft-anything.png" alt="" width="32" height="32"> | **Craft Anything** | See how any software works, then build your own | [GitHub](https://github.com/storytold/craft-anything) | [Website](https://getartcraft.com/apps/craft-anything) |
| <img src="https://raw.githubusercontent.com/tangency-labs/solidcraft/main/assets/icons/app/linux/exports/hicolor/64x64/apps/io.tangencylabs.solidcraft.png" alt="" width="32" height="32"> | **SolidCraft** | Parametric 3D CAD by Tangency Labs (community app) | [GitHub](https://github.com/tangency-labs/solidcraft) | |


## License and credits

Craft Anything is dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option.
Copyright (c) 2026 ArtCraft Team and the Craft Anything contributors ([CONTRIBUTORS.md](CONTRIBUTORS.md)). Required notices are in [NOTICE](NOTICE).

Bundled fonts, icons, images and other assets keep their own open licenses; each one is listed
with its author, source and license in [ATTRIBUTION.md](ATTRIBUTION.md).

The screenshots show sample projects made for them: example.com, a snake game and a small
calculator program.

The ArtCraft name, wordmark and logos in [`docs/brand/`](docs/brand/) are trademarks of the
ArtCraft Team and are not covered by this license. They may be used only unmodified, and only as
part of this repository and Craft Anything, under [`docs/brand/LICENSE-brand.txt`](docs/brand/LICENSE-brand.txt).
Forks and modified versions must remove them.

<p align="center">
  <a href="https://getartcraft.com/"><img alt="ArtCraft" src="docs/brand/artcraft-mark.svg" width="28"></a><br>
  <sub>Made by the <a href="https://getartcraft.com/">ArtCraft</a> team and community.</sub>
</p>
