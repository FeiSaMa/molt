//! Semantic colors and terminal color degradation.
//!
//! The palette never paints a background: every surface uses `Color::Reset`, so
//! the terminal's own background (and a compositor's opacity/blur) shows
//! through. `ColorMode::Ansi16` only uses the sixteen basic ANSI colors so the
//! UI stays readable on a minimal terminal, `Ansi256` uses indexed colors, and
//! `TrueColor` uses RGB values.

use std::fmt;
use std::str::FromStr;

use anyhow::{bail, Result};
use ratatui::style::{Color, Modifier, Style};

/// Terminal color capability selected by `--color`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum ColorMode {
    /// Basic sixteen ANSI colors.
    Ansi16,
    /// 256-color palette. This is the default because it is widely available.
    #[default]
    Ansi256,
    /// 24-bit RGB.
    TrueColor,
}

impl ColorMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ansi16 => "16",
            Self::Ansi256 => "256",
            Self::TrueColor => "truecolor",
        }
    }
}

impl fmt::Display for ColorMode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ColorMode {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        match value {
            "16" | "ansi16" => Ok(Self::Ansi16),
            "256" | "ansi256" => Ok(Self::Ansi256),
            "truecolor" | "true-color" | "24bit" => Ok(Self::TrueColor),
            other => bail!("invalid color mode `{other}` (expected 16, 256, or truecolor)"),
        }
    }
}

/// Semantic palette shared by every view.
#[derive(Debug, Clone, Copy)]
pub struct Theme {
    pub mode: ColorMode,
    /// Always [`Color::Reset`]: the TUI never fills the terminal background.
    pub bg: Color,
    /// Panel background. Also [`Color::Reset`]; panels are separated by border
    /// glyphs, not by a darker fill.
    pub panel: Color,
    /// True when the background is the terminal default. Kept explicit so the
    /// `--transparent` alias can assert the intent in tests.
    pub transparent: bool,
    pub text: Color,
    pub muted: Color,
    pub accent: Color,
    pub success: Color,
    pub warning: Color,
    pub danger: Color,
    pub info: Color,
    pub diff_add: Color,
    pub diff_del: Color,
    pub border: Color,
    /// Snake mascot body (`mascot.body`).
    pub mascot_body: Color,
    /// Snake mascot eye (`mascot.eye`).
    pub mascot_eye: Color,
    /// Snake mascot tongue (`mascot.tongue`).
    pub mascot_tongue: Color,
}

impl Theme {
    pub const fn new(mode: ColorMode) -> Self {
        match mode {
            ColorMode::Ansi16 => Self {
                mode,
                bg: Color::Reset,
                panel: Color::Reset,
                transparent: true,
                text: Color::White,
                muted: Color::DarkGray,
                accent: Color::Cyan,
                success: Color::Green,
                warning: Color::Yellow,
                danger: Color::Red,
                info: Color::Blue,
                diff_add: Color::Green,
                diff_del: Color::Red,
                border: Color::DarkGray,
                mascot_body: Color::Green,
                mascot_eye: Color::White,
                mascot_tongue: Color::Red,
            },
            ColorMode::Ansi256 => Self {
                mode,
                bg: Color::Reset,
                panel: Color::Reset,
                transparent: true,
                text: Color::Indexed(252),
                muted: Color::Indexed(244),
                accent: Color::Indexed(81),
                success: Color::Indexed(114),
                warning: Color::Indexed(221),
                danger: Color::Indexed(203),
                info: Color::Indexed(111),
                diff_add: Color::Indexed(114),
                diff_del: Color::Indexed(203),
                border: Color::Indexed(240),
                mascot_body: Color::Indexed(114),
                mascot_eye: Color::Indexed(231),
                mascot_tongue: Color::Indexed(203),
            },
            ColorMode::TrueColor => Self {
                mode,
                bg: Color::Reset,
                panel: Color::Reset,
                transparent: true,
                text: Color::Rgb(201, 209, 217),
                muted: Color::Rgb(110, 118, 129),
                accent: Color::Rgb(86, 182, 194),
                success: Color::Rgb(126, 231, 135),
                warning: Color::Rgb(210, 153, 34),
                danger: Color::Rgb(248, 81, 73),
                info: Color::Rgb(121, 192, 255),
                diff_add: Color::Rgb(46, 160, 67),
                diff_del: Color::Rgb(248, 81, 73),
                border: Color::Rgb(48, 54, 61),
                mascot_body: Color::Rgb(126, 231, 135),
                mascot_eye: Color::Rgb(230, 237, 243),
                mascot_tongue: Color::Rgb(248, 81, 73),
            },
        }
    }

