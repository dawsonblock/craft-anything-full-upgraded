//! What each coding agent can be held to, as data rather than prose.
//!
//! A build's agent writes and runs code with the person's own access unless something holds it in.
//! Three things can: our approval tool (every edit, command and fetch asks us), the agent's own
//! sandbox, or nothing. The default is nothing, so a new adapter never claims more than it has
//! been checked for; `[ai] require_confined_build` refuses a build whose agent says nothing.

use serde::Serialize;

/// What holds an agent's work in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Bound {
    /// Every edit, command and fetch asks us first, through our approval tool.
    OurApprovals,
    /// The agent confines its own work (a workspace sandbox) and asks us for what leaves it.
    OwnSandbox,
    /// Neither: it runs with the person's own access.
    Nothing,
}

impl Bound {
    pub fn as_str(self) -> &'static str {
        match self {
            Bound::OurApprovals => "our-approvals",
            Bound::OwnSandbox => "own-sandbox",
            Bound::Nothing => "nothing",
        }
    }
}

/// What an adapter can be held to in each access mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Confinement {
    /// A build: the agent may edit files and run commands.
    pub build: Bound,
    /// A chat outside a build.
    pub chat: Bound,
    /// A question about a build (reading only).
    pub question: Bound,
}

impl Confinement {
    /// The honest default for an adapter nobody has checked: it runs with the person's access.
    pub const NOTHING: Confinement = Confinement { build: Bound::Nothing, chat: Bound::Nothing, question: Bound::Nothing };

    /// Our own agent loop: every tool call goes through the approval policy.
    pub const OUR_APPROVALS: Confinement = Confinement { build: Bound::OurApprovals, chat: Bound::OurApprovals, question: Bound::OurApprovals };

    /// A build held in by the agent's own workspace sandbox; its other modes are not established.
    pub const OWN_SANDBOX_BUILD: Confinement = Confinement { build: Bound::OwnSandbox, chat: Bound::Nothing, question: Bound::Nothing };

    /// A build that asks us before it edits, commands or fetches; its other modes are not established.
    pub const OUR_APPROVALS_BUILD: Confinement = Confinement { build: Bound::OurApprovals, chat: Bound::Nothing, question: Bound::Nothing };

    /// Whether a build by this adapter would run with the person's own access.
    pub fn build_is_unconfined(self) -> bool {
        self.build == Bound::Nothing
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_claims_nothing() {
        assert!(Confinement::NOTHING.build_is_unconfined());
        assert_eq!(Confinement::NOTHING.build.as_str(), "nothing");
    }

    #[test]
    fn our_own_loop_is_held_by_our_approvals() {
        assert!(!Confinement::OUR_APPROVALS.build_is_unconfined());
        assert_eq!(Confinement::OUR_APPROVALS.build.as_str(), "our-approvals");
    }

    #[test]
    fn a_sandboxed_build_still_says_nothing_about_a_chat() {
        let confinement = Confinement::OWN_SANDBOX_BUILD;
        assert!(!confinement.build_is_unconfined());
        assert_eq!(confinement.chat, Bound::Nothing);
        assert_eq!(confinement.question, Bound::Nothing);
    }
}
