//! Tree view: fork-aware session history, node detail, rollback modal.

use std::collections::BTreeSet;

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Modifier,
    text::Line,
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
    Frame,
};

use crate::app::{styled_line, wrap_text, App, StatusLevel};
use crate::mock::{Importance, NodeKind, SessionKind};

/// One visible tree line. Session headers have `node == None`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TreeRow {
    pub session: usize,
    pub node: Option<usize>,
}

#[derive(Debug, Clone)]
pub struct RollbackState {
    pub session: usize,
    pub target: usize,
    pub cursor: usize,
    pub session_on: bool,
    pub config_on: bool,
    pub files_on: bool,
    pub files_available: bool,
    pub files: u32,
    pub checkpoint_id: Option<String>,
    pub backup_ts: Option<String>,
    pub compaction_warning: bool,
    /// `false` while picking targets, `true` after the first Enter staged a
    /// file-restoring rollback. Only the second Enter (or `y`) applies it.
    /// Session/config-only rollbacks apply on the first Enter.
    pub confirm_stage: bool,
}

impl RollbackState {
    pub fn backup_path(&self, session_id: &str) -> String {
        match &self.backup_ts {
            Some(ts) => format!("~/.local/share/molt/agent/backups/{session_id}/{ts}/"),
            None => format!("~/.local/share/molt/agent/backups/{session_id}/<none>/"),
        }
    }

    pub const fn field(&self) -> usize {
        self.cursor % 3
    }
}

#[derive(Debug, Clone)]
pub struct TreeState {
    pub selected: usize,
    pub expanded: BTreeSet<String>,
    pub marks: BTreeSet<(String, String)>,
    pub pinned: BTreeSet<(String, String)>,
    pub query: String,
    pub search_input: bool,
    pub rollback: Option<RollbackState>,
}

impl TreeState {
    pub fn new(mock: &crate::mock::MockData) -> Self {
        let expanded = mock
            .sessions
            .iter()
            .map(|session| session.id.clone())
            .collect();
        let selected = usize::from(
            mock.sessions
                .first()
                .is_some_and(|session| !session.nodes.is_empty()),
        );
        Self {
            selected,
            expanded,
            marks: BTreeSet::new(),
            pinned: BTreeSet::new(),
            query: String::new(),
            search_input: false,
            rollback: None,
        }
    }
}

