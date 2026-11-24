//! Snake mascot for the header slot: three 6x3 frames at 4 fps.
//!
//! The mascot is static art. Whether it is drawn at all, and whether it
//! animates, is decided by [`policy`]: narrow terminals, `TERM=dumb`,
//! `NO_COLOR`, modal overlays and streaming hide or freeze it.

use ratatui::{
    layout::{Alignment, Rect},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use crate::theme::{ColorMode, Theme};

/// Nominal mascot width in terminal cells.
pub const WIDTH: u16 = 6;
/// Nominal mascot height in terminal rows.
pub const HEIGHT: u16 = 3;

pub const IDLE: [&str; 3] = [" ╭──╮ ", "╭╯o ╰╮", "╰─╮╭─╯"];
pub const BLINK: [&str; 3] = [" ╭──╮ ", "╭╯- ╰╮", "╰─╮╭─╯"];
pub const TONGUE: [&str; 3] = [" ╭──╮ ", "╭╯o ╰~", "╰─╮╭─╯"];

/// Idle frame, kept as the stable `LINES` name used by the first integration.
pub const LINES: [&str; 3] = IDLE;

pub const FRAMES: [[&str; 3]; 3] = [IDLE, BLINK, TONGUE];
pub const FRAME_NAMES: [&str; 3] = ["idle", "blink", "tongue"];
pub const FRAME_COUNT: u8 = 3;
/// Four frames per second.
pub const FRAME_MS: u64 = 250;

/// What the header should do with the mascot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MascotMode {
    Hidden,
    Static,
    Animate,
}

/// Terminal capabilities that affect the mascot.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Environment {
    pub dumb: bool,
    pub no_color: bool,
}

impl Environment {
    pub fn detect() -> Self {
        let term = std::env::var("TERM").unwrap_or_default();
        Self {
            dumb: term == "dumb",
            no_color: std::env::var_os("NO_COLOR").is_some_and(|value| !value.is_empty()),
        }
    }
}

/// Decide whether the mascot is hidden, frozen, or animated.
pub fn policy(
    width: u16,
    mode: ColorMode,
    environment: Environment,
    modal: bool,
    streaming: bool,
) -> MascotMode {
    if width < 80 || environment.dumb || environment.no_color {
        return MascotMode::Hidden;
    }
    if mode == ColorMode::Ansi16 || modal || streaming {
        return MascotMode::Static;
    }
    MascotMode::Animate
}

/// Draw the idle frame right-aligned in the header slot.
pub fn render(frame: &mut Frame, area: Rect, theme: &Theme) {
    render_frame(frame, area, theme, 0);
}

/// Draw one frame right-aligned. `index` wraps modulo [`FRAME_COUNT`].
pub fn render_frame(frame: &mut Frame, area: Rect, theme: &Theme, index: u8) {
    if area.width == 0 || area.height == 0 {
        return;
    }
    let frame_lines = FRAMES[usize::from(index) % usize::from(FRAME_COUNT)];
    let lines: Vec<Line<'_>> = frame_lines
        .iter()
        .map(|line| Line::from(spans_for(line, theme)))
        .collect();
    let paragraph = Paragraph::new(lines).alignment(Alignment::Right);
    frame.render_widget(paragraph, area);
}

fn spans_for(line: &str, theme: &Theme) -> Vec<Span<'static>> {
    line.chars()
        .map(|ch| {
            let style = match ch {
                'o' | '-' => theme.mascot_eye(),
                '~' => theme.mascot_tongue(),
                _ => theme.mascot_body(),
            };
            Span::styled(ch.to_string(), style)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use unicode_width::UnicodeWidthStr;

    #[test]
    fn all_frames_share_the_nominal_size() {
        assert_eq!(FRAMES.len(), usize::from(FRAME_COUNT));
        for (index, frame) in FRAMES.iter().enumerate() {
            assert_eq!(frame.len(), usize::from(HEIGHT), "frame {index}");
            for line in frame {
                assert_eq!(
                    UnicodeWidthStr::width(*line),
                    usize::from(WIDTH),
                    "frame {index} line {line:?}"
                );
            }
        }
    }

    #[test]
    fn assets_match_the_code_frames() {
        for (index, name) in FRAME_NAMES.iter().enumerate() {
            let asset = match *name {
                "idle" => include_str!("../assets/mascot/idle.txt"),
                "blink" => include_str!("../assets/mascot/blink.txt"),
                "tongue" => include_str!("../assets/mascot/tongue.txt"),
                other => panic!("unknown frame {other}"),
            };
            let expected = format!("{}\n", FRAMES[index].join("\n"));
            assert_eq!(asset.replace("\r\n", "\n"), expected, "asset {name}");
        }
    }

    #[test]
    fn policy_hides_or_freezes_when_capability_is_low() {
        let env = Environment::default();
        assert_eq!(
            policy(79, ColorMode::TrueColor, env, false, false),
            MascotMode::Hidden
        );
        assert_eq!(
            policy(
                120,
                ColorMode::TrueColor,
                Environment {
                    dumb: true,
                    no_color: false
                },
                false,
                false
            ),
            MascotMode::Hidden
        );
        assert_eq!(
            policy(
                120,
                ColorMode::TrueColor,
                Environment {
                    dumb: false,
                    no_color: true
                },
                false,
                false
            ),
            MascotMode::Hidden
        );
        assert_eq!(
            policy(120, ColorMode::Ansi16, env, false, false),
            MascotMode::Static
        );
        assert_eq!(
            policy(120, ColorMode::Ansi256, env, true, false),
            MascotMode::Static
        );
        assert_eq!(
            policy(120, ColorMode::TrueColor, env, false, true),
            MascotMode::Static
        );
        assert_eq!(
            policy(120, ColorMode::TrueColor, env, false, false),
            MascotMode::Animate
        );
    }
}
