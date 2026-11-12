//! A game that draws isn't yet a game that plays: a first slice can draw a level with no player
//! in it. The brief asks every game for a `--self-test <file.json>` option (play the core loop
//! with scripted input, measure it from the game's own state, save the results, exit), and
//! [`SelfTestCheck`] holds the build to it: it starts the finished game that way after the
//! screenshot check, and the game isn't ready until every step of the core loop passes.

use super::screenshot::{NO_PROGRAM, OwnRun, run_with};
use super::*;
use craft_anything_engine::self_test::{SELF_TEST_VERSION as VERSION, SelfTestReport as Report};
use craft_anything_engine::tools::web_common::{decode_png, perceptual_similarity, resize};
use craft_anything_engine::workspace::Claim;

/// What every game brief asks for, so the check can see the core loop played.
pub const SELF_TEST_CONTRACT: &str = "Give the game a `--self-test <file.json>` option too: start a game the way a player would, drive the player with scripted input for a few seconds, measure from the game's own state whether each step of the core loop works, save the results to that file and exit: {\"version\": 1, \"picture\": \"shots/after.png\", \"steps\": [{\"step\": \"move\", \"passed\": true, \"detail\": \"walked 3.1 m forward\"}, {\"step\": \"act\", \"passed\": true, \"detail\": \"fired 5 shots, 2 hit\"}, {\"step\": \"hud\", \"passed\": true, \"detail\": \"health, ammo and the objective are drawn\"}]}. If the game has a window, save a picture of it after the scripted input as a PNG and put its path (relative to where the game runs) in \"picture\": Craft Anything compares it with the --screenshot picture, and a screen that did not change doesn't pass. \"move\": the player (or what they steer) moves; \"act\": their main action works (firing, mining, jumping, eating…); \"hud\": the on-screen information the original shows (score, health, ammo…) is drawn. Craft Anything runs it after each build, and the game isn't ready until every step passes.";

/// The core loop, as the steps a self-test must pass: (step, what it shows).
const CORE_LOOP: [(&str, &str); 3] = [("move", "the player moves"), ("act", "the player's main action works"), ("hud", "the on-screen information is drawn")];

/// A picture this large isn't a screenshot; refuse to read it.
const MAX_PICTURE: u64 = 32 * 1024 * 1024;
/// At or above this likeness the two pictures are the same screen.
const SAME_PICTURE: f64 = 0.999;

/// What the picture the game saved after its scripted input shows: it must differ from the
/// `--screenshot` picture, or the check only has the game's word that anything happened.
fn picture_verdict(cx: &CheckCx, bytes: &[u8], note: String) -> (bool, String) {
    let Ok(report) = serde_json::from_slice::<Report>(bytes) else { return (true, note) };
    let baseline = cx.ws.checks_dir().join(format!("round-{}", cx.round)).join("screenshot.png");
    if !baseline.is_file() {
        // A game without a window has no --screenshot picture to compare with.
        return (true, note);
    }
    let Some(path) = report.picture.as_deref().map(str::trim).filter(|p| !p.is_empty()) else {
        return (
            false,
            format!("The self-test didn't save a picture after its scripted input, so only the game's own word says anything happened. {SELF_TEST_CONTRACT}"),
        );
    };
    let candidate = if Path::new(path).is_absolute() { PathBuf::from(path) } else { cx.ws.app_dir().join(path) };
    let (Ok(real), Ok(root)) = (std::fs::canonicalize(&candidate), std::fs::canonicalize(cx.ws.app_dir())) else {
        return (false, format!("The picture the self-test names ({}) isn't a readable file. {SELF_TEST_CONTRACT}", candidate.display()));
    };
    if !real.starts_with(&root) {
        return (false, format!("The picture the self-test names ({}) is outside the project. {SELF_TEST_CONTRACT}", candidate.display()));
    }
    let picture = std::fs::metadata(&real)
        .ok()
        .filter(|m| m.is_file() && m.len() <= MAX_PICTURE)
        .and_then(|_| std::fs::read(&real).ok())
        .and_then(|b| decode_png(&b).ok());
    let Some(picture) = picture else {
        return (false, format!("The picture the self-test names ({}) isn't a readable PNG. {SELF_TEST_CONTRACT}", candidate.display()));
    };
    let Some(shot) = std::fs::read(&baseline).ok().and_then(|b| decode_png(&b).ok()) else { return (true, note) };
    let picture = if (picture.width, picture.height) == (shot.width, shot.height) { picture } else { resize(&picture, shot.width, shot.height) };
    let alike = perceptual_similarity(&picture, &shot);
    if !picture_changed(alike) {
        return (
            false,
            format!(
                "The picture after the scripted input is the same as the --screenshot picture ({:.0}% alike): the screen didn't change, so the self-test's steps are only the game's word. Make the scripted input drive the player and save the picture after it. {SELF_TEST_CONTRACT}",
                alike * 100.0
            ),
        );
    }
    (true, format!("{note} The picture after the scripted input differs from the --screenshot picture ({:.0}% alike).", alike * 100.0))
}

