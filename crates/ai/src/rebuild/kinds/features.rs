//! A project with documents about the original (a manual, notes in `reference/docs/`) keeps a
//! checklist of what they describe in `app/features.json` ([`craft_anything_engine::features`]).
//! [`FeaturesCheck`] holds the build to it: every feature is built, saying where, or left out on
//! purpose, saying why (a maintainer's decision, 2026-11-21: a feature left out with its reason
//! counts as accounted for, and the reasons are shown with the check). Any other feature keeps the
//! build from Ready.

use super::*;
use craft_anything_engine::features::{self, ChecklistProblem, FeatureStatus, Unaccounted};
use craft_anything_engine::workspace::Claim;

/// What the brief asks for when the project has documents, so the check can read the checklist.
pub const FEATURES_CONTRACT: &str = "List every feature the documents describe in app/features.json, which Craft Anything reads after each build: {\"version\": 1, \"features\": [{\"name\": \"Export to PDF\", \"source\": \"reference/docs/Manual.pdf, chapter 4\", \"status\": \"built\", \"where\": \"src/export.rs\"}, {\"name\": \"Cloud sync\", \"status\": \"left_out\", \"reason\": \"it needs the vendor's own servers\"}, {\"name\": \"Macros\", \"status\": \"todo\"}]}. Each feature is \"built\" (say where), \"left_out\" on purpose (say why: the person sees the reason) or \"todo\". The build isn't ready while any is todo, or built without a where, or left out without a reason.";

/// At most this many names are listed in a note.
const LISTED: usize = 8;
/// At most this many claims are kept, one per feature.
const MAX_CLAIMS: usize = 60;

pub struct FeaturesCheck;

impl FeaturesCheck {
    pub const ID: &'static str = "features";
}

impl Check for FeaturesCheck {
    fn id(&self) -> &'static str {
        Self::ID
    }

    /// Only a project with documents about the original is asked for a checklist.
    fn applies(&self, cx: &CheckCx) -> bool {
        !cx.ws.reference_documents().is_empty()
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let round = cx.round;
        if let Err(e) = cx.ws.app_dir_checked() {
            return Ok(CheckResult::unknown(self.id(), round, e.message));
        }
        let list = match features::read(&cx.ws.features_path()) {
            Ok(l) => l,
            Err(p) => return Ok(CheckResult::new(self.id(), round, false, format!("{} {FEATURES_CONTRACT}", problem_note(&p)))),
        };
        Ok(judge(&list, round))
    }
}

/// The check's result for a readable checklist.
pub(crate) fn judge(list: &features::Features, round: u32) -> CheckResult {
    let count = |s: FeatureStatus| list.features.iter().filter(|f| f.status == s).count();
    let (built, left_out) = (count(FeatureStatus::Built), count(FeatureStatus::LeftOut));
    let mut note = format!("app/features.json lists {} features from the documents: {built} built, {left_out} left out on purpose.", list.features.len());
    let open: Vec<String> = list
        .features
        .iter()
        .filter_map(|f| {
            f.unaccounted().map(|u| match u {
                Unaccounted::Todo => format!("{} (todo)", f.name),
                Unaccounted::NoLocation => format!("{} (built, but not where)", f.name),
                Unaccounted::NoReason => format!("{} (left out, but not why)", f.name),
            })
        })
        .collect();
    if !open.is_empty() {
        note.push_str(&format!(
            " {} aren't accounted for yet: {}. Build each one and say where, or mark it \"left_out\" with the reason the person will see.",
            open.len(),
            listed(&open)
        ));
    }
    // The reasons are the person's to read, so the note keeps them.
    let reasons: Vec<String> = list
        .features
        .iter()
        .filter(|f| f.status == FeatureStatus::LeftOut && f.unaccounted().is_none())
        .map(|f| format!("{}: {}", f.name, f.reason.trim()))
        .collect();
    if !reasons.is_empty() {
        note.push_str(&format!(" Left out: {}.", listed(&reasons)));
    }
    let claims = list
        .features
        .iter()
        .take(MAX_CLAIMS)
        .map(|f| match (f.status, f.unaccounted()) {
            (FeatureStatus::Built, None) => Claim::new(format!("{} is built ({})", f.name, f.location.trim()), true, ""),
            (FeatureStatus::LeftOut, None) => Claim::new(format!("{} is left out on purpose: {}", f.name, f.reason.trim()), true, ""),
            (_, Some(Unaccounted::NoLocation)) => Claim::new(format!("{} is built", f.name), false, "it doesn't say where"),
            (_, Some(Unaccounted::NoReason)) => Claim::new(format!("{} is left out on purpose", f.name), false, "it doesn't say why"),
            _ => Claim::new(format!("{} is accounted for", f.name), false, "still to do"),
        })
        .collect();
    CheckResult { claims, ..CheckResult::new(FeaturesCheck::ID, round, open.is_empty(), note) }
}

