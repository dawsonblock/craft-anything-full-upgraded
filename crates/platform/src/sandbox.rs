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
            user_arg(host_user()),
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

/// Run `command`, a shell line, inside a container built from `spec`.
pub fn run(spec: &Spec, command: &str, timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    run_argv(spec, &["sh".to_string(), "-c".to_string(), command.to_string()], timeout, cancel)
}

/// Run a program and its arguments inside a container built from `spec`, without a shell.
pub fn run_argv(spec: &Spec, command: &[String], timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    let mut argv = spec.argv_for(command)?;
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
        // `C:\Users\me\app` becomes `C:/Users/me/app`; Docker accepts the drive letter.
        return Ok(text.replace('\\', "/"));
    }
    if text.contains(':') {
        return Err(ToolError::invalid("The project folder's path contains `:`, which Docker reads as a volume separator.")
            .with_remediation("Move the project to a folder whose path has no colon, or turn the sandbox off in Settings → Build."));
    }
    Ok(text.into_owned())
}

/// The `--user` value: the host id, except root. Root on the host must not become root in the
/// container; a fixed non-root id is used instead (Windows has no host id to mirror).
fn user_arg(host: Option<String>) -> String {
    host.filter(|u| !names_root(u)).unwrap_or_else(|| "1000:1000".to_string())
}

/// Whether a `uid:gid` pair has a zero in it. A zero uid is root whatever its group is (`0:20` is
/// still root), and a zero gid would give a non-root user root's group.
fn names_root(user: &str) -> bool {
    user.split(':').any(|id| id.trim() == "0")
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
        assert_eq!(user_arg(Some("501:20".into())), "501:20");
        assert_eq!(user_arg(None), "1000:1000");
    }

    /// Opt-in: needs Docker. The container really runs as the host user, and never as root.
    /// `cargo test -p craft-anything-platform the_container_runs_as_the_host_user_not_root -- --ignored`
    #[test]
    #[ignore]
    fn the_container_runs_as_the_host_user_not_root() {
        let dir = tempfile::tempdir().unwrap();
        let spec = Spec { project: dir.path().to_path_buf(), ..Default::default() };
        let out = run(&spec, "id -u", Duration::from_secs(60), None).expect("docker run");
        assert!(out.success, "{}", out.stderr_text());
        let uid = out.stdout_text().trim().to_string();
        assert_ne!(uid, "0", "the container ran as root");
        if let Some(host) = host_user() {
            assert_eq!(Some(uid.as_str()), host.split(':').next(), "the container runs as the host user");
        }
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
}
