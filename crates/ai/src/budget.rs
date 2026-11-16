//! A hard ceiling on what builds may spend, per run and per day.
//!
//! A coding agent's figure is its own estimate at API prices, so this is a spending guard, not a
//! bill: a run that reaches its ceiling stops between turns, and a day's builds share one ledger
//! (`spend.json` beside `config.toml`) so several projects can't each spend the whole day's limit.
//! The read-modify-write takes a lock inside this process, so two parallel builds can't overwrite
//! each other's figure; two processes (the app and the CLI) adding at the same instant can still
//! lose one turn's number, which the guard tolerates and a bill would not.

use craft_anything_evidence::{ErrorCode, ToolError};
use craft_anything_platform::fs::write_atomic_private;
use craft_anything_platform::sync::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// How many days the ledger keeps (the oldest are dropped).
const KEEP_DAYS: usize = 31;
/// The ledger is a few hundred bytes; a larger file is treated as unreadable.
const MAX_LEDGER_BYTES: u64 = 64 * 1024;
const SPEND_VERSION: u32 = 1;
const SECONDS_PER_DAY: u64 = 86_400;
/// How long one process waits for another to finish with the ledger before it gives up and stops
/// the build: a bounded wait, never a lock-up.
const LOCK_WAIT: Duration = Duration::from_secs(5);
/// A lock file older than this was left by a process that died holding it.
const LOCK_STALE: Duration = Duration::from_secs(30);
const LOCK_POLL: Duration = Duration::from_millis(10);

/// Held while the ledger is read, changed and written, so parallel builds in this process add up.
static LEDGER: Mutex<()> = Mutex::new(());

/// A lock file beside the ledger, so two processes (the app and the CLI can both build) don't
/// write the day's total over each other. The wait is bounded: a lock-up would stop a build for
/// ever, so it gives up after [`LOCK_WAIT`] and the run stops rather than spend unrecorded.
struct FileLock {
    path: PathBuf,
}

impl FileLock {
    fn acquire(ledger: &Path) -> Result<FileLock, ToolError> {
        let path = ledger.with_extension("json.lock");
        let deadline = Instant::now() + LOCK_WAIT;
        loop {
            match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(_) => return Ok(FileLock { path }),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    if stale(&path) {
                        let _ = std::fs::remove_file(&path);
                        continue;
                    }
                    if Instant::now() >= deadline {
                        return Err(ToolError::new(
                            ErrorCode::FileError,
                            "Another Craft Anything process is writing the AI spending ledger, so this build stopped instead of spending without recording it.",
                        )
                        .with_remediation("Wait a moment, then continue the build."));
                    }
                    std::thread::sleep(LOCK_POLL);
                }
                Err(e) => return Err(ToolError::io(path.display(), e)),
            }
        }
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Whether a lock file was left behind by a process that died holding it.
fn stale(path: &Path) -> bool {
    std::fs::metadata(path).and_then(|m| m.modified()).ok().and_then(|at| SystemTime::now().duration_since(at).ok()).is_some_and(|age| age > LOCK_STALE)
}

/// The ceilings from `[ai]`, in US dollars. `None` is no ceiling; `0` allows no spending.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct SpendLimits {
    /// The most one build may spend.
    pub run_usd: Option<f64>,
    /// The most a day's builds together may spend.
    pub daily_usd: Option<f64>,
}

impl SpendLimits {
    pub fn is_set(self) -> bool {
        self.run_usd.is_some() || self.daily_usd.is_some()
    }

    /// Refuse a ceiling that isn't a number of dollars (negative or not finite).
    pub fn validate(self) -> Result<(), ToolError> {
        for (name, value) in [("max_run_cost_usd", self.run_usd), ("max_daily_cost_usd", self.daily_usd)] {
            if let Some(v) = value
                && (!v.is_finite() || v < 0.0)
            {
                return Err(ToolError::new(ErrorCode::BadSettings, format!("`[ai] {name}` must be a number of US dollars, zero or more."))
                    .with_remediation("Fix or remove it in the [ai] section of config.toml."));
            }
        }
        Ok(())
    }
}