fn picture_changed(alike: f64) -> bool {
    alike < SAME_PICTURE
}

pub struct SelfTestCheck;

impl SelfTestCheck {
    pub const ID: &'static str = "game-self-test";
}

impl Check for SelfTestCheck {
    fn id(&self) -> &'static str {
        Self::ID
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let round = cx.round;
        let file = cx.ws.checks_dir().join(format!("round-{round}")).join("self-test.json");
        let (passed, note, claims) = match run_with(cx, "--self-test", &file)? {
            OwnRun::NoProgram => (false, NO_PROGRAM.to_string(), Vec::new()),
            OwnRun::NoFile(why) => (false, format!("The game didn't save a report with --self-test.{why} {SELF_TEST_CONTRACT}"), Vec::new()),
            OwnRun::Saved { bytes, .. } => {
                let (passed, note) = verdict(&bytes);
                let (passed, note) = if passed { picture_verdict(cx, &bytes, note) } else { (passed, note) };
                (passed, note, claims(&bytes))
            }
        };
        let artifacts = file.strip_prefix(&cx.ws.root).ok().map(Path::to_path_buf).filter(|_| file.is_file()).into_iter().collect();
        Ok(CheckResult { artifacts, claims, ..CheckResult::new(self.id(), round, passed, note) })
    }
}

/// Whether a self-test report shows every step of the core loop passing, with the note.
/// Each step of a saved self-test as a claim of its own: the core loop's steps (one the game
/// didn't test fails as "not tested"), then any steps the game added. An unreadable report makes
/// none ([`verdict`] says why).
pub fn claims(bytes: &[u8]) -> Vec<Claim> {
    let Ok(report) = serde_json::from_slice::<Report>(bytes) else { return Vec::new() };
    let core = |s: &str| CORE_LOOP.iter().any(|(step, _)| s.trim().eq_ignore_ascii_case(step));
    let mut out: Vec<Claim> = CORE_LOOP
        .iter()
        .map(|(step, shows)| {
            let title = format!("In play, {shows}");
            let matching: Vec<_> = report.steps.iter().filter(|s| s.step.trim().eq_ignore_ascii_case(step)).collect();
            match matching.as_slice() {
                [s] => {
                    Claim::new(title, s.passed, if s.detail.trim().is_empty() { "the self-test says it failed".into() } else { s.detail.trim().to_string() })
                }
                [] => Claim::new(title, false, "not tested"),
                _ => Claim::new(title, false, "the self-test reported this step more than once"),
            }
        })
        .collect();
    out.extend(
        report.steps.iter().filter(|s| !core(&s.step)).take(20).map(|s| Claim::new(format!("Self-test step \"{}\"", s.step.trim()), s.passed, s.detail.trim())),
    );
    out
}

