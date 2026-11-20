//! Docker-backed confinement for the commands a build runs.
//!
//! A build command is a shell string the person approved; this module decides how it runs when
//! they ask for confinement. Docker is the one container runtime macOS, Windows and Linux share,
//! so the sandbox is a container: only the project folder is mounted, the rest of the computer is
//! not visible, the network is off unless the person allows it, and the command gets a memory, CPU
//! and PID budget. Docker itself stays a privileged daemon, so this confines the command, not
//! Docker: the person's approval is still required.
//!
//! Every run goes through [`crate::process`], so it has a deadline, capped output and a killed
//! process group like any other external program.

use crate::process::{Cancel, Output, Process};
use craft_anything_evidence::{ErrorCode, ToolError};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

/// How often, at most, the project folder is measured while a build runs (a large tree is measured
/// less often: see [`ProjectWatch`]).
const WATCH_EVERY: Duration = Duration::from_millis(250);
/// Entries one measurement walks before it gives up. A folder with more can't be measured, so its
/// ceiling can't be held and the build is stopped (a dependency folder of a large Node.js project
/// is a few hundred thousand entries).
const WATCH_ENTRIES: usize = 1_000_000;

/// Where the project folder is mounted inside the container.
pub const WORKDIR: &str = "/work";

/// Marks every container this module starts, so one left behind can be found and removed.
pub const LABEL: &str = "ai.storyteller.craft-anything.sandbox";

/// A small Debian with a POSIX shell and nothing else: a build that needs Node, Rust or Python
/// must name an image that has it.
pub const DEFAULT_IMAGE: &str = "debian:bookworm-slim";

/// What a containerised build command may reach and use.
#[derive(Debug, Clone, PartialEq)]
pub struct Spec {
    /// Image the command runs in.
    pub image: String,
    /// Digest the image must match (`sha256:...`); `None` uses the tag in [`Spec::image`].
    pub digest: Option<String>,
    /// The project folder: the only host path the container can see, mounted at [`WORKDIR`].
    pub project: PathBuf,
    /// Whether the container may reach the network. Package installs need it.
    pub network: bool,
    /// Memory ceiling in MiB (256-65536).
    pub memory_mb: u64,
    /// CPU ceiling in cores (0.1-64).
    pub cpus: f32,
    /// Process ceiling inside the container (16-32768).
    pub pids: u32,
    /// Writable-layer ceiling in MiB; 0 leaves Docker's default. Needs a storage driver with
    /// quota support (overlay2 with pquota; not Docker Desktop).
    pub storage_mb: u64,
    /// Ceiling in MiB for the project folder itself; 0 is no ceiling. The folder is a bind mount,
    /// so Docker can't quota it: it is measured while the build runs and the build is stopped when
    /// it grows past this.
    pub project_mb: u64,
    /// Environment variables the container gets, set by the caller. The host's environment is
    /// never passed: a build that needs one has it named here.
    pub env: Vec<(String, String)>,
}

impl Default for Spec {
    fn default() -> Self {
        Spec {
            image: DEFAULT_IMAGE.to_string(),
            digest: None,
            project: PathBuf::new(),
            network: false,
            memory_mb: 4096,
            cpus: 2.0,
            pids: 512,
            storage_mb: 0,
            project_mb: 0,
            env: Vec::new(),
        }
    }
}

impl Spec {
    /// The `docker run` arguments for `command`, a shell line.
    ///
    /// Everything the command can touch is decided here, so this is the security boundary and the
    /// tests read it directly. Values outside their range are clamped rather than refused: a
    /// person who typed `0` gets the floor, not a command that runs unbounded.
    pub fn argv(&self, command: &str) -> Result<Vec<String>, ToolError> {
        self.argv_for(&["sh".to_string(), "-c".to_string(), command.to_string()])
    }

    /// The `docker run` arguments for a program and its arguments, run without a shell between
    /// them. A check's own build is a program and argv from the toolchain table, so no quoting is
    /// involved.
    pub fn argv_for(&self, command: &[String]) -> Result<Vec<String>, ToolError> {
        let image = self.image.trim();
        if image.is_empty() || image.starts_with('-') || image.chars().any(char::is_whitespace) {
            return Err(ToolError::invalid(format!("`{}` isn't a Docker image name.", self.image))
                .with_remediation("Set the image in Settings → Build, for example `debian:bookworm-slim`."));
        }
        if !self.project.is_absolute() {
            return Err(ToolError::invalid("The sandbox needs an absolute project folder."));
        }
        let image_ref = match self.digest.as_deref() {
            Some(digest) => {
                let hex = digest.strip_prefix("sha256:").unwrap_or_default();
                if hex.len() != 64 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
                    return Err(ToolError::invalid(format!("`{digest}` isn't a sha256 image digest."))
                        .with_remediation("Use `sha256:` followed by 64 hex digits, or leave the digest empty to use the tag."));
                }
                if image.contains('@') {
                    return Err(ToolError::invalid("The image already names a digest; set the image to its name without `@`."));
                }
                format!("{image}@{digest}")
            }
            None => image.to_string(),
        };
        let host = host_path(&self.project)?;
        let memory = self.memory_mb.clamp(256, 65_536);
        let cpus = if self.cpus.is_finite() { self.cpus.clamp(0.1, 64.0) } else { 2.0 };
        let pids = self.pids.clamp(16, 32_768);
        let mut argv: Vec<String> = vec![
            "run".into(),
            "--rm".into(),
            "--init".into(),
            "--network".into(),
            if self.network { "bridge" } else { "none" }.into(),
            "--memory".into(),
            format!("{memory}m"),
            "--memory-swap".into(),
            format!("{memory}m"),
            "--cpus".into(),
            format!("{cpus}"),
            "--pids-limit".into(),
            pids.to_string(),
        ];
        if self.storage_mb > 0 {
            argv.push("--storage-opt".into());
            argv.push(format!("size={}m", self.storage_mb));
        }
        argv.extend([
            "--cap-drop".into(),
            "ALL".into(),
            "--security-opt".into(),
            "no-new-privileges".into(),
            "-v".into(),
            format!("{host}:{WORKDIR}"),
            "-w".into(),
            WORKDIR.into(),
            "--user".into(),
            sandbox_user(),
        ]);
        for (key, value) in &self.env {
            if !is_env_name(key) || value.contains('\0') {
                return Err(ToolError::invalid(format!("`{key}` isn't a usable environment variable name."))
                    .with_remediation("Use a name of letters, digits and underscores, and a value without a NUL."));
            }
            argv.push("-e".into());
            argv.push(format!("{key}={value}"));
        }
        if command.first().is_none_or(|program| program.trim().is_empty()) {
            return Err(ToolError::invalid("The sandbox needs a program to run."));
        }
        argv.push(image_ref);
        argv.extend(command.iter().cloned());
        Ok(argv)
    }
}

