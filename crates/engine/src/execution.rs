//! One policy for every route that runs a build's own generated code.
//!
//! The Docker sandbox confines a build command. It cannot confine a program that is native to
//! this computer (a built game, a self-test) or a coding agent that runs its own tools and signs
//! in as the person. Those routes are not silently outside the boundary: each declares what
//! confines it ([`Route::confinement`]), and [`Policy`] decides whether it may run once the person
//! has asked for confinement. `[build] sandbox_unconfined = "refuse"` turns every route that
//! can't be confined into a plain error (a tool) or a check that records `unknown` with the
//! reason, instead of a quiet fallback.

use crate::settings::{BuildSettings, SandboxMode, Unconfined};
use craft_anything_evidence::{ErrorCode, ToolError};
use serde_json::json;

/// A route by which a build's own generated code runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    /// A shell command the builder proposed (`build_command_run`).
    BuildCommand,
    /// A check's own build of the project (`cargo build`, `npm run build`, an engine's build).
    BuildCheck,
    /// The runs comparison: the built console program is started with the original's inputs.
    RunsCheck,
    /// A person's own project's build or tests, edited in place.
    OwnCheck,
    /// The independent replay probe driving the built game (`build_probe_run`).
    GameProbe,
    /// A build's own self-test or screenshot run of the built program.
    SelfTest,
    /// A coding agent process (Claude Code, Codex, Gemini CLI, …).
    Agent,
}

/// What confines a route, as the code can actually enforce it today.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Confinement {
    /// A Docker container that sees only what the route is given.
    Container,
    /// It must run on this computer, for the stated reason.
    HostOnly(&'static str),
}

impl Route {
    /// What confines this route. A reason is shown to the person and recorded in results.
    pub fn confinement(self) -> Confinement {
        match self {
            Route::BuildCommand => Confinement::Container,
            Route::BuildCheck => Confinement::HostOnly("the check builds with the project's own toolchain on this computer"),
            Route::RunsCheck => Confinement::HostOnly("the built program is native to this computer"),
            Route::OwnCheck => Confinement::HostOnly("the project's own toolchain runs on this computer"),
            Route::GameProbe => Confinement::HostOnly("the built game is native to this computer"),
            Route::SelfTest => Confinement::HostOnly("the built program is native to this computer and opens a window"),
            Route::Agent => Confinement::HostOnly("the agent runs its own tools and signs in as you"),
        }
    }

    /// The route's stable name, for results and errors.
    pub fn id(self) -> &'static str {
        match self {
            Route::BuildCommand => "build-command",
            Route::BuildCheck => "build-check",
            Route::RunsCheck => "runs-check",
            Route::OwnCheck => "own-check",
            Route::GameProbe => "game-probe",
            Route::SelfTest => "self-test",
            Route::Agent => "coding-agent",
        }
    }

    /// A plain-English name for errors.
    pub fn title(self) -> &'static str {
        match self {
            Route::BuildCommand => "A build command",
            Route::BuildCheck => "The check's build",
            Route::RunsCheck => "The runs comparison",
            Route::OwnCheck => "A person's own project build or tests",
            Route::GameProbe => "The game probe",
            Route::SelfTest => "A build's screenshot or self-test",
            Route::Agent => "A coding agent",
        }
    }
}

/// The tools whose route the engine checks before it runs them.
///
/// A tool that runs a build's code but isn't here is checked by its own code (a check) or by
/// the build that starts it (an agent); a test keeps this list in step with the build tools.
pub const TOOL_ROUTES: &[(&str, Route)] = &[("build_command_run", Route::BuildCommand), ("build_probe_run", Route::GameProbe)];

/// The route a tool id runs, when the engine can decide it before the tool starts.
pub fn route_of(tool: &str) -> Option<Route> {
    TOOL_ROUTES.iter().find(|(id, _)| *id == tool).map(|(_, route)| *route)
}

/// What the person asked for, as it applies to a route.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Policy {
    pub sandbox: SandboxMode,
    pub unconfined: Unconfined,
}

impl Policy {
    pub fn of(build: &BuildSettings) -> Policy {
        Policy { sandbox: build.sandbox, unconfined: build.sandbox_unconfined }
    }