pub fn verdict(bytes: &[u8]) -> (bool, String) {
    let report: Report = match serde_json::from_slice(bytes) {
        Ok(r) => r,
        Err(e) => return (false, format!("The --self-test file can't be read: {e}. {SELF_TEST_CONTRACT}")),
    };
    if report.version != VERSION {
        return (false, format!("The --self-test file says version {}; this Craft Anything reads version {VERSION}.", report.version));
    }
    let (mut played, mut not) = (Vec::new(), Vec::new());
    for (step, shows) in CORE_LOOP {
        let matching: Vec<_> = report.steps.iter().filter(|s| s.step.trim().eq_ignore_ascii_case(step)).collect();
        match matching.as_slice() {
            [s] if s.passed => played.push(if s.detail.trim().is_empty() { step.to_string() } else { format!("{step} ({})", s.detail.trim()) }),
            [s] => not.push(format!("{step}: {shows}, failed{}", if s.detail.trim().is_empty() { String::new() } else { format!(" ({})", s.detail.trim()) })),
            [] => not.push(format!("{step}: {shows}, not tested")),
            _ => not.push(format!("{step}: {shows}, reported more than once (ambiguous result)")),
        }
    }
    if !not.is_empty() {
        return (
            false,
            format!(
                "The self-test doesn't show the core loop working yet: {} of {} steps don't pass ({}). Build what's missing so a player can move, act and see the on-screen information, then measure each step from the game's own state in --self-test.",
                not.len(),
                CORE_LOOP.len(),
                not.join("; ")
            ),
        );
    }
    (true, format!("The self-test plays the core loop: {}.", played.join(", ")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use craft_anything_engine::tools::web_common::Image;

    #[test]
    fn a_picture_that_did_not_change_is_not_evidence() {
        let shot = Image { width: 8, height: 8, rgba: vec![20; 8 * 8 * 4] };
        let same = Image { width: 8, height: 8, rgba: vec![20; 8 * 8 * 4] };
        let changed = Image { width: 8, height: 8, rgba: vec![200; 8 * 8 * 4] };
        assert!(!picture_changed(perceptual_similarity(&same, &shot)), "the same screen is not evidence");
        assert!(picture_changed(perceptual_similarity(&changed, &shot)), "a changed screen is");
        assert!(SELF_TEST_CONTRACT.contains("\"picture\""), "the brief asks for the picture");
    }

    #[test]
    fn the_core_loop_passes_only_when_every_step_does() {
        let all = br#"{"version": 1, "steps": [{"step": "move", "passed": true, "detail": "moved 4 cells"}, {"step": "Act", "passed": true}, {"step": "hud", "passed": true, "detail": "score 1"}]}"#;
        let (ok, note) = verdict(all);
        assert!(ok, "{note}");
        assert_eq!(note, "The self-test plays the core loop: move (moved 4 cells), act, hud (score 1).");

        // A first slice: the level draws, but nobody plays in it.
        let slice = br#"{"steps": [{"step": "move", "passed": false, "detail": "no player in the level"}, {"step": "hud", "passed": true}]}"#;
        let (ok, note) = verdict(slice);
        assert!(!ok);
        assert!(note.contains("2 of 3 steps don't pass (move") && note.contains("move: the player moves, failed (no player in the level)"), "{note}");
        assert!(note.contains("act: the player's main action works, not tested") && !note.contains("hud:"), "{note}");

        for bad in [&b"not json"[..], br#"{"version": 1}"#, br#"{"steps": [{"step": "move"}]}"#] {
            let (ok, note) = verdict(bad);
            assert!(!ok && note.contains("can't be read") && note.contains("--self-test <file.json>"), "{note}");
        }
        let (ok, note) = verdict(br#"{"version": 2, "steps": []}"#);
        assert!(!ok && note.contains("version 2"), "{note}");
        let (ok, note) = verdict(br#"{"version": 0, "steps": []}"#);
        assert!(!ok && note.contains("version 0"), "{note}");
        let (ok, note) =
            verdict(br#"{"steps": [{"step":"move","passed":true},{"step":"move","passed":false},{"step":"act","passed":true},{"step":"hud","passed":true}]}"#);
        assert!(!ok && note.contains("reported more than once"), "{note}");
    }

    #[test]
    fn each_step_of_the_self_test_is_a_claim() {
        assert!(claims(all_steps()).iter().all(|c| c.passed) && claims(all_steps()).len() == 3);
        let slice = br#"{"steps": [{"step": "move", "passed": false, "detail": "no player in the level"}, {"step": "hud", "passed": true}, {"step": "reload", "passed": false, "detail": "ammo stays at 0"}]}"#;
        let got: Vec<(String, bool, String)> = claims(slice).into_iter().map(|c| (c.title, c.passed, c.detail)).collect();
        assert_eq!(
            got,
            [
                ("In play, the player moves".to_string(), false, "no player in the level".to_string()),
                ("In play, the player's main action works".to_string(), false, "not tested".to_string()),
                ("In play, the on-screen information is drawn".to_string(), true, String::new()),
                ("Self-test step \"reload\"".to_string(), false, "ammo stays at 0".to_string()),
            ],
            "the core loop first (untested steps fail), then the game's own steps"
        );
        assert!(claims(b"not json").is_empty());
        let duplicate = br#"{"steps": [{"step": "move", "passed": true}, {"step": "MOVE", "passed": true}, {"step": "act", "passed": true}, {"step": "hud", "passed": true}]}"#;
        assert!(!claims(duplicate)[0].passed, "duplicate core-loop steps have ambiguous evidence");
    }

    fn all_steps() -> &'static [u8] {
        br#"{"version": 1, "steps": [{"step": "move", "passed": true, "detail": "moved 4 cells"}, {"step": "Act", "passed": true}, {"step": "hud", "passed": true, "detail": "score 1"}]}"#
    }
}
