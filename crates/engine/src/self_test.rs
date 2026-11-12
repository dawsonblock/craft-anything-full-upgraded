//! What a built game's `--self-test <file.json>` saves: each step of the core loop it played with
//! scripted input, and whether it passed (read by the rebuild's self-test check). Published as
//! `docs/schemas/self-test.schema.json`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The report's current version; one without a version is read as this.
pub const SELF_TEST_VERSION: u32 = 1;

/// A self-test report.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SelfTestReport {
    /// The format version ([`SELF_TEST_VERSION`]); missing means 1.
    #[serde(default = "version")]
    pub version: u32,
    /// A picture the game saved after its scripted input, as a path relative to where it ran.
    /// Craft Anything compares it with the `--screenshot` picture: a screen that did not change
    /// is a report the check cannot trust.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub picture: Option<String>,
    /// The core loop's steps ("move", "act", "hud") as the game measured them.
    pub steps: Vec<SelfTestStep>,
}

fn version() -> u32 {
    SELF_TEST_VERSION
}

/// One step of the core loop.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SelfTestStep {
    /// "move", "act" or "hud" (any case).
    pub step: String,
    pub passed: bool,
    /// What was measured ("walked 3.1 m forward").
    #[serde(default)]
    pub detail: String,
}

/// The JSON Schema of the report as published in `docs/schemas/self-test.schema.json`.
pub fn self_test_schema() -> String {
    let schema = schemars::generate::SchemaSettings::draft2020_12().into_generator().into_root_schema_for::<SelfTestReport>();
    serde_json::to_string_pretty(&schema).map(|s| s + "\n").unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_schema_describes_the_report_and_old_reports_read() {
        let schema = self_test_schema();
        assert!(schema.contains("\"steps\"") && schema.contains("\"passed\""), "{schema}");
        let r: SelfTestReport = serde_json::from_str(r#"{"steps": [{"step": "move", "passed": true}]}"#).unwrap();
        assert_eq!((r.version, r.steps[0].detail.as_str()), (SELF_TEST_VERSION, ""), "no version means 1, no detail means empty");
        assert!(r.picture.is_none() && schema.contains("\"picture\""), "an old report has no picture, and the schema names one");
    }
}
