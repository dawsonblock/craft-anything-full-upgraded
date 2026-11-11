//! A person's own project, edited in place with nothing to recreate. There's no original to
//! compare it with and nothing may be added to it for our sake (no `--screenshot` option, no
//! mechanics sheet), so it's judged by what it does for itself: its build, then its tests.

use super::super::check::{OwnVerdict, own_build_check, own_tests_check};
use super::*;

/// A check whose verdict may be `DidNotRun`: nothing failed (`passed`), but what never ran is
/// unverified, so the result can't count as good enough. It isn't a failed check either, so the
/// run doesn't send the builder back to fix something it has no way to fix.
fn own_result(id: &'static str, round: u32, (verdict, note): (OwnVerdict, String)) -> CheckResult {
    let mut result = CheckResult::new(id, round, verdict.ok(), note);
    result.unverified = verdict == OwnVerdict::DidNotRun;
    result
}

pub struct OwnBuildCheck;

impl Check for OwnBuildCheck {
    fn id(&self) -> &'static str {
        CheckResult::BUILD
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let verdict = own_build_check(&cx.ws.app_dir(), cx.ws.app_place(), &cx.ws.manifest.stack, cx.editor(), &cx.stop());
        Ok(own_result(self.id(), cx.round, verdict))
    }
}

pub struct OwnTestsCheck;

impl OwnTestsCheck {
    pub const ID: &'static str = "own-tests";
}

impl Check for OwnTestsCheck {
    fn id(&self) -> &'static str {
        Self::ID
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let app = cx.ws.app_dir();
        let verdict = own_tests_check(&app, stacks::toolchain_in(&app), &cx.stop());
        Ok(own_result(self.id(), cx.round, verdict))
    }
}

/// What judges a person's own project, whatever kind it is.
pub(super) fn checks() -> Vec<Box<dyn Check>> {
    vec![Box::new(OwnBuildCheck), Box::new(OwnTestsCheck)]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_check_that_never_ran_is_unverified_not_unknown() {
        let never = own_result(CheckResult::BUILD, 2, (OwnVerdict::DidNotRun, "Nothing was built: no known toolchain.".into()));
        assert!(never.passed && never.unverified && never.unknowns.is_empty(), "nothing failed, and nothing is missing to compare: it never ran");
        assert!(!never.good(90.0));
        let failed = own_result(CheckResult::BUILD, 2, (OwnVerdict::Ran(false), "Build failed".into()));
        assert!(!failed.passed && !failed.unverified);
        let passed = own_result(CheckResult::BUILD, 2, (OwnVerdict::Ran(true), "Build succeeded".into()));
        assert!(passed.passed && !passed.unverified && passed.good(90.0));
    }
}
