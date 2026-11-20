//! Rebuild control as tools, registered from this layer next to the engine's own
//! ([`craft_anything_engine::Engine::shared`]): start a build (new or a follow-up), see whether
//! it's running, add a note while it works, and stop it. The conversation, questions and result
//! are the project tools' (`project_show`, `project_inbox_answer`, `project_play`…).

use super::*;
use craft_anything_engine::EngineRef;
use craft_anything_engine::questions::{self, AskedQuestion};
use craft_anything_engine::tool::{Ctx, Effects, Requires, ToolOutput, ToolSpec, Toolset, parse_params, schema_of};
use schemars::JsonSchema;
use serde::Deserialize;

/// The AI layer's tools, for [`craft_anything_engine::Engine::shared`].
pub fn specs() -> Vec<ToolSpec> {
    vec![
        ToolSpec {
            id: "rebuild_start",
            title: "Start a rebuild",
            summary: "Start building: a new project from a request and a target (a web address, an absolute path, or - for none), or a follow-up on an existing project. A coding agent studies, builds, checks and refines in the background; follow it with rebuild_status and project_show, answer its questions with project_inbox_answer. Up to `build.parallel` projects build at once (2 by default); more are queued and start on their own as others finish. It runs a coding agent that writes and builds code, so it needs consent.",
            beginner: "Starts making something.",
            toolset: Toolset::Projects,
            effects: Effects::LAUNCHES_PROCESS.union(Effects::EXECUTES_FOREIGN_CODE).union(Effects::WRITES_FILESYSTEM).union(Effects::ACCESSES_NETWORK),
            requires: Requires::Nothing,
            input_schema: schema_of::<StartParams>,
            run: rebuild_start,
        },
        ToolSpec {
            id: "rebuild_status",
            title: "Rebuild status",
            summary: "Whether a project's build is running in this session (or queued, and where), its status and note, the latest check, and how many questions are waiting.",
            beginner: "Shows how a build is going.",
            toolset: Toolset::Projects,
            effects: Effects::READ_ONLY,
            requires: Requires::Nothing,
            input_schema: schema_of::<ProjectParams>,
            run: rebuild_status,
        },
        ToolSpec {
            id: "rebuild_activity_show",
            title: "Show a build's activity",
            summary: "What a project's builds did, line by line, newest last: each stage, every command and file the builder touched and how it ended, what it said, and what programs printed outside the conversation. Secrets are hidden. Works while a build runs and after. `limit` lines (200 by default, at most 2000), `offset` lines back from the newest.",
            beginner: "Shows the details of what a build has been doing.",
            toolset: Toolset::Projects,
            effects: Effects::READ_ONLY.union(Effects::CARRIES_TARGET_CONTENT),
            requires: Requires::Nothing,
            input_schema: schema_of::<ActivityParams>,
            run: rebuild_activity_show,
        },
        ToolSpec {
            id: "rebuild_note",
            title: "Add to a running build",
            summary: "Tell a running build something more; the agent reads it at its next step. With no build running, start a follow-up with rebuild_start instead.",
            beginner: "Adds information while it builds.",
            toolset: Toolset::Projects,
            effects: Effects::CHANGES_UI_STATE.union(Effects::WRITES_FILESYSTEM),
            requires: Requires::Nothing,
            input_schema: schema_of::<NoteParams>,
            run: rebuild_note,
        },
        ToolSpec {
            id: "rebuild_note_withdraw",
            title: "Take back a note",
            summary: "Take back a note given with rebuild_note (by the id it returned) while the agent hasn't read it yet; it returns the note's text. Once the agent has read it, it can't be taken back.",
            beginner: "Takes back something you added before the builder reads it.",
            toolset: Toolset::Projects,
            effects: Effects::CHANGES_UI_STATE,
            requires: Requires::Nothing,
            input_schema: schema_of::<WithdrawParams>,
            run: rebuild_note_withdraw,
        },
        ToolSpec {
            id: "rebuild_stop",
            title: "Stop a rebuild",
            summary: "Pause a project's running build. Its work so far is kept; a follow-up continues it.",
            beginner: "Pauses a build.",
            toolset: Toolset::Projects,
            effects: Effects::CHANGES_UI_STATE,
            requires: Requires::Nothing,
            input_schema: schema_of::<ProjectParams>,
            run: rebuild_stop,
        },
        ToolSpec {
            id: QUESTION_TOOL,
            title: "Ask about a build",
            summary: "Ask a question about a project's build (\"where are the latest files?\", \"why this engine?\") without starting a build or a round, and without changing anything. Claude Code answers from a copy of the builder's own session, which isn't kept, reading files only (none in Clean room); an AI with an API key answers with the tools that change nothing. Other coding agents are refused. Safe while a build runs: it doesn't interrupt it. Returns the answer, how it was answered and its cost, and keeps it in the project (`rebuild_questions_list`).",
            beginner: "Asks about what was made, without changing it.",
            toolset: Toolset::Projects,
            effects: Effects::LAUNCHES_PROCESS.union(Effects::ACCESSES_NETWORK),
            requires: Requires::Nothing,
            input_schema: schema_of::<QuestionParams>,
            run: rebuild_question_ask,
        },
        ToolSpec {
            id: "rebuild_questions_list",
            title: "List questions about a build",
            summary: "The questions asked about a project's build and their answers, oldest first (the newest 200).",
            beginner: "Shows what you've asked about this project.",
            toolset: Toolset::Projects,
            effects: Effects::READ_ONLY,
            requires: Requires::Nothing,
            input_schema: schema_of::<ProjectParams>,
            run: rebuild_questions_list,
        },
        ToolSpec {
            id: "rebuild_check",
            title: "Check my build",
            summary: "For the builder: test the project's current build against the original now, with the same checks Craft Anything runs after each turn (a website's likeness at each width with the padding, colour, font and layout differences; an app's or game's picture against the original's; whether it builds; the mechanics sheet; credits). The project's running build answers it, so the builder can fix what it lists and check again before replying. Not counted as a round. project defaults to the project this tool server works for.",
            beginner: "Lets the builder test its work against the original while it builds.",
            toolset: Toolset::Core,
            effects: Effects::WRITES_FILESYSTEM,
            requires: Requires::Nothing,
            input_schema: schema_of::<CheckParams>,
            run: rebuild_check,
        },
    ]
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StartParams {
    /// What to make (a new project), or the follow-up message (with `project`).
    pub request: String,
    /// For a new project: a web address, an absolute path to an app, game or file, or `-` for none.
    #[serde(default)]
    pub target: Option<String>,
    /// Continue this project folder instead of starting a new one.
    #[serde(default)]
    pub project: Option<PathBuf>,
    /// With `project`: carry on where its last run stopped (paused, or the app or the computer
    /// stopped) instead of sending `request` as a follow-up.
    #[serde(default)]
    pub resume: bool,
    /// Where a new project's folder goes (default: the projects folder from settings).
    #[serde(default)]
    pub projects_dir: Option<PathBuf>,
    /// Start the new project in this folder itself instead (it's added to the project list). If
    /// it already has files, `existing` says what they are.
    #[serde(default)]
    pub project_folder: Option<PathBuf>,
    /// With `project_folder` that has files: `edit` (they're the app, edited in place) or
    /// `start_from` (material to start from; the app is built in app/ beside them).
    #[serde(default)]
    pub existing: Option<craft_anything_engine::workspace::ExistingFiles>,
    /// What to build with: an engine (unity, unreal, godot, rust) or a stack's full name.
    #[serde(default)]
    pub build_with: Option<String>,
    /// For a new game: the builder may generate pictures and 3D models (paid from the person's
    /// asset provider key, within the project's budget). Default false; change it later with
    /// project_assets_allow.
    #[serde(default)]
    pub generate_assets: bool,
    /// The coding agent to use (an enabled backend name), kept with a new project for its later
    /// changes; default: the one set up for building.
    #[serde(default)]
    pub backend: Option<String>,
    /// Refinement rounds after the first build (default 2).
    #[serde(default)]
    pub rounds: Option<u32>,
    /// Pictures of the original (absolute paths): the look the person expects. They're copied
    /// into the project's reference/ folder for the builder.
    #[serde(default)]
    pub pictures: Vec<PathBuf>,
    /// Documents about the original (absolute paths): a manual or notes on what it does and how
    /// it behaves (.pdf, .md, .markdown, .txt, .html, .htm, up to 20 MB each). They're copied into
    /// the project's reference/docs/ folder, and the builder reads them before it plans.
    #[serde(default)]
    pub documents: Vec<PathBuf>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CheckParams {
    /// The project's folder; default: the project this tool server was started for.
    #[serde(default)]
    pub project: Option<PathBuf>,
    /// Longest wait for the result, in minutes (1..=60, default 20): a website with several pages
    /// or a game's engine build takes a few minutes.
    #[serde(default = "d_wait_minutes")]
    pub wait_minutes: u64,
}

fn d_wait_minutes() -> u64 {
    20
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProjectParams {
    /// The project's folder.
    pub project: PathBuf,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ActivityParams {
    /// The project's folder.
    pub project: PathBuf,
    /// How many lines to return (200 by default, at most 2000).
    #[serde(default)]
    pub limit: Option<usize>,
    /// How many lines back from the newest to end at (0 by default).
    #[serde(default)]
    pub offset: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NoteParams {
    /// The project's folder.
    pub project: PathBuf,
    /// What to tell the builder.
    pub text: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WithdrawParams {
    /// The project's folder.
    pub project: PathBuf,
    /// The note's id, as rebuild_note returned it.
    pub id: u64,
}

/// Builds started by tools in this session, by project folder.
struct Runs(Arc<Mutex<RunSet>>);

/// How often queued builds are checked for a free place between tool calls.
const QUEUE_TICK: Duration = Duration::from_secs(2);

fn runs(ctx: &mut Ctx<'_>) -> Arc<Runs> {
    match ctx.session.ext.get::<Runs>() {
        Some(r) => r,
        None => {
            let r = Arc::new(Runs(Arc::new(Mutex::new(RunSet::new(ctx.session.settings.build.parallel)))));
            ctx.session.ext.insert(r.clone());
            r
        }
    }
}

/// The engine the rebuild tools were registered on.
fn engine(ctx: &Ctx<'_>) -> Option<Arc<Engine>> {
    ctx.session.ext.get::<EngineRef>().and_then(|r| r.0.upgrade())
}

/// Bring every run up to date and start queued ones in free places. Events are kept in each
/// project's feed by the run itself, so they're dropped here.
fn refresh(ctx: &Ctx<'_>, set: &mut RunSet) {
    set.set_limit(ctx.session.settings.build.parallel);
    if let Some(engine) = engine(ctx) {
        let hub = AiHub::with_stores(engine, ctx.session.config_dir().join("config.toml"), crate::KeyStore::system());
        let _ = set.poll(&hub);
    }
}

fn open(project: &Path) -> Result<Workspace, ToolError> {
    Workspace::open(project).map_err(|e| {
        ToolError::new(ErrorCode::NotFound, format!("{} isn't a project folder: {}", project.display(), e.message))
            .with_remediation("Pass a folder from project_list (it holds craft.json).")
    })
}

/// The question tool's id.
pub const QUESTION_TOOL: &str = "rebuild_question_ask";

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct QuestionParams {
    /// The project's folder.
    pub project: PathBuf,
    /// The question, in plain words.
    pub question: String,
}

fn rebuild_question_ask(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: QuestionParams = parse_params(params)?;
    let engine = engine(ctx).ok_or_else(|| {
        ToolError::new(ErrorCode::FeatureMissing, "Questions about a build aren't available in this engine.")
            .with_remediation("Use the app or `craft-anything-cli`.")
    })?;
    let ws = open(&p.project)?;
    let after = craft_anything_engine::feed::load(&ws).len();
    ctx.report(None, "Asking…");
    // The session is this call's while it runs: the question gets its settings from here.
    let mut settings = ctx.session.settings.clone();
    settings.deep_mode = !ctx.session.clean_room();
    let answered =
        super::question::ask(&AiHub::new(engine), &settings, Some(crate::failure_log::dir_in(&ctx.session.config_dir())), &ws, &p.question, &ctx.cancel)?;
    let question = p.question.trim();
    let from_session = answered.from == super::question::AnsweredFrom::BuildersSession;
    let asked = AskedQuestion {
        deep: settings.deep_mode,
        ..AskedQuestion::new(question.to_string(), answered.answer.clone(), from_session, answered.backend.clone(), answered.cost_usd, after)
    };
    // The replacement notice is for this caller; the saved question keeps the AI that answered.
    // An answer that couldn't be kept is still the answer.
    let not_kept = questions::add(&ws, asked).err().map(|e| e.message);
    Ok(ToolOutput::Value(json!({"project": ws.root, "question": question, "answered": answered, "not_kept": not_kept})))
}

fn rebuild_questions_list(_ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: ProjectParams = parse_params(params)?;
    let questions = questions::load(&open(&p.project)?);
    Ok(ToolOutput::Value(json!({"count": questions.len(), "questions": questions})))
}

/// Refuse a named picture or document that [`Workspace::add_references`] would skip or refuse: a
/// file that isn't there, one of a kind it doesn't take, or one over its size limit.
fn refuse_unaddable_references(pictures: &[PathBuf], documents: &[PathBuf]) -> Result<(), ToolError> {
    use craft_anything_engine::workspace::{DOCUMENT_EXTENSIONS, document_too_large, is_document, is_picture, picture_too_large};
    if let Some(f) = pictures.iter().chain(documents).find(|f| !f.is_file()) {
        return Err(
            ToolError::invalid(format!("{} can't be added: there's no file there.", f.display())).with_remediation("Pass the full path of a file that exists.")
        );
    }
    if let Some(f) = pictures.iter().find(|f| !is_picture(f)) {
        return Err(ToolError::invalid(format!("{} isn't a picture Craft Anything can add.", f.display()))
            .with_remediation("Add pictures as .png, .jpg, .jpeg, .webp or .gif, and manuals or notes as `documents`."));
    }
    if let Some(f) = pictures.iter().find(|f| picture_too_large(f)) {
        return Err(ToolError::new(ErrorCode::OverLimit, format!("{} can't be added: a picture can be up to 20 MB.", f.display()))
            .with_remediation("Add a smaller picture, or save it as a JPEG."));
    }
    if let Some(f) = documents.iter().find(|f| !is_document(f)) {
        return Err(ToolError::invalid(format!("{} isn't a document Craft Anything can add.", f.display()))
            .with_remediation(format!("Add a manual or notes as {}.", DOCUMENT_EXTENSIONS.iter().map(|e| format!(".{e}")).collect::<Vec<_>>().join(", "))));
    }
    if let Some(f) = documents.iter().find(|f| document_too_large(f)) {
        return Err(ToolError::new(ErrorCode::OverLimit, format!("{} can't be added: a document can be up to 20 MB.", f.display()))
            .with_remediation("Add the chapters that describe what the program does, or save the manual as plain text."));
    }
    Ok(())
}

fn rebuild_start(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: StartParams = parse_params(params)?;
    let engine = engine(ctx).ok_or_else(|| {
        ToolError::new(ErrorCode::FeatureMissing, "Building isn't available in this engine.").with_remediation("Use the app or `craft-anything-cli rebuild`.")
    })?;
    let runs = runs(ctx);
    // A named picture or document that can't be added is an error, not a silent skip (the person
    // asked for it to be used), and it's refused before a project folder is made.
    refuse_unaddable_references(&p.pictures, &p.documents)?;
    let cfg = crate::AiConfig::from_settings(&ctx.session.settings);
    // Refuse a start that can only spend money it may not, before a project or pictures are made.
    crate::budget::refuse_over_daily(cfg.spend_limits(), &crate::budget::ledger_path(&ctx.session.config_dir().join("config.toml")))?;
    // A new project would leave its folder behind if the run then refused its agent; check first.
    if p.project.is_none() {
        let entry = super::builder_backend(&cfg, p.backend.as_deref())?;
        super::refuse_unconfined(&cfg, &entry)?;
    }
    // Refuse an unknown or switched-off name before opening or making a project or adding pictures:
    // a refused start must not leave anything behind.
    if let Some(name) = p.backend.as_deref() {
        builder_backend(&cfg, Some(name))?;
    }
    let (ws, message) = match &p.project {
        Some(dir) => {
            if p.build_with.is_some() {
                return Err(ToolError::invalid("build_with is for new projects; a project keeps the stack it was built with."));
            }
            if p.generate_assets {
                return Err(ToolError::invalid("generate_assets is for new projects.").with_remediation("Use project_assets_allow for an existing project."));
            }
            let ws = open(dir)?;
            ctx.session.remember_project(&ws.root)?;
            (ws, (!p.resume).then(|| p.request.clone()))
        }
        None if p.resume => return Err(ToolError::invalid("resume is for an existing project.").with_remediation("Pass its folder as `project`.")),
        None => {
            let target = p.target.as_deref().unwrap_or("-");
            let place = match (&p.project_folder, &p.projects_dir) {
                (Some(_), Some(_)) => return Err(ToolError::invalid("Pass projects_dir or project_folder, not both.")),
                (Some(f), None) => project::Place::At(f, p.existing),
                (None, Some(d)) => project::Place::In(d),
                (None, None) => project::Place::Projects,
            };
            let ws = project::new_project(&ctx.session.settings, target, &p.request, place, p.build_with.as_deref(), p.generate_assets, p.backend.as_deref())?;
            if p.project_folder.is_some() || matches!(place, project::Place::At(..)) {
                ctx.session.remember_project(&ws.root)?;
            }
            (ws, None)
        }
    };
    if !p.pictures.is_empty() || !p.documents.is_empty() {
        let files: Vec<PathBuf> = p.pictures.iter().chain(&p.documents).cloned().collect();
        ws.add_references(&files)?;
    }
    // A headless start can't show the app's toast: say which AI builds when the saved one is gone.
    let builder_notice =
        p.project.as_ref().and_then(|_| super::builder_replacement(&cfg, &ws, p.backend.as_deref()).map(|r| super::replaced_builder_notice(&r.saved, &r.used)));
    // A coding agent runs its own tools and signs in as the person, so it can't be put in the
    // container. Say so even when the person allowed it, so the build is never mistaken for one
    // that is confined.
    let effective = builder_backend(&cfg, p.backend.as_deref().or_else(|| super::project_builder(&cfg, &ws)))?;
    let agent_outside = (effective.config.kind == crate::BackendKind::AgentCli)
        .then(|| craft_anything_engine::execution::Policy::of(&ctx.session.settings.build).outside(craft_anything_engine::execution::Route::Agent))
        .flatten();
    let mut set = runs.0.lock().unwrap_or_else(PoisonError::into_inner);
    refresh(ctx, &mut set);
    if set.is_busy(&ws.root) {
        return Err(ToolError::new(ErrorCode::BadInput, "This project is already building.")
            .with_remediation("Add to it with rebuild_note, or stop it with rebuild_stop."));
    }
    // What the builder can be held to, as data, so a caller can decide rather than read prose.
    let builder = super::builder_backend(&cfg, p.backend.as_deref().or_else(|| super::project_builder(&cfg, &ws)))?;
    let confinement = crate::cli_agents::confinement_for(&builder);
    let root = ws.root.clone();
    let opts = RunOptions {
        backend: p.backend,
        shown: message.clone(),
        message,
        continuing: p.resume,
        max_rounds: p.rounds.unwrap_or(2),
        beginner: false,
        clean_room: Some(ctx.session.clean_room()),
        build: Some(ctx.session.settings.build.clone()),
        ..RunOptions::default()
    };
    // A tool runs with the session locked, so the hub reads the saved file: use this session's
    // config file (the person's own in the app), not the machine's default one.
    let hub = AiHub::with_stores(engine, ctx.session.config_dir().join("config.toml"), crate::KeyStore::system());
    let mut out = match set.start(&hub, ws, opts)? {
        Started::Running => json!({"project": root.display().to_string(), "started": true}),
        Started::Queued { position } => {
            // It starts when a place comes free, whether or not another tool is called.
            drop(set);
            runs::keep_queue_moving(&runs.0, &hub.engine, ctx.session.config_dir().join("config.toml"), QUEUE_TICK);
            json!({"project": root.display().to_string(), "started": false, "queued": position})
        }
    };
    if let Some(notice) = builder_notice {
        out["builder_notice"] = json!(notice);
    }
    if let Some(why) = agent_outside {
        out["sandbox"] = json!({"runs": "on this computer, outside the Docker sandbox", "why": why});
    }
    out["agent_confinement"] = json!({
        "build": confinement.build.as_str(),
        "chat": confinement.chat.as_str(),
        "question": confinement.question.as_str(),
    });
    if confinement.build_is_unconfined() {
        out["agent_confinement_note"] =
            json!(format!("{} runs with your own access during a build: it doesn't ask us before edits or commands.", builder.config.name));
    }
    Ok(ToolOutput::Value(out))
}

fn rebuild_status(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: ProjectParams = parse_params(params)?;
    let ws = open(&p.project)?;
    let runs = runs(ctx);
    let mut set = runs.0.lock().unwrap_or_else(PoisonError::into_inner);
    refresh(ctx, &mut set);
    let m = &ws.manifest;
    Ok(ToolOutput::Value(json!({
        "project": ws.root.display().to_string(),
        "running": set.is_running(&ws.root),
        "queued": set.position(&ws.root),
        "status": m.status,
        "status_label": m.status.label(),
        "note": m.note,
        "last_check": m.checks.last(),
        "waiting": Inbox::new(ws.inbox_dir()).pending().len(),
    })))
}

fn rebuild_activity_show(_ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: ActivityParams = parse_params(params)?;
    let ws = open(&p.project)?;
    let limit = p.limit.unwrap_or(200).min(2000);
    let (lines, total) = super::activity::read_log(&ws.root, p.offset.unwrap_or(0), limit)?;
    Ok(ToolOutput::Value(json!({
        "project": ws.root.display().to_string(),
        "log": ws.root.join(craft_anything_engine::workspace::ACTIVITY_LOG).display().to_string(),
        "lines": lines,
        "total_lines_read": total,
    })))
}

fn rebuild_note(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: NoteParams = parse_params(params)?;
    let ws = open(&p.project)?;
    let runs = runs(ctx);
    let mut set = runs.0.lock().unwrap_or_else(PoisonError::into_inner);
    refresh(ctx, &mut set);
    match set.get(&ws.root).filter(|r| r.is_running()) {
        Some(run) => {
            let id = run.add_note(p.text.clone(), p.text, Vec::new());
            Ok(ToolOutput::Value(json!({"id": id, "queued": run.queued()})))
        }
        None => Err(ToolError::new(ErrorCode::BadInput, "No build is running for this project in this session.")
            .with_remediation("Start a follow-up with rebuild_start (pass project and your message).")),
    }
}

fn rebuild_note_withdraw(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: WithdrawParams = parse_params(params)?;
    let ws = open(&p.project)?;
    let runs = runs(ctx);
    let mut set = runs.0.lock().unwrap_or_else(PoisonError::into_inner);
    refresh(ctx, &mut set);
    match set.get(&ws.root).and_then(|r| r.unsend(p.id)) {
        Some(note) => Ok(ToolOutput::Value(json!({"withdrawn": note.text}))),
        None => Err(ToolError::new(ErrorCode::BadInput, format!("Note {} isn't waiting: the agent has read it, or it isn't from this session.", p.id))),
    }
}

fn rebuild_stop(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: ProjectParams = parse_params(params)?;
    let ws = open(&p.project)?;
    let runs = runs(ctx);
    let mut set = runs.0.lock().unwrap_or_else(PoisonError::into_inner);
    refresh(ctx, &mut set);
    match set.stop(&ws.root) {
        Stopped::Stopping => Ok(ToolOutput::Value(json!({"stopping": ws.root.display().to_string()}))),
        Stopped::Unqueued => Ok(ToolOutput::Value(json!({"unqueued": ws.root.display().to_string()}))),
        Stopped::Nothing => Err(ToolError::new(ErrorCode::BadInput, "No build is running for this project in this session.")),
    }
}

/// Ask the project's running build to check the current build (see [`super::self_check`]) and
/// wait for what it found.
fn rebuild_check(ctx: &mut Ctx<'_>, params: Value) -> Result<ToolOutput, ToolError> {
    let p: CheckParams = parse_params(params)?;
    if !(1..=60).contains(&p.wait_minutes) {
        return Err(ToolError::invalid("wait_minutes must be 1..=60"));
    }
    // The tool server is started with the project's inbox (<project>/inbox).
    let project = p
        .project
        .or_else(|| ctx.session.ext.get::<InboxDir>().and_then(|d| d.0.parent().map(Path::to_path_buf)))
        .ok_or_else(|| ToolError::invalid("No project given.").with_remediation("Pass project: the project's folder (it holds craft.json)."))?;
    let ws = open(&project)?;
    if self_check::building_here(&ws.root) {
        return Err(ToolError::new(ErrorCode::FeatureMissing, "This builder can't check its own work while it builds.")
            .with_remediation("Finish the turn: Craft Anything checks the build after it."));
    }
    let asks = Inbox::new(self_check::asks_dir(&ws.root));
    let id = asks.post(craft_anything_engine::inbox::RequestKind::Question, "Check the build", "", Vec::new(), false, None)?;
    ctx.report(None, "Checking the build against the original…");
    let cancel = ctx.cancel.clone();
    match asks.wait(&id, Duration::from_secs(p.wait_minutes * 60), &|| cancel.is_cancelled()) {
        Some(a) => Ok(ToolOutput::Value(json!({"project": ws.root.display().to_string(), "report": a.text.unwrap_or_default()}))),
        None if ctx.cancel.is_cancelled() => Err(ToolError::new(ErrorCode::Cancelled, "Stopped.")),
        None => Err(ToolError::new(ErrorCode::NotFound, "No answer: no build is running for this project, or the check took too long.")
            .with_remediation("Use this while Craft Anything is building the project; otherwise build it and finish the turn, and it is checked after.")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use craft_anything_engine::{CallOptions, Caller, Consent, Engine};

    #[test]
    fn a_question_through_the_engine_comes_back_instead_of_waiting_for_the_session() {
        // The tool runs while the call holds the session; reading it again would wait forever.
        let mut s = craft_anything_engine::testing::session();
        s.settings.ai =
            toml::from_str("[[backends]]\nname = \"Agent\"\nkind = \"agent_cli\"\nmodel = \"claude-code\"\ncommand = \"craft-anything-no-such-agent\"\n")
                .unwrap();
        let e = std::sync::Arc::new(Engine::shared(s, specs()));
        let d = tempfile::tempdir().unwrap();
        let ws = Workspace::create(d.path(), "Snake", "A snake game", TargetRef::None).unwrap();
        let (tx, rx) = std::sync::mpsc::channel();
        let e2 = e.clone();
        std::thread::spawn(move || {
            let r = e2.call(QUESTION_TOOL, json!({"project": ws.root, "question": "where are the files?"}), CallOptions::new(Caller::Cli));
            let _ = tx.send(r.map(|o| o.to_json()));
        });
        let r = rx.recv_timeout(std::time::Duration::from_secs(60)).expect("the question came back");
        // No such agent here: an error that says so, not a hang.
        assert!(r.is_err(), "{r:?}");
    }

    #[test]
    fn rebuild_tools_are_registered_from_the_ai_layer_and_check_their_input() {
        let d = tempfile::tempdir().unwrap();
        let e = Engine::shared(craft_anything_engine::testing::session(), specs());
        let ok = || CallOptions::new(Caller::Cli).with_consent(Consent::Granted);
        for id in ["rebuild_start", "rebuild_status", "rebuild_note", "rebuild_note_withdraw", "rebuild_stop", "rebuild_check"] {
            assert!(e.registry().get(id).is_some(), "{id}");
        }
        // Starting runs a coding agent: refused without consent, before anything is created.
        let p = json!({"request": "A page", "target": "-", "projects_dir": d.path()});
        assert!(e.call("rebuild_start", p, CallOptions::new(Caller::Mcp)).is_err());
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0, "nothing created without consent");
        // A bad target or stack is refused before a project is made.
        assert!(e.call("rebuild_start", json!({"request": "x", "target": "not a path", "projects_dir": d.path()}), ok()).is_err());
        assert!(e.call("rebuild_start", json!({"request": "x", "build_with": "cobol", "projects_dir": d.path()}), ok()).is_err());
        // Status, notes and stop on a project with nothing running.
        let ws = Workspace::create(d.path(), "Snake", "A snake game", TargetRef::None).unwrap();
        // A project's activity reads through the tool, none yet being no lines, not an error.
        let shown = e.call("rebuild_activity_show", json!({"project": ws.root}), ok()).unwrap().to_json();
        assert_eq!(shown["result"]["lines"], json!([]), "{shown}");
        std::fs::write(ws.root.join(craft_anything_engine::workspace::ACTIVITY_LOG), "one\ntwo\nthree\n").unwrap();
        let shown = e.call("rebuild_activity_show", json!({"project": ws.root, "limit": 1, "offset": 1}), ok()).unwrap().to_json();
        assert_eq!(shown["result"]["lines"], json!(["two"]), "{shown}");
        let st = e.call("rebuild_status", json!({"project": ws.root}), ok()).unwrap().to_json();
        assert_eq!(st["result"]["running"], false);
        assert_eq!(st["result"]["queued"], Value::Null);
        assert!(e.call("rebuild_note", json!({"project": ws.root, "text": "blue"}), ok()).is_err());
        assert!(e.call("rebuild_note_withdraw", json!({"project": ws.root, "id": 1}), ok()).is_err());
        assert!(e.call("rebuild_stop", json!({"project": ws.root}), ok()).is_err());
    }

    #[test]
    fn a_disabled_or_unknown_backend_is_refused_before_the_tool_makes_a_project() {
        let d = tempfile::tempdir().unwrap();
        let mut s = craft_anything_engine::testing::session();
        s.settings.ai = toml::from_str("[[backends]]\nname = \"Off\"\nkind = \"anthropic\"\nmodel = \"claude-test\"\nenabled = false\n").unwrap();
        let e = Engine::shared(s, specs());
        let ok = || CallOptions::new(Caller::Cli).with_consent(Consent::Granted);
        for (backend, expected) in [("Off", "turned off"), ("No Such AI", "No AI backend named")] {
            let p = json!({"request": "A page", "target": "-", "projects_dir": d.path(), "backend": backend});
            let err = e.call("rebuild_start", p, ok()).unwrap_err();
            assert!(err.message.contains(expected), "{backend}: {err:?}");
            assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0, "{backend}: a refused start must not make a project");
        }
    }

    /// #1102: a tool holds the session while it runs, so starting a build on an API backend must not
    /// ask for it again; over MCP the call waited on itself until the deadlock was broken.
    #[test]
    fn rebuild_start_on_an_api_backend_does_not_wait_on_its_own_session() {
        let d = tempfile::tempdir().unwrap();
        let mut s = craft_anything_engine::testing::session();
        let mut ai = crate::AiConfig::default();
        ai.upsert(crate::BackendEntry {
            config: crate::BackendConfig {
                name: "Local".into(),
                kind: crate::BackendKind::OpenaiCompatible,
                model: "qwen3:8b".into(),
                // Nothing listens there: the run only has to start, not to get an answer.
                base_url: Some("http://127.0.0.1:9/v1".into()),
                ssh_host: None,
                enabled: true,
            },
            options: Default::default(),
        });
        s.settings.ai = ai.to_table().unwrap();
        s.settings.save_to(&s.config_dir().join("config.toml")).unwrap();
        let e = Engine::shared(s, specs());
        let ws = Workspace::create(d.path(), "Notes", "A notes app", TargetRef::None).unwrap();
        let ok = || CallOptions::new(Caller::Mcp).with_consent(Consent::Granted);
        let (tx, rx) = std::sync::mpsc::channel();
        let (call, root) = (e.clone(), ws.root.clone());
        std::thread::spawn(move || {
            let _ = tx.send(call.call("rebuild_start", json!({"project": root, "request": "make it blue", "rounds": 0, "backend": "Local"}), ok()));
        });
        let started = rx.recv_timeout(std::time::Duration::from_secs(60)).expect("rebuild_start returned instead of waiting on its own session");
        assert!(started.is_ok(), "{:?}", started.err());
        let _ = e.call("rebuild_stop", json!({"project": ws.root}), ok());
    }

    /// A saved builder that is off is named in the start result, so MCP callers see the
    /// replacement too.
    #[cfg(unix)]
    #[test]
    fn rebuild_start_names_a_replaced_saved_builder() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let script = d.path().join("claude");
        std::fs::write(
            &script,
            "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"Made it blue.\"}]}}'\nprintf '%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"num_turns\":1}'\n",
        )
        .unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut s = craft_anything_engine::testing::session();
        let mut ai = crate::AiConfig::default();
        ai.upsert(crate::BackendEntry {
            config: crate::BackendConfig {
                name: "Off".into(),
                kind: crate::BackendKind::Anthropic,
                model: "claude-test".into(),
                base_url: None,
                ssh_host: None,
                enabled: false,
            },
            options: Default::default(),
        });
        ai.upsert(crate::BackendEntry {
            config: crate::BackendConfig {
                name: "Claude".into(),
                kind: crate::BackendKind::AgentCli,
                model: "claude-code".into(),
                base_url: None,
                ssh_host: None,
                enabled: true,
            },
            options: crate::BackendOptions { command: Some(script.display().to_string()), ..Default::default() },
        });
        s.settings.ai = ai.to_table().unwrap();
        // The tool runs with the session locked; save the same settings where its hub reads them.
        s.settings.save_to(&s.config_dir().join("config.toml")).unwrap();
        let e = Engine::shared(s, specs());
        let mut ws = Workspace::create(d.path(), "Notes", "A notes app", TargetRef::None).unwrap();
        ws.set_person(|m| m.builder = Some("Off".into())).unwrap();
        let ok = || CallOptions::new(Caller::Cli).with_consent(Consent::Granted);
        let out = e.call("rebuild_start", json!({"project": ws.root, "request": "make it blue", "rounds": 0}), ok()).unwrap().to_json();
        let notice = out["result"]["builder_notice"].as_str().unwrap_or_default();
        assert!(notice.contains("Off") && notice.contains("Claude"), "{out}");
        let _ = e.call("rebuild_stop", json!({"project": ws.root}), ok());
    }

    /// A named picture or document that can't be added (missing, the wrong kind, too large) is
    /// refused before a project folder is made, never skipped without a word.
    #[test]
    fn rebuild_start_refuses_a_document_it_cant_add_before_making_a_project() {
        let d = tempfile::tempdir().unwrap();
        let s = craft_anything_engine::testing::session();
        let projects = s.settings.projects_dir();
        let e = Engine::shared(s, specs());
        let ok = || CallOptions::new(Caller::Cli).with_consent(Consent::Granted);
        let word = d.path().join("manual.docx");
        std::fs::write(&word, "x").unwrap();
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "documents": [word]}), ok()).unwrap_err();
        assert_eq!(err.code, ErrorCode::BadInput, "{}", err.message);
        assert!(err.message.contains("isn't a document"), "{}", err.message);
        assert!(err.remediation.as_deref().is_some_and(|r| r.contains(".pdf") && r.contains(".md")), "{:?}", err.remediation);
        let big = d.path().join("manual.pdf");
        std::fs::File::create(&big).unwrap().set_len(21 * 1024 * 1024).unwrap();
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "documents": [big]}), ok()).unwrap_err();
        assert_eq!(err.code, ErrorCode::OverLimit, "{}", err.message);
        let missing = d.path().join("gone.pdf");
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "documents": [missing]}), ok()).unwrap_err();
        assert!(err.message.contains("no file there"), "{}", err.message);
        let missing = d.path().join("gone.png");
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "pictures": [missing]}), ok()).unwrap_err();
        assert!(err.message.contains("no file there"), "{}", err.message);
        let notes = d.path().join("notes.txt");
        std::fs::write(&notes, "x").unwrap();
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "pictures": [notes]}), ok()).unwrap_err();
        assert!(err.message.contains("isn't a picture"), "{}", err.message);
        let huge = d.path().join("screen.png");
        std::fs::File::create(&huge).unwrap().set_len(21 * 1024 * 1024).unwrap();
        let err = e.call("rebuild_start", json!({"target": "-", "request": "Recreate this app", "pictures": [huge]}), ok()).unwrap_err();
        assert_eq!(err.code, ErrorCode::OverLimit, "{}", err.message);
        let made = std::fs::read_dir(&projects).map(|r| r.count()).unwrap_or(0);
        assert_eq!(made, 0, "no project folder was left behind");
    }

    /// A tool holds the session while it starts a run, so `try_session` fails and the run must
    /// take the build policy the tool passes, not the copy saved to disk. A `refuse` set in the
    /// session but not yet written must still stop an agent build.
    #[test]
    fn rebuild_start_carries_the_sandbox_policy_when_the_session_is_held() {
        let d = tempfile::tempdir().unwrap();
        let mut s = craft_anything_engine::testing::session();
        // This test is about the sandbox policy, not the agent gate: let the stand-in build.
        let mut ai = crate::AiConfig { require_confined_build: false, ..Default::default() };
        ai.upsert(crate::BackendEntry {
            config: crate::BackendConfig {
                name: "Stand-in".into(),
                kind: crate::BackendKind::AgentCli,
                model: "stand-in".into(),
                base_url: None,
                ssh_host: None,
                enabled: true,
            },
            options: Default::default(),
        });
        s.settings.ai = ai.to_table().unwrap();
        // Save the default (allow) policy, then change it only in memory: the tool must pass the
        // session's copy, not read this file.
        s.settings.save_to(&s.config_dir().join("config.toml")).unwrap();
        s.settings.build.sandbox = craft_anything_engine::settings::SandboxMode::Docker;
        s.settings.build.sandbox_unconfined = craft_anything_engine::settings::Unconfined::Refuse;
        let e = Engine::shared(s, specs());
        let ws = Workspace::create(d.path(), "Notes", "A notes app", TargetRef::None).unwrap();
        let ok = || CallOptions::new(Caller::Cli).with_consent(Consent::Granted);
        let err = e.call("rebuild_start", json!({"project": ws.root, "request": "make it blue", "backend": "Stand-in", "rounds": 0}), ok()).unwrap_err();
        assert_eq!(err.code, ErrorCode::FeatureMissing, "{}", err.message);
        assert!(err.message.contains("outside the Docker sandbox"), "{}", err.message);
    }

    #[test]
    fn the_builder_asks_the_running_build_for_a_check_and_gets_its_report() {
        let d = tempfile::tempdir().unwrap();
        let e = Engine::shared(craft_anything_engine::testing::session(), specs());
        let ok = || CallOptions::new(Caller::Mcp);
        let ws = Workspace::create(d.path(), "Snake", "A snake game", TargetRef::None).unwrap();
        assert!(e.call("rebuild_check", json!({"project": ws.root, "wait_minutes": 0}), ok()).is_err(), "wait is checked");
        assert!(e.call("rebuild_check", json!({"project": d.path().join("nope")}), ok()).is_err(), "not a project");
        assert!(e.call("rebuild_check", json!({"wait_minutes": 1}), ok()).is_err(), "no project and no inbox to infer it from");
        // A stand-in for the run: answer the first check request with a report.
        let asks = Inbox::new(self_check::asks_dir(&ws.root));
        let answerer = std::thread::spawn(move || {
            for _ in 0..200 {
                if let Some(r) = asks.pending().first() {
                    asks.answer(&r.id, &Answer { text: Some("build: passed — Build succeeded.".into()), ..Default::default() }).unwrap();
                    return true;
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            false
        });
        // The project comes from the tool server's inbox (<project>/inbox) when not given.
        e.session().ext.insert(Arc::new(InboxDir(ws.inbox_dir())));
        let v = e.call("rebuild_check", json!({"wait_minutes": 1}), ok()).unwrap().to_json();
        assert!(answerer.join().unwrap(), "the request reached checks/asks/");
        assert_eq!(v["result"]["report"], "build: passed — Build succeeded.");
        // A build running in this process can't answer a check asked from inside it.
        let _here = self_check::BuildingHere::new(&ws.root);
        let err = e.call("rebuild_check", json!({"project": ws.root}), ok()).err().unwrap();
        assert_eq!(err.code, ErrorCode::FeatureMissing);
    }
}
