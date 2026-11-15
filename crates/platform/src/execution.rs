//! The execution context every process launch passes through.
//!
//! A program launch is classified at the boundary that asked for it (a tool, a check, a rebuild
//! run or a chat), and [`crate::process`] consults the context before it spawns. The policy itself
//! lives above this layer: the engine installs one with [`with_policy`], so this module knows only
//! the class and the decision, not what a build sandbox is.
//!
//! A launch with no context is allowed (tests, setup helpers); an [`Class::Unclassified`] launch
//! under a policy that refuses it does not start. The context is thread-local and restored when
//! the call returns, so one tool's class cannot leak into the next.

use craft_anything_evidence::ToolError;
use std::cell::RefCell;
use std::sync::Arc;

/// What a process launch is for, as the boundary that asked for it knows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// A route that can run the build's generated code, by its stable id.
    Generated(&'static str),
    /// An external tool used to study the target (adb, Frida, Ghidra, …).
    TargetTool,
    /// A vendor installer.
    Installer,
    /// A coding agent process.
    Agent,
    /// Our own housekeeping (keep-awake, open a folder, a version check).
    Housekeeping,
    /// No boundary classified this launch; under `refuse` it must not start.
    Unclassified,
}

/// The decision a launch is held to.
pub trait Policy: Send + Sync {
    /// Whether `class` may start now.
    fn check(&self, class: Class) -> Result<(), ToolError>;
}

struct Context {
    policy: Arc<dyn Policy>,
    default: Class,
}

thread_local! {
    static CONTEXT: RefCell<Option<Context>> = const { RefCell::new(None) };
}

/// Run `f` with `policy` as the decision for every process launch it makes, classified as
/// `default` unless the launch is given a class of its own.
pub fn with_policy<R>(policy: Arc<dyn Policy>, default: Class, f: impl FnOnce() -> R) -> R {
    struct Restore(Option<Context>);
    impl Drop for Restore {
        fn drop(&mut self) {
            CONTEXT.with(|c| *c.borrow_mut() = self.0.take());
        }
    }
    let previous = CONTEXT.with(|c| c.borrow_mut().replace(Context { policy, default }));
    let _restore = Restore(previous);
    f()
}

/// Whether the current launch may start. No context means no decision to make.
pub(crate) fn check() -> Result<(), ToolError> {
    CONTEXT.with(|c| {
        let c = c.borrow();
        let Some(ctx) = c.as_ref() else { return Ok(()) };
        ctx.policy.check(ctx.default)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct OnlyAgent;

    impl Policy for OnlyAgent {
        fn check(&self, class: Class) -> Result<(), ToolError> {
            if class == Class::Agent { Ok(()) } else { Err(ToolError::invalid("not an agent")) }
        }
    }

    #[test]
    fn a_policy_sees_the_default_class() {
        with_policy(Arc::new(OnlyAgent), Class::Agent, || assert!(check().is_ok()));
        with_policy(Arc::new(OnlyAgent), Class::Housekeeping, || assert!(check().is_err()));
    }

    #[test]
    fn the_context_is_restored_after_a_call() {
        with_policy(Arc::new(OnlyAgent), Class::Housekeeping, || assert!(check().is_err()));
        assert!(check().is_ok(), "outside a context there is no decision");
    }

    #[test]
    fn a_nested_call_restores_its_parent() {
        with_policy(Arc::new(OnlyAgent), Class::Agent, || {
            with_policy(Arc::new(OnlyAgent), Class::Housekeeping, || assert!(check().is_err()));
            assert!(check().is_ok(), "the parent class is back");
        });
    }

    struct Refuse;

    impl Policy for Refuse {
        fn check(&self, _class: Class) -> Result<(), ToolError> {
            Err(ToolError::new(craft_anything_evidence::ErrorCode::FeatureMissing, "refused"))
        }
    }

    struct Allow;

    impl Policy for Allow {
        fn check(&self, _class: Class) -> Result<(), ToolError> {
            Ok(())
        }
    }

    /// The check is at the process boundary, before `spawn`: an unclassified launch under a
    /// refusing policy never starts.
    #[cfg(unix)]
    #[test]
    fn an_unclassified_launch_is_refused_before_it_starts() {
        let err = with_policy(Arc::new(Refuse), Class::Unclassified, || {
            crate::process::Process::new("/bin/echo").args(["hi"]).timeout(std::time::Duration::from_secs(5)).run()
        })
        .unwrap_err();
        assert_eq!(err.code, craft_anything_evidence::ErrorCode::FeatureMissing);
    }

    /// A classified launch the policy allows still runs.
    #[cfg(unix)]
    #[test]
    fn a_classified_launch_runs_when_the_policy_allows_it() {
        let out = with_policy(Arc::new(Allow), Class::TargetTool, || {
            crate::process::Process::new("/bin/echo").args(["hi"]).timeout(std::time::Duration::from_secs(5)).run()
        })
        .unwrap();
        assert!(out.success);
    }
}
