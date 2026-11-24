//! Calls the existing `molt version` CLI for an explicitly selected workspace.

use std::fs::{self, DirBuilder};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde::Deserialize;
use serde_json::Value;

const HISTORY_PAGE_SIZE: usize = 8;
const READER_PREVIEW_BYTES: usize = 4096;

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

#[derive(Debug)]
pub struct VersionWorkspace {
    pub binary: PathBuf,
    pub workspace: PathBuf,
    pub state_id: String,
    pub generation: u64,
    pub active_snapshot: Option<String>,
    pub last_event: Option<String>,
    pub history_view: VersionHistory,
    pub pending_request: Option<String>,
    pub last_save_receipt: Option<SaveReceipt>,
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
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
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

    fn call(&self, arguments: &[&str]) -> Result<Value> {
        let output = Command::new(&self.binary)
            .arg("version")
            .arg("--workspace")
            .arg(&self.workspace)
            .args(arguments)
            .output()
            .with_context(|| format!("start molt CLI {}", self.binary.display()))?;
        if !output.status.success() {
            bail!("molt {}", String::from_utf8_lossy(&output.stderr).trim());
        }
        serde_json::from_slice(&output.stdout).context("parse molt CLI JSON response")
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
        self.pending_request = status["pending_request"].as_str().map(str::to_owned);
        let receipt = vec![
            format!("state_id {}", self.state_id),
            format!("generation {}", self.generation),
            format!(
                "active {}",
                self.active_snapshot.as_deref().unwrap_or("none")
            ),
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

    pub fn rollback_last(&mut self) -> Result<()> {
        let history = self.call(&["history"])?;
        let events = history
            .as_array()
            .context("history response is not an array")?;
        let snapshot = events
            .last()
            .and_then(|event| event["from"].as_str())
            .context("no previous snapshot to roll back to")?;
        let snapshot = snapshot.to_owned();
        let generation = self.generation.to_string();
        let result = self.call(&["rollback", &snapshot, "--expect-generation", &generation])?;
        let receipt = vec![
            format!(
                "rollback target {}",
                result["snapshot"].as_str().unwrap_or("unknown")
            ),
            "local active version changed".to_owned(),
        ];
        self.refresh().map(|()| self.detail.extend(receipt))
    }
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

fn create_reader_parent() -> Result<PathBuf> {
    let parent = std::env::temp_dir().join(format!(
        "molt-tui-reader-{}-{}",
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
    Ok(parent)
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
            history_view,
            pending_request: None,
            last_save_receipt: None,
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
}
