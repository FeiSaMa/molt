//! Diff view: two mock files, three hunks, unified/split layouts, and the
//! restore confirmation modal.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Modifier,
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph},
    Frame,
};
use unicode_width::UnicodeWidthStr;

use crate::app::{fit, spans_line, styled_line, App, StatusLevel};
use crate::mock::{DiffKind, DiffLine};
use crate::theme::Theme;

#[derive(Debug, Clone, Default)]
pub struct DiffState {
    pub current: usize,
    pub split: bool,
    pub restore: bool,
}

impl App {
    pub(crate) fn handle_diff_key(&mut self, key: KeyEvent) -> bool {
        if self.diff.restore {
            match key.code {
                KeyCode::Enter => {
                    self.diff.restore = false;
                    self.set_status(
                        StatusLevel::Success,
                        "restored working tree from checkpoint (mock)",
                    );
                }
                KeyCode::Esc => {
                    self.diff.restore = false;
                    self.set_status(StatusLevel::Info, "restore cancelled");
                }
                _ => {}
            }
            return true;
        }
        let count = self.mock.hunk_count();
        match key.code {
            KeyCode::Char('n') => {
                if count > 0 {
                    self.diff.current = (self.diff.current + 1) % count;
                    self.announce_hunk();
                }
                true
            }
            KeyCode::Char('N') => {
                if count > 0 {
                    self.diff.current = (self.diff.current + count - 1) % count;
                    self.announce_hunk();
                }
                true
            }
            KeyCode::Char('s') => {
                let width = self.viewport.get().0;
                if width < 120 {
                    self.set_status(
                        StatusLevel::Warning,
                        format!("split needs a terminal ≥120 wide (current {width})"),
                    );
                } else {
                    self.diff.split = !self.diff.split;
                    self.set_status(
                        StatusLevel::Info,
                        if self.diff.split {
                            "layout: split"
                        } else {
                            "layout: unified"
                        },
                    );
                }
                true
            }
            KeyCode::Char('a') => {
                self.set_status(StatusLevel::Success, "patch applied to worktree (mock)");
                true
            }
            KeyCode::Char('r') => {
                self.diff.restore = true;
                true
            }
            KeyCode::Char('o') => {
                self.set_status(
                    StatusLevel::Info,
                    format!("opened {} in $PAGER (mock)", self.mock.large_file.path),
                );
                true
            }
            _ => false,
        }
    }

    fn announce_hunk(&mut self) {
        let indexes = self.mock.hunk_indexes();
        let count = indexes.len();
        if let Some((file, _)) = indexes.get(self.diff.current.min(count.saturating_sub(1))) {
            let path = self
                .mock
                .diffs
                .get(*file)
                .map(|file| file.path.as_str())
                .unwrap_or("?");
            self.set_status(
                StatusLevel::Info,
                format!("hunk {}/{} · {path}", self.diff.current + 1, count),
            );
        }
    }

