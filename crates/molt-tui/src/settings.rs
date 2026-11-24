//! Settings view: config DAG, field-level diff against the active config,
//! routing cycle, and the detail overlay.

use std::collections::BTreeSet;

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Modifier,
    text::Line,
    widgets::{Block, Borders, Clear, Paragraph},
    Frame,
};

use crate::app::{styled_line, wrap_text, App, StatusLevel};
use crate::mock::Config;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RoutingMode {
    Off,
    Suggest,
    Auto,
}

impl RoutingMode {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Suggest => "suggest",
            Self::Auto => "auto",
        }
    }

    pub const fn next(self) -> Self {
        match self {
            Self::Off => Self::Suggest,
            Self::Suggest => Self::Auto,
            Self::Auto => Self::Off,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiffOp {
    Add,
    Remove,
    Modify,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldDiff {
    pub op: DiffOp,
    pub field: &'static str,
    pub value: String,
}

#[derive(Debug, Clone)]
pub struct SettingsState {
    pub selected: usize,
    pub routing: RoutingMode,
    pub detail_open: bool,
}

impl SettingsState {
    pub fn new(mock: &crate::mock::MockData) -> Self {
        let selected = mock
            .configs
            .iter()
            .position(|config| config.id == mock.active_config)
            .unwrap_or(0);
        Self {
            selected,
            routing: RoutingMode::Suggest,
            detail_open: false,
        }
    }
}

/// Compare every user-visible field of `target` against `active`.
pub fn config_diff(target: &Config, active: &Config) -> Vec<FieldDiff> {
    let mut diff = Vec::new();
    // Set changes come first: they are the most useful lines in a narrow pane.
    for (field, active_value, target_value) in [
        ("tools", active.tools.as_str(), target.tools.as_str()),
        ("mcp", active.mcp.as_str(), target.mcp.as_str()),
    ] {
        let active_set: BTreeSet<&str> = active_value.split(',').collect();
        let target_set: BTreeSet<&str> = target_value.split(',').collect();
        for item in target_set.difference(&active_set) {
            diff.push(FieldDiff {
                op: DiffOp::Add,
                field,
                value: (*item).to_owned(),
            });
        }
        for item in active_set.difference(&target_set) {
            diff.push(FieldDiff {
                op: DiffOp::Remove,
                field,
                value: (*item).to_owned(),
            });
        }
    }
    for (field, active_value, target_value) in [
        ("model", active.model.as_str(), target.model.as_str()),
        (
            "provider",
            active.provider.as_str(),
            target.provider.as_str(),
        ),
        (
            "thinking",
            active.thinking.as_str(),
            target.thinking.as_str(),
        ),
        (
            "permissions",
            active.permissions.as_str(),
            target.permissions.as_str(),
        ),
    ] {
        if active_value != target_value {
            diff.push(FieldDiff {
                op: DiffOp::Modify,
                field,
                value: format!("{active_value} → {target_value}"),
            });
        }
    }
    if active.prompt != target.prompt {
        diff.push(FieldDiff {
            op: DiffOp::Modify,
            field: "prompt",
            value: format!(
                "{} → {}",
                short(&active.prompt, 22),
                short(&target.prompt, 22)
            ),
        });
    }
    diff
}

fn short(text: &str, width: usize) -> String {
    let mut output = text.chars().take(width).collect::<String>();
    if text.chars().count() > width {
        output.push('…');
    }
    output
}

impl App {
    pub fn select_config(&mut self, index: usize) {
        self.settings.selected = index.min(self.mock.configs.len().saturating_sub(1));
    }

    pub(crate) fn handle_settings_key(&mut self, key: KeyEvent) -> bool {
        if self.settings.detail_open {
            if matches!(key.code, KeyCode::Esc | KeyCode::Enter) {
                self.settings.detail_open = false;
            }
            return true;
        }
        match key.code {
            KeyCode::Char('j') | KeyCode::Down => {
                self.settings.selected =
                    (self.settings.selected + 1).min(self.mock.configs.len().saturating_sub(1));
                true
            }
            KeyCode::Char('k') | KeyCode::Up => {
                self.settings.selected = self.settings.selected.saturating_sub(1);
                true
            }
            KeyCode::Char('a') => {
                self.apply_config();
                true
            }
            KeyCode::Char('d') => {
                self.screen = crate::app::Screen::Diff;
                self.set_status(StatusLevel::Info, "opened diff screen");
                true
            }
            KeyCode::Char('r') => {
                self.settings.routing = self.settings.routing.next();
                self.set_status(
                    StatusLevel::Info,
                    format!("routing mode: {}", self.settings.routing.label()),
                );
                true
            }
            KeyCode::Enter => {
                self.settings.detail_open = true;
                true
            }
            _ => false,
        }
    }

    fn apply_config(&mut self) {
        let Some(config) = self.mock.configs.get(self.settings.selected) else {
            return;
        };
        if config.scenario_version != self.mock.scenario_version {
            self.set_status(
                StatusLevel::Danger,
                format!(
                    "apply rejected · scenario_mismatch (v{} != v{})",
                    config.scenario_version, self.mock.scenario_version
                ),
            );
            return;
        }
        let id = config.id.clone();
        self.mock.active_config = id.clone();
        self.set_status(StatusLevel::Success, format!("applied {id} (mock)"));
    }

    pub(crate) fn render_settings(&self, frame: &mut Frame, area: Rect) {
        if area.width < 44 {
            self.render_settings_list(frame, area);
            return;
        }
        let columns = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(46), Constraint::Percentage(54)])
            .split(area);
        self.render_settings_list(frame, columns[0]);
        let block = Block::default()
            .borders(Borders::LEFT)
            .border_style(self.theme.border());
        let inner = block.inner(columns[1]);
        frame.render_widget(block, columns[1]);
        self.render_settings_detail(frame, inner);
    }

    fn settings_row_lines(&self, config: &Config, width: u16) -> Vec<Line<'static>> {
        let index = self
            .mock
            .configs
            .iter()
            .position(|candidate| candidate.id == config.id)
            .unwrap_or(0);
        let selected = index == self.settings.selected;
        let active = config.id == self.mock.active_config;
        let prefix = if selected { "▸ " } else { "  " };
        let connector = if index == 0 {
            "●"
        } else if index + 1 == self.mock.configs.len() {
            "└─"
        } else {
            "├─"
        };
        let suffix = if active { "  [active]" } else { "" };
        let style = if selected {
            self.theme.accent().add_modifier(Modifier::BOLD)
        } else if active {
            self.theme.success()
        } else {
            self.theme.text()
        };
        let match_note = if config.scenario_version != self.mock.scenario_version {
            " !"
        } else {
            ""
        };
        let mut lines = vec![styled_line(
            format!("{prefix}{connector} {}{suffix}{match_note}", config.id),
            width,
            style,
        )];
        let parents = if config.parents.is_empty() {
            "root config".to_owned()
        } else {
            format!("parents: {}", config.parents.join(" + "))
        };
        lines.push(styled_line(
            format!("   {parents}"),
            width,
            self.theme.muted(),
        ));
        if config.scenario_version != self.mock.scenario_version {
            lines.push(styled_line(
                format!(
                    "   scenario_mismatch v{} vs v{}",
                    config.scenario_version, self.mock.scenario_version
                ),
                width,
                self.theme.warning(),
            ));
        }
        lines
    }

    fn render_settings_list(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Min(0)])
            .split(area);
        frame.render_widget(
            Paragraph::new(styled_line(
                format!("CONFIG DAG · routing {}", self.settings.routing.label()),
                area.width,
                self.theme.muted(),
            )),
            rows[0],
        );
        let body = rows[1];
        if body.width == 0 || body.height == 0 {
            return;
        }
        let lines = self
            .mock
            .configs
            .iter()
            .flat_map(|config| self.settings_row_lines(config, body.width))
            .collect::<Vec<_>>();
        frame.render_widget(Paragraph::new(lines).style(self.theme.base()), body);
    }

    fn render_settings_detail(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let Some(config) = self.mock.configs.get(self.settings.selected) else {
            return;
        };
        let Some(active) = self.mock.active_config() else {
            return;
        };
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Min(0)])
            .split(area);
        frame.render_widget(
            Paragraph::new(styled_line(
                format!("CONFIG {}", config.id),
                area.width,
                self.theme.title(),
            )),
            rows[0],
        );
        let body = rows[1];
        if body.width == 0 || body.height == 0 {
            return;
        }
        let mut lines = vec![
            styled_line(
                format!("model    {}", config.model),
                body.width,
                self.theme.text(),
            ),
            styled_line(
                format!("provider {}", config.provider),
                body.width,
                self.theme.text(),
            ),
            styled_line(
                format!("thinking {}", config.thinking),
                body.width,
                self.theme.text(),
            ),
            styled_line(
                format!("tools    {}", config.tools),
                body.width,
                self.theme.text(),
            ),
            styled_line(
                format!("mcp      {}", config.mcp),
                body.width,
                self.theme.text(),
            ),
            styled_line(
                format!("perm     {}", config.permissions),
                body.width,
                self.theme.text(),
            ),
        ];
        for line in wrap_text(&format!("prompt   {}", config.prompt), body.width) {
            lines.push(styled_line(line, body.width, self.theme.muted()));
        }
        lines.push(styled_line(
            format!(
                "scenario v{} · {}",
                config.scenario_version,
                if config.parents.is_empty() {
                    "root".to_owned()
                } else {
                    config.parents.join(" + ")
                }
            ),
            body.width,
            self.theme.muted(),
        ));
        lines.push(Line::default());
        let diff = config_diff(config, active);
        lines.push(styled_line(
            format!("diff vs {}", active.id),
            body.width,
            self.theme.info(),
        ));
        if diff.is_empty() {
            lines.push(styled_line(
                "no field changes",
                body.width,
                self.theme.muted(),
            ));
        }
        for entry in diff {
            let (sign, style) = match entry.op {
                DiffOp::Add => ("+", self.theme.diff_add()),
                DiffOp::Remove => ("-", self.theme.diff_del()),
                DiffOp::Modify => ("~", self.theme.warning()),
            };
            lines.push(styled_line(
                format!("{sign} {} {}", entry.field, entry.value),
                body.width,
                style,
            ));
        }
        frame.render_widget(Paragraph::new(lines).style(self.theme.base()), body);
    }

    pub(crate) fn render_settings_detail_overlay(&self, frame: &mut Frame, area: Rect) {
        let Some(config) = self.mock.configs.get(self.settings.selected) else {
            return;
        };
        if area.width < 32 || area.height < 10 {
            return;
        }
        let width = area.width.saturating_sub(4).min(76);
        let height = area.height.saturating_sub(2).min(18);
        let rect = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height - height) / 2,
            width,
            height,
        );
        frame.render_widget(Clear, rect);
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(self.theme.accent())
            .style(self.theme.panel())
            .title(Line::from(format!(" CONFIG · {} ", config.id)));
        let inner = block.inner(rect);
        frame.render_widget(block, rect);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let mut lines = vec![
            styled_line(
                format!("id       {}", config.id),
                inner.width,
                self.theme.title(),
            ),
            styled_line(
                format!("model    {} · {}", config.model, config.provider),
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                format!("thinking {}", config.thinking),
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                format!("tools    {}", config.tools),
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                format!("mcp      {}", config.mcp),
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                format!("perm     {}", config.permissions),
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                format!(
                    "parents  {}",
                    if config.parents.is_empty() {
                        "(root)".to_owned()
                    } else {
                        config.parents.join(" + ")
                    }
                ),
                inner.width,
                self.theme.muted(),
            ),
            styled_line(
                format!(
                    "scenario v{} (active v{})",
                    config.scenario_version, self.mock.scenario_version
                ),
                inner.width,
                self.theme.muted(),
            ),
            Line::default(),
            styled_line("prompt", inner.width, self.theme.info()),
        ];
        for line in wrap_text(&config.prompt, inner.width) {
            lines.push(styled_line(line, inner.width, self.theme.text()));
        }
        lines.push(styled_line(
            "Enter/Esc close",
            inner.width,
            self.theme.muted(),
        ));
        frame.render_widget(Paragraph::new(lines).style(self.theme.panel()), inner);
    }
}
