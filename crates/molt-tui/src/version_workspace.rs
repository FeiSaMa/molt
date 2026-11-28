//! Calls the existing `molt version` CLI for an explicitly selected workspace.

use std::collections::BTreeMap;
use std::fs::{self, DirBuilder};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use anyhow::{bail, Context, Result};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde::Deserialize;
use serde_json::Value;

const HISTORY_PAGE_SIZE: usize = 8;
const READER_PREVIEW_BYTES: usize = 4096;
/// Managed-file budget from `docs/operations/version-cli.md` and
/// `molt/version_files.py`.
const MAX_MANAGED_FILES: usize = 64;
const MAX_MANAGED_FILE_BYTES: u64 = 256 * 1024;
const MAX_MANAGED_TREE_BYTES: u64 = 2 * 1024 * 1024;
const RECOVERY_REASON: &str = "TUI file recovery to the previous main tree";
const RECOVERY_SOURCE: &str = "local TUI operator";

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct HistoryEvent {
    pub event: String,
    pub generation: u64,
    pub action: String,
}

impl HistoryEvent {
    fn matches(&self, query: &str, generation: Option<u64>) -> bool {
        self.event.to_ascii_lowercase().contains(query)
            || self.action.to_ascii_lowercase().contains(query)
            || Some(self.generation) == generation
    }
}

#[derive(Debug, Clone, Default)]
pub struct VersionHistory {
    events: Vec<HistoryEvent>,
    matches: Option<Vec<usize>>,
    filter: String,
    draft: Option<String>,
    selected: usize,
}

impl VersionHistory {
    pub(crate) fn replace_events(&mut self, events: Vec<HistoryEvent>) {
        self.events = events;
        self.apply_filter();
    }

    fn apply_filter(&mut self) {
        let query = self.filter.to_ascii_lowercase();
        let generation = query.strip_prefix('g').unwrap_or(&query).parse().ok();
        self.matches = Some(
            self.events
                .iter()
                .enumerate()
                .filter(|(_, event)| query.is_empty() || event.matches(&query, generation))
                .map(|(index, _)| index)
                .collect(),
        );
        self.selected = 0;
    }

    fn event_at(&self, index: usize) -> Option<&HistoryEvent> {
        let event_index = *self.matches.as_ref()?.get(index)?;
        self.events.get(event_index)
    }

    fn len(&self) -> usize {
        self.matches.as_ref().map_or(0, Vec::len)
    }
}

#[derive(Debug)]
pub struct SaveReceipt {
    pub generation: u64,
    pub snapshot: String,
    pub event: String,
    pub request_id: String,
}

/// How a `b` file-recovery attempt ended. Only [`Restored`](Self::Restored) and
/// [`NoEffect`](Self::NoEffect) establish a usable result; the other two keep
/// the user from blind retries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileRecoveryOutcome {
    Restored,
    NoEffect,
    Rejected,
    Unknown,
}

/// Lines that the Versions panel and the status line render after `b`.
#[derive(Debug, Clone)]
pub struct FileRecoveryReport {
    pub outcome: FileRecoveryOutcome,
    pub lines: Vec<String>,
    /// Request ID named by the CLI, when it named a trusted one. A pending ID
    /// from another action is recorded here but labelled as foreign.
    pub request_id: Option<String>,
}