/// Whether `key` is a POSIX environment variable name.
fn is_env_name(key: &str) -> bool {
    let mut chars = key.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_') && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// What one measurement of the project folder found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Measured {
    /// Within the ceiling: the folder's bytes.
    Under(u64),
    /// Past the ceiling: the bytes counted when the walk passed it.
    Over(u64),
    /// More entries than one measurement walks. The size isn't known, so the ceiling can't be held:
    /// treated like [`Measured::Over`], never as "probably fine" (a build could otherwise make many
    /// small files first and then write without limit).
    TooManyEntries,
    /// A folder or file in it can't be read (a build can `chmod 300` a folder and write into it),
    /// so its size isn't known either: treated the same way, never counted as empty.
    Unreadable,
}

impl Measured {
    fn holds(self) -> bool {
        matches!(self, Measured::Under(_))
    }
}

/// Why the watcher stopped a build (`STOP_*`), kept in one atomic so the run can say which.
const STOP_NONE: u8 = 0;
const STOP_OVER: u8 = 1;
const STOP_TOO_MANY: u8 = 2;
const STOP_UNREADABLE: u8 = 3;

/// Measures the project folder while a container runs, so a build that writes past the ceiling is
/// stopped instead of filling the disk. The folder is a bind mount, which Docker can't quota.
struct ProjectWatch {
    stop: Arc<AtomicU8>,
    done: Arc<AtomicBool>,
}

impl ProjectWatch {
    /// Start watching, or an error when no watcher can run: a ceiling that isn't watched isn't
    /// held, so the build is refused rather than run without it.
    fn start(root: PathBuf, limit: u64, max_entries: usize) -> Result<ProjectWatch, ToolError> {
        let stop = Arc::new(AtomicU8::new(STOP_NONE));
        let done = Arc::new(AtomicBool::new(false));
        let thread = {
            let stop = stop.clone();
            let done = done.clone();
            std::thread::Builder::new().name("sandbox-disk".into()).spawn(move || {
                let mut wait = WATCH_EVERY;
                while !done.load(Ordering::Relaxed) {
                    std::thread::sleep(wait);
                    if done.load(Ordering::Relaxed) {
                        return;
                    }
                    let started = std::time::Instant::now();
                    let measured = measure(&root, limit, max_entries);
                    // A large tree takes a while to walk: wait at least four times as long as the
                    // walk took, so the watcher never keeps the disk busier than the build does.
                    wait = WATCH_EVERY.max(started.elapsed().saturating_mul(4));
                    let why = match measured {
                        Measured::Under(_) => continue,
                        Measured::Over(_) => STOP_OVER,
                        Measured::TooManyEntries => STOP_TOO_MANY,
                        Measured::Unreadable => STOP_UNREADABLE,
                    };
                    stop.store(why, Ordering::SeqCst);
                    return;
                }
            })
        };
        if let Err(e) = thread {
            return Err(ToolError::new(
                ErrorCode::ProgramFailed,
                format!("The sandboxed build didn't start: the watcher for its project folder's {} MiB ceiling couldn't start ({e}).", limit / (1024 * 1024)),
            )
            .with_remediation(
                "Try again; if it keeps happening, close other programs, or set `[build] sandbox_project_mb = 0` to build without the ceiling.",
            ));
        }
        Ok(ProjectWatch { stop, done })
    }

    /// Why the watcher stopped the build, if it did.
    fn stopped(&self) -> Option<Measured> {
        match self.stop.load(Ordering::SeqCst) {
            STOP_OVER => Some(Measured::Over(0)),
            STOP_TOO_MANY => Some(Measured::TooManyEntries),
            STOP_UNREADABLE => Some(Measured::Unreadable),
            _ => None,
        }
    }
}

impl Drop for ProjectWatch {
    fn drop(&mut self) {
        self.done.store(true, Ordering::SeqCst);
    }
}

/// The bytes under `root`, stopping as soon as they pass `limit` or the walk passes `max_entries`.
/// Links are not followed, so a link back up the tree can't loop. A folder or file that can't be
/// read makes the size unknown ([`Measured::Unreadable`]); one that vanished while the walk ran
/// (the build deleted it) is simply gone.
fn measure(root: &Path, limit: u64, max_entries: usize) -> Measured {
    let gone = |e: &std::io::Error| e.kind() == std::io::ErrorKind::NotFound;
    let mut total = 0u64;
    let mut seen = 0usize;
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(e) if gone(&e) => continue,
            Err(_) => return Measured::Unreadable,
        };
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(e) if gone(&e) => continue,
                Err(_) => return Measured::Unreadable,
            };
            seen = seen.saturating_add(1);
            if seen > max_entries {
                return Measured::TooManyEntries;
            }
            let meta = match entry.metadata() {
                Ok(meta) => meta,
                Err(e) if gone(&e) => continue,
                Err(_) => return Measured::Unreadable,
            };
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total = total.saturating_add(meta.len());
                if total > limit {
                    return Measured::Over(total);
                }
            }
        }
    }
    Measured::Under(total)
}

