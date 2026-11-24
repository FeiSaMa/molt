//! Approval modal: pending `proc.exec`, three decisions, and saved rules.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{layout::Rect, style::Modifier, text::Line, widgets::Paragraph, Frame};

use crate::app::{styled_line, wrap_text, App, Screen, StatusLevel};
use crate::mock::ApprovalState;

#[derive(Debug, Clone, Default)]
pub struct ApprovalUiState {
    pub selected: usize,
    pub saved_rules: Vec<String>,
}

impl App {
    pub(crate) fn handle_approval_key(&mut self, key: KeyEvent) -> bool {
        match key.code {
            KeyCode::Esc => {
                self.screen = Screen::Chat;
                self.composer_focused = true;
                self.set_status(StatusLevel::Info, "approval deferred (later)");
                true
            }
            KeyCode::Char('y') => {
                self.decide_approval(ApprovalDecision::Once);
                true
            }
            KeyCode::Char('A') => {
                self.decide_approval(ApprovalDecision::Always);
                true
            }
            KeyCode::Char('x') => {
                self.decide_approval(ApprovalDecision::Reject);
                true
            }
            _ => false,
        }
    }

    fn decide_approval(&mut self, decision: ApprovalDecision) {
        let pending: Vec<String> = self
            .mock
            .pending_approvals()
            .map(|approval| approval.id.clone())
            .collect();
        if pending.is_empty() {
            self.set_status(StatusLevel::Warning, "no pending approval");
            return;
        }
        let index = self.approvals.selected.min(pending.len() - 1);
        let id = pending[index].clone();
        let Some(approval) = self
            .mock
            .approvals
            .iter_mut()
            .find(|approval| approval.id == id)
        else {
            return;
        };
        let action = approval.action.clone();
        let resource = approval.resource.clone();
        approval.state = match decision {
            ApprovalDecision::Once | ApprovalDecision::Always => ApprovalState::Approved,
            ApprovalDecision::Reject => ApprovalState::Rejected,
        };
        match decision {
            ApprovalDecision::Once => {
                self.set_status(
                    StatusLevel::Success,
                    format!("approval {id} · once (approved) (mock)"),
                );
            }
            ApprovalDecision::Always => {
                let rule = format!("{action} · {resource} · scope=session · ttl=24h");
                self.approvals.saved_rules.push(rule);
                self.set_status(
                    StatusLevel::Success,
                    format!("approval {id} · always (approved) (mock) · saved rule session/24h"),
                );
            }
            ApprovalDecision::Reject => {
                self.set_status(
                    StatusLevel::Danger,
                    format!("approval {id} · rejected (mock)"),
                );
            }
        }
        let remaining = self.mock.pending_approvals().count();
        self.approvals.selected = self.approvals.selected.min(remaining.saturating_sub(1));
    }

    pub(crate) fn render_approval(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let pending: Vec<_> = self.mock.pending_approvals().collect();
        let selected = self.approvals.selected.min(pending.len().saturating_sub(1));
        let position = if pending.is_empty() {
            "0/0".to_owned()
        } else {
            format!("{}/{}", selected + 1, pending.len())
        };
        let mut lines = vec![styled_line(
            format!("{} pending · approval modal · {position}", pending.len()),
            area.width,
            self.theme.muted(),
        )];
        if pending.is_empty() {
            lines.push(styled_line(
                "no pending approvals",
                area.width,
                self.theme.text(),
            ));
        } else {
            let approval = pending[self.approvals.selected.min(pending.len() - 1)];
            lines.push(styled_line(
                format!("action    {}", approval.action),
                area.width,
                self.theme.accent(),
            ));
            lines.push(styled_line(
                format!("resource  {}", approval.resource),
                area.width,
                self.theme.warning().add_modifier(Modifier::BOLD),
            ));
            lines.push(styled_line(
                format!("command   {}", approval.command),
                area.width,
                self.theme.text(),
            ));
            lines.push(styled_line(
                format!("cwd       {}", approval.cwd),
                area.width,
                self.theme.muted(),
            ));
            lines.push(styled_line(
                format!("tier      {}", approval.tier),
                area.width,
                self.theme.muted(),
            ));
            lines.push(styled_line(
                format!("sandbox   {}", approval.sandbox),
                area.width,
                self.theme.info(),
            ));
            lines.push(styled_line(
                format!("risk      {}", approval.risk),
                area.width,
                self.theme.danger(),
            ));
            if let Some(preview) = &approval.diff_preview {
                let mut preview_lines = preview.split('\n');
                if let Some(first) = preview_lines.next() {
                    lines.push(styled_line(
                        format!("diff      {first}"),
                        area.width,
                        self.theme.diff_add(),
                    ));
                }
                for line in preview_lines {
                    lines.push(styled_line(
                        format!("          {line}"),
                        area.width,
                        self.theme.diff_add(),
                    ));
                }
            }
            lines.push(styled_line(
                format!("reason    {}", approval.reason),
                area.width,
                self.theme.muted(),
            ));
            lines.push(styled_line(
                "options   [y] once · [A] always · [x] reject · [Esc] later",
                area.width,
                self.theme.info(),
            ));
            lines.push(styled_line(
                "always → saved rule: scope=session · ttl=24h",
                area.width,
                self.theme.muted(),
            ));
        }
        if !self.approvals.saved_rules.is_empty() {
            lines.push(Line::default());
            lines.push(styled_line("SAVED RULES", area.width, self.theme.title()));
            for rule in &self.approvals.saved_rules {
                for line in wrap_text(&format!("  {rule}"), area.width) {
                    lines.push(styled_line(line, area.width, self.theme.success()));
                }
            }
        }
        frame.render_widget(Paragraph::new(lines).style(self.theme.base()), area);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ApprovalDecision {
    Once,
    Always,
    Reject,
}