impl FileRecoveryReport {
    pub fn headline(&self) -> String {
        self.lines.first().cloned().unwrap_or_default()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WorkspaceStatus {
    state_id: String,
    generation: u64,
    active: Option<String>,
    last_event: Option<String>,
    main_head: Option<String>,
    pending_request: Option<String>,
    branch_count: usize,
}

impl WorkspaceStatus {
    fn parse(value: &Value) -> Result<Self> {
        let branches = value["branches"]
            .as_object()
            .context("status omitted branches")?;
        let generation = value["generation"]
            .as_u64()
            .context("status omitted generation")?;
        let current_generation = value["current"]["generation"].as_u64();
        if generation == 0 && !value["current"].is_null() {
            bail!("status generation 0 carries a committed current pointer");
        }
        if generation > 0 && current_generation != Some(generation) {
            bail!("status current generation does not match generation");
        }
        Ok(Self {
            state_id: value["state_id"]
                .as_str()
                .context("status omitted state_id")?
                .to_owned(),
            generation,
            active: value["active"].as_str().map(str::to_owned),
            last_event: value["last_event"].as_str().map(str::to_owned),
            main_head: branches
                .get("main")
                .and_then(Value::as_str)
                .map(str::to_owned),
            pending_request: value["pending_request"].as_str().map(str::to_owned),
            branch_count: branches.len(),
        })
    }

    /// First difference against the cached baseline, or `None` when the panel
    /// baseline still describes this status (pending is checked separately).
    fn baseline_mismatch(&self, baseline: &Self) -> Option<String> {
        if self.state_id != baseline.state_id {
            return Some(format!(
                "state_id {} (baseline {})",
                self.state_id, baseline.state_id
            ));
        }
        if self.generation != baseline.generation {
            return Some(format!(
                "generation {} (baseline {})",
                self.generation, baseline.generation
            ));
        }
        if self.active != baseline.active {
            return Some(format!(
                "active {} (baseline {})",
                display_optional(self.active.as_deref()),
                display_optional(baseline.active.as_deref())
            ));
        }
        if self.last_event != baseline.last_event {
            return Some(format!(
                "last event {} (baseline {})",
                display_optional(self.last_event.as_deref()),
                display_optional(baseline.last_event.as_deref())
            ));
        }
        if self.main_head != baseline.main_head {
            return Some(format!(
                "main head {} (baseline {})",
                display_optional(self.main_head.as_deref()),
                display_optional(baseline.main_head.as_deref())
            ));
        }
        None
    }
}

#[derive(Debug, Clone, Deserialize)]
struct RecoveryEvent {
    event: String,
    generation: u64,
    action: String,
    #[serde(default)]
    from: Option<String>,
    #[serde(default)]
    to: Option<String>,
    #[serde(default)]
    active_after: Option<String>,
    #[serde(default)]
    branch: Option<String>,
    #[serde(default)]
    previous_head: Option<String>,
    #[serde(default)]
    parents: Vec<String>,
    #[serde(default)]
    base: Option<EventBase>,
}

#[derive(Debug, Clone, Deserialize)]
struct EventBase {
    generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ManagedFile {
    sha256: String,
    size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RecoveryTarget {
    from: String,
    to: String,
    files: BTreeMap<String, ManagedFile>,
}

#[derive(Debug)]
pub struct VersionWorkspace {
    pub binary: PathBuf,
    pub workspace: PathBuf,
    pub state_id: String,
    pub generation: u64,
    pub active_snapshot: Option<String>,
    pub last_event: Option<String>,
    /// Cached `status.branches.main`; part of the D3 panel baseline.
    pub main_head: Option<String>,
    pub history_view: VersionHistory,
    pub pending_request: Option<String>,
    pub last_save_receipt: Option<SaveReceipt>,
    pub last_file_recovery: Option<FileRecoveryReport>,
    pub detail: Vec<String>,
    pub draft: String,
    pub editing: bool,
    pub pinned_reader: Option<String>,
    pub reader_parent: Option<PathBuf>,
    pub reader_tree: Option<PathBuf>,
    pub reader_snapshot: Option<String>,
    pub reader_files: Vec<PathBuf>,
    pub reader_selected: usize,
    pub reader_preview: String,
    pub retired_reader_parents: Vec<PathBuf>,
}

impl VersionWorkspace {
    pub fn connect(binary: impl Into<PathBuf>, workspace: impl AsRef<Path>) -> Result<Self> {
        let binary = binary.into();
        let workspace = workspace.as_ref().to_path_buf();
        let mut connected = Self {
            binary,
            workspace,
            state_id: String::new(),
            generation: 0,
            active_snapshot: None,
            last_event: None,
            main_head: None,
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
            last_file_recovery: None,
            detail: Vec::new(),
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        };
        connected.recover()?;
        connected.refresh()?;
        Ok(connected)
    }

    fn execute(&self, arguments: &[&str]) -> Result<Output> {
        Command::new(&self.binary)
            .arg("version")
            .arg("--workspace")
            .arg(&self.workspace)
            .args(arguments)
            .output()
            .with_context(|| format!("start molt CLI {}", self.binary.display()))
    }

    fn call(&self, arguments: &[&str]) -> Result<Value> {
        let output = self.execute(arguments)?;
        if !output.status.success() {
            bail!("molt {}", String::from_utf8_lossy(&output.stderr).trim());
        }
        serde_json::from_slice(&output.stdout).context("parse molt CLI JSON response")
    }

    fn read_status(&self) -> Result<WorkspaceStatus> {
        WorkspaceStatus::parse(&self.call(&["status"])?)
    }

    fn history_records(&self) -> Result<Vec<RecoveryEvent>> {
        serde_json::from_value(self.call(&["history"])?).context("parse history events")
    }

    pub fn refresh(&mut self) -> Result<()> {
        let status = self.call(&["status"])?;
        let state_id = status["state_id"]
            .as_str()
            .context("status omitted state_id")?;
        let generation = status["generation"]
            .as_u64()
            .context("status omitted generation")?;
        self.state_id = state_id.to_owned();
        self.generation = generation;
        self.active_snapshot = status["active"].as_str().map(str::to_owned);
        self.last_event = status["last_event"].as_str().map(str::to_owned);
        self.main_head = status["branches"]
            .as_object()
            .and_then(|branches| branches.get("main"))
            .and_then(Value::as_str)
            .map(str::to_owned);
        self.pending_request = status["pending_request"].as_str().map(str::to_owned);
        let receipt = vec![
            format!("state_id {}", self.state_id),
            format!("generation {}", self.generation),
            format!(
                "active {}",
                self.active_snapshot.as_deref().unwrap_or("none")
            ),
            format!("main head {}", self.main_head.as_deref().unwrap_or("none")),
            format!(
                "last event {}",
                self.last_event.as_deref().unwrap_or("none")
            ),
            format!(
                "pending {}",
                self.pending_request.as_deref().unwrap_or("none")
            ),
        ];
        self.detail = receipt;
        Ok(())
    }

    pub fn recover(&mut self) -> Result<()> {
        let result = self.call(&["recover"])?;
        self.detail = vec![format!("recovery {}", result)];
        Ok(())
    }

    /// `b`: restore the file tree of the previous `main` snapshot with one
    /// forward commit. Never calls kernel `rollback`; every rejection leaves
    /// the workspace untouched.
    pub fn file_recovery(&mut self) -> FileRecoveryReport {
        let report = self.attempt_file_recovery();
        self.detail = report.lines.clone();
        self.last_file_recovery = Some(report.clone());
        report
    }

    fn baseline(&self) -> WorkspaceStatus {
        WorkspaceStatus {
            state_id: self.state_id.clone(),
            generation: self.generation,
            active: self.active_snapshot.clone(),
            last_event: self.last_event.clone(),
            main_head: self.main_head.clone(),
            pending_request: self.pending_request.clone(),
            branch_count: 0,
        }
    }

    fn attempt_file_recovery(&mut self) -> FileRecoveryReport {
        let baseline = self.baseline();
        if let Some(pending) = baseline.pending_request.as_deref() {
            return rejection(
                "pending",
                format!(
                    "another request is pending ({pending}); run u recover before restoring files"
                ),
            );
        }
        let status = match self.read_status() {
            Ok(status) => status,
            Err(error) => return rejection("read status", format!("{error:#}")),
        };
        if let Some(mismatch) = status.baseline_mismatch(&baseline) {
            return rejection(
                "baseline",
                format!(
                    "workspace changed since the last panel refresh ({mismatch}); press r before b"
                ),
            );
        }
        if let Some(pending) = status.pending_request.as_deref() {
            return rejection(
                "pending",
                format!(
                    "another request is pending ({pending}); run u recover before restoring files"
                ),
            );
        }
        let history = match self.history_records() {
            Ok(history) => history,
            Err(error) => return rejection("read history", format!("{error:#}")),
        };
        if status.generation == 0 {
            if status.branch_count != 0
                || status.active.is_some()
                || status.last_event.is_some()
                || !history.is_empty()
            {
                return rejection(
                    "initial",
                    "generation 0 is inconsistent with committed branches, pointers, or history",
                );
            }
            return rejection(
                "target",
                "workspace has no committed generation; nothing to restore",
            );
        }
        let Some(main_head) = status.main_head.clone() else {
            return rejection("target", "main branch has no head");
        };
        match status.active.as_deref() {
            Some(active) if active == main_head => {}
            Some(_) => {
                return rejection(
                    "target",
                    "main is not the active snapshot; activate main before restoring its predecessor",
                );
            }
            None => return rejection("target", "workspace has no active snapshot"),
        }
        let target = match self.recovery_target(&history, &status) {
            Ok(target) => target,
            Err(error) => return rejection("target", error),
        };

        let parent = match create_private_parent("recover") {
            Ok(parent) => parent,
            Err(error) => return rejection("prepare export", format!("{error:#}")),
        };
        if let Err(error) = require_outside_workspace(&parent, &self.workspace) {
            let mut report = rejection("prepare export", format!("{error:#}"));
            append_cleanup(&mut report, &parent);
            return report;
        }
        let parent_arg = parent.to_string_lossy().into_owned();
        let export = match self.call(&["export", &target.from, "--into", &parent_arg]) {
            Ok(export) => export,
            Err(error) => {
                let mut report = rejection("export", format!("{error:#}"));
                append_cleanup(&mut report, &parent);
                return report;
            }
        };
        if export["snapshot"].as_str() != Some(target.from.as_str()) {
            let mut report = rejection("export", "export did not echo the target snapshot");
            append_cleanup(&mut report, &parent);
            return report;
        }
        let Some(tree) = export["tree"].as_str().map(PathBuf::from) else {
            let mut report = rejection("export", "export omitted the tree path");
            append_cleanup(&mut report, &parent);
            return report;
        };
        if let Err(error) = validate_exported_tree(&parent, &tree, &target.files) {
            let mut report = rejection("export tree", format!("{error:#}"));
            append_cleanup(&mut report, &parent);
            return report;
        }
        let manifest = parent.join("manifest.json");
        if manifest.starts_with(&tree) {
            let mut report = rejection("manifest", "manifest must live outside the exported tree");
            append_cleanup(&mut report, &parent);
            return report;
        }
        let paths: Vec<&str> = target.files.keys().map(String::as_str).collect();
        let encoded = match serde_json::to_vec(&paths) {
            Ok(encoded) => encoded,
            Err(error) => {
                let mut report = rejection("manifest", format!("encode manifest: {error}"));
                append_cleanup(&mut report, &parent);
                return report;
            }
        };
        if let Err(error) = fs::write(&manifest, encoded) {
            let mut report = rejection("manifest", format!("write manifest: {error}"));
            append_cleanup(&mut report, &parent);
            return report;
        }

        let confirm = match self.read_status() {
            Ok(confirm) => confirm,
            Err(error) => {
                let mut report = rejection("confirm", format!("{error:#}"));
                append_cleanup(&mut report, &parent);
                return report;
            }
        };
        if let Some(mismatch) = confirm.baseline_mismatch(&baseline) {
            let mut report = rejection(
                "confirm",
                format!(
                    "workspace changed before the commit ({mismatch}); press r before retrying"
                ),
            );
            append_cleanup(&mut report, &parent);
            return report;
        }
        if let Some(pending) = confirm.pending_request.as_deref() {
            let mut report = rejection(
                "confirm",
                format!(
                    "another request became pending ({pending}); run u recover before retrying"
                ),
            );
            append_cleanup(&mut report, &parent);
            return report;
        }

        let generation_arg = status.generation.to_string();
        let tree_arg = tree.to_string_lossy().into_owned();
        let manifest_arg = manifest.to_string_lossy().into_owned();
        let arguments = [
            "commit-files",
            "--root",
            &tree_arg,
            "--manifest",
            &manifest_arg,
            "--branch",
            "main",
            "--expect-generation",
            &generation_arg,
            "--expect-head",
            &main_head,
            "--activate",
            "--reason",
            RECOVERY_REASON,
            "--reason-source",
            RECOVERY_SOURCE,
            "--include-request-id",
        ];
        let output = match self.execute(&arguments) {
            Ok(output) => output,
            Err(error) => {
                let mut report = unknown("start commit-files", format!("{error:#}"), None);
                append_cleanup(&mut report, &parent);
                return report;
            }
        };
        let mut report = classify_commit(&output, status.generation, &main_head, &self.workspace);
        append_cleanup(&mut report, &parent);
        if let Err(error) = self.refresh() {
            report
                .lines
                .push(format!("status refresh warning: {error:#}"));
        }
        report
    }

    fn recovery_target(
        &self,
        history: &[RecoveryEvent],
        status: &WorkspaceStatus,
    ) -> std::result::Result<RecoveryTarget, String> {
        validate_history(history, status)?;
        let index = history
            .iter()
            .rposition(|event| event.from != event.active_after)
            .ok_or_else(|| "no event changed the active snapshot".to_owned())?;
        if index > 0 {
            let expected = history[index - 1].active_after.as_deref();
            let actual = history[index].from.as_deref();
            if expected.is_none() || expected != actual {
                return Err(
                    "the selected event does not start from the snapshot the previous event left active"
                        .to_owned(),
                );
            }
        }
        for position in index + 1..history.len() {
            if history[position].from != history[position - 1].active_after {
                return Err("history does not chain from the selected event to the tail".to_owned());
            }
        }
        let event = &history[index];
        if event.action != "commit" && event.action != "merge" {
            return Err(format!(
                "the last change to the active snapshot was `{}`; only a main commit or merge can be restored",
                event.action
            ));
        }
        if event.branch.as_deref() != Some("main") {
            return Err("the last active change did not write main".to_owned());
        }
        let Some(to) = event.to.clone() else {
            return Err("the selected event omitted its target snapshot".to_owned());
        };
        let Some(from) = event.from.clone().filter(|snapshot| !snapshot.is_empty()) else {
            return Err("the previous tree has no snapshot parent (initial commit)".to_owned());
        };
        if event.active_after.as_deref() != Some(to.as_str()) {
            return Err(
                "the selected event did not leave its target as the active snapshot".to_owned(),
            );
        }
        if status.active.as_deref() != Some(to.as_str())
            || status.main_head.as_deref() != Some(to.as_str())
        {
            return Err("the selected event does not describe the current main head".to_owned());
        }
        if from == to {
            return Err("the previous and current snapshots are identical (echo)".to_owned());
        }
        let from_show = self
            .call(&["show", &from])
            .map_err(|error| format!("{error:#}"))?;
        let to_show = self
            .call(&["show", &to])
            .map_err(|error| format!("{error:#}"))?;
        let files = parse_managed_tree(&from_show, &from)?;
        parse_managed_tree(&to_show, &to)?;
        let parents = to_show["descriptor"]["parents"].as_array();
        let first_parent = parents
            .and_then(|parents| parents.first())
            .and_then(Value::as_str);
        if first_parent != Some(from.as_str()) {
            return Err(format!(
                "snapshot {to} does not list {from} as its first parent"
            ));
        }
        match event.action.as_str() {
            "commit" if event.previous_head.as_deref() != Some(from.as_str()) => {
                return Err(
                    "the last commit came from another active snapshot, not from the previous main tree"
                        .to_owned(),
                );
            }
            "merge" if event.parents.first().map(String::as_str) != Some(from.as_str()) => {
                return Err("the last merge did not start from the previous main tree".to_owned());
            }
            _ => {}
        }
        Ok(RecoveryTarget { from, to, files })
    }

    pub fn history(&mut self) -> Result<()> {
        let mut events: Vec<HistoryEvent> =
            serde_json::from_value(self.call(&["history"])?).context("parse history events")?;
        events.reverse();
        self.history_view.replace_events(events);
        self.render_history_page();
        Ok(())
    }

    pub fn begin_history_filter(&mut self) {
        if self.history_view.matches.is_none() {
            self.detail = vec!["load history with h before filtering".to_owned()];
            return;
        }
        self.history_view.draft = Some(String::new());
    }

    pub fn history_filter_draft(&self) -> Option<&str> {
        self.history_view.draft.as_deref()
    }

    pub fn history_filter(&self) -> &str {
        &self.history_view.filter
    }

    pub fn handle_history_filter_key(&mut self, key: KeyEvent) {
        let Some(draft) = &mut self.history_view.draft else {
            return;
        };
        match key.code {
            KeyCode::Esc => self.history_view.draft = None,
            KeyCode::Enter => {
                self.history_view.filter = draft.trim().to_owned();
                self.history_view.draft = None;
                self.history_view.apply_filter();
                self.render_history_page();
            }
            KeyCode::Backspace => {
                draft.pop();
            }
            KeyCode::Char(character)
                if !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                draft.push(character);
            }
            _ => {}
        }
    }

    pub fn clear_history_filter(&mut self) {
        if self.history_view.matches.is_some() {
            self.history_view.filter.clear();
            self.history_view.apply_filter();
            self.render_history_page();
        }
    }

    pub fn move_history_selection(&mut self, offset: isize) {
        let history = &mut self.history_view;
        if history.len() == 0 {
            return;
        }
        history.selected = history
            .selected
            .saturating_add_signed(offset)
            .min(history.len() - 1);
        self.render_history_page();
    }

    pub fn page_history(&mut self, offset: isize) {
        let history = &mut self.history_view;
        if history.len() == 0 || offset == 0 {
            return;
        }
        let current_page = history.selected / HISTORY_PAGE_SIZE;
        let last_page = (history.len() - 1) / HISTORY_PAGE_SIZE;
        let target_page = current_page.saturating_add_signed(offset).min(last_page);
        history.selected = target_page * HISTORY_PAGE_SIZE;
        self.render_history_page();
    }

    fn render_history_page(&mut self) {
        let history = &self.history_view;
        if history.len() == 0 {
            let message = if history.filter.is_empty() {
                "no events"
            } else {
                "history 0-0 of 0 · no matching events"
            };
            self.detail = vec![message.to_owned()];
            return;
        }
        let start = history.selected / HISTORY_PAGE_SIZE * HISTORY_PAGE_SIZE;
        let end = (start + HISTORY_PAGE_SIZE).min(history.len());
        self.detail = vec![format!(
            "history {}-{} of {}",
            start + 1,
            end,
            history.len()
        )];
        self.detail.extend((start..end).filter_map(|index| {
            let event = history.event_at(index)?;
            let marker = if index == history.selected { '>' } else { ' ' };
            Some(format!(
                "{marker} g{} {} {}",
                event.generation,
                event.action,
                display_event_id(&event.event)
            ))
        }));
    }

    fn selected_event(&self) -> Option<&str> {
        if self.history_view.matches.is_none() {
            return self.last_event.as_deref();
        }
        self.history_view
            .event_at(self.history_view.selected)
            .map(|event| event.event.as_str())
    }

    pub fn audit_last(&mut self) -> Result<()> {
        if self.history_view.matches.is_some() && self.history_view.len() == 0 {
            self.render_history_page();
            if !self.history_view.filter.is_empty() {
                bail!("no matching event to audit");
            }
        }
        let event = self.selected_event().context("no event to audit")?;
        let result = self.call(&["audit", event])?;
        self.detail = Self::audit_receipt(event, &result);
        Ok(())
    }

    fn audit_receipt(event: &str, result: &Value) -> Vec<String> {
        let evidence = &result["evidence"];
        let receipt = vec![
            format!("event {}", event),
            format!(
                "action {}",
                evidence["action"].as_str().unwrap_or("unknown")
            ),
            format!(
                "reason {}",
                evidence["reason"]["text"]
                    .as_str()
                    .unwrap_or("not recorded")
            ),
            format!(
                "digest {}",
                result["digest_status"].as_str().unwrap_or("unknown")
            ),
        ];
        let mut receipt = receipt;
        if let Some(files) = result["projection"]["after"]["files"].as_object() {
            for (path, metadata) in files {
                if let Some(sha256) = metadata["sha256"].as_str() {
                    receipt.push(format!("after file {path} {}", display_sha256(sha256)));
                }
            }
        }
        if receipt.len() == 4 {
            receipt.push("after files not recorded".to_owned());
        }
        receipt
    }

    pub fn begin_edit(&mut self) {
        if self.draft.is_empty() {
            self.draft = format!(
                "{{\"reply_format\":\"plain\",\"prefix\":\"[tui synthetic g{}] \"}}",
                self.generation + 1
            );
        }
        self.editing = true;
        self.detail = vec!["editing config.json · Enter inserts a line · Esc finishes".to_owned()];
    }

    pub fn handle_editor_key(&mut self, key: crossterm::event::KeyEvent) {
        use crossterm::event::KeyCode;

        match key.code {
            KeyCode::Esc => self.editing = false,
            KeyCode::Backspace => {
                self.draft.pop();
            }
            KeyCode::Enter => self.draft.push('\n'),
            KeyCode::Char(character) => self.draft.push(character),
            _ => {}
        }
        self.detail = vec![
            "editing config.json · Enter inserts a line · Esc finishes".to_owned(),
            format!("draft bytes {}", self.draft.len()),
            self.draft.clone(),
        ];
    }

    pub fn pin_reader(&mut self) -> Result<()> {
        let pin = self.call(&["pin"])?;
        let snapshot = pin["snapshot"]
            .as_str()
            .context("pin response omitted snapshot")?
            .to_owned();
        self.pinned_reader = Some(snapshot.clone());
        self.detail = vec![
            format!("reader ID to load {snapshot}"),
            format!(
                "displayed tree {}",
                self.reader_snapshot.as_deref().unwrap_or("none")
            ),
        ];
        Ok(())
    }

    pub fn read_pinned(&mut self) -> Result<()> {
        let snapshot = self
            .pinned_reader
            .clone()
            .context("pin a reader before reading")?;
        let parent = create_reader_parent()?;
        let parent_arg = parent.to_string_lossy().into_owned();
        let read_result = (|| {
            let exported = self.call(&["export", &snapshot, "--into", &parent_arg])?;
            let exported_snapshot = exported["snapshot"]
                .as_str()
                .context("export response omitted snapshot")?;
            if exported_snapshot != snapshot {
                bail!("exported snapshot {exported_snapshot} does not match requested {snapshot}");
            }
            let returned_tree = PathBuf::from(
                exported["tree"]
                    .as_str()
                    .context("export response omitted tree")?,
            );
            let returned_metadata = fs::symlink_metadata(&returned_tree)?;
            if returned_metadata.file_type().is_symlink() || !returned_metadata.is_dir() {
                bail!("exported tree is not a real directory");
            }
            let canonical_parent = fs::canonicalize(&parent)?;
            let tree = fs::canonicalize(returned_tree)?;
            if tree == canonical_parent || !tree.starts_with(&canonical_parent) {
                bail!("exported tree is outside the private reader directory");
            }
            let mut files = Vec::new();
            collect_reader_files(&tree, &tree, &mut files)?;
            files.sort();
            Ok::<_, anyhow::Error>((tree, files))
        })();
        let (tree, files) = match read_result {
            Ok(result) => result,
            Err(error) => {
                let _ = remove_reader_parent(&parent);
                return Err(error);
            }
        };
        let previous = self.reader_parent.replace(parent);
        self.reader_tree = Some(tree);
        self.reader_snapshot = Some(snapshot.clone());
        self.reader_files = files;
        self.reader_selected = 0;
        self.reader_preview = self.selected_reader_preview();
        self.detail = vec![
            format!("reader ID to load {snapshot}"),
            format!("reader snapshot displayed {snapshot}"),
            format!("managed files {}", self.reader_files.len()),
        ];
        if let Some(previous) = previous {
            if remove_reader_parent(&previous).is_err() {
                self.retired_reader_parents.push(previous);
                self.detail
                    .push("previous reader export cleanup deferred until exit".to_owned());
            }
        }
        Ok(())
    }

    pub fn move_reader_selection(&mut self, direction: i8) {
        if self.reader_files.is_empty() {
            return;
        }
        self.reader_selected = if direction < 0 {
            self.reader_selected.saturating_sub(1)
        } else {
            self.reader_selected
                .saturating_add(1)
                .min(self.reader_files.len() - 1)
        };
        self.reader_preview = self.selected_reader_preview();
    }

    fn selected_reader_preview(&self) -> String {
        let (Some(tree), Some(relative_path)) = (
            self.reader_tree.as_ref(),
            self.reader_files.get(self.reader_selected),
        ) else {
            return if self.reader_snapshot.is_some() {
                "(empty managed tree)".to_owned()
            } else {
                "(no tree loaded)".to_owned()
            };
        };
        read_reader_preview(&tree.join(relative_path))
    }

    pub fn commit_synthetic(&mut self) -> Result<()> {
        let head = self.call(&["status"])?["branches"]["main"]
            .as_str()
            .map(str::to_owned);
        let temp_dir = std::env::temp_dir().join(format!(
            "molt-tui-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos()
        ));
        std::fs::create_dir(&temp_dir)?;
        let config_path = temp_dir.join("config.json");
        let tool_path = temp_dir.join("tool.json");
        let manifest_path = temp_dir.join("manifest.json");
        if self.draft.is_empty() {
            self.draft = format!(
                r#"{{"reply_format":"plain","prefix":"[tui synthetic g{}] "}}"#,
                self.generation + 1
            );
        }
        std::fs::write(&config_path, self.draft.as_bytes())?;
        std::fs::write(&tool_path, br#"{"tool":"echo","output_format":"config"}"#)?;
        std::fs::write(&manifest_path, br#"["config.json","tool.json"]"#)?;
        let root = temp_dir.to_string_lossy().into_owned();
        let manifest = manifest_path.to_string_lossy().into_owned();
        let mut args = vec![
            "commit-files",
            "--root",
            &root,
            "--manifest",
            &manifest,
            "--reason",
            "Saved from the TUI synthetic Versions panel",
            "--reason-source",
            "local TUI operator",
            "--include-request-id",
            "--expect-generation",
        ];
        let generation = self.generation.to_string();
        args.push(&generation);
        if let Some(head) = head.as_deref() {
            args.extend(["--expect-head", head]);
        }
        args.push("--activate");
        let result = self.call(&args);
        let cleanup = std::fs::remove_dir_all(&temp_dir).context("remove temporary molt inputs");
        let result = result?;
        let summary = if result["changed"]
            .as_bool()
            .context("save omitted changed")?
        {
            let generation = result["current"]["generation"]
                .as_u64()
                .context("save omitted generation")?;
            let field = |name: &str| {
                result[name]
                    .as_str()
                    .map(str::to_owned)
                    .with_context(|| format!("save omitted {name}"))
            };
            self.last_save_receipt = Some(SaveReceipt {
                generation,
                snapshot: field("snapshot")?,
                event: field("event")?,
                request_id: field("request_id")?,
            });
            format!("saved generation {generation} · v view receipt")
        } else {
            "no changes to save".to_owned()
        };
        cleanup.with_context(|| summary.clone())?;
        self.refresh()
            .with_context(|| format!("{summary} · status refresh failed"))?;
        self.editing = false;
        self.detail.insert(0, summary);
        Ok(())
    }
}

fn display_optional(value: Option<&str>) -> &str {
    value.unwrap_or("none")
}

fn push_wrapped(lines: &mut Vec<String>, text: &str) {
    for line in text.lines() {
        lines.push(truncate_text(line, 400));
    }
}

fn truncate_text(text: &str, limit: usize) -> String {
    let mut characters = text.chars();
    let prefix: String = characters.by_ref().take(limit).collect();
    if characters.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}

fn rejection(stage: &str, detail: impl Into<String>) -> FileRecoveryReport {
    let mut lines = vec![
        "file recovery rejected · not submitted".to_owned(),
        format!("stage {stage}"),
    ];
    push_wrapped(&mut lines, &detail.into());
    FileRecoveryReport {
        outcome: FileRecoveryOutcome::Rejected,
        lines,
        request_id: None,
    }
}

fn unknown(
    stage: &str,
    detail: impl Into<String>,
    request_id: Option<String>,
) -> FileRecoveryReport {
    let mut lines = vec![
        "file recovery result unknown · do not retry".to_owned(),
        format!("stage {stage}"),
    ];
    push_wrapped(&mut lines, &detail.into());
    FileRecoveryReport {
        outcome: FileRecoveryOutcome::Unknown,
        lines,
        request_id,
    }
}

fn commit_report(
    outcome: FileRecoveryOutcome,
    headline: &str,
    diagnostics: Vec<String>,
    guidance: Option<String>,
    request_id: Option<String>,
) -> FileRecoveryReport {
    let mut lines = vec![headline.to_owned()];
    lines.extend(diagnostics);
    if let Some(guidance) = guidance {
        push_wrapped(&mut lines, &guidance);
    }
    FileRecoveryReport {
        outcome,
        lines,
        request_id,
    }
}

fn append_cleanup(report: &mut FileRecoveryReport, parent: &Path) {
    if let Err(error) = remove_reader_parent(parent) {
        report.lines.push(format!("cleanup warning: {error:#}"));
    }
}

fn describe_exit(status: &std::process::ExitStatus) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return format!("signal {signal}");
        }
    }
    match status.code() {
        Some(code) => format!("exit {code}"),
        None => "exit unknown".to_owned(),
    }
}

fn extract_request_id(stderr: &str) -> Option<String> {
    let (_, rest) = stderr.split_once("request ")?;
    let candidate: String = rest
        .chars()
        .take_while(|character| {
            !character.is_whitespace() && *character != ';' && *character != ','
        })
        .collect();
    if candidate.is_empty() {
        None
    } else {
        Some(candidate)
    }
}

fn is_stale_message(stderr: &str) -> bool {
    stderr.contains("stale")
        && (stderr.contains("generation") || stderr.contains("head") || stderr.contains("branch"))
}

fn classify_commit(
    output: &Output,
    generation: u64,
    head: &str,
    workspace: &Path,
) -> FileRecoveryReport {
    let exit = describe_exit(&output.status);
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    let mut diagnostics = vec!["stage commit-files".to_owned()];
    diagnostics.push(if stderr.is_empty() {
        exit
    } else {
        format!("{exit} · {stderr}")
    });
    match output.status.code() {
        Some(0) => match serde_json::from_slice::<Value>(&output.stdout) {
            Ok(receipt) => classify_receipt(&receipt, generation, head, diagnostics),
            Err(error) => {
                let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
                let mut report = commit_report(
                    FileRecoveryOutcome::Unknown,
                    "file recovery result unknown · do not retry",
                    diagnostics,
                    Some(
                        "the CLI exited 0 but its receipt is not valid JSON; run u recover before retrying"
                            .to_owned(),
                    ),
                    None,
                );
                push_wrapped(&mut report.lines, &format!("stdout {stdout}"));
                push_wrapped(&mut report.lines, &format!("parse error: {error}"));
                report
            }
        },
        Some(4) => {
            let request_id = extract_request_id(&stderr);
            if stderr.contains("needs recovery") {
                let guidance = match &request_id {
                    Some(id) => format!(
                        "this request needs recovery; query `molt version --workspace {} request-status {id}` (read-only) or run u; do not start over with a new request ID",
                        workspace.display()
                    ),
                    None => "this request needs recovery; run u; do not start over with a new request ID".to_owned(),
                };
                commit_report(
                    FileRecoveryOutcome::Unknown,
                    "file recovery result unknown · do not retry",
                    diagnostics,
                    Some(guidance),
                    request_id,
                )
            } else if stderr.contains("pending request") {
                let guidance = match &request_id {
                    Some(id) => format!(
                        "pending request {id} belongs to another action and was not submitted by b; run u to recover it before retrying"
                    ),
                    None => "another request is pending and was not submitted by b; run u to recover it before retrying".to_owned(),
                };
                commit_report(
                    FileRecoveryOutcome::Rejected,
                    "file recovery rejected · another request is pending",
                    diagnostics,
                    Some(guidance),
                    request_id,
                )
            } else {
                commit_report(
                    FileRecoveryOutcome::Unknown,
                    "file recovery result unknown · do not retry",
                    diagnostics,
                    Some("recovery is required but the message does not identify this request; run u".to_owned()),
                    request_id,
                )
            }
        }
        Some(3) => {
            if is_stale_message(&stderr) {
                commit_report(
                    FileRecoveryOutcome::Rejected,
                    "file recovery rejected · stale main",
                    diagnostics,
                    Some("the workspace changed before the write; press r to refresh, then try b again".to_owned()),
                    None,
                )
            } else {
                commit_report(
                    FileRecoveryOutcome::Unknown,
                    "file recovery result unknown · do not retry",
                    diagnostics,
                    Some("exit 3 without a clear stale generation or head is not classified; run u".to_owned()),
                    None,
                )
            }
        }
        Some(2) => commit_report(
            FileRecoveryOutcome::Rejected,
            "file recovery rejected · input or workspace refused",
            diagnostics,
            Some("the CLI refused the inputs before writing; fix the reported condition and try b again".to_owned()),
            None,
        ),
        _ => commit_report(
            FileRecoveryOutcome::Unknown,
            "file recovery result unknown · runtime failure · do not retry",
            diagnostics,
            Some("the CLI did not report a usable result; run u recover and do not retry with a new request ID".to_owned()),
            None,
        ),
    }
}

fn classify_receipt(
    receipt: &Value,
    generation: u64,
    head: &str,
    diagnostics: Vec<String>,
) -> FileRecoveryReport {
    match receipt["changed"].as_bool() {
        Some(true) => {
            let new_generation = receipt["current"]["generation"].as_u64();
            let snapshot = receipt["snapshot"]
                .as_str()
                .filter(|value| !value.is_empty());
            let event = receipt["event"].as_str().filter(|value| !value.is_empty());
            let request_id = receipt["request_id"]
                .as_str()
                .filter(|value| !value.is_empty());
            match (new_generation, snapshot, event, request_id) {
                (Some(new_generation), Some(snapshot), Some(event), Some(request_id))
                    if new_generation == generation + 1 =>
                {
                    let mut lines = diagnostics;
                    lines.push(format!("generation {new_generation}"));
                    lines.push(format!("snapshot {snapshot}"));
                    lines.push(format!("event {event}"));
                    lines.push(format!("request ID {request_id}"));
                    FileRecoveryReport {
                        outcome: FileRecoveryOutcome::Restored,
                        lines: {
                            let mut report = vec![format!(
                                "file recovery committed · generation {new_generation}"
                            )];
                            report.extend(lines);
                            report
                        },
                        request_id: Some(request_id.to_owned()),
                    }
                }
                _ => {
                    let mut report = commit_report(
                        FileRecoveryOutcome::Unknown,
                        "file recovery result unknown · do not retry",
                        diagnostics,
                        Some("the CLI exited 0 but the receipt did not confirm generation, snapshot, event, and request ID; run u recover before retrying".to_owned()),
                        None,
                    );
                    push_wrapped(&mut report.lines, &format!("receipt {receipt}"));
                    report
                }
            }
        }
        Some(false) => {
            let current = receipt["current"]["generation"].as_u64();
            let snapshot = receipt["snapshot"].as_str();
            if current == Some(generation) && snapshot == Some(head) && receipt["event"].is_null() {
                commit_report(
                    FileRecoveryOutcome::NoEffect,
                    "file recovery no effect · generation unchanged",
                    diagnostics,
                    Some(format!("main head {head} already has the target tree")),
                    None,
                )
            } else {
                let mut report = commit_report(
                    FileRecoveryOutcome::Unknown,
                    "file recovery result unknown · do not retry",
                    diagnostics,
                    Some("the CLI exited 0 but the no-effect receipt did not match the original generation and head; press r".to_owned()),
                    None,
                );
                push_wrapped(&mut report.lines, &format!("receipt {receipt}"));
                report
            }
        }
        _ => {
            let mut report = commit_report(
                FileRecoveryOutcome::Unknown,
                "file recovery result unknown · do not retry",
                diagnostics,
                Some("the CLI exited 0 but the receipt omitted changed; run u recover before retrying".to_owned()),
                None,
            );
            push_wrapped(&mut report.lines, &format!("receipt {receipt}"));
            report
        }
    }
}

fn validate_history(
    history: &[RecoveryEvent],
    status: &WorkspaceStatus,
) -> std::result::Result<(), String> {
    for (index, event) in history.iter().enumerate() {
        if index > 0 && event.generation <= history[index - 1].generation {
            return Err("history generations are not strictly increasing".to_owned());
        }
        let expected = match &event.base {
            None => 1,
            Some(base) => base
                .generation
                .checked_add(1)
                .ok_or_else(|| "history base generation overflowed".to_owned())?,
        };
        if event.generation != expected {
            return Err(format!(
                "event {} reports generation {} but its base implies {expected}",
                event.event, event.generation
            ));
        }
    }
    let Some(tail) = history.last() else {
        return Err("history is empty for a committed workspace".to_owned());
    };
    if tail.generation == 0 || tail.generation > status.generation {
        return Err(format!(
            "history tail generation {} is outside 1..={}",
            tail.generation, status.generation
        ));
    }
    if status.last_event.as_deref() != Some(tail.event.as_str()) {
        return Err("history tail is not the status last event".to_owned());
    }
    if tail.active_after != status.active {
        return Err("history tail does not end at the status active snapshot".to_owned());
    }
    if tail.branch.as_deref() == Some("main")
        && matches!(tail.action.as_str(), "commit" | "merge")
        && tail.to != status.main_head
    {
        return Err("history tail wrote main but its snapshot is not the main head".to_owned());
    }
    Ok(())
}

fn parse_managed_tree(
    show: &Value,
    snapshot: &str,
) -> std::result::Result<BTreeMap<String, ManagedFile>, String> {
    if show["snapshot"].as_str() != Some(snapshot) {
        return Err(format!("show did not echo snapshot {snapshot}"));
    }
    let files = show["files"].as_object().ok_or_else(|| {
        format!("snapshot {snapshot} has no managed files (echo or non-file snapshot)")
    })?;
    if files.len() > MAX_MANAGED_FILES {
        return Err(format!(
            "snapshot {snapshot} has more than {MAX_MANAGED_FILES} managed files"
        ));
    }
    let mut tree = BTreeMap::new();
    let mut total = 0u64;
    for (path, metadata) in files {
        if !valid_managed_path(path) {
            return Err(format!(
                "snapshot {snapshot} contains an invalid managed path: {path}"
            ));
        }
        let sha256 = metadata["sha256"]
            .as_str()
            .ok_or_else(|| format!("snapshot {snapshot} omitted the sha256 of {path}"))?;
        if !valid_sha256(sha256) {
            return Err(format!(
                "snapshot {snapshot} has an invalid sha256 for {path}"
            ));
        }
        let size = metadata["size"]
            .as_u64()
            .ok_or_else(|| format!("snapshot {snapshot} omitted the size of {path}"))?;
        if size > MAX_MANAGED_FILE_BYTES {
            return Err(format!(
                "{path} exceeds the {MAX_MANAGED_FILE_BYTES} byte file limit"
            ));
        }
        total = total.saturating_add(size);
        if total > MAX_MANAGED_TREE_BYTES {
            return Err(format!(
                "snapshot {snapshot} exceeds the {MAX_MANAGED_TREE_BYTES} byte tree limit"
            ));
        }
        tree.insert(
            path.clone(),
            ManagedFile {
                sha256: sha256.to_owned(),
                size,
            },
        );
    }
    for path in tree.keys() {
        let parts: Vec<&str> = path.split('/').collect();
        for boundary in 1..parts.len() {
            if tree.contains_key(&parts[..boundary].join("/")) {
                return Err(format!("{path} conflicts with a parent directory path"));
            }
        }
    }
    Ok(tree)
}

fn valid_managed_path(path: &str) -> bool {
    if path.is_empty() || path.len() > 240 {
        return false;
    }
    path.split('/').all(valid_managed_component)
}

fn valid_managed_component(component: &str) -> bool {
    if component.is_empty() || component.len() > 100 {
        return false;
    }
    if component == "." || component == ".." {
        return false;
    }
    if !component
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-'))
    {
        return false;
    }
    let lowered = component.to_ascii_lowercase();
    lowered != ".git" && lowered != ".env" && !lowered.starts_with(".env.")
}

fn valid_sha256(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .chars()
                .all(|character| matches!(character, '0'..='9' | 'a'..='f'))
    })
}