impl App {
    pub(crate) fn render_managed_tree(&self, frame: &mut Frame, area: Rect) {
        let Some(workspace) = self.version_workspace.as_ref() else {
            return;
        };
        let snapshot = workspace
            .reader_snapshot
            .as_deref()
            .unwrap_or("none loaded");
        let outer = Block::default()
            .title(format!(" TREE · snapshot {snapshot} "))
            .borders(Borders::ALL)
            .border_style(self.theme.border())
            .style(self.theme.base());
        let inner = outer.inner(area);
        frame.render_widget(outer, area);
        if inner.width < 4 || inner.height == 0 {
            return;
        }
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Min(0)])
            .split(inner);
        frame.render_widget(
            Paragraph::new(styled_line(
                "j/k or ↑/↓ browse · PgUp/PgDn · Ctrl+V Versions",
                inner.width,
                self.theme.muted(),
            )),
            rows[0],
        );
        let columns = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(46), Constraint::Percentage(54)])
            .split(rows[1]);
        let selected = workspace
            .reader_selected
            .min(workspace.reader_files.len().saturating_sub(1));
        let file_lines = if workspace.reader_files.is_empty() {
            vec![styled_line(
                "(empty managed tree)",
                columns[0].width,
                self.theme.muted(),
            )]
        } else {
            workspace
                .reader_files
                .iter()
                .enumerate()
                .map(|(index, path)| {
                    let marker = if index == selected { "> " } else { "  " };
                    let line = format!("{marker}{}", path.display());
                    let style = if index == selected {
                        self.theme.accent().add_modifier(Modifier::BOLD)
                    } else {
                        self.theme.text()
                    };
                    styled_line(line, columns[0].width, style)
                })
                .collect()
        };
        let list_height = usize::from(columns[0].height.saturating_sub(2)).max(1);
        let offset = selected.saturating_sub(list_height.saturating_sub(1));
        frame.render_widget(
            Paragraph::new(file_lines)
                .scroll((offset.min(u16::MAX as usize) as u16, 0))
                .block(
                    Block::default()
                        .title(format!(" FILES {} ", workspace.reader_files.len()))
                        .borders(Borders::ALL)
                        .border_style(self.theme.border()),
                ),
            columns[0],
        );
        let selected_path = workspace
            .reader_files
            .get(selected)
            .map(|path| path.display().to_string())
            .unwrap_or_else(|| "preview".to_owned());
        frame.render_widget(
            Paragraph::new(workspace.reader_preview.as_str())
                .wrap(Wrap { trim: false })
                .block(
                    Block::default()
                        .title(format!(" {selected_path} "))
                        .borders(Borders::ALL)
                        .border_style(self.theme.border()),
                ),
            columns[1],
        );
    }

    /// Flatten the three sessions into visible rows, honouring folds and the
    /// mock search filter.
    pub fn tree_rows(&self) -> Vec<TreeRow> {
        let query = self.tree.query.to_lowercase();
        let mut rows = Vec::new();
        for (session_index, session) in self.mock.sessions.iter().enumerate() {
            rows.push(TreeRow {
                session: session_index,
                node: None,
            });
            if !self.tree.expanded.contains(&session.id) {
                continue;
            }
            let session_hit = !query.is_empty() && session.id.to_lowercase().contains(&query);
            for (node_index, node) in session.nodes.iter().enumerate() {
                if !query.is_empty()
                    && !session_hit
                    && !node.id.to_lowercase().contains(&query)
                    && !node.kind.label().contains(&query)
                    && !node.summary.to_lowercase().contains(&query)
                {
                    continue;
                }
                rows.push(TreeRow {
                    session: session_index,
                    node: Some(node_index),
                });
            }
        }
        rows
    }

    pub fn selected_tree_row(&self) -> Option<TreeRow> {
        let rows = self.tree_rows();
        rows.get(self.tree.selected.min(rows.len().saturating_sub(1)))
            .copied()
    }

    /// Select a node by index; used by `--selftest` and tests.
    pub fn select_tree_node(&mut self, session: usize, node: usize) {
        let rows = self.tree_rows();
        if let Some(position) = rows
            .iter()
            .position(|row| row.session == session && row.node == Some(node))
        {
            self.tree.selected = position;
        }
    }

    fn clamp_tree_selection(&mut self) {
        let count = self.tree_rows().len();
        self.tree.selected = self.tree.selected.min(count.saturating_sub(1));
    }

    fn select_session_header(&mut self, session: usize) {
        let rows = self.tree_rows();
        if let Some(position) = rows
            .iter()
            .position(|row| row.session == session && row.node.is_none())
        {
            self.tree.selected = position;
        }
    }

    fn node_key(&self, row: TreeRow) -> Option<(String, String)> {
        let session = self.mock.sessions.get(row.session)?;
        let node = session.nodes.get(row.node?)?;
        Some((session.id.clone(), node.id.clone()))
    }

    pub(crate) fn handle_tree_key(&mut self, key: KeyEvent) -> bool {
        if self.tree.rollback.is_some() {
            return self.handle_rollback_key(key);
        }
        if self.tree.search_input {
            return self.handle_tree_search_key(key);
        }
        match key.code {
            KeyCode::Char('j') | KeyCode::Down => {
                let count = self.tree_rows().len();
                self.tree.selected = (self.tree.selected + 1).min(count.saturating_sub(1));
                true
            }
            KeyCode::Char('k') | KeyCode::Up => {
                self.tree.selected = self.tree.selected.saturating_sub(1);
                true
            }
            KeyCode::Char('h') | KeyCode::Left => {
                self.collapse_tree_row();
                true
            }
            KeyCode::Char('l') | KeyCode::Right => {
                self.expand_tree_row();
                true
            }
            KeyCode::Char('f') => {
                self.fork_from_selection();
                true
            }
            KeyCode::Char('r') => {
                self.open_rollback();
                true
            }
            KeyCode::Char('p') => {
                self.toggle_pin();
                true
            }
            KeyCode::Char(' ') => {
                self.toggle_mark();
                true
            }
            KeyCode::Char('/') => {
                self.tree.search_input = true;
                self.tree.query.clear();
                self.clamp_tree_selection();
                true
            }
            _ => false,
        }
    }

    fn handle_tree_search_key(&mut self, key: KeyEvent) -> bool {
        match key.code {
            KeyCode::Esc => {
                self.tree.search_input = false;
                self.tree.query.clear();
                self.clamp_tree_selection();
            }
            KeyCode::Enter => {
                self.tree.search_input = false;
            }
            KeyCode::Backspace => {
                self.tree.query.pop();
                self.clamp_tree_selection();
            }
            KeyCode::Char(character) => {
                self.tree.query.push(character);
                self.clamp_tree_selection();
            }
            _ => {}
        }
        true
    }

    fn collapse_tree_row(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(row.session) else {
            return;
        };
        self.tree.expanded.remove(&session.id);
        self.select_session_header(row.session);
    }

    fn expand_tree_row(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(row.session) else {
            return;
        };
        self.tree.expanded.insert(session.id.clone());
    }

    fn fork_from_selection(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(row.session) else {
            return;
        };
        let node_index = row.node.unwrap_or_else(|| session.leaf_index());
        let Some(node) = session.nodes.get(node_index) else {
            return;
        };
        let parent_id = session.id.clone();
        let node_id = node.id.clone();
        let summary = node.summary.clone();
        let model = node.model.clone();
        let config = self.mock.active_config.clone();

        let base = format!("fork/{parent_id}@{node_id}");
        let mut id = base.clone();
        let mut suffix = 2;
        while self.mock.session(&id).is_some() {
            id = format!("{base}-{suffix}");
            suffix += 1;
        }
        let fork_node = crate::mock::Node {
            id: "n1".to_owned(),
            kind: NodeKind::Fork,
            summary: format!("fork from {parent_id} @ {node_id} · {summary}"),
            importance: Importance::Normal,
            role: "system".to_owned(),
            time: "now".to_owned(),
            model,
            tokens: 0,
            checkpoint: false,
            checkpoint_files: 0,
            backup_ts: None,
            config: config.clone(),
            parent_id: None,
            origin_node_id: Some(node_id.clone()),
        };
        let prompt_node = crate::mock::Node {
            id: "n2".to_owned(),
            kind: NodeKind::User,
            summary: "forked · ready for a new prompt".to_owned(),
            importance: Importance::Normal,
            role: "user".to_owned(),
            time: "now".to_owned(),
            model: config.clone(),
            tokens: 0,
            checkpoint: false,
            checkpoint_files: 0,
            backup_ts: None,
            config,
            parent_id: Some("n1".to_owned()),
            origin_node_id: None,
        };
        let new_session = crate::mock::Session {
            id: id.clone(),
            kind: SessionKind::Fork,
            parent: Some(parent_id.clone()),
            leaf: "n2".to_owned(),
            nodes: vec![fork_node, prompt_node],
        };
        self.mock.sessions.push(new_session);
        self.tree.expanded.insert(id.clone());
        let session_index = self.mock.sessions.len() - 1;
        self.select_session_header(session_index);
        self.set_status(
            StatusLevel::Success,
            format!("forked {id} from {parent_id} @ {node_id} (mock)"),
        );
    }

    fn toggle_pin(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(key) = self.node_key(row) else {
            self.set_status(
                StatusLevel::Warning,
                "pin needs a node, not a session header",
            );
            return;
        };
        let label = key.1.clone();
        if self.tree.pinned.remove(&key) {
            self.set_status(StatusLevel::Info, format!("unpinned {label}"));
        } else {
            self.tree.pinned.insert(key);
            self.set_status(StatusLevel::Info, format!("pinned {label}"));
        }
    }

    fn toggle_mark(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(key) = self.node_key(row) else {
            self.set_status(
                StatusLevel::Warning,
                "mark needs a node, not a session header",
            );
            return;
        };
        let label = key.1.clone();
        if self.tree.marks.remove(&key) {
            self.set_status(
                StatusLevel::Info,
                format!("unmarked {label} · {} marked", self.tree.marks.len()),
            );
        } else {
            self.tree.marks.insert(key);
            self.set_status(
                StatusLevel::Info,
                format!("marked {label} · {} marked", self.tree.marks.len()),
            );
        }
    }

    fn open_rollback(&mut self) {
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(row.session) else {
            return;
        };
        let target = row.node.unwrap_or_else(|| session.leaf_index());
        if session.nodes.get(target).is_none() {
            return;
        }
        let checkpoint = session.checkpoint_at_or_before(target);
        self.tree.rollback = Some(RollbackState {
            session: row.session,
            target,
            cursor: 0,
            session_on: true,
            config_on: true,
            files_on: false,
            files_available: checkpoint.is_some(),
            files: checkpoint.map_or(0, |node| node.checkpoint_files),
            checkpoint_id: checkpoint.map(|node| node.id.clone()),
            backup_ts: checkpoint.and_then(|node| node.backup_ts.clone()),
            compaction_warning: session.compaction_after(target),
            confirm_stage: false,
        });
    }

    fn handle_rollback_key(&mut self, key: KeyEvent) -> bool {
        if self
            .tree
            .rollback
            .as_ref()
            .is_some_and(|state| state.confirm_stage)
        {
            match key.code {
                KeyCode::Enter | KeyCode::Char('y') => self.apply_rollback(),
                KeyCode::Esc => {
                    if let Some(state) = self.tree.rollback.as_mut() {
                        state.confirm_stage = false;
                    }
                    self.set_status(StatusLevel::Info, "rollback confirmation dismissed");
                }
                _ => {}
            }
            return true;
        }
        let Some(state) = self.tree.rollback.as_mut() else {
            return true;
        };
        let mut apply = false;
        match key.code {
            KeyCode::Esc => {
                self.tree.rollback = None;
                self.set_status(StatusLevel::Info, "rollback cancelled");
            }
            KeyCode::Enter => {
                if !state.session_on && !state.config_on && !state.files_on {
                    self.set_status(StatusLevel::Warning, "select at least one target");
                } else if state.files_on {
                    // File restore is destructive: require a second Enter.
                    state.confirm_stage = true;
                    self.set_status(
                        StatusLevel::Warning,
                        "rollback staged · press Enter/y again to apply",
                    );
                } else {
                    // Session/config-only rollback has no file side effects, so
                    // one Enter is enough.
                    apply = true;
                }
            }
            KeyCode::Char('j') | KeyCode::Down => {
                state.cursor = (state.cursor + 1) % 3;
            }
            KeyCode::Char('k') | KeyCode::Up => {
                state.cursor = (state.cursor + 2) % 3;
            }
            KeyCode::Char(' ') => match state.field() {
                0 => state.session_on = !state.session_on,
                1 => state.config_on = !state.config_on,
                _ => {
                    if state.files_available {
                        state.files_on = !state.files_on;
                    } else {
                        self.set_status(
                            StatusLevel::Warning,
                            "cannot_restore_files · no checkpoint at or before this node",
                        );
                    }
                }
            },
            _ => {}
        }
        if apply {
            self.apply_rollback();
        }
        true
    }

    fn apply_rollback(&mut self) {
        let Some(state) = self.tree.rollback.take() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(state.session) else {
            return;
        };
        let Some(node) = session.nodes.get(state.target) else {
            return;
        };
        let target_id = node.id.clone();
        let target_config = node.config.clone();
        let files = if state.files_on { state.files } else { 0 };
        if state.session_on {
            if let Some(session) = self.mock.sessions.get_mut(state.session) {
                session.leaf = target_id.clone();
            }
        }
        let mut level = StatusLevel::Success;
        let mut missing_config = false;
        if state.config_on {
            if self.mock.config(&target_config).is_some() {
                self.mock.active_config = target_config.clone();
            } else {
                missing_config = true;
            }
        }
        let mut message = format!("simulated rollback (mock) (files={files})");
        if missing_config {
            // Never report a silent success when the target config is absent
            // from the mock state.
            message.push_str(&format!(" · config not found (mock): {target_config}"));
            level = StatusLevel::Warning;
        }
        if state.compaction_warning {
            message.push_str(" · summary only");
        }
        self.set_status(level, message);
    }

    pub(crate) fn render_tree(&self, frame: &mut Frame, area: Rect) {
        if area.width < 44 {
            self.render_tree_list(frame, area);
            return;
        }
        let columns = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(58), Constraint::Percentage(42)])
            .split(area);
        self.render_tree_list(frame, columns[0]);
        let block = Block::default()
            .borders(Borders::LEFT)
            .border_style(self.theme.border());
        let inner = block.inner(columns[1]);
        frame.render_widget(block, columns[1]);
        self.render_tree_detail(frame, inner);
    }

    fn render_tree_list(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let search_visible = self.tree.search_input || !self.tree.query.is_empty();
        let header_height = 1 + u16::from(search_visible);
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(header_height.min(area.height)),
                Constraint::Min(0),
            ])
            .split(area);
        let header = format!(
            "TREE · {} marked · {} pinned",
            self.tree.marks.len(),
            self.tree.pinned.len()
        );
        frame.render_widget(
            Paragraph::new(styled_line(header, area.width, self.theme.muted())),
            rows[0],
        );
        let mut list_area = rows[1];
        if search_visible && area.height > 1 {
            let search_area = Rect::new(area.x, area.y + 1, area.width, 1);
            let cursor = if self.tree.search_input { "▌" } else { "" };
            frame.render_widget(
                Paragraph::new(styled_line(
                    format!("search: {}{cursor}", self.tree.query),
                    area.width,
                    self.theme.accent(),
                )),
                search_area,
            );
            list_area = Rect::new(area.x, area.y + 2, area.width, area.height - 2);
        }
        if list_area.width == 0 || list_area.height == 0 {
            return;
        }
        let tree_rows = self.tree_rows();
        let selected = self.tree.selected.min(tree_rows.len().saturating_sub(1));
        let height = usize::from(list_area.height);
        let offset = selected.saturating_sub(height.saturating_sub(1));
        let lines = tree_rows
            .iter()
            .enumerate()
            .map(|(index, row)| self.tree_row_line(*row, index == selected, list_area.width))
            .collect::<Vec<_>>();
        frame.render_widget(
            Paragraph::new(lines)
                .style(self.theme.base())
                .scroll((offset.min(u16::MAX as usize) as u16, 0)),
            list_area,
        );
    }

    fn tree_row_line(&self, row: TreeRow, selected: bool, width: u16) -> Line<'static> {
        let Some(session) = self.mock.sessions.get(row.session) else {
            return styled_line("", width, self.theme.muted());
        };
        let prefix = if selected { "▸ " } else { "  " };
        let (text, style) = match row.node {
            None => {
                let expanded = self.tree.expanded.contains(&session.id);
                let arrow = if expanded { "▾" } else { "▸" };
                let connector = if row.session == 0 {
                    "●"
                } else if row.session + 1 == self.mock.sessions.len() {
                    "└─"
                } else {
                    "├─"
                };
                let kind = match session.kind {
                    SessionKind::Root => "root",
                    SessionKind::Fork => "fork",
                    SessionKind::AutoFork => "auto",
                };
                (
                    format!(
                        "{prefix}{connector} {arrow} {} · {kind} · {}n",
                        session.id,
                        session.nodes.len()
                    ),
                    self.theme.accent(),
                )
            }
            Some(node_index) => {
                let Some(node) = session.nodes.get(node_index) else {
                    return styled_line("", width, self.theme.muted());
                };
                let branch = if node_index + 1 == session.nodes.len() {
                    "└─"
                } else {
                    "├─"
                };
                let mut badges = String::new();
                if node.importance == Importance::High {
                    badges.push_str(" [high]");
                }
                if node.kind == NodeKind::Compaction {
                    badges.push_str(" ⇣compaction");
                }
                if node.checkpoint {
                    badges.push_str(" ◆checkpoint");
                }
                if node.id == session.leaf {
                    badges.push_str(" [leaf]");
                }
                if node.origin_node_id.is_some() {
                    badges.push_str(" [boundary]");
                }
                if self
                    .tree
                    .pinned
                    .contains(&(session.id.clone(), node.id.clone()))
                {
                    badges.push_str(" *pin");
                }
                if self
                    .tree
                    .marks
                    .contains(&(session.id.clone(), node.id.clone()))
                {
                    badges.push_str(" ✓");
                }
                let text = format!(
                    "{prefix}  {branch} {} {}{badges} · {}",
                    node.id,
                    node.kind.label(),
                    node.summary
                );
                let style = match node.kind {
                    NodeKind::User => self.theme.accent(),
                    NodeKind::Tool => self.theme.info(),
                    NodeKind::Compaction => self.theme.warning(),
                    NodeKind::Fork => self.theme.info(),
                    NodeKind::Assistant => self.theme.text(),
                };
                (text, style)
            }
        };
        if selected {
            styled_line(text, width, style.add_modifier(Modifier::BOLD))
        } else {
            styled_line(text, width, style)
        }
    }

    fn render_tree_detail(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let Some(row) = self.selected_tree_row() else {
            return;
        };
        let Some(session) = self.mock.sessions.get(row.session) else {
            return;
        };
        let (title, lines) = match row.node {
            None => (
                format!("SESSION {}", session.id),
                vec![
                    format!("kind    {}", session.kind.label()),
                    format!("parent  {}", session.parent.as_deref().unwrap_or("(root)")),
                    format!("leaf    {}", session.leaf),
                    format!("nodes   {}", session.nodes.len()),
                    format!("high    {}", session.high_importance_nodes()),
                    "select a node for field detail".to_owned(),
                ],
            ),
            Some(node_index) => {
                let Some(node) = session.nodes.get(node_index) else {
                    return;
                };
                let mut lines = vec![
                    format!("id      {}", node.id),
                    format!("kind    {}", node.kind.label()),
                    format!("role    {}", node.role),
                    format!("time    {}", node.time),
                    format!("model   {}", node.model),
                    format!("tokens  {}", node.tokens),
                    format!(
                        "checkpt {} · {} files",
                        if node.checkpoint { "yes" } else { "no" },
                        node.checkpoint_files
                    ),
                    format!(
                        "high    {}",
                        if node.importance == Importance::High {
                            "yes"
                        } else {
                            "no"
                        }
                    ),
                    format!(
                        "pinned  {}",
                        if self
                            .tree
                            .pinned
                            .contains(&(session.id.clone(), node.id.clone()))
                        {
                            "yes"
                        } else {
                            "no"
                        }
                    ),
                    format!("config  {}", node.config),
                ];
                lines.push("summary".to_owned());
                lines.extend(wrap_text(&node.summary, area.width));
                (format!("NODE {}", node.id), lines)
            }
        };
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Min(0)])
            .split(area);
        frame.render_widget(
            Paragraph::new(styled_line(title, area.width, self.theme.title())),
            rows[0],
        );
        let body = rows[1];
        if body.width == 0 || body.height == 0 {
            return;
        }
        let lines = lines
            .into_iter()
            .map(|line| styled_line(line, body.width, self.theme.text()))
            .collect::<Vec<_>>();
        frame.render_widget(
            Paragraph::new(lines)
                .style(self.theme.base())
                .scroll((0, 0)),
            body,
        );
    }

    pub(crate) fn render_rollback(&self, frame: &mut Frame, area: Rect) {
        let Some(state) = self.tree.rollback.as_ref() else {
            return;
        };
        if area.width < 28 || area.height < 8 {
            return;
        }
        let width = area.width.saturating_sub(4).min(68);
        let height = area.height.saturating_sub(2).min(16);
        let rect = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height - height) / 2,
            width,
            height,
        );
        frame.render_widget(Clear, rect);
        let session_id = self
            .mock
            .sessions
            .get(state.session)
            .map(|session| session.id.as_str())
            .unwrap_or("mock");
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(self.theme.warning())
            .style(self.theme.panel())
            .title(Line::from(format!(" ROLLBACK · {session_id} ")));
        let inner = block.inner(rect);
        frame.render_widget(block, rect);
        if inner.width == 0 || inner.height == 0 {
            return;
        }

        let node = self
            .mock
            .sessions
            .get(state.session)
            .and_then(|session| session.nodes.get(state.target));
        let target_id = node.map(|node| node.id.as_str()).unwrap_or("?");
        let config = node.map(|node| node.config.as_str()).unwrap_or("?");
        let cursor = state.field();
        let mut lines = Vec::new();
        lines.push(styled_line(
            format!(
                "{} session  {session_id}  [{}]",
                marker(cursor == 0),
                check(state.session_on)
            ),
            inner.width,
            if cursor == 0 {
                self.theme.accent()
            } else {
                self.theme.text()
            },
        ));
        lines.push(styled_line(
            format!(
                "{} config   {config}  [{}]",
                marker(cursor == 1),
                check(state.config_on)
            ),
            inner.width,
            if cursor == 1 {
                self.theme.accent()
            } else {
                self.theme.text()
            },
        ));
        if state.files_available {
            lines.push(styled_line(
                format!(
                    "{} files    [{}] restore {} files (target {target_id})",
                    marker(cursor == 2),
                    check(state.files_on),
                    state.files
                ),
                inner.width,
                if cursor == 2 {
                    self.theme.accent()
                } else {
                    self.theme.text()
                },
            ));
        } else {
            lines.push(styled_line(
                format!(
                    "{} files    [–] cannot_restore_files (target {target_id})",
                    marker(cursor == 2)
                ),
                inner.width,
                self.theme.muted(),
            ));
        }
        lines.push(Line::default());
        if state.compaction_warning {
            lines.push(styled_line(
                "⚠ 此处上下文已压缩，回退后只能看到摘要",
                inner.width,
                self.theme.warning(),
            ));
        }
        if let Some(checkpoint) = &state.checkpoint_id {
            lines.push(styled_line(
                format!("restore  {checkpoint} · {} files", state.files),
                inner.width,
                self.theme.muted(),
            ));
        } else {
            lines.push(styled_line(
                "restore  none · no checkpoint at or before this node",
                inner.width,
                self.theme.muted(),
            ));
        }
        if state.confirm_stage {
            lines.push(Line::default());
            lines.push(styled_line(
                format!(
                    "confirm  target {target_id} · checkpoint {}",
                    state.checkpoint_id.as_deref().unwrap_or("none")
                ),
                inner.width,
                self.theme.warning(),
            ));
            lines.push(styled_line(
                format!("files    {} · backup below", state.files),
                inner.width,
                self.theme.muted(),
            ));
        }
        for line in wrap_text(
            &format!("backup   {}", state.backup_path(session_id)),
            inner.width,
        ) {
            lines.push(styled_line(line, inner.width, self.theme.muted()));
        }
        if state.confirm_stage {
            lines.push(styled_line(
                "press Enter/y again to apply · Esc back",
                inner.width,
                self.theme.warning(),
            ));
        } else {
            lines.push(styled_line(
                if state.files_on {
                    "Space toggle · j/k field · Enter confirm · Esc cancel"
                } else {
                    "Space toggle · j/k field · Enter apply · Esc cancel"
                },
                inner.width,
                self.theme.info(),
            ));
        }
        frame.render_widget(Paragraph::new(lines).style(self.theme.panel()), inner);
    }
}

fn marker(active: bool) -> &'static str {
    if active {
        ">"
    } else {
        " "
    }
}

fn check(on: bool) -> &'static str {
    if on {
        "x"
    } else {
        " "
    }
}
