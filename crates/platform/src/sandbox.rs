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
use std::time::Duration;

/// Where the project folder is mounted inside the container.
pub const WORKDIR: &str = "/work";

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
        }
        argv.extend([image.to_string(), "sh".into(), "-c".into(), command.to_string()]);
        Ok(argv)
    }
}

/// Run `command` inside a container built from `spec`.
pub fn run(spec: &Spec, command: &str, timeout: Duration, cancel: Option<Cancel>) -> Result<Output, ToolError> {
    let argv = spec.argv(command)?;
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
    process.run().map_err(docker_error)
}

/// Turn Docker's absence into something a person can act on.
fn docker_error(e: ToolError) -> ToolError {
    if e.code == ErrorCode::EngineMissing {
        return ToolError::new(ErrorCode::EngineMissing, "Docker isn't running, so the build can't run sandboxed.")
            .with_remediation("Start Docker Desktop (or install Docker) and try again, or turn the sandbox off in Settings → Build.");
    }
    e
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
/// and a file created at `/work` keeps the host owner. Looked up once per process.
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
        for flag in ["--rm", "--init", "--cap-drop ALL", "--security-opt no-new-privileges", "--pids-limit", "--memory", "--cpus"] {
            assert!(text.contains(flag), "{flag} missing from {text}");
        }
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
    fn the_image_comes_before_the_shell() {
        let argv = spec().argv("x").unwrap_or_default();
        let image = argv.iter().position(|a| a == DEFAULT_IMAGE).unwrap_or_default();
        let shell = argv.iter().position(|a| a == "sh").unwrap_or_default();
        assert!(image < shell, "{argv:?}");
    }
}
