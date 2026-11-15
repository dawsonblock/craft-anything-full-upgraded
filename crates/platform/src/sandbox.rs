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
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

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
}

impl Default for Spec {
    fn default() -> Self {
        Spec { image: DEFAULT_IMAGE.to_string(), project: PathBuf::new(), network: false, memory_mb: 4096, cpus: 2.0, pids: 512 }
    }
}

impl Spec {
    /// The `docker run` arguments for `command`, after the program name.
    ///
    /// Everything the command can touch is decided here, so this is the security boundary and the
    /// tests read it directly. Values outside their range are clamped rather than refused: a
    /// person who typed `0` gets the floor, not a command that runs unbounded.
    pub fn argv(&self, command: &str) -> Result<Vec<String>, ToolError> {
        let image = self.image.trim();
        if image.is_empty() || image.starts_with('-') || image.chars().any(char::is_whitespace) {
            return Err(ToolError::invalid(format!("`{}` isn't a Docker image name.", self.image))
                .with_remediation("Set the image in Settings → Build, for example `debian:bookworm-slim`."));
        }
        if !self.project.is_absolute() {
            return Err(ToolError::invalid("The sandbox needs an absolute project folder."));
        }
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
            "--cap-drop".into(),
            "ALL".into(),
            "--security-opt".into(),
            "no-new-privileges".into(),
            "-v".into(),
            format!("{host}:{WORKDIR}"),
            "-w".into(),
            WORKDIR.into(),
        ];
        if let Some(user) = host_user() {
            argv.push("--user".into());
            argv.push(user);
        } else {
            // Windows has no host id to mirror. A fixed non-root id is better than leaving the
            // image's default, which is often root.
            argv.push("--user".into());
            argv.push("1000:1000".into());
        }
        argv.extend([image.to_string(), "sh".into(), "-c".into(), command.to_string()]);
        Ok(argv)
    }
}

/// Run `command` inside a container built from `spec`.
pub fn run(spec: &Spec, command: &str, timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    let mut argv = spec.argv(command)?;
    let name = container_name();
    // A name and a label let a container be found and stopped even when the Docker client that
    // started it is gone.
    argv.insert(1, "--name".into());
    argv.insert(2, name.clone());
    argv.insert(3, "--label".into());
    argv.insert(4, LABEL.into());
    let mut process = Process::new("docker")
        .args(argv)
        .cwd(&spec.project)
        .timeout(timeout)
        .stdout_cap(4 * 1024 * 1024)
        .stderr_cap(4 * 1024 * 1024)
        .label("the sandboxed build command");
    if let Some(cancel) = cancel {
        process = process.cancel_when(move || cancel());
    }
    let result = process.run();
    // `--rm` removes a container that exits. A timeout or cancel kills the Docker client first,
    // so the daemon keeps the container running; stop it by name. An error from Docker itself
    // also comes here, where removing a container that was never made does nothing.
    if result.is_err() {
        stop(&name);
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

/// The project folder as Docker's `-v host:container` expects it.
fn host_path(project: &Path) -> Result<String, ToolError> {
    let text = project.to_string_lossy();
    if cfg!(windows) {
        // `C:\Users\me\app` becomes `C:/Users/me/app`; Docker accepts the drive letter.
        return Ok(text.replace('\\', "/"));
    }
    if text.contains(':') {
        return Err(ToolError::invalid("The project folder's path contains `:`, which Docker reads as a volume separator.")
            .with_remediation("Move the project to a folder whose path has no colon, or turn the sandbox off in Settings → Build."));
    }
    Ok(text.into_owned())
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
        Spec { project: PathBuf::from("/tmp/craft-build"), ..Default::default() }
    }

    fn joined(argv: &[String]) -> String {
        argv.join(" ")
    }

    #[test]
    fn a_container_sees_only_the_project() {
        let argv = spec().argv("npm install").unwrap_or_default();
        let text = joined(&argv);
        assert!(text.contains("-v /tmp/craft-build:/work"), "{text}");
        assert!(text.contains("-w /work"), "{text}");
        assert_eq!(text.matches("-v ").count(), 1, "exactly one mount: {text}");
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
}
