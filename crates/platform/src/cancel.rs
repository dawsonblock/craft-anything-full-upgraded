//! Cooperative cancellation, shared with long-running work: the engine's tools, the processes it
//! runs and the transports (`engines`) they talk through.

use craft_anything_evidence::ToolError;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

/// Cooperative cancellation, shared with long-running work.
#[derive(Debug, Clone, Default)]
pub struct CancelToken(Arc<AtomicBool>);

impl CancelToken {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Relaxed);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }

    /// `Err(cancelled)` once cancelled; call it in loops.
    pub fn check(&self) -> Result<(), ToolError> {
        if self.is_cancelled() { Err(ToolError::cancelled()) } else { Ok(()) }
    }

    /// `true` when both tokens are one flag: clones of the same token, not two tokens that
    /// happen to read alike. A token dropped for the wrong call can't cancel the live one.
    pub fn same(&self, other: &CancelToken) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}