/// The spend ledger kept beside `config.toml`.
pub fn ledger_path(settings: &Path) -> PathBuf {
    settings.with_file_name("spend.json")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct Ledger {
    version: u32,
    /// UTC day (`YYYY-MM-DD`) → dollars spent.
    days: BTreeMap<String, f64>,
}

impl Default for Ledger {
    fn default() -> Self {
        Ledger { version: SPEND_VERSION, days: BTreeMap::new() }
    }
}

/// One run's spending against its ceilings, and the day's total shared with other runs.
#[derive(Debug, Clone)]
pub struct SpendBudget {
    limits: SpendLimits,
    ledger: PathBuf,
    day: String,
    run_usd: f64,
    today_usd: f64,
}

impl SpendBudget {
    /// A budget for a run, or `None` when no ceiling is set. Reads today's total from the ledger.
    pub fn open(limits: SpendLimits, ledger: PathBuf) -> Option<SpendBudget> {
        if !limits.is_set() {
            return None;
        }
        let day = today();
        let today_usd = read_ledger(&ledger).days.get(&day).copied().unwrap_or(0.0);
        Some(SpendBudget { limits, ledger, day, run_usd: 0.0, today_usd })
    }

    /// Add one turn's reported cost to this run and to the day's ledger.
    pub fn add(&mut self, usd: f64) -> Result<(), ToolError> {
        if !usd.is_finite() || usd <= 0.0 {
            return Ok(());
        }
        let guard = LEDGER.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.label("ai spend ledger");
        // Another process (the app and the CLI can both build) holds the file lock while it
        // writes; wait a bounded time rather than lose its figure.
        let _file = FileLock::acquire(&self.ledger)?;
        let mut ledger = read_ledger(&self.ledger);
        let saved = ledger.days.get(&self.day).copied().unwrap_or(0.0);
        self.run_usd += usd;
        self.today_usd = saved.max(self.today_usd) + usd;
        ledger.days.insert(self.day.clone(), self.today_usd);
        while ledger.days.len() > KEEP_DAYS {
            let Some(oldest) = ledger.days.keys().next().cloned() else { break };
            ledger.days.remove(&oldest);
        }
        ledger.version = SPEND_VERSION;
        let json = serde_json::to_vec_pretty(&ledger).map_err(|e| ToolError::internal(e.to_string()))?;
        write_atomic_private(&self.ledger, &json)
    }

    /// The ceiling this run has reached, if any, as a plain-words error.
    pub fn exceeded(&mut self) -> Option<ToolError> {
        if let Some(limit) = self.limits.run_usd
            && self.run_usd >= limit
        {
            return Some(run_limit_error(limit, self.run_usd));
        }
        self.daily_exceeded()
    }

    /// The day's ceiling, if it has been reached (this run's own spending included).
    pub fn daily_exceeded(&mut self) -> Option<ToolError> {
        let limit = self.limits.daily_usd?;
        self.refresh_today();
        (self.today_usd >= limit).then(|| daily_limit_error(limit, self.today_usd))
    }

    fn refresh_today(&mut self) {
        let saved = read_ledger(&self.ledger).days.get(&self.day).copied().unwrap_or(0.0);
        self.today_usd = self.today_usd.max(saved);
    }
}

/// Refuse a start when today's builds have already reached the daily ceiling.
pub fn refuse_over_daily(limits: SpendLimits, ledger: &Path) -> Result<(), ToolError> {
    match SpendBudget::open(limits, ledger.to_path_buf()).as_mut().and_then(SpendBudget::daily_exceeded) {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

fn run_limit_error(limit: f64, spent: f64) -> ToolError {
    ToolError::new(
        ErrorCode::OverLimit,
        format!("This build reached its AI spending limit of {} and stopped; it had spent about {}.", dollars(limit), dollars(spent)),
    )
    .with_remediation("Raise `max_run_cost_usd` in the [ai] section of config.toml, then continue the build, or start a new one.")
}

fn daily_limit_error(limit: f64, spent: f64) -> ToolError {
    ToolError::new(
        ErrorCode::OverLimit,
        format!("Today's builds have reached the AI spending limit of {} (about {} spent today, UTC), so this build stopped.", dollars(limit), dollars(spent)),
    )
    .with_remediation("Raise `max_daily_cost_usd` in the [ai] section of config.toml, or wait until tomorrow.")
}

fn dollars(value: f64) -> String {
    format!("${value:.2}")
}

fn read_ledger(path: &Path) -> Ledger {
    let Ok(meta) = std::fs::metadata(path) else { return Ledger::default() };
    if !meta.is_file() || meta.len() > MAX_LEDGER_BYTES {
        return Ledger::default();
    }
    let Ok(bytes) = std::fs::read(path) else { return Ledger::default() };
    match serde_json::from_slice::<Ledger>(&bytes) {
        Ok(ledger) if ledger.version <= SPEND_VERSION && ledger.days.values().all(|v| v.is_finite() && *v >= 0.0) => ledger,
        _ => {
            log::warn!("Ignoring an unreadable AI spend ledger at {}", path.display());
            Ledger::default()
        }
    }
}

/// Today's UTC date as `YYYY-MM-DD`.
fn today() -> String {
    let days = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() / SECONDS_PER_DAY).unwrap_or(0);
    civil_date(days)
}

/// `YYYY-MM-DD` in UTC for a count of days since 1970-01-01 (Howard Hinnant's `civil_from_days`).
fn civil_date(days: u64) -> String {
    let z = i64::try_from(days).unwrap_or(i64::MAX).saturating_add(719_468);
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let mut y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    if m <= 2 {
        y += 1;
    }
    format!("{y:04}-{m:02}-{d:02}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_ledger(dir: &Path) -> PathBuf {
        dir.join("spend.json")
    }

    #[test]
    fn a_limit_must_be_a_number_of_dollars() {
        assert!(SpendLimits::default().validate().is_ok());
        assert!(SpendLimits { run_usd: Some(0.0), daily_usd: Some(5.0) }.validate().is_ok());
        for bad in [-1.0, f64::NAN, f64::INFINITY] {
            assert!(SpendLimits { run_usd: Some(bad), daily_usd: None }.validate().is_err(), "{bad}");
            assert!(SpendLimits { run_usd: None, daily_usd: Some(bad) }.validate().is_err(), "{bad}");
        }
    }

    #[test]
    fn civil_dates_are_utc() {
        assert_eq!(civil_date(0), "1970-01-01");
        assert_eq!(civil_date(19_723), "2024-01-01");
        assert_eq!(civil_date(20_772), "2026-11-15");
    }

    #[test]
    fn a_run_stops_at_its_ceiling() {
        let dir = tempfile::tempdir().expect("temp");
        let mut budget = SpendBudget::open(SpendLimits { run_usd: Some(0.05), daily_usd: None }, temp_ledger(dir.path())).expect("budget");
        assert!(budget.exceeded().is_none());
        budget.add(0.04).expect("add");
        assert!(budget.exceeded().is_none(), "still under");
        budget.add(0.02).expect("add");
        let error = budget.exceeded().expect("over");
        assert_eq!(error.code, ErrorCode::OverLimit);
        assert!(error.message.contains("$0.05"), "{}", error.message);
    }

    #[test]
    fn a_zero_ceiling_refuses_before_any_spending() {
        let dir = tempfile::tempdir().expect("temp");
        let mut budget = SpendBudget::open(SpendLimits { run_usd: Some(0.0), daily_usd: None }, temp_ledger(dir.path())).expect("budget");
        assert!(budget.exceeded().is_some());
    }

    #[test]
    fn the_daily_ceiling_is_shared_through_the_ledger() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let limits = SpendLimits { run_usd: None, daily_usd: Some(0.10) };
        let mut first = SpendBudget::open(limits, path.clone()).expect("first");
        first.add(0.06).expect("add");
        assert!(first.exceeded().is_none());
        let mut second = SpendBudget::open(limits, path).expect("second");
        second.add(0.06).expect("add");
        let error = second.exceeded().expect("over");
        assert!(error.message.contains("$0.10"), "{}", error.message);
        assert!(error.message.contains("$0.12"), "{}", error.message);
    }

    #[test]
    fn a_start_is_refused_once_the_day_is_spent() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let limits = SpendLimits { run_usd: None, daily_usd: Some(1.0) };
        let mut budget = SpendBudget::open(limits, path.clone()).expect("budget");
        budget.add(1.25).expect("add");
        assert!(refuse_over_daily(limits, &path).is_err());
        assert!(refuse_over_daily(SpendLimits { run_usd: Some(1.0), daily_usd: None }, &path).is_ok(), "a run ceiling alone doesn't refuse a start");
    }

    #[test]
    fn the_ledger_keeps_a_bounded_history() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let mut budget = SpendBudget::open(SpendLimits { run_usd: None, daily_usd: Some(100.0) }, path.clone()).expect("budget");
        budget.add(0.5).expect("add");
        let ledger = read_ledger(&path);
        assert_eq!(ledger.days.len(), 1);
        assert_eq!(ledger.days.values().copied().next(), Some(0.5));
    }

    #[test]
    fn an_unreadable_ledger_is_ignored_without_panicking() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        std::fs::write(&path, b"not json").expect("write");
        let mut budget = SpendBudget::open(SpendLimits { run_usd: None, daily_usd: Some(1.0) }, path.clone()).expect("budget");
        assert!(budget.exceeded().is_none());
        budget.add(0.5).expect("a fresh ledger is written");
        assert_eq!(read_ledger(&path).days.values().copied().next(), Some(0.5));
    }

    #[test]
    fn a_budget_is_only_made_for_a_set_ceiling() {
        let dir = tempfile::tempdir().expect("temp");
        assert!(SpendBudget::open(SpendLimits::default(), temp_ledger(dir.path())).is_none());
    }

    /// Parallel builds add their turns to one ledger instead of overwriting each other's figure.
    #[test]
    fn parallel_builds_add_up() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let limits = SpendLimits { run_usd: None, daily_usd: Some(10.0) };
        let builds: Vec<_> = (0..8)
            .map(|_| {
                let path = path.clone();
                std::thread::spawn(move || {
                    let mut budget = SpendBudget::open(limits, path).expect("budget");
                    for _ in 0..50 {
                        budget.add(0.01).expect("add");
                    }
                })
            })
            .collect();
        for build in builds {
            build.join().expect("a build's thread");
        }
        let total: f64 = read_ledger(&path).days.values().copied().sum();
        assert!((total - 4.0).abs() < 1e-6, "every turn's figure is in the ledger, got {total}");
    }

    /// A lock held by another process is waited for, not written over.
    #[test]
    fn another_process_lock_is_waited_for() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let lock = path.with_extension("json.lock");
        let holder = std::thread::spawn({
            let lock = lock.clone();
            move || {
                std::fs::File::create(&lock).expect("lock");
                std::thread::sleep(Duration::from_millis(80));
                std::fs::remove_file(&lock).expect("release");
            }
        });
        std::thread::sleep(Duration::from_millis(20));
        let mut budget = SpendBudget::open(SpendLimits { run_usd: None, daily_usd: Some(10.0) }, path.clone()).expect("budget");
        budget.add(0.5).expect("waits for the other process");
        holder.join().expect("holder");
        assert_eq!(read_ledger(&path).days.values().copied().next(), Some(0.5));
        assert!(!lock.exists(), "the lock is released");
    }

    /// A lock file left by a process that died is cleared instead of blocking for ever.
    #[test]
    fn a_stale_lock_is_cleared() {
        let dir = tempfile::tempdir().expect("temp");
        let path = temp_ledger(dir.path());
        let lock = path.with_extension("json.lock");
        let file = std::fs::File::create(&lock).expect("lock");
        file.set_modified(SystemTime::now() - Duration::from_secs(60)).expect("age the lock");
        let mut budget = SpendBudget::open(SpendLimits { run_usd: None, daily_usd: Some(10.0) }, path.clone()).expect("budget");
        budget.add(0.25).expect("clears the stale lock");
        assert!(!lock.exists(), "the lock is released");
    }
}
