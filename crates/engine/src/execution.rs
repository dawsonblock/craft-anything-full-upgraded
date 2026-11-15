//! One policy for every route that runs a build's own generated code.
//!
//! The Docker sandbox confines a build command. It cannot confine a program that is native to
//! this computer (a built game, a self-test) or a coding agent that runs its own tools and signs
//! in as the person. Those routes are not silently outside the boundary: each declares what
//! confines it ([`Route::confinement`]), and [`Policy`] decides whether it may run once the person
//! has asked for confinement. `[build] sandbox_unconfined = "refuse"` turns every route that
//! can't be confined into a plain error (a tool) or a check that records `unknown` with the
//! reason, instead of a quiet fallback.
//!
//! The policy is also installed as a [`craft_anything_platform::execution::Policy`] around every
//! tool call, check and agent run, so a process launch the route list misses still fails before it
//! starts under `refuse` ([`BuildPolicy`], [`tool_class`], [`check_class`]).

use crate::settings::{BuildSettings, SandboxMode, Unconfined};
use craft_anything_evidence::{ErrorCode, ToolError};
use craft_anything_platform::execution::Class;
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
    /// Playing the built app or game (`project_play`, `project_pin_open`).
    ProjectPlay,
    /// Running a program under a terminal with scripted input (`runtime_walkthrough_record`).
    RuntimeWalkthrough,
    /// Starting a program to picture its window (`native_window_capture`).
    NativeWindowCapture,
    /// A browser running the built website.
    Website,
    /// The built Electron app.
    Electron,
    /// A coding agent process (Claude Code, Codex, Gemini CLI, …).
    Agent,
    /// A process launch no boundary classified.
    Unclassified,
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
            Route::ProjectPlay => Confinement::HostOnly("the built program is native to this computer"),
            Route::RuntimeWalkthrough => Confinement::HostOnly("the program is native to this computer"),
            Route::NativeWindowCapture => Confinement::HostOnly("the program is native to this computer and opens a window"),
            Route::Website => Confinement::HostOnly("the browser runs the built website on this computer"),
            Route::Electron => Confinement::HostOnly("the built Electron app is native to this computer"),
            Route::Agent => Confinement::HostOnly("the agent runs its own tools and signs in as you"),
            Route::Unclassified => Confinement::HostOnly("this launch is not classified"),
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
            Route::ProjectPlay => "project-play",
            Route::RuntimeWalkthrough => "runtime-walkthrough",
            Route::NativeWindowCapture => "native-window-capture",
            Route::Website => "website",
            Route::Electron => "electron",
            Route::Agent => "coding-agent",
            Route::Unclassified => "unclassified",
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
            Route::ProjectPlay => "Playing the build",
            Route::RuntimeWalkthrough => "The runtime walkthrough",
            Route::NativeWindowCapture => "The window capture",
            Route::Website => "The built website",
            Route::Electron => "The built Electron app",
            Route::Agent => "A coding agent",
            Route::Unclassified => "An unclassified launch",
        }
    }
}

/// The route id a process class names, when it names a known route.
pub fn route_by_id(id: &str) -> Option<Route> {
    Some(match id {
        "build-command" => Route::BuildCommand,
        "build-check" => Route::BuildCheck,
        "runs-check" => Route::RunsCheck,
        "own-check" => Route::OwnCheck,
        "game-probe" => Route::GameProbe,
        "self-test" => Route::SelfTest,
        "project-play" => Route::ProjectPlay,
        "runtime-walkthrough" => Route::RuntimeWalkthrough,
        "native-window-capture" => Route::NativeWindowCapture,
        "website" => Route::Website,
        "electron" => Route::Electron,
        _ => return None,
    })
}

/// What a tool's process launches are, at the engine boundary.
///
/// A tool that starts a program but is not here is [`Class::Unclassified`], which `refuse` stops
/// at the process boundary. That is deliberate: a new tool must say what it is before it can run
/// code under the strict policy.
pub fn tool_class(tool: &str) -> Class {
    match tool {
        "build_command_run" => Class::Generated("build-command"),
        "build_probe_run" => Class::Generated("game-probe"),
        "project_play" | "project_pin_open" => Class::Generated("project-play"),
        "runtime_walkthrough_record" => Class::Generated("runtime-walkthrough"),
        "native_window_capture" => Class::Generated("native-window-capture"),
        "web_site_open" | "web_site_capture" | "web_page_show" | "web_page_states_capture" | "web_page_screenshot_take" | "web_walkthrough_check" => {
            Class::Generated("website")
        }
        _ if tool.starts_with("electron_") => Class::Generated("electron"),
        _ if tool.starts_with("adb_")
            || tool.starts_with("frida_")
            || tool.starts_with("gdb_")
            || tool.starts_with("rizin_")
            || tool.starts_with("packet_")
            || tool.starts_with("external_disasm_")
            || tool.starts_with("apple_")
            || tool.starts_with("ghidra_")
            || tool.starts_with("jadx_")
            || tool.starts_with("apktool_")
            || tool.starts_with("mcp_") =>
        {
            Class::TargetTool
        }
        _ if tool.starts_with("install_") || tool == "engines_install" => Class::Installer,
        "project_open_folder" | "project_preview" => Class::Housekeeping,
        _ => Class::Unclassified,
    }
}