/// Refuse a build whose project folder is already past `spec.project_mb` or too large to measure.
/// Returns the ceiling in bytes (0: none).
fn refuse_over_ceiling(spec: &Spec, max_entries: usize) -> Result<u64, ToolError> {
    let limit = spec.project_mb.saturating_mul(1024 * 1024);
    if limit > 0 {
        let measured = measure(&spec.project, limit, max_entries);
        if !measured.holds() {
            return Err(ceiling_error(&spec.project, spec.project_mb, measured, false, max_entries));
        }
    }
    Ok(limit)
}

/// The error for a project folder whose ceiling can't be held: past it, or too large to measure.
/// `running` is false when the build was refused before Docker started.
fn ceiling_error(project: &Path, limit_mb: u64, measured: Measured, running: bool, max_entries: usize) -> ToolError {
    let message = match (measured, running) {
        (Measured::TooManyEntries, false) => format!(
            "The project folder {} holds more than {max_entries} files and folders, too many to measure against its {limit_mb} MiB ceiling, so the sandboxed build didn't start.",
            project.display()
        ),
        (Measured::TooManyEntries, true) => format!(
            "The build made more than {max_entries} files and folders in {}, too many to measure against its {limit_mb} MiB ceiling, so it was stopped.",
            project.display()
        ),
        (Measured::Unreadable, false) => format!(
            "A folder or file in the project folder {} can't be read, so it can't be measured against its {limit_mb} MiB ceiling and the sandboxed build didn't start.",
            project.display()
        ),
        (Measured::Unreadable, true) => format!(
            "The build made a folder or file in {} that can't be read, so the folder can't be measured against its {limit_mb} MiB ceiling and the build was stopped.",
            project.display()
        ),
        (_, false) => format!("The project folder {} is already more than {limit_mb} MiB, so the sandboxed build didn't start.", project.display()),
        (_, true) => format!("The build wrote more than {limit_mb} MiB into {}, so it was stopped before it filled the disk.", project.display()),
    };
    ToolError::new(ErrorCode::OverLimit, message).with_remediation(
        "Delete what the build no longer needs, raise `[build] sandbox_project_mb` (0 turns the ceiling off), or turn the sandbox off in Settings → Build.",
    )
}

/// Run `command`, a shell line, inside a container built from `spec`.
pub fn run(spec: &Spec, command: &str, timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    run_argv(spec, &["sh".to_string(), "-c".to_string(), command.to_string()], timeout, cancel)
}

/// Run a program and its arguments inside a container built from `spec`, without a shell.
pub fn run_argv(spec: &Spec, command: &[String], timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    let mut argv = spec.argv_for(command)?;
    // A folder that is already past the ceiling (or too large to measure) has nothing left to
    // protect: refuse before Docker starts, rather than let the build add to it. Checked before
    // the user check, which depends on who runs the host, so this refusal is the same everywhere.
    let limit = refuse_over_ceiling(spec, WATCH_ENTRIES)?;
    // The container runs as this user, so it must be able to write the one folder it sees. A
    // root host's folder is often root-owned, and the fallback id is not root.
    let user = sandbox_user();
    if !writable_by(&spec.project, &user) {
        return Err(ToolError::invalid(format!("The sandbox runs as {user}, which can't write the project folder {}.", spec.project.display()))
            .with_remediation("Make the folder writable by that user, or turn the sandbox off in Settings → Build."));
    }
    let name = container_name();
    // A name and a label let a container be found and stopped even when the Docker client that
    // started it is gone.
    argv.insert(1, "--name".into());
    argv.insert(2, name.clone());
    argv.insert(3, "--label".into());
    argv.insert(4, LABEL.into());
    let watch = if limit > 0 { Some(ProjectWatch::start(spec.project.clone(), limit, WATCH_ENTRIES)?) } else { None };
    let mut process = Process::new("docker")
        .args(argv)
        .cwd(&spec.project)
        .timeout(timeout)
        .stdout_cap(4 * 1024 * 1024)
        .stderr_cap(4 * 1024 * 1024)
        .label("the sandboxed build command");
    {
        let watched = watch.as_ref().map(|w| w.stop.clone());
        process = process.cancel_when(move || cancel.as_ref().is_some_and(|c| c()) || watched.as_ref().is_some_and(|w| w.load(Ordering::SeqCst) != STOP_NONE));
    }
    let result = process.run();
    // `--rm` removes a container that exits. A timeout or cancel kills the Docker client first,
    // so the daemon keeps the container running; stop it by name. An error from Docker itself
    // also comes here, where removing a container that was never made does nothing.
    if result.is_err() {
        stop(&name);
    }
    // The watcher cancels the run, so its error is "cancelled": say what actually happened.
    if let Some(measured) = watch.as_ref().and_then(ProjectWatch::stopped) {
        return Err(ceiling_error(&spec.project, spec.project_mb, measured, true, WATCH_ENTRIES));
    }
    let out = result.map_err(docker_error)?;
    // Docker's client reports a stopped daemon on stderr with a non-zero exit, not a spawn
    // error. Say what to do instead of leaving the person with "failed to connect to the docker
    // API" and no next step.
    if let Some(e) = daemon_down(&out) {
        return Err(e);
    }
    Ok(out)
}