    /// Explicit alias for the default palette. `--transparent` is retained as a
    /// compatibility flag; it changes nothing because every surface already
    /// uses [`Color::Reset`].
    pub const fn with_transparent(self) -> Self {
        self
    }

    pub const fn base(self) -> Style {
        Style::new().fg(self.text).bg(self.bg)
    }

    pub const fn title(self) -> Style {
        Style::new()
            .fg(self.accent)
            .bg(self.bg)
            .add_modifier(Modifier::BOLD)
    }

    pub const fn muted(self) -> Style {
        Style::new().fg(self.muted).bg(self.bg)
    }

    pub const fn text(self) -> Style {
        Style::new().fg(self.text).bg(self.bg)
    }

    pub const fn accent(self) -> Style {
        Style::new().fg(self.accent).bg(self.bg)
    }

    pub const fn success(self) -> Style {
        Style::new().fg(self.success).bg(self.bg)
    }

    pub const fn warning(self) -> Style {
        Style::new().fg(self.warning).bg(self.bg)
    }

    pub const fn danger(self) -> Style {
        Style::new().fg(self.danger).bg(self.bg)
    }

    pub const fn info(self) -> Style {
        Style::new().fg(self.info).bg(self.bg)
    }

    pub const fn border(self) -> Style {
        Style::new().fg(self.border).bg(self.bg)
    }

    pub const fn diff_add(self) -> Style {
        Style::new().fg(self.diff_add).bg(self.bg)
    }

    pub const fn diff_del(self) -> Style {
        Style::new().fg(self.diff_del).bg(self.bg)
    }

    pub const fn panel(self) -> Style {
        Style::new().fg(self.text).bg(self.panel)
    }

    pub const fn mascot_body(self) -> Style {
        Style::new().fg(self.mascot_body).bg(self.bg)
    }

    pub const fn mascot_eye(self) -> Style {
        Style::new().fg(self.mascot_eye).bg(self.bg)
    }

    pub const fn mascot_tongue(self) -> Style {
        Style::new().fg(self.mascot_tongue).bg(self.bg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_all_color_modes() {
        assert_eq!("16".parse::<ColorMode>().unwrap(), ColorMode::Ansi16);
        assert_eq!("256".parse::<ColorMode>().unwrap(), ColorMode::Ansi256);
        assert_eq!(
            "truecolor".parse::<ColorMode>().unwrap(),
            ColorMode::TrueColor
        );
        assert!("88".parse::<ColorMode>().is_err());
    }

    #[test]
    fn every_mode_uses_the_terminal_default_background() {
        for mode in [ColorMode::Ansi16, ColorMode::Ansi256, ColorMode::TrueColor] {
            let theme = Theme::new(mode);
            assert_eq!(theme.bg, Color::Reset, "{mode}");
            assert_eq!(theme.panel, Color::Reset, "{mode}");
            assert!(theme.transparent, "{mode}");
            assert_eq!(theme.base().bg, Some(Color::Reset), "{mode}");
            assert_eq!(theme.panel().bg, Some(Color::Reset), "{mode}");
            assert_eq!(theme.with_transparent().bg, Color::Reset, "{mode}");
        }
    }

    #[test]
    fn ansi16_never_uses_rgb_or_indexed_colors() {
        let theme = Theme::new(ColorMode::Ansi16);
        for color in [
            theme.bg,
            theme.panel,
            theme.text,
            theme.muted,
            theme.accent,
            theme.success,
            theme.warning,
            theme.danger,
            theme.info,
            theme.diff_add,
            theme.diff_del,
            theme.border,
            theme.mascot_body,
            theme.mascot_eye,
            theme.mascot_tongue,
        ] {
            assert!(!matches!(color, Color::Rgb(..) | Color::Indexed(..)));
        }
    }

    #[test]
    fn mascot_tokens_differ_in_every_mode() {
        for mode in [ColorMode::Ansi16, ColorMode::Ansi256, ColorMode::TrueColor] {
            let theme = Theme::new(mode);
            assert_ne!(theme.mascot_body, theme.mascot_eye, "{mode}");
            assert_ne!(theme.mascot_body, theme.mascot_tongue, "{mode}");
        }
    }
}