    /// Why `route` runs on this computer when the person asked for confinement, if it does.
    ///
    /// The caller puts this in its approval card or result, so an unconfined run is never
    /// mistaken for a confined one.
    pub fn outside(self, route: Route) -> Option<&'static str> {
        match (self.sandbox, route.confinement()) {
            (SandboxMode::Docker, Confinement::HostOnly(why)) => Some(why),
            _ => None,
        }
    }

    /// Whether `route` may run now.
    pub fn check(self, route: Route) -> Result<(), ToolError> {
        let Some(why) = self.outside(route) else { return Ok(()) };
        if self.unconfined == Unconfined::Allow {
            return Ok(());
        }
        // Not ConsentRequired: approving a consent card retries the call with consent granted,
        // and this refusal ignores consent. Clients that ask on ConsentRequired would offer an
        // approval that can never let the route run.
        Err(ToolError::new(
            ErrorCode::FeatureMissing,
            format!("{} can't run: it runs on this computer, outside the Docker sandbox, because {why}.", route.title()),
        )
        .with_remediation("Set `[build] sandbox_unconfined = \"allow\"` in config.toml (or turn the sandbox off) to let it run here.")
        .with_details(json!({"reason": "sandbox_unconfined", "route": route.id(), "confinement": "host-only", "why": why})))
    }
}

/// A sentence for a result that ran on this computer while the person asked for the sandbox.
///
/// `None` when the sandbox is off or the route is confined, so a caller can append it blindly.
pub fn outside_note(policy: Policy, route: Route) -> Option<String> {
    policy.outside(route).map(|why| format!(" It ran on this computer, outside the Docker sandbox: {why}."))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build(sandbox: SandboxMode, unconfined: Unconfined) -> BuildSettings {
        BuildSettings { sandbox, sandbox_unconfined: unconfined, ..Default::default() }
    }

    const ALL: [Route; 7] = [Route::BuildCommand, Route::BuildCheck, Route::RunsCheck, Route::OwnCheck, Route::GameProbe, Route::SelfTest, Route::Agent];
    const HOST_ONLY: [Route; 6] = [Route::BuildCheck, Route::RunsCheck, Route::OwnCheck, Route::GameProbe, Route::SelfTest, Route::Agent];

    #[test]
    fn with_the_sandbox_off_every_route_runs_here() {
        let policy = Policy::of(&build(SandboxMode::Off, Unconfined::Refuse));
        for route in ALL {
            assert!(policy.check(route).is_ok(), "{route:?}");
            assert_eq!(policy.outside(route), None, "{route:?}");
        }
    }

    #[test]
    fn a_confined_command_is_never_refused() {
        let policy = Policy::of(&build(SandboxMode::Docker, Unconfined::Refuse));
        assert!(policy.check(Route::BuildCommand).is_ok());
        assert_eq!(policy.outside(Route::BuildCommand), None);
    }

    #[test]
    fn a_route_that_cannot_be_confined_says_so_when_it_is_allowed() {
        let policy = Policy::of(&build(SandboxMode::Docker, Unconfined::Allow));
        for route in HOST_ONLY {
            assert!(policy.check(route).is_ok(), "{route:?}");
            assert!(policy.outside(route).is_some_and(|why| !why.is_empty()), "{route:?}");
            assert!(outside_note(policy, route).is_some_and(|note| note.contains("outside the Docker sandbox")), "{route:?}");
        }
        assert_eq!(outside_note(policy, Route::BuildCommand), None);
    }

    #[test]
    fn refuse_stops_every_route_that_would_run_outside() {
        let policy = Policy::of(&build(SandboxMode::Docker, Unconfined::Refuse));
        for route in HOST_ONLY {
            let err = policy.check(route).unwrap_err();
            assert_eq!(err.code, ErrorCode::FeatureMissing, "{route:?}");
            assert!(err.message.contains("outside the Docker sandbox"), "{}", err.message);
            assert!(err.remediation.as_deref().is_some_and(|r| r.contains("sandbox_unconfined")), "{route:?}");
            let reason = err.details.as_ref().and_then(|d| d.get("reason")).and_then(|v| v.as_str());
            assert_eq!(reason, Some("sandbox_unconfined"), "{route:?}");
        }
        assert!(policy.check(Route::BuildCommand).is_ok());
    }

    #[test]
    fn the_build_tools_are_the_routes_the_engine_can_check() {
        assert_eq!(route_of("build_command_run"), Some(Route::BuildCommand));
        assert_eq!(route_of("build_probe_run"), Some(Route::GameProbe));
        assert_eq!(route_of("build_file_write"), None);
        assert_eq!(TOOL_ROUTES.len(), 2);
    }
}