fn require_real_directory(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        bail!("{} is not a real directory", path.display());
    }
    Ok(())
}

#[cfg(unix)]
fn require_mode(path: &Path, expected: u32) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mode = fs::symlink_metadata(path)?.permissions().mode() & 0o777;
    if mode != expected {
        bail!(
            "{} has mode {mode:o}, expected {expected:o}",
            path.display()
        );
    }
    Ok(())
}

#[cfg(not(unix))]
fn require_mode(_path: &Path, _expected: u32) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn current_uid() -> Result<u32> {
    let status = fs::read_to_string("/proc/self/status").context("read /proc/self/status")?;
    let line = status
        .lines()
        .find(|line| line.starts_with("Uid:"))
        .context("/proc/self/status omitted Uid")?;
    line.split_whitespace()
        .nth(1)
        .context("parse /proc/self/status Uid")?
        .parse()
        .context("parse /proc/self/status uid")
}

fn require_private_parent(parent: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(parent)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        bail!("export parent is not a real directory");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if metadata.uid() != current_uid()? {
            bail!("export parent is not owned by the current user");
        }
        if metadata.permissions().mode() & 0o777 != 0o700 {
            bail!("export parent is not mode 0700");
        }
    }
    Ok(())
}

fn require_outside_workspace(path: &Path, workspace: &Path) -> Result<()> {
    let canonical_workspace =
        fs::canonicalize(workspace).context("resolve version workspace for export")?;
    let canonical_path = fs::canonicalize(path).context("resolve export parent")?;
    if canonical_path.starts_with(&canonical_workspace) {
        bail!("export parent is inside the version workspace");
    }
    Ok(())
}