/// A unique container name: the process id plus a counter, so two runs never collide.
fn container_name() -> String {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    format!("craft-anything-build-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed))
}

/// Stop and remove a container by name. Errors are ignored: the run is already over, and a
/// container that `--rm` already removed is the normal case.
fn stop(name: &str) {
    // A container can still be starting when the client is killed; give the daemon a moment to
    // create it before `rm` looks for it. `--rm` already removed one that exited normally, so
    // "No such container" is not a failure worth retrying forever.
    for attempt in 0..3 {
        let out = Process::new("docker")
            .args(["rm", "--force", name])
            .timeout(Duration::from_secs(15))
            .stdout_cap(4096)
            .stderr_cap(4096)
            .label("the sandboxed container's cleanup")
            .run();
        if out.is_ok_and(|o| o.success) || attempt == 2 {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Turn Docker's absence into something a person can act on.
fn docker_error(e: ToolError) -> ToolError {
    if e.code == ErrorCode::EngineMissing { docker_missing() } else { e }
}

/// The one message for a Docker that isn't there or isn't running.
fn docker_missing() -> ToolError {
    ToolError::new(ErrorCode::EngineMissing, "Docker isn't running, so the build can't run sandboxed.")
        .with_remediation("Start Docker Desktop (or install Docker) and try again, or turn the sandbox off in Settings → Build.")
}

/// A stopped daemon exits non-zero and says so on stderr; the spawn itself succeeded, so
/// [`docker_error`] never sees it.
fn daemon_down(out: &Output) -> Option<ToolError> {
    if out.success {
        return None;
    }
    let text = out.stderr_text();
    let first = text.trim_start().lines().next().unwrap_or_default().trim().to_ascii_lowercase();
    let down = first.starts_with("failed to connect to the docker api")
        || first.starts_with("cannot connect to the docker daemon")
        || first.starts_with("error during connect");
    down.then(docker_missing)
}

/// The project folder as Docker's `-v host:container` expects it: resolved, existing and a real
/// folder. A missing path would make `-v` create a root-owned directory; a link is resolved so the
/// mount is the folder the person chose, not whatever it pointed at.
fn host_path(project: &Path) -> Result<String, ToolError> {
    let real = std::fs::canonicalize(project).map_err(|e| {
        ToolError::invalid(format!("The sandbox's project folder {} can't be read: {e}.", project.display()))
            .with_remediation("Create the project folder first, or turn the sandbox off in Settings → Build.")
    })?;
    if !real.is_dir() {
        return Err(ToolError::invalid(format!("The sandbox's project folder {} isn't a folder.", real.display()))
            .with_remediation("Use the folder that holds the app, or turn the sandbox off in Settings → Build."));
    }
    let text = real.to_string_lossy();
    if cfg!(windows) {
        return Ok(docker_windows_path(&text));
    }
    if text.contains(':') {
        return Err(ToolError::invalid("The project folder's path contains `:`, which Docker reads as a volume separator.")
            .with_remediation("Move the project to a folder whose path has no colon, or turn the sandbox off in Settings → Build."));
    }
    Ok(text.into_owned())
}

/// A Windows path as Docker's `-v` expects it: no `\\?\` verbatim prefix, forward slashes.
///
/// `std::fs::canonicalize` returns a verbatim path (`\\?\C:\Users\me\app`), which Docker Desktop
/// does not accept; the ordinary spelling is `C:/Users/me/app`. A network path keeps its `\\`
/// prefix (`\\?\UNC\server\share` is `//server/share`).
fn docker_windows_path(text: &str) -> String {
    let plain = text
        .strip_prefix(r"\\?\UNC\")
        .map(|rest| format!(r"\\{rest}"))
        .or_else(|| text.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or_else(|| text.to_string());
    plain.replace('\\', "/")
}

/// The `--user` value: the host id, except root. Root on the host must not become root in the
/// container; a fixed non-root id is used instead (Windows has no host id to mirror).
fn user_arg(host: Option<String>) -> String {
    host.filter(|u| !names_root(u)).unwrap_or_else(|| "1000:1000".to_string())
}

/// Whether a `uid:gid` pair has a zero in it. A zero uid is root whatever its group is (`0:20` is
/// still root), and a zero gid would give a non-root user root's group. The ids are parsed, not
/// compared as text: Docker reads `00:00` and `+0:0` as zero too.
fn names_root(user: &str) -> bool {
    user.split(':').any(|id| id.trim().parse::<i64>().is_ok_and(|n| n == 0))
}

/// The user the container runs as: the host's id, except root (see [`user_arg`]).
fn sandbox_user() -> String {
    user_arg(host_user())
}

/// Whether `user`, a `uid:gid` pair, can write and search `path` by its permission bits.
///
/// The container runs as this user, so a folder it can't write would fail every build that
/// writes into it. A directory needs execute as well as write to be entered and to have entries
/// created in it. ACLs aren't read: a folder whose bits say no is refused even if an ACL would
/// allow it, and the error says how to change that.
#[cfg(unix)]
fn writable_by(path: &Path, user: &str) -> bool {
    use std::os::unix::fs::MetadataExt as _;
    let mut ids = user.split(':');
    let uid = ids.next().and_then(|id| id.parse::<u32>().ok());
    let gid = ids.next().and_then(|id| id.parse::<u32>().ok());
    let (Some(uid), Some(gid)) = (uid, gid) else { return false };
    let Ok(meta) = std::fs::metadata(path) else { return false };
    let bits = if uid == meta.uid() {
        (meta.mode() >> 6) & 0o7
    } else if gid == meta.gid() {
        (meta.mode() >> 3) & 0o7
    } else {
        meta.mode() & 0o7
    };
    bits & 0o3 == 0o3
}

/// Windows has no numeric host id to check, and Docker Desktop maps the mount itself.
#[cfg(not(unix))]
fn writable_by(_path: &Path, _user: &str) -> bool {
    true
}

/// The current user, so files the build writes stay theirs instead of root's.
///
/// Container images rarely have the host's account, but a numeric id works: `sh` is world-readable
/// and a file created at `/work` keeps the host owner. `None` on Windows, where [`Spec::argv`]
/// uses a fixed non-root id instead. Looked up once per process.
fn host_user() -> Option<String> {
    static USER: OnceLock<Option<String>> = OnceLock::new();
    if cfg!(windows) {
        return None;
    }
    USER.get_or_init(|| {
        let id = |flag: &str| -> Option<String> {
            let out =
                Process::new("id").args([flag]).timeout(Duration::from_secs(5)).stdout_cap(1024).stderr_cap(1024).label("the current user id").run().ok()?;
            let text = out.stdout_text().trim().to_string();
            if out.success && !text.is_empty() && text.chars().all(|c| c.is_ascii_digit()) { Some(text) } else { None }
        };
        Some(format!("{}:{}", id("-u")?, id("-g")?))
    })
    .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> Spec {
        Spec { project: std::env::temp_dir(), ..Default::default() }
    }

    fn joined(argv: &[String]) -> String {
        argv.join(" ")
    }

    #[test]
    fn a_container_sees_only_the_project() {
        let argv = spec().argv("npm install").unwrap_or_default();
        let text = joined(&argv);
        let host = std::fs::canonicalize(spec().project).unwrap_or_default();
        assert!(text.contains(&format!("-v {}:{WORKDIR}", host.display())), "{text}");
        assert!(text.contains("-w /work"), "{text}");
        assert_eq!(text.matches("-v ").count(), 1, "exactly one mount: {text}");
        assert!(!text.contains("--env") && !text.contains("-e "), "no host environment: {text}");
    }

    #[test]
    fn the_network_is_off_unless_asked_for() {
        assert!(joined(&spec().argv("x").unwrap_or_default()).contains("--network none"));
        let open = Spec { network: true, ..spec() };
        assert!(joined(&open.argv("x").unwrap_or_default()).contains("--network bridge"));
    }

    #[test]
    fn privileges_and_limits_are_always_set() {
        let text = joined(&spec().argv("x").unwrap_or_default());
        for flag in ["--rm", "--init", "--cap-drop ALL", "--security-opt no-new-privileges", "--pids-limit", "--memory", "--cpus", "--user"] {
            assert!(text.contains(flag), "{flag} missing from {text}");
        }
    }

    /// The image's default user is often root, and Windows has no host id to mirror; the
    /// container must never fall back to root.
    #[test]
    fn the_container_never_runs_as_root() {
        let argv = spec().argv("x").unwrap_or_default();
        let at = argv.iter().position(|a| a == "--user").expect("--user is always set");
        let user = argv.get(at + 1).cloned().unwrap_or_default();
        let ids: Vec<&str> = user.split(':').collect();
        assert_eq!(ids.len(), 2, "{user}");
        assert!(ids.iter().all(|id| !id.is_empty() && id.chars().all(|c| c.is_ascii_digit())), "{user}");
        assert_ne!(user, "0:0", "never root");
    }

    #[test]
    fn every_run_gets_its_own_container_name() {
        let first = container_name();
        let second = container_name();
        assert_ne!(first, second);
        assert!(first.starts_with("craft-anything-build-"), "{first}");
    }

    /// Opt-in: needs Docker and the default image. `cargo test -p craft-anything-platform sandbox -- --ignored`
    #[test]
    #[ignore]
    fn a_timed_out_command_leaves_no_container_running() {
        let dir = tempfile::tempdir().unwrap();
        let spec = Spec { project: dir.path().to_path_buf(), ..Default::default() };
        let err = run(&spec, "sleep 300", Duration::from_secs(1), None).unwrap_err();
        assert_eq!(err.code, ErrorCode::TimedOut, "{err:?}");
        let out = Process::new("docker")
            .args(["ps", "-aq", "--filter", &format!("label={LABEL}")])
            .timeout(Duration::from_secs(30))
            .stdout_cap(64 * 1024)
            .run()
            .expect("docker ps");
        assert!(out.success, "{}", out.stderr_text());
        assert!(out.stdout_text().trim().is_empty(), "a container survived the timeout: {}", out.stdout_text());
    }

    #[test]
    fn out_of_range_limits_are_clamped_not_ignored() {
        let odd = Spec { memory_mb: 0, cpus: f32::NAN, pids: 0, ..spec() };
        let text = joined(&odd.argv("x").unwrap_or_default());
        assert!(text.contains("--memory 256m"), "{text}");
        assert!(text.contains("--cpus 2"), "{text}");
        assert!(text.contains("--pids-limit 16"), "{text}");
    }

    #[test]
    fn the_command_is_the_last_argument() {
        let argv = spec().argv("cargo build --release").unwrap_or_default();
        assert_eq!(argv.get(argv.len() - 3..), Some(["sh".to_string(), "-c".to_string(), "cargo build --release".to_string()].as_slice()));
    }

    /// A check's build is a program and its argv, so it runs as it is, with no shell to quote.
    #[test]
    fn a_direct_command_runs_without_a_shell() {
        let command = ["cargo".to_string(), "build".to_string(), "--quiet".to_string()];
        let argv = spec().argv_for(&command).unwrap_or_default();
        assert_eq!(argv.get(argv.len() - 3..), Some(command.as_slice()));
        assert!(!argv.iter().any(|a| a == "sh" || a == "-c"), "{argv:?}");
        assert!(spec().argv_for(&[]).is_err(), "nothing to run");
        assert!(spec().argv_for(&[" ".to_string()]).is_err(), "an empty program");
    }

    /// The container gets the environment the caller names, and never the host's.
    #[test]
    fn only_our_own_environment_reaches_the_container() {
        assert!(!joined(&spec().argv("x").unwrap_or_default()).contains("-e "), "the default spec passes nothing");
        let with = Spec { env: vec![("HOME".into(), "/tmp".into()), ("CARGO_HOME".into(), "/tmp/cargo".into())], ..spec() };
        let argv = with.argv("x").unwrap_or_default();
        let at = argv.iter().position(|a| a == "-e").unwrap_or_default();
        assert_eq!(argv.get(at + 1).map(String::as_str), Some("HOME=/tmp"), "{argv:?}");
        assert!(joined(&argv).contains("-e CARGO_HOME=/tmp/cargo"), "{argv:?}");
        // They come before the image, as Docker's options do.
        let image = argv.iter().position(|a| a == DEFAULT_IMAGE).unwrap_or_default();
        assert!(at < image, "{argv:?}");
        for (key, value) in [("".to_string(), "x".to_string()), ("A B".to_string(), "x".to_string()), ("HOME".to_string(), "a\0b".to_string())] {
            let bad = Spec { env: vec![(key.clone(), value.clone())], ..spec() };
            assert!(bad.argv("x").is_err(), "{key:?}={value:?} should be refused");
        }
    }

    #[test]
    fn a_bad_image_is_refused_before_anything_runs() {
        for image in ["", "   ", "-rm", "debian latest"] {
            let bad = Spec { image: image.to_string(), ..spec() };
            assert!(bad.argv("x").is_err(), "{image} should be refused");
        }
    }

    #[test]
    fn a_relative_project_is_refused() {
        let bad = Spec { project: PathBuf::from("app"), ..Default::default() };
        assert!(bad.argv("x").is_err());
    }

    #[test]
    fn a_daemon_that_is_not_running_says_so() {
        let out = Output {
            code: Some(1),
            success: false,
            stdout: Vec::new(),
            stderr: b"failed to connect to the docker API at unix:///tmp/none.sock; check if the daemon is running".to_vec(),
            stdout_truncated: false,
            stderr_truncated: false,
            duration: Duration::ZERO,
        };
        let e = daemon_down(&out).expect("a stopped daemon is an error");
        assert_eq!(e.code, ErrorCode::EngineMissing);
        assert!(e.message.contains("Docker isn't running"), "{}", e.message);
    }

    #[test]
    fn a_build_commands_own_error_is_not_a_daemon_error() {
        let out = Output {
            code: Some(1),
            success: false,
            stdout: Vec::new(),
            stderr: b"error: test failed".to_vec(),
            stdout_truncated: false,
            stderr_truncated: false,
            duration: Duration::ZERO,
        };
        assert!(daemon_down(&out).is_none());
    }

    #[test]
    fn the_image_comes_before_the_shell() {
        let argv = spec().argv("x").unwrap_or_default();
        let image = argv.iter().position(|a| a == DEFAULT_IMAGE).unwrap_or_default();
        let shell = argv.iter().position(|a| a == "sh").unwrap_or_default();
        assert!(image < shell, "{argv:?}");
    }

    #[test]
    fn root_on_the_host_never_becomes_root_in_the_container() {
        assert_eq!(user_arg(Some("0:0".into())), "1000:1000");
        assert_eq!(user_arg(Some("0:20".into())), "1000:1000", "a zero uid is root whatever its group");
        assert_eq!(user_arg(Some("0:1000".into())), "1000:1000");
        assert_eq!(user_arg(Some("501:0".into())), "1000:1000", "a zero gid is remapped too");
        assert_eq!(user_arg(Some("00:00".into())), "1000:1000", "Docker reads 00:00 as zero");
        assert_eq!(user_arg(Some("000:0".into())), "1000:1000");
        assert_eq!(user_arg(Some("+0:0".into())), "1000:1000");
        assert_eq!(user_arg(Some("-0:0".into())), "1000:1000");
        assert_eq!(user_arg(Some("501:20".into())), "501:20");
        assert_eq!(user_arg(None), "1000:1000");
    }

    /// Opt-in: needs Docker. The container really runs as the user the sandbox names, and never
    /// as root; a root host falls back to `1000:1000`, so the container's id is the fallback, not
    /// the host's.
    /// `cargo test -p craft-anything-platform the_container_runs_as_the_host_user_not_root -- --ignored`
    #[test]
    #[ignore]
    fn the_container_runs_as_the_host_user_not_root() {
        let dir = tempfile::tempdir().unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            // A root host's temp folder is root-owned; the fallback id could not write it, and
            // the run would be refused before Docker started.
            std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o777)).unwrap();
        }
        let spec = Spec { project: dir.path().to_path_buf(), ..Default::default() };
        let expected = sandbox_user();
        let out = run(&spec, "id -u; id -g; touch /work/.sandbox-write-test", Duration::from_secs(60), None).expect("docker run");
        assert!(out.success, "{}", out.stderr_text());
        let stdout = out.stdout_text();
        let ids: Vec<&str> = stdout.lines().map(str::trim).filter(|line| !line.is_empty()).collect();
        assert_eq!(ids.len(), 2, "id prints the uid and gid: {stdout}");
        assert_ne!(ids[0], "0", "the container ran as root");
        let mut expected_ids = expected.split(':');
        assert_eq!(Some(ids[0]), expected_ids.next(), "the container's uid is the one --user names ({expected})");
        assert_eq!(Some(ids[1]), expected_ids.next(), "the container's gid is the one --user names ({expected})");
    }

    #[test]
    fn a_digest_pins_the_image() {
        let digest = format!("sha256:{}", "a".repeat(64));
        let pinned = Spec { digest: Some(digest.clone()), ..spec() };
        let text = joined(&pinned.argv("x").unwrap_or_default());
        assert!(text.contains(&format!("{DEFAULT_IMAGE}@{digest}")), "{text}");
    }

    #[test]
    fn a_bad_digest_is_refused() {
        for digest in ["sha256:xyz", "md5:abc", ""] {
            let bad = Spec { digest: Some(digest.into()), ..spec() };
            assert!(bad.argv("x").is_err(), "{digest} should be refused");
        }
    }

    #[test]
    fn a_storage_limit_is_passed_when_set() {
        let limited = Spec { storage_mb: 2048, ..spec() };
        let text = joined(&limited.argv("x").unwrap_or_default());
        assert!(text.contains("--storage-opt size=2048m"), "{text}");
        assert!(!joined(&spec().argv("x").unwrap_or_default()).contains("--storage-opt"));
    }

    #[test]
    fn a_missing_project_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let bad = Spec { project: dir.path().join("missing"), ..Default::default() };
        assert!(bad.argv("x").is_err());
    }

    /// Windows canonical paths are verbatim (`\\?\C:\…`); Docker wants the ordinary spelling.
    #[test]
    fn a_windows_path_reaches_docker_without_its_verbatim_prefix() {
        assert_eq!(docker_windows_path(r"\\?\C:\Users\me\app"), "C:/Users/me/app");
        assert_eq!(docker_windows_path(r"\\?\UNC\server\share\app"), "//server/share/app");
        assert_eq!(docker_windows_path(r"C:\Users\me\app"), "C:/Users/me/app");
        assert_eq!(docker_windows_path(r"\\server\share\app"), "//server/share/app");
    }

    /// A project link is resolved to the folder it names; the mount is never the link itself.
    #[cfg(unix)]
    #[test]
    fn a_project_link_is_resolved_to_its_folder() {
        let dir = tempfile::tempdir().unwrap();
        let link = dir.path().join("app-link");
        std::os::unix::fs::symlink(dir.path(), &link).unwrap();
        let text = joined(&Spec { project: link, ..Default::default() }.argv("x").unwrap_or_default());
        let real = std::fs::canonicalize(dir.path()).unwrap();
        assert!(text.contains(&format!("-v {}:{WORKDIR}", real.display())), "{text}");
    }

    #[cfg(unix)]
    #[test]
    fn writable_by_reads_the_owner_group_and_other_bits() {
        use std::os::unix::fs::{MetadataExt as _, PermissionsExt as _};
        let dir = tempfile::tempdir().unwrap();
        let meta = std::fs::metadata(dir.path()).unwrap();
        let owner = format!("{}:{}", meta.uid(), meta.gid());
        let other = format!("{}:{}", meta.uid().wrapping_add(1), meta.gid().wrapping_add(1));
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        assert!(writable_by(dir.path(), &owner));
        assert!(!writable_by(dir.path(), &other));
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o770)).unwrap();
        let group = format!("{}:{}", meta.uid().wrapping_add(1), meta.gid());
        assert!(writable_by(dir.path(), &group), "group write");
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o707)).unwrap();
        assert!(writable_by(dir.path(), &other), "other write");
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o200)).unwrap();
        assert!(!writable_by(dir.path(), &owner), "write without search can't be used");
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o300)).unwrap();
        assert!(writable_by(dir.path(), &owner), "write and search");
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    }

    /// The project folder is a bind mount Docker can't quota, so its size is measured: a folder
    /// already past the ceiling stops the build before Docker starts.
    #[test]
    fn a_project_folder_already_over_its_ceiling_is_refused_before_docker_starts() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("blob.bin"), vec![0u8; 2 * 1024 * 1024]).unwrap();
        let spec = Spec { project: dir.path().to_path_buf(), project_mb: 1, ..Default::default() };
        let err = run_argv(&spec, &["true".into()], Duration::from_secs(1), None).expect_err("over the ceiling");
        assert_eq!(err.code, ErrorCode::OverLimit);
        assert!(err.message.contains("more than 1 MiB"), "{}", err.message);
        assert!(err.message.contains("didn't start"), "{}", err.message);
    }

    /// A folder too large to measure can't be held to its ceiling, so it is refused too, with a
    /// message that says why rather than claiming it is over.
    #[test]
    fn a_project_folder_too_large_to_measure_is_refused_before_docker_starts() {
        let dir = tempfile::tempdir().unwrap();
        for i in 0..5 {
            std::fs::write(dir.path().join(format!("f{i}")), b"x").unwrap();
        }
        let spec = Spec { project: dir.path().to_path_buf(), project_mb: 1, ..Default::default() };
        let err = refuse_over_ceiling(&spec, 3).expect_err("too many entries to measure");
        assert_eq!(err.code, ErrorCode::OverLimit);
        assert!(err.message.contains("too many to measure"), "{}", err.message);
        assert!(err.message.contains("didn't start"), "{}", err.message);
        assert_eq!(refuse_over_ceiling(&spec, 100).expect("a measurable folder under its ceiling"), 1024 * 1024);
        let off = Spec { project_mb: 0, ..spec };
        assert_eq!(refuse_over_ceiling(&off, 3).expect("no ceiling, nothing to measure"), 0);
    }

    /// The measurement counts a tree, stops as soon as it passes the ceiling, and says so when a
    /// folder is too large to walk.
    #[test]
    fn the_folder_measurement_counts_and_stops_at_the_ceiling() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("sub")).unwrap();
        std::fs::write(dir.path().join("a.bin"), vec![0u8; 1000]).unwrap();
        std::fs::write(dir.path().join("sub/b.bin"), vec![0u8; 2000]).unwrap();
        assert_eq!(measure(dir.path(), 1_000_000, 100), Measured::Under(3000), "under the ceiling, the whole tree is counted");
        // Entries come back in whatever order the filesystem gives them, so the stopping point is
        // the first total past the ceiling, not a fixed number.
        for limit in [0, 1500, 2500] {
            let Measured::Over(stopped) = measure(dir.path(), limit, 100) else { panic!("limit {limit} is passed") };
            assert!(stopped > limit && stopped <= 3000, "limit {limit} stopped at {stopped}");
        }
        // Three entries (a.bin, sub, sub/b.bin): a walk allowed two can't know the size.
        assert_eq!(measure(dir.path(), 1_000_000, 2), Measured::TooManyEntries);
        assert!(!Measured::TooManyEntries.holds(), "an unknown size never passes as under the ceiling");
    }

    /// A folder the host can't list (a build can `chmod 300` one and write into it) makes the size
    /// unknown, so it can't hide bytes from the ceiling.
    #[cfg(unix)]
    #[test]
    fn a_folder_that_cant_be_read_isnt_counted_as_empty() {
        use std::os::unix::fs::PermissionsExt as _;
        let dir = tempfile::tempdir().unwrap();
        let hidden = dir.path().join("hidden");
        std::fs::create_dir(&hidden).unwrap();
        std::fs::write(hidden.join("big.bin"), vec![0u8; 8192]).unwrap();
        std::fs::set_permissions(&hidden, std::fs::Permissions::from_mode(0o300)).unwrap();
        // A read that fails for any reason but "it's gone" is unknown, for any user, root included
        // (here, a file where a folder is expected).
        assert_eq!(measure(&hidden.join("big.bin"), 4096, WATCH_ENTRIES), Measured::Unreadable);
        assert_eq!(measure(&dir.path().join("gone"), 4096, WATCH_ENTRIES), Measured::Under(0), "a folder deleted meanwhile is simply gone");
        let listable = std::fs::read_dir(&hidden).is_ok();
        let measured = measure(dir.path(), 4096, WATCH_ENTRIES);
        std::fs::set_permissions(&hidden, std::fs::Permissions::from_mode(0o700)).unwrap();
        if listable {
            // Running as root: the folder can still be listed, so the bytes are counted.
            assert!(matches!(measured, Measured::Over(_)), "{measured:?}");
            return;
        }
        assert_eq!(measured, Measured::Unreadable, "never counted as zero bytes");
        let err = ceiling_error(dir.path(), 1, Measured::Unreadable, true, WATCH_ENTRIES);
        assert!(err.message.contains("can't be read") && err.message.contains("was stopped"), "{}", err.message);
    }

    /// Waits up to five seconds for the watcher to stop the build.
    fn wait_for_stop(watch: &ProjectWatch) -> Option<Measured> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while watch.stopped().is_none() && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        watch.stopped()
    }

    /// The watcher trips when the folder grows past its ceiling while a build runs, which is what
    /// cancels the container.
    #[test]
    fn the_watcher_trips_when_the_folder_grows_past_the_ceiling() {
        let dir = tempfile::tempdir().unwrap();
        let watch = ProjectWatch::start(dir.path().to_path_buf(), 4096, WATCH_ENTRIES).expect("watcher");
        std::thread::sleep(WATCH_EVERY);
        assert!(watch.stopped().is_none(), "an empty folder is under the ceiling");
        std::fs::write(dir.path().join("big.bin"), vec![0u8; 8192]).unwrap();
        assert!(matches!(wait_for_stop(&watch), Some(Measured::Over(_))), "the watcher sees the growth");
    }

    /// Many small files first and then one large write must not slip past the ceiling: a folder
    /// that grows too large to measure stops the build, and the error says it couldn't be measured.
    #[test]
    fn the_watcher_stops_a_build_that_grows_too_large_to_measure() {
        let dir = tempfile::tempdir().unwrap();
        let watch = ProjectWatch::start(dir.path().to_path_buf(), 1024 * 1024, 4).expect("watcher");
        std::thread::sleep(WATCH_EVERY);
        assert!(watch.stopped().is_none(), "an empty folder is measured and under the ceiling");
        for i in 0..10 {
            std::fs::write(dir.path().join(format!("small{i}")), b"x").unwrap();
        }
        let stopped = wait_for_stop(&watch);
        assert_eq!(stopped, Some(Measured::TooManyEntries), "the watcher stops a folder it can't measure");
        let err = ceiling_error(dir.path(), 1, Measured::TooManyEntries, true, 4);
        assert_eq!(err.code, ErrorCode::OverLimit);
        assert!(err.message.contains("too many to measure") && err.message.contains("was stopped"), "{}", err.message);
    }

    #[cfg(unix)]
    #[test]
    fn a_folder_the_sandbox_user_cannot_write_is_refused() {
        use std::os::unix::fs::PermissionsExt as _;
        let dir = tempfile::tempdir().unwrap();
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o500)).unwrap();
        let spec = Spec { project: dir.path().to_path_buf(), ..Default::default() };
        let err = run_argv(&spec, &["true".into()], Duration::from_secs(1), None).expect_err("read-only folder");
        assert!(err.message.contains("can't write"), "{err:?}");
        assert!(err.remediation.as_deref().is_some_and(|text| text.contains("writable")), "{err:?}");
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    }
}
