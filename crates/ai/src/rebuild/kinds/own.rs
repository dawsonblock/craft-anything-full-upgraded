//! A person's own project, edited in place with nothing to recreate. There's no original to
//! compare it with and nothing may be added to it for our sake (no `--screenshot` option, no
//! mechanics sheet), so it's judged by what it does for itself: its build, then its tests.

use super::super::check::{own_build_check, own_tests_check};
use super::*;

pub struct OwnBuildCheck;

impl Check for OwnBuildCheck {
    fn id(&self) -> &'static str {
        CheckResult::BUILD
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let (outcome, note) = own_build_check(&cx.ws.app_dir(), cx.ws.app_place(), &cx.ws.manifest.stack, cx.editor(), &cx.stop());
        Ok(CheckResult::with_outcome(self.id(), cx.round, outcome, note))
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

    fn required(&self) -> bool {
        false
    }

    fn run(&self, cx: &mut CheckCx) -> Result<CheckResult, ToolError> {
        let app = cx.ws.app_dir();
        let (outcome, note) = own_tests_check(&app, stacks::toolchain_in(&app), &cx.stop());
        Ok(CheckResult { required: false, ..CheckResult::with_outcome(self.id(), cx.round, outcome, note) })
    }
}

/// What judges a person's own project, whatever kind it is.
pub(super) fn checks() -> Vec<Box<dyn Check>> {
    vec![Box::new(OwnBuildCheck), Box::new(OwnTestsCheck)]
}