fn walk_published_tree(root: &Path) -> Result<BTreeMap<String, u64>> {
    let mut files = BTreeMap::new();
    walk_tree_directory(root, root, &mut files)?;
    Ok(files)
}

fn walk_tree_directory(
    root: &Path,
    directory: &Path,
    files: &mut BTreeMap<String, u64>,
) -> Result<()> {
    require_mode(directory, 0o500)?;
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_symlink() {
            bail!("exported tree contains a symbolic link: {}", path.display());
        }
        if file_type.is_dir() {
            walk_tree_directory(root, &path, files)?;
        } else if file_type.is_file() {
            require_mode(&path, 0o400)?;
            let relative = path
                .strip_prefix(root)?
                .to_str()
                .context("exported path is not UTF-8")?
                .replace(std::path::MAIN_SEPARATOR, "/");
            if !valid_managed_path(&relative) {
                bail!("exported tree contains an invalid managed path: {relative}");
            }
            files.insert(relative, entry.metadata()?.len());
        } else {
            bail!(
                "exported tree contains a non-regular path: {}",
                path.display()
            );
        }
    }
    Ok(())
}

fn validate_exported_tree(
    parent: &Path,
    tree: &Path,
    expected: &BTreeMap<String, ManagedFile>,
) -> Result<()> {
    require_private_parent(parent)?;
    require_real_directory(tree)?;
    let slot = tree
        .parent()
        .context("exported tree has no slot directory")?;
    let name = slot
        .file_name()
        .and_then(|name| name.to_str())
        .context("exported slot has no UTF-8 name")?;
    if !name.starts_with("molt-tree-") || name.len() == "molt-tree-".len() {
        bail!("exported tree is not inside a molt-tree slot");
    }
    if tree.file_name() != Some(std::ffi::OsStr::new("project")) {
        bail!("exported tree is not a project directory");
    }
    require_real_directory(slot)?;
    require_mode(slot, 0o700)?;
    let canonical_parent = fs::canonicalize(parent)?;
    let canonical_slot = fs::canonicalize(slot)?;
    if canonical_slot.parent() != Some(canonical_parent.as_path()) {
        bail!("exported slot is outside the private parent");
    }
    if fs::canonicalize(tree)? != canonical_slot.join("project") {
        bail!("exported tree path is not canonical");
    }
    require_mode(tree, 0o500)?;
    let actual = walk_published_tree(tree)?;
    if actual.len() != expected.len() {
        bail!(
            "exported tree has {} files, expected {}",
            actual.len(),
            expected.len()
        );
    }
    for (path, metadata) in expected {
        match actual.get(path) {
            Some(size) if *size == metadata.size => {}
            Some(size) => bail!(
                "{path} has {size} bytes in the exported tree, expected {}",
                metadata.size
            ),
            None => bail!("exported tree is missing {path}"),
        }
    }
    Ok(())
}