fn problem_note(p: &ChecklistProblem) -> String {
    match p {
        ChecklistProblem::Missing => "There's no app/features.json yet.".into(),
        ChecklistProblem::TooLarge => format!("app/features.json is over {} KB: list the features, not their descriptions.", features::MAX_BYTES / 1024),
        ChecklistProblem::Unreadable(why) => format!("app/features.json can't be read: {why}."),
        ChecklistProblem::Newer(v) => format!("app/features.json says version {v}; this Craft Anything reads version {}.", features::FEATURES_VERSION),
        ChecklistProblem::Empty => "app/features.json lists no features.".into(),
    }
}

/// "a, b, c and 4 more".
fn listed(names: &[String]) -> String {
    let shown = names.iter().take(LISTED).cloned().collect::<Vec<_>>().join(", ");
    match names.len().saturating_sub(LISTED) {
        0 => shown,
        more => format!("{shown} and {more} more"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn list(json: &str) -> features::Features {
        features::parse(json.as_bytes()).unwrap()
    }

    #[test]
    fn built_and_left_out_with_a_reason_pass_and_the_reasons_are_shown() {
        let r = judge(
            &list(
                r#"{"features": [
                    {"name": "Export", "status": "built", "where": "src/export.rs"},
                    {"name": "Cloud sync", "status": "left_out", "reason": "it needs the vendor's servers"}
                ]}"#,
            ),
            1,
        );
        assert!(r.passed(), "{}", r.note);
        assert!(r.note.contains("Left out: Cloud sync: it needs the vendor's servers."), "{}", r.note);
        assert!(r.claims.iter().all(|c| c.passed));
        assert!(r.claims.iter().any(|c| c.title == "Cloud sync is left out on purpose: it needs the vendor's servers"), "{:?}", r.claims);
    }

    #[test]
    fn todo_or_a_missing_where_or_reason_keeps_the_build_from_ready() {
        let r = judge(
            &list(
                r#"{"features": [
                    {"name": "Export", "status": "built", "where": "src/export.rs"},
                    {"name": "Macros", "status": "todo"},
                    {"name": "Themes", "status": "built", "where": " "},
                    {"name": "Plugins", "status": "left_out"}
                ]}"#,
            ),
            2,
        );
        assert!(r.failed());
        assert!(r.note.contains("3 aren't accounted for yet: Macros (todo), Themes (built, but not where), Plugins (left out, but not why)"), "{}", r.note);
        assert_eq!(r.claims.iter().filter(|c| !c.passed).count(), 3);
    }

    #[test]
    fn a_missing_checklist_fails_with_what_to_write() {
        let note = format!("{} {FEATURES_CONTRACT}", problem_note(&ChecklistProblem::Missing));
        assert!(note.starts_with("There's no app/features.json yet.") && note.contains("\"left_out\""), "{note}");
        let parsed = features::parse(
            br#"{"version": 1, "features": [{"name": "Export to PDF", "source": "reference/docs/Manual.pdf, chapter 4", "status": "built", "where": "src/export.rs"}, {"name": "Cloud sync", "status": "left_out", "reason": "it needs the vendor's own servers"}, {"name": "Macros", "status": "todo"}]}"#,
        );
        assert!(parsed.is_ok(), "the contract's own example reads");
        assert!(FEATURES_CONTRACT.contains(r#""where": "src/export.rs""#), "and is the example above");
    }
}