    pub(crate) fn render_diff(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(1),
                Constraint::Length(1),
                Constraint::Length(1),
                Constraint::Min(0),
            ])
            .split(area);
        let count = self.mock.hunk_count();
        let current = self.diff.current.min(count.saturating_sub(1));
        let split = self.diff.split && self.viewport.get().0 >= 120;
        let mode = if split { "split" } else { "unified" };
        frame.render_widget(
            Paragraph::new(styled_line(
                format!(
                    "{} files · {count} hunks · {mode} · hunk {}/{}",
                    self.mock.diffs.len(),
                    current + 1,
                    count
                ),
                area.width,
                self.theme.muted(),
            )),
            rows[0],
        );
        frame.render_widget(
            Paragraph::new(styled_line(
                format!("large {}", self.mock.large_file.path),
                area.width,
                self.theme.warning(),
            )),
            rows[1],
        );
        frame.render_widget(
            Paragraph::new(styled_line(
                format!(
                    "{} · 打开外部 $PAGER (o)",
                    self.mock.large_file.human_size()
                ),
                area.width,
                self.theme.muted(),
            )),
            rows[2],
        );
        let body = rows[3];
        if body.width == 0 || body.height == 0 {
            return;
        }
        let (lines, current_line) = self.diff_body_lines(body.width, current, split);
        let height = usize::from(body.height);
        let offset = current_line.saturating_sub(1);
        let offset = if offset + height <= lines.len() {
            offset
        } else {
            lines.len().saturating_sub(height)
        };
        frame.render_widget(
            Paragraph::new(lines)
                .style(self.theme.base())
                .scroll((offset.min(u16::MAX as usize) as u16, 0)),
            body,
        );
    }

    fn diff_body_lines(
        &self,
        width: u16,
        current: usize,
        split: bool,
    ) -> (Vec<Line<'static>>, usize) {
        let mut lines = Vec::new();
        let mut current_line = 0;
        let mut hunk_counter = 0;
        for file in &self.mock.diffs {
            lines.push(styled_line(
                format!("--- a/{}", file.path),
                width,
                self.theme.muted(),
            ));
            lines.push(styled_line(
                format!("+++ b/{}", file.path),
                width,
                self.theme.muted(),
            ));
            for hunk in &file.hunks {
                if hunk_counter == current {
                    current_line = lines.len();
                }
                lines.push(styled_line(
                    format!(
                        "{} {}",
                        if hunk_counter == current { "▶" } else { " " },
                        hunk.header
                    ),
                    width,
                    self.theme.info().add_modifier(Modifier::BOLD),
                ));
                if split {
                    for line in &hunk.lines {
                        lines.push(split_line(line, width, self.theme));
                    }
                } else {
                    for line in &hunk.lines {
                        lines.push(unified_line(line, width, self.theme));
                    }
                }
                hunk_counter += 1;
            }
        }
        (lines, current_line)
    }

    pub(crate) fn render_restore(&self, frame: &mut Frame, area: Rect) {
        if !self.diff.restore {
            return;
        }
        if area.width < 32 || area.height < 8 {
            return;
        }
        let width = area.width.saturating_sub(4).min(56);
        let height = 7;
        let rect = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height - height) / 2,
            width,
            height,
        );
        frame.render_widget(Clear, rect);
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(self.theme.danger())
            .style(self.theme.panel())
            .title(Line::from(" RESTORE "));
        let inner = block.inner(rect);
        frame.render_widget(block, rect);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let lines = vec![
            styled_line(
                "Restore the mock working tree from checkpoint?",
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                "the applied patch is discarded (mock)",
                inner.width,
                self.theme.muted(),
            ),
            styled_line(
                "Enter confirm · Esc cancel",
                inner.width,
                self.theme.warning(),
            ),
        ];
        frame.render_widget(Paragraph::new(lines).style(self.theme.panel()), inner);
    }
}

fn unified_line(line: &DiffLine, width: u16, theme: Theme) -> Line<'static> {
    let old = line
        .old
        .map(|number| format!("{number:>4}"))
        .unwrap_or_else(|| "    ".to_owned());
    let new = line
        .new
        .map(|number| format!("{number:>4}"))
        .unwrap_or_else(|| "    ".to_owned());
    let prefix = format!("{old} {new} │ ");
    let prefix_width = UnicodeWidthStr::width(prefix.as_str()) as u16;
    if width <= prefix_width {
        return styled_line("", width, theme.text());
    }
    let (sign, style) = match line.kind {
        DiffKind::Add => ("+", theme.diff_add()),
        DiffKind::Del => ("-", theme.diff_del()),
        DiffKind::Context => (" ", theme.text()),
    };
    let content = fit(
        &format!("{sign}{}", line.text),
        width.saturating_sub(prefix_width),
    );
    spans_line(
        vec![
            Span::styled(prefix, theme.muted()),
            Span::styled(content, style),
        ],
        width,
    )
}

fn split_line(line: &DiffLine, width: u16, theme: Theme) -> Line<'static> {
    let half = width.saturating_sub(13) / 2;
    if half == 0 {
        return styled_line("", width, theme.text());
    }
    let (style, left_text, right_text) = match line.kind {
        DiffKind::Context => (theme.text(), line.text.clone(), line.text.clone()),
        DiffKind::Del => (theme.diff_del(), line.text.clone(), String::new()),
        DiffKind::Add => (theme.diff_add(), String::new(), line.text.clone()),
    };
    let left_number = line
        .old
        .map(|number| format!("{number:>4}"))
        .unwrap_or_else(|| "    ".to_owned());
    let right_number = line
        .new
        .map(|number| format!("{number:>4}"))
        .unwrap_or_else(|| "    ".to_owned());
    spans_line(
        vec![
            Span::styled(format!("{left_number} "), theme.muted()),
            Span::styled(fit(&left_text, half), style),
            Span::styled(" │ ", theme.muted()),
            Span::styled(format!("{right_number} "), theme.muted()),
            Span::styled(fit(&right_text, half), style),
        ],
        width,
    )
}