fn display_event_id(event: &str) -> String {
    let mut characters = event.chars();
    let prefix: String = characters.by_ref().take(20).collect();
    if characters.next().is_some() {
        format!("{prefix}...")
    } else {
        prefix
    }
}

fn display_sha256(sha256: &str) -> String {
    let mut characters = sha256.chars();
    let prefix: String = characters.by_ref().take(23).collect();
    if characters.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}

fn create_private_parent(label: &str) -> Result<PathBuf> {
    let parent = std::env::temp_dir().join(format!(
        "molt-tui-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    let mut builder = DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&parent)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Err(error) = fs::set_permissions(&parent, fs::Permissions::from_mode(0o700)) {
            let _ = remove_reader_parent(&parent);
            return Err(error.into());
        }
    }
    require_private_parent(&parent)?;
    Ok(parent)
}

fn create_reader_parent() -> Result<PathBuf> {
    create_private_parent("reader")
}

fn collect_reader_files(root: &Path, directory: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let path = entry.path();
        if file_type.is_dir() {
            collect_reader_files(root, &path, files)?;
        } else if file_type.is_file() {
            files.push(path.strip_prefix(root)?.to_path_buf());
        } else {
            bail!("exported tree contains a non-regular managed path");
        }
    }
    Ok(())
}

