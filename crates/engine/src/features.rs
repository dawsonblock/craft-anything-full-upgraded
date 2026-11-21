//! `app/features.json`: what the documents about the original (a manual, notes in
//! `reference/docs/`) say it does, as a checklist the features check reads after each build. Each
//! feature is either built, saying where, or left out on purpose, saying why; one that is neither
//! keeps the build from Ready. Its schema is published in `docs/schemas/features.schema.json`
//! (`cargo xtask schemas`).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::path::Path;

/// The checklist's current version. One without a version is read as this version.
pub const FEATURES_VERSION: u32 = 1;
/// Its file name inside `app/`.
pub const FEATURES: &str = "features.json";
/// A bigger checklist isn't read.
pub const MAX_BYTES: u64 = 1024 * 1024;
/// At most this many features are read; the rest are ignored.
pub const MAX_FEATURES: usize = 1000;

/// Every feature the documents describe.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Features {
    #[serde(default = "current_version")]
    pub version: u32,
    pub features: Vec<Feature>,
}

fn current_version() -> u32 {
    FEATURES_VERSION
}

/// One feature: "Export to PDF", from chapter 4 of the manual, built in `src/export.rs`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Feature {
    /// What the documents call it.
    pub name: String,
    /// Where the documents describe it ("reference/docs/Manual.pdf, chapter 4"), or empty.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub source: String,
    pub status: FeatureStatus,
    /// For a built feature: where it is in `app/` (a file, a screen).
    #[serde(default, rename = "where", skip_serializing_if = "String::is_empty")]
    pub location: String,
    /// For a feature left out: why.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub reason: String,
}

/// Where a feature stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum FeatureStatus {
    /// In the build; `where` says where.
    Built,
    /// Not built, on purpose; `reason` says why.
    LeftOut,
    /// Not built yet.
    Todo,
}

/// What a feature still needs before it counts as accounted for, if anything.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Unaccounted {
    /// Marked `todo`.
    Todo,
    /// Built, but without saying where.
    NoLocation,
    /// Left out, but without saying why.
    NoReason,
}

impl Feature {
    /// `None` when it's built (saying where) or left out on purpose (saying why).
    pub fn unaccounted(&self) -> Option<Unaccounted> {
        match self.status {
            FeatureStatus::Todo => Some(Unaccounted::Todo),
            FeatureStatus::Built if self.location.trim().is_empty() => Some(Unaccounted::NoLocation),
            FeatureStatus::LeftOut if self.reason.trim().is_empty() => Some(Unaccounted::NoReason),
            _ => None,
        }
    }
}

/// Why a checklist can't be used.
#[derive(Debug, Clone, PartialEq)]
pub enum ChecklistProblem {
    Missing,
    TooLarge,
    /// Not JSON, or not this shape: the parser's reason.
    Unreadable(String),
    /// Written for a newer Craft Anything.
    Newer(u32),
    /// No features.
    Empty,
}

/// Read a checklist from `path`. Features past [`MAX_FEATURES`] are dropped.
pub fn read(path: &Path) -> Result<Features, ChecklistProblem> {
    let Ok(meta) = std::fs::metadata(path) else { return Err(ChecklistProblem::Missing) };
    if !meta.is_file() {
        return Err(ChecklistProblem::Missing);
    }
    if meta.len() > MAX_BYTES {
        return Err(ChecklistProblem::TooLarge);
    }
    let bytes = std::fs::read(path).map_err(|_| ChecklistProblem::Missing)?;
    parse(&bytes)
}

/// Parse a checklist's bytes.
pub fn parse(bytes: &[u8]) -> Result<Features, ChecklistProblem> {
    let mut list: Features = serde_json::from_slice(bytes).map_err(|e| ChecklistProblem::Unreadable(e.to_string()))?;
    if list.version > FEATURES_VERSION {
        return Err(ChecklistProblem::Newer(list.version));
    }
    list.features.truncate(MAX_FEATURES);
    if list.features.is_empty() {
        return Err(ChecklistProblem::Empty);
    }
    Ok(list)
}

/// The JSON Schema of `features.json` as published in `docs/schemas/features.schema.json`.
pub fn features_schema() -> String {
    let schema = schemars::generate::SchemaSettings::draft2020_12().into_generator().into_root_schema_for::<Features>();
    serde_json::to_string_pretty(&schema).map(|s| s + "\n").unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_checklist_reads_built_left_out_and_todo() {
        let list = parse(
            br#"{"features": [
                {"name": "Export to PDF", "source": "reference/docs/Manual.pdf, chapter 4", "status": "built", "where": "src/export.rs"},
                {"name": "Cloud sync", "status": "left_out", "reason": "it needs the vendor's servers"},
                {"name": "Macros", "status": "todo"},
                {"name": "Themes", "status": "built"},
                {"name": "Plugins", "status": "left_out", "reason": "  "}
            ]}"#,
        )
        .unwrap();
        assert_eq!(list.version, FEATURES_VERSION, "a missing version is the current one");
        let states: Vec<_> = list.features.iter().map(Feature::unaccounted).collect();
        assert_eq!(states, [None, None, Some(Unaccounted::Todo), Some(Unaccounted::NoLocation), Some(Unaccounted::NoReason)]);
        assert_eq!(list.features[0].location, "src/export.rs", "`where` is read");
    }

    #[test]
    fn a_checklist_that_cant_be_used_says_why() {
        assert_eq!(parse(br#"{"features": []}"#), Err(ChecklistProblem::Empty));
        assert_eq!(parse(br#"{"version": 9, "features": [{"name": "a", "status": "todo"}]}"#), Err(ChecklistProblem::Newer(9)));
        assert!(matches!(parse(b"{ not json"), Err(ChecklistProblem::Unreadable(_))));
        assert!(matches!(parse(br#"{"features": [{"name": "a", "status": "maybe"}]}"#), Err(ChecklistProblem::Unreadable(_))), "an unknown status");
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read(&dir.path().join("features.json")), Err(ChecklistProblem::Missing));
        assert_eq!(read(dir.path()), Err(ChecklistProblem::Missing), "a folder isn't a checklist");
        let big = dir.path().join("big.json");
        std::fs::File::create(&big).unwrap().set_len(MAX_BYTES + 1).unwrap();
        assert_eq!(read(&big), Err(ChecklistProblem::TooLarge));
    }

    #[test]
    fn a_long_checklist_is_cut_at_its_limit() {
        let features: Vec<_> = (0..MAX_FEATURES + 5).map(|i| serde_json::json!({"name": format!("f{i}"), "status": "todo"})).collect();
        let bytes = serde_json::to_vec(&serde_json::json!({"features": features})).unwrap();
        assert_eq!(parse(&bytes).unwrap().features.len(), MAX_FEATURES);
    }
}
