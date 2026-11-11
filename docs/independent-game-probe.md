# Independent replay probe (opt-in research tool)

The game's own self-test is **self-reported evidence**, not proof of working gameplay.
This repository now ships a separate Python 3 standard-library probe that runs a
**trusted** replay-capable executable, reads its telemetry and separately decodes
real screenshot PNG bytes. It checks that movement, an action and the HUD all
change. Deliberately broken fixtures show that the checker rejects some common
false successes.

This is a limited, optional evidence layer. The current Rust rebuild checker
does **not** automatically launch this script or cause games to implement its
protocol. Game adapters and safe process isolation must be built before it
becomes a default readiness gate.

## Run the fixture and regression tests

From the repository root:

    python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v

The test suite invokes scripts/tests/fixture_probe_game.py as a subprocess
and checks a known-good fixture alongside intentionally broken movement, action,
HUD, screenshot and path-traversal examples. V4 also tests excessive stdout,
excessive stderr, target timeouts, and tampered saved reports.

To run the probe against the synthetic fixture:

    mkdir -p /tmp/craft-probe-fixture
    python3 scripts/independent_game_probe.py \
      --case scripts/game_probe_example.json \
      --output-folder /tmp/craft-probe-fixture \
      --report /tmp/craft-probe-fixture/report.json \
      -- python3 "$PWD"/scripts/tests/fixture_probe_game.py

To recheck the saved telemetry and captured screenshot bytes later, without
running the target program again:

    python3 scripts/verify_probe_report.py \
      --case scripts/game_probe_example.json \
      --report /tmp/craft-probe-fixture/report.json

The second command must run on a machine where the original absolute
`capture_folder` still exists and the screenshots have not been moved.
The verifier recomputes the original checks, PNG hashes and case digest,
and refuses reports that no longer match. A malicious actor able to rewrite
all the report and image files can still falsify them; they are **not signed**.

Use an isolated, disposable output folder and invoke only programs you
already trust. The command **executes the supplied program** with the
working directory set to a newly created `replay-...` subdirectory of
`--output-folder` for each invocation. The report records this directory;
old screenshots elsewhere cannot count. Standard output is drained with
a one-megabyte budget and standard error with a 256-kilobyte budget; a
timeout or excess output stops the direct process (and its process group
on Unix-like systems). This tool is **not a sandbox**. Windows descendant
cleanup and malicious programs remain outside the supplied guarantees.
Because the working directory changes, pass absolute paths for the executable
or script files (the `"$PWD"/...` in the example resolves before the probe
starts). Bare executable names on `PATH`, such as `python3`, still work.

## Replay protocol version 1

The program reads one JSON object from standard input:

    {"protocol":1,"nonce":"<fresh 32-hex-character challenge>","steps":[
      {"id":"initial","keys":[]},
      {"id":"moved","keys":["Right"]},
      {"id":"acted","keys":["Space"]},
      {"id":"hud","keys":[]}
    ]}

It must emit a single object to standard output with protocol: 1, an identical
`nonce` field, and four
ordered samples with the matching id values. Every sample must provide:

- x, y: finite game-world or screen coordinates of the controlled player.
- action_count: finite numeric cumulative count of successfully triggered actions.
- hud_text: HUD's visible text (the fourth sample must include a requested string).
- screenshot: a relative path to a screenshot created in the output folder.

The example case supplies minimum movement, action-count increase, screenshot
pixel-change fraction and required HUD text. The checker reads screenshots from
disk, rejects paths escaping the **fresh run subfolder**, validates PNG CRCs, decompresses
scanlines, checks dimensions and compares decoded pixel bytes at each transition.
Only non-interlaced 8-bit RGB or RGBA PNG is supported; images and output are
resource-bounded. The report includes the SHA-256 of the exact case and each
accepted PNG, the observed response telemetry, plus the fresh capture path.
The challenge nonce and exclusive
run directory discourage accidental evidence reuse; a cooperating malicious
executable can still forge all telemetry and images. A nonzero result means
the evidence failed or was malformed.

## What a passing report means—and does not mean

**Passing:** The program exited successfully, echoed the run-specific challenge,
returned well-formed telemetry,
reported movement/action/HUD updates, and supplied valid screenshots with
enough actual pixel differences for each expected transition.

**Not proved:** Game physics accuracy, actual input receipt, correctness of
screen text recognition, frame timing, match against an original game, and
whether the program fabricated telemetry or screenshots. A malicious program
can intentionally satisfy this protocol. For stronger assurance, a future
adapter must drive inputs independently (OS/input APIs), synchronize frame
capture independently, measure time, compare against original-game baselines,
and run the untrusted target inside an OS sandbox or isolated virtual machine.

## Planned integration

1. Build a game-engine adapter for one target stack (e.g. Godot) that injects
   scripted inputs and collects telemetry/frame captures independently.
2. Define target-specific expectations and replay timings in saved, versioned
   test cases instead of assuming "Right" and "Space" apply to every game.
3. Have the Rust checker run the opt-in probe with a bounded subprocess and
   only accept an explicit passing, fresh report tied to the exact build hash.
4. Extend regression tests to honest games with real observed success/failure,
   cross-platform capture, timeouts and sandbox escape attempts.