fn read_reader_preview(path: &Path) -> String {
    let read_result = (|| {
        let file = fs::File::open(path)?;
        let mut bytes = Vec::with_capacity(READER_PREVIEW_BYTES + 1);
        file.take((READER_PREVIEW_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        Ok::<_, std::io::Error>(bytes)
    })();
    let mut bytes = match read_result {
        Ok(bytes) => bytes,
        Err(error) => return format!("(preview read failed: {error})"),
    };
    let truncated = bytes.len() > READER_PREVIEW_BYTES;
    bytes.truncate(READER_PREVIEW_BYTES);
    if truncated {
        if let Err(error) = std::str::from_utf8(&bytes) {
            if error.error_len().is_some() {
                return "(preview unavailable: file is not UTF-8 text)".to_owned();
            }
            bytes.truncate(error.valid_up_to());
        }
    }
    match String::from_utf8(bytes) {
        Ok(mut text) => {
            if truncated {
                text.push_str("\n[preview truncated at 4096 bytes]");
            }
            text
        }
        Err(_) => "(preview unavailable: file is not UTF-8 text)".to_owned(),
    }
}

fn make_reader_directories_writable(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        bail!("reader cleanup path is not an owned directory");
    }
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            make_reader_directories_writable(&entry.path())?;
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn remove_reader_parent(parent: &Path) -> Result<()> {
    if !parent.exists() {
        return Ok(());
    }
    make_reader_directories_writable(parent)?;
    fs::remove_dir_all(parent)?;
    Ok(())
}

impl Drop for VersionWorkspace {
    fn drop(&mut self) {
        if let Some(parent) = self.reader_parent.take() {
            let _ = remove_reader_parent(&parent);
        }
        for parent in self.retired_reader_parents.drain(..) {
            let _ = remove_reader_parent(&parent);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        display_event_id, display_sha256, HistoryEvent, VersionHistory, VersionWorkspace,
        READER_PREVIEW_BYTES,
    };
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use std::fs;
    use std::path::PathBuf;

    fn event(event: &str, generation: u64, action: &str) -> HistoryEvent {
        HistoryEvent {
            event: event.to_owned(),
            generation,
            action: action.to_owned(),
        }
    }

    fn workspace() -> VersionWorkspace {
        let mut history_view = VersionHistory::default();
        history_view.replace_events(vec![
            event("newest", 2, "commit"),
            event("older", 1, "commit"),
        ]);
        VersionWorkspace {
            binary: PathBuf::from("molt"),
            workspace: PathBuf::from("/tmp/synthetic"),
            state_id: String::new(),
            generation: 0,
            active_snapshot: None,
            last_event: Some("latest".to_owned()),
            main_head: None,
            history_view,
            pending_request: None,
            last_save_receipt: None,
            last_file_recovery: None,
            detail: vec![
                "> g2 commit newest".to_owned(),
                "  g1 commit older".to_owned(),
            ],
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        }
    }

    fn test_directory(label: &str) -> PathBuf {
        let directory = std::env::temp_dir().join(format!(
            "molt-tui-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        directory
    }

    #[test]
    fn reader_preview_is_bounded_and_explains_binary_and_read_errors() {
        let directory = test_directory("reader-preview");
        let text_path = directory.join("large.txt");
        fs::write(&text_path, "a".repeat(READER_PREVIEW_BYTES + 20)).unwrap();
        let preview = super::read_reader_preview(&text_path);
        assert!(preview.starts_with(&"a".repeat(READER_PREVIEW_BYTES)));
        assert!(preview.ends_with("[preview truncated at 4096 bytes]"));

        let binary_path = directory.join("binary.bin");
        fs::write(&binary_path, [0xff, 0xfe, 0x00]).unwrap();
        assert_eq!(
            super::read_reader_preview(&binary_path),
            "(preview unavailable: file is not UTF-8 text)"
        );
        assert!(super::read_reader_preview(&directory.join("missing.txt"))
            .starts_with("(preview read failed:"));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn reader_cleanup_removes_read_only_export_subdirectories() {
        let parent = test_directory("reader-cleanup");
        let tree = parent.join("molt-tree").join("project").join("nested");
        fs::create_dir_all(&tree).unwrap();
        fs::write(tree.join("file.txt"), "content").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(tree.join("file.txt"), fs::Permissions::from_mode(0o400)).unwrap();
            fs::set_permissions(&tree, fs::Permissions::from_mode(0o500)).unwrap();
            fs::set_permissions(tree.parent().unwrap(), fs::Permissions::from_mode(0o500)).unwrap();
            fs::set_permissions(
                tree.parent().unwrap().parent().unwrap(),
                fs::Permissions::from_mode(0o500),
            )
            .unwrap();
        }

        super::remove_reader_parent(&parent).unwrap();

        assert!(!parent.exists());
    }

    #[test]
    fn history_selection_stays_in_bounds_and_targets_selected_event() {
        let mut workspace = workspace();
        workspace.move_history_selection(1);
        assert_eq!(workspace.selected_event(), Some("older"));
        assert_eq!(workspace.detail[0], "history 1-2 of 2");
        assert_eq!(workspace.detail[1], "  g2 commit newest");
        assert_eq!(workspace.detail[2], "> g1 commit older");

        workspace.move_history_selection(1);
        assert_eq!(workspace.selected_event(), Some("older"));
        workspace.move_history_selection(-2);
        assert_eq!(workspace.selected_event(), Some("newest"));
        workspace.move_history_selection(-1);
        assert_eq!(workspace.selected_event(), Some("newest"));
    }

    #[test]
    fn only_unloaded_history_falls_back_to_latest_event() {
        let mut workspace = workspace();
        workspace.history_view = VersionHistory::default();
        assert_eq!(workspace.selected_event(), Some("latest"));
        workspace.move_history_selection(1);
        assert_eq!(workspace.selected_event(), Some("latest"));

        workspace.begin_history_filter();
        assert!(workspace.history_filter_draft().is_none());
        assert_eq!(workspace.detail, ["load history with h before filtering"]);

        workspace.history_view.replace_events(Vec::new());
        assert_eq!(workspace.selected_event(), None);
        assert_eq!(
            workspace.audit_last().unwrap_err().to_string(),
            "no event to audit"
        );
        assert_eq!(workspace.detail, ["no events"]);
    }

    #[test]
    fn history_pages_include_older_events_and_clamp_navigation() {
        let mut workspace = workspace();
        workspace.history_view.replace_events(
            (0..19)
                .map(|index| event(&format!("event-{index}"), 19 - index, "commit"))
                .collect(),
        );
        workspace.render_history_page();

        assert_eq!(workspace.detail[0], "history 1-8 of 19");
        assert_eq!(workspace.detail.len(), 9);
        assert_eq!(workspace.selected_event(), Some("event-0"));

        workspace.page_history(1);
        assert_eq!(workspace.selected_event(), Some("event-8"));
        assert_eq!(workspace.detail[0], "history 9-16 of 19");
        workspace.page_history(1);
        assert_eq!(workspace.selected_event(), Some("event-16"));
        assert_eq!(workspace.detail[0], "history 17-19 of 19");
        workspace.page_history(1);
        assert_eq!(workspace.selected_event(), Some("event-16"));

        workspace.page_history(-1);
        assert_eq!(workspace.selected_event(), Some("event-8"));
        workspace.move_history_selection(1);
        assert_eq!(workspace.selected_event(), Some("event-9"));
        assert!(workspace.detail[2].starts_with("> g10 commit event-9"));
    }

    fn apply_filter(workspace: &mut VersionWorkspace, filter: &str) {
        workspace.begin_history_filter();
        for character in filter.chars() {
            workspace.handle_history_filter_key(KeyEvent::new(
                KeyCode::Char(character),
                KeyModifiers::NONE,
            ));
        }
        workspace.handle_history_filter_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    }

    #[test]
    fn history_filter_matches_structured_fields_and_keeps_full_event_id() {
        let mut workspace = workspace();
        let older = "sha256:0123456789abcdefabcdef";
        workspace.history_view.replace_events(vec![
            event("sha256:abcde", 19, "commit"),
            event(older, 9, "rollback"),
        ]);

        for query in [older, "CDEFABCDEF", "G9", "BACK"] {
            apply_filter(&mut workspace, query);
            assert_eq!(workspace.detail[0], "history 1-1 of 1");
            assert_eq!(workspace.detail[1], "> g9 rollback sha256:0123456789abc...");
            assert_eq!(workspace.selected_event(), Some(older));
        }
    }

    #[test]
    fn history_filter_paginates_matches_and_blocks_audit_when_empty() {
        let mut workspace = workspace();
        workspace.history_view.replace_events(
            (1..=20)
                .rev()
                .map(|generation| {
                    let action = if generation % 2 == 0 {
                        "commit"
                    } else {
                        "rollback"
                    };
                    event(&format!("id-{generation}"), generation, action)
                })
                .collect(),
        );
        apply_filter(&mut workspace, "commit");
        assert_eq!(workspace.detail[0], "history 1-8 of 10");
        assert_eq!(workspace.selected_event(), Some("id-20"));
        workspace.page_history(1);
        assert_eq!(workspace.detail[0], "history 9-10 of 10");
        assert_eq!(workspace.selected_event(), Some("id-4"));
        workspace.move_history_selection(2);
        assert_eq!(workspace.selected_event(), Some("id-2"));

        apply_filter(&mut workspace, "missing");
        assert_eq!(workspace.detail, ["history 0-0 of 0 · no matching events"]);
        assert_eq!(workspace.selected_event(), None);
        assert_eq!(
            workspace.audit_last().unwrap_err().to_string(),
            "no matching event to audit"
        );

        workspace.clear_history_filter();
        assert_eq!(workspace.detail[0], "history 1-8 of 20");
        assert_eq!(workspace.selected_event(), Some("id-20"));
    }

    #[test]
    fn audit_receipt_displays_after_file_hashes_without_mislabeling_event_id() {
        let result = serde_json::json!({
            "evidence": {
                "action": "commit",
                "reason": {"text": "synthetic reason"}
            },
            "projection": {
                "after": {
                    "files": {
                        "config.json": {"sha256": "sha256:config-hash", "size": 4}
                    }
                }
            },
            "digest_status": "pending"
        });

        let receipt = VersionWorkspace::audit_receipt("sha256:event-id", &result);

        assert!(receipt.iter().any(|line| line == "event sha256:event-id"));
        assert!(receipt
            .iter()
            .any(|line| line == "after file config.json sha256:config-hash"));
        assert!(!receipt
            .iter()
            .any(|line| line == "evidence sha256:event-id"));
    }

    #[test]
    fn history_line_shortens_long_event_ids_for_narrow_panels() {
        assert_eq!(
            display_event_id("sha256:0123456789abcdef0123456789abcdef"),
            "sha256:0123456789abc..."
        );
        assert_eq!(display_event_id("event-1"), "event-1");
    }

    #[test]
    fn sha256_display_uses_an_ellipsis_for_a_short_preview() {
        assert_eq!(
            display_sha256("sha256:0123456789abcdef0123456789abcdef"),
            "sha256:0123456789abcdef…"
        );
        assert_eq!(display_sha256("sha256:short"), "sha256:short");
    }

    #[test]
    fn managed_paths_follow_the_cli_file_rules() {
        for valid in ["config.json", "nested/tool.json", "a.b-c_d"] {
            assert!(super::valid_managed_path(valid), "{valid}");
        }
        for invalid in [
            "",
            "/absolute",
            "../escape",
            "nested/../escape",
            "./same",
            "nested//file",
            ".git/config",
            ".ENV",
            ".env.local",
            "nested/.git/objects",
            "space name",
            "nested/trailing/",
        ] {
            assert!(!super::valid_managed_path(invalid), "{invalid}");
        }
        assert!(super::valid_sha256(&format!("sha256:{}", "a".repeat(64))));
        assert!(!super::valid_sha256(&format!("sha256:{}", "A".repeat(64))));
        assert!(!super::valid_sha256("sha256:abc"));
    }
}