/// What a check's process launches are, at the rebuild worker.
pub fn check_class(check: &str) -> Class {
    match check {
        crate::workspace::CheckResult::BUILD => Class::Generated("build-check"),
        "app-screenshot" | "game-self-test" => Class::Generated("self-test"),
        "app-runs" => Class::Generated("runs-check"),
        "own-tests" => Class::Generated("own-check"),
        "mechanics" | "credits" | "web-visual" => Class::Generated("website"),
        _ => Class::Unclassified,
    }
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

/// The policy installed at every tool, check and agent boundary.
pub struct BuildPolicy {
    pub build: BuildSettings,
}

impl craft_anything_platform::execution::Policy for BuildPolicy {
    fn check(&self, class: Class) -> Result<(), ToolError> {
        let route = match class {
            Class::Generated(id) => route_by_id(id).unwrap_or(Route::Unclassified),
            Class::Agent => Route::Agent,
            Class::Unclassified => Route::Unclassified,
            Class::TargetTool | Class::Installer | Class::Housekeeping => return Ok(()),
        };
        Policy::of(&self.build).check(route)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build(sandbox: SandboxMode, unconfined: Unconfined) -> BuildSettings {
        BuildSettings { sandbox, sandbox_unconfined: unconfined, ..Default::default() }
    }

    const ALL: [Route; 13] = [
        Route::BuildCommand,
        Route::BuildCheck,
        Route::RunsCheck,
        Route::OwnCheck,
        Route::GameProbe,
        Route::SelfTest,
        Route::ProjectPlay,
        Route::RuntimeWalkthrough,
        Route::NativeWindowCapture,
        Route::Website,
        Route::Electron,
        Route::Agent,
        Route::Unclassified,
    ];
    const HOST_ONLY: [Route; 12] = [
        Route::BuildCheck,
        Route::RunsCheck,
        Route::OwnCheck,
        Route::GameProbe,
        Route::SelfTest,
        Route::ProjectPlay,
        Route::RuntimeWalkthrough,
        Route::NativeWindowCapture,
        Route::Website,
        Route::Electron,
        Route::Agent,
        Route::Unclassified,
    ];

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
    fn every_tool_class_names_a_known_route_or_a_non_generated_one() {
        for tool in [
            "build_command_run",
            "build_probe_run",
            "project_play",
            "project_pin_open",
            "runtime_walkthrough_record",
            "native_window_capture",
            "web_site_open",
            "web_page_screenshot_take",
            "electron_app_launch",
        ] {
            let Class::Generated(id) = tool_class(tool) else { panic!("{tool} is not generated") };
            assert!(route_by_id(id).is_some(), "{tool}: {id}");
        }
        for tool in ["adb_devices_find", "frida_attach", "gdb_start", "packet_capture_start", "install_tool"] {
            assert!(matches!(tool_class(tool), Class::TargetTool | Class::Installer), "{tool}");
        }
        assert_eq!(tool_class("build_file_write"), Class::Unclassified);
        assert_eq!(check_class("app-screenshot"), Class::Generated("self-test"));
        assert_eq!(check_class("app-runs"), Class::Generated("runs-check"));
        assert_eq!(check_class("game-self-test"), Class::Generated("self-test"));
        assert_eq!(check_class("build"), Class::Generated("build-check"));
        assert_eq!(check_class("unknown"), Class::Unclassified);
    }

    #[test]
    fn the_installed_policy_refuses_host_only_and_unclassified_launches() {
        let policy = BuildPolicy { build: build(SandboxMode::Docker, Unconfined::Refuse) };
        use craft_anything_platform::execution::Policy as _;
        for class in [
            Class::Generated("game-probe"),
            Class::Generated("project-play"),
            Class::Generated("runtime-walkthrough"),
            Class::Generated("native-window-capture"),
            Class::Generated("website"),
            Class::Generated("electron"),
            Class::Agent,
            Class::Unclassified,
        ] {
            let err = policy.check(class).unwrap_err();
            assert_eq!(err.code, ErrorCode::FeatureMissing, "{class:?}");
        }
        assert!(policy.check(Class::Generated("build-command")).is_ok());
        assert!(policy.check(Class::TargetTool).is_ok());
        assert!(policy.check(Class::Installer).is_ok());
        assert!(policy.check(Class::Housekeeping).is_ok());
    }
}
