//! Final logo art for the v3 shell (W-08).
//!
//! Three self-drawn candidates share one lockup rule: the `O` of `MOLT` is an
//! ouroboros, a snake biting its own tail. Shapes are stored as pixel maps;
//! colour is derived from the polar angle at draw time, so the same map
//! degrades from truecolor to 256 indexed colours and to the sixteen basic ANSI
//! colours. Every glyph is one terminal cell wide; no emoji or image protocol is
//! involved.
//!
//! Public surface kept stable for W-09: [`CANDIDATES`], [`render`], and the
//! [`Selection`] parser behind `--logo`.

use std::fmt::Write as _;
use std::io::Write as _;
use std::str::FromStr;

use anyhow::{bail, Result};
use ratatui::{
    layout::Rect,
    style::{Color, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use crate::theme::{ColorMode, Theme};

/// Which logo candidate `--logo` should print or start with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Selection {
    All,
    One(u8),
}

impl FromStr for Selection {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        match value {
            "all" => Ok(Self::All),
            "1" => Ok(Self::One(1)),
            "2" => Ok(Self::One(2)),
            "3" => Ok(Self::One(3)),
            other => bail!("invalid logo selection `{other}` (expected all, 1, 2, or 3)"),
        }
    }
}

/// One reviewed candidate. Art stays private so the public surface is data only.
#[derive(Debug, Clone, Copy)]
pub struct Candidate {
    pub name: &'static str,
    pub tagline: &'static str,
}

pub const CANDIDATES: [Candidate; 3] = [
    Candidate {
        name: "Ouroboros Pixel",
        tagline: "half-block MOLT; the O is a self-drawn ouroboros ring",
    },
    Candidate {
        name: "Braille Ring",
        tagline: "half-block wordmark; fine braille ouroboros, accent head",
    },
    Candidate {
        name: "Terminal Crest",
        tagline: "9x4 ouroboros crest badge with a wide half-block MOLT",
    },
];

/// Candidate `--logo` starts with when no value is given (zero-based, so 2 =
/// candidate 3, "Terminal Crest").
pub const DEFAULT_CANDIDATE: usize = 2;
/// Default `--logo` selection when the flag has no value.
pub const DEFAULT_SELECTION: Selection = Selection::One(3);

/// Header slot is capped at fifteen cells by three rows. Candidates 1 and 2
/// keep their twelve-cell art and pad the extra three cells with blanks.
pub const COMPACT_WIDTH: u16 = 15;
pub const COMPACT_HEIGHT: u16 = 3;

/// Large (startup) or compact (header) rendering.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Variant {
    Compact,
    Large,
}

// ---------------------------------------------------------------------------
// Cell raster
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Class {
    Empty,
    Body,
    Word,
    Head,
    Crest,
    Tongue,
    Eye,
}

fn class_of(ch: char) -> Class {
    match ch {
        '#' => Class::Body,
        'h' => Class::Head,
        'c' => Class::Crest,
        't' => Class::Tongue,
        'o' => Class::Eye,
        'w' => Class::Word,
        _ => Class::Empty,
    }
}

#[derive(Debug, Clone, Copy)]
struct Cell {
    ch: char,
    fg: Option<Color>,
    bg: Option<Color>,
}

struct HalfArt {
    /// Two pixel rows per terminal row; one character per cell column.
    rows: &'static [&'static str],
    /// Ring centre in pixel coordinates, used for the polar gradient.
    center: (f32, f32),
    /// Half-open column ranges that belong to the wordmark, not the snake.
    word_spans: &'static [(usize, usize)],
}

struct CellArt {
    chars: &'static [&'static str],
    classes: &'static [&'static str],
    /// Ring centre in cell coordinates, used for the polar gradient.
    center: (f32, f32),
}

enum LargeArt {
    Half(HalfArt),
    Cells(CellArt),
}

// ---------------------------------------------------------------------------
// C1 · Ouroboros Pixel
// ---------------------------------------------------------------------------

const C1_LARGE: HalfArt = HalfArt {
    rows: &[
        "............cch.................",
        "........###hhhh.................",
        "#   #..####hohh......#    .#####",
        "## ##..###.hhhh......#    .  #  ",
        "# # #.###...hh#tttt..#    .  #  ",
        "#   #.##.....###.....#    .  #  ",
        "#   #.##.....###.....#    .  #  ",
        "#   #.##.....###.....#    .  #  ",
        "#   #.##.....###.....#    .  #  ",
        "#   #.###...###......#    .  #  ",
        "#   #..###.###.......#    .  #  ",
        "#   #..#######.......#####.  #  ",
        "........#####...................",
        "................................",
    ],
    center: (10.5, 7.0),
    word_spans: &[(0, 5), (21, 26), (27, 32)],
};

const C1_COMPACT: CellArt = CellArt {
    chars: &["█▄█ ████ ▀█▀   ", "█▀█ █·██▄▄█    ", "▀ ▀ ▀▀▀▀▀ ▀    "],
    classes: &["www.###wwwww...", "www.#o#wttww...", "www.###wwwww..."],
    center: (5.0, 1.5),
};

// ---------------------------------------------------------------------------
// C2 · Braille Ring
// ---------------------------------------------------------------------------

const C2_LARGE: CellArt = CellArt {
    chars: &[
        "█▄█ ⠀⣠⣶⡶⢶⣶⣶⡏⠀ █   ▀█▀",
        "█▀█ ⢸⡿⠁⠀⠀⠿⢿⡥⠤ █    █ ",
        "█ █ ⢸⣷⡀⠀⠀⢀⣾⡇⠀ █    █ ",
        "█ █ ⠀⠙⠿⠷⠾⠿⠋⠀⠀ █    █ ",
        "█ █           █▄▄  █ ",
    ],
    classes: &[
        "www..####hoc..w...www",
        "www.###..hhtt.w....w.",
        "w.w.###..###..w....w.",
        "w.w..######...w....w.",
        "w.w...........www..w.",
    ],
    center: (12.0, 2.0),
};

const C2_COMPACT: CellArt = CellArt {
    chars: &["█▄█ ⣰⢷⡄█ ▀█▀   ", "█▀█ ⣇⢀⡗█  █    ", "▀ ▀ ⠘⠟⠀▀▀ ▀    "],
    classes: &["www.##owwwww...", "www.##twwwww...", "www.##.wwwww..."],
    center: (5.5, 1.5),
};

// ---------------------------------------------------------------------------
// C3 · Terminal Crest
// ---------------------------------------------------------------------------

const C3_LARGE: HalfArt = HalfArt {
    // The 9x4 crest badge (columns 0-13) is unchanged from W-08. The wordmark
    // now uses five pixels per letter plus a one-pixel gap: 4 x 5 + 3 = 23
    // cells, starting after one blank column.
    rows: &[
        "...............#   #. ### .#    .#####",
        "...##hchc......## ##.#   #.#    .  #  ",
        ".####hhoh......# # #.#   #.#    .  #  ",
        "###..hchh......# # #.#   #.#    .  #  ",
        "##...hhhttt....#   #.#   #.#    .  #  ",
        "##....##.......#   #.#   #.#    .  #  ",
        "###..###.......#   #.#   #.#    .  #  ",
        ".######........#   #.#   #.#    .  #  ",
        "...............#   #.#   #.#    .  #  ",
        "...............#   #. ### .#####.  #  ",
    ],
    center: (4.0, 5.0),
    word_spans: &[(15, 20), (21, 26), (27, 32), (33, 38)],
};

const C3_COMPACT: CellArt = CellArt {
    chars: &["█▄█ █▀█ █   ▀█▀", "█ █ █·█ █    █ ", "▀ ▀ ▀▀▀ ▀▀▀  ▀ "],
    classes: &["www.hch.www.www", "www.hoh.www.www", "www.hhh.www.www"],
    center: (5.5, 1.5),
};

fn large_art(index: usize) -> Option<LargeArt> {
    match index {
        0 => Some(LargeArt::Half(C1_LARGE)),
        1 => Some(LargeArt::Cells(C2_LARGE)),
        2 => Some(LargeArt::Half(C3_LARGE)),
        _ => None,
    }
}

fn compact_art(index: usize) -> Option<&'static CellArt> {
    match index {
        0 => Some(&C1_COMPACT),
        1 => Some(&C2_COMPACT),
        2 => Some(&C3_COMPACT),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

fn pixel_color(class: Class, x: f32, y: f32, center: (f32, f32), theme: &Theme) -> Option<Color> {
    match class {
        Class::Empty => None,
        Class::Body => Some(gradient(theme, (y - center.1).atan2(x - center.0))),
        Class::Word => Some(theme.accent),
        Class::Head => Some(theme.warning),
        Class::Crest => Some(theme.info),
        Class::Tongue => Some(theme.danger),
        Class::Eye => Some(theme.text),
    }
}

/// Hue sweeps from green to cyan once around the ring. 16-colour terminals get
/// two fixed colours instead of a gradient.
fn gradient(theme: &Theme, angle: f32) -> Color {
    let turn = (angle / std::f32::consts::TAU + 0.5).rem_euclid(1.0);
    let hue = 0.30 + 0.22 * turn;
    match theme.mode {
        ColorMode::TrueColor => {
            let (r, g, b) = hsv_to_rgb(hue, 0.55, 0.92);
            Color::Rgb(r, g, b)
        }
        ColorMode::Ansi256 => {
            let (r, g, b) = hsv_to_rgb(hue, 0.55, 0.92);
            let level = |value: u8| (f32::from(value) / 255.0 * 5.0).round() as u8;
            Color::Indexed(16 + 36 * level(r) + 6 * level(g) + level(b))
        }
        ColorMode::Ansi16 => {
            if turn < 0.5 {
                theme.success
            } else {
                theme.accent
            }
        }
    }
}

fn hsv_to_rgb(hue: f32, saturation: f32, value: f32) -> (u8, u8, u8) {
    let h = hue.rem_euclid(1.0) * 6.0;
    let sector = h.floor();
    let fraction = h - sector;
    let p = value * (1.0 - saturation);
    let q = value * (1.0 - saturation * fraction);
    let t = value * (1.0 - saturation * (1.0 - fraction));
    let (r, g, b) = match sector as u32 % 6 {
        0 => (value, t, p),
        1 => (q, value, p),
        2 => (p, value, t),
        3 => (p, q, value),
        4 => (t, p, value),
        _ => (value, p, q),
    };
    (
        (r * 255.0).round() as u8,
        (g * 255.0).round() as u8,
        (b * 255.0).round() as u8,
    )
}

// ---------------------------------------------------------------------------
// Rasterisation
// ---------------------------------------------------------------------------

fn cells(index: usize, variant: Variant, theme: &Theme) -> Option<Vec<Vec<Cell>>> {
    match variant {
        Variant::Large => match large_art(index)? {
            LargeArt::Half(art) => Some(half_cells(&art, theme)),
            LargeArt::Cells(art) => Some(cell_art_cells(&art, theme)),
        },
        Variant::Compact => Some(cell_art_cells(compact_art(index)?, theme)),
    }
}

fn half_cells(art: &HalfArt, theme: &Theme) -> Vec<Vec<Cell>> {
    let rows: Vec<Vec<char>> = art.rows.iter().map(|row| row.chars().collect()).collect();
    let width = rows.iter().map(Vec::len).max().unwrap_or(0);
    let height = rows.len() / 2;
    let mut output = Vec::with_capacity(height);
    for y in 0..height {
        let mut line = Vec::with_capacity(width);
        for x in 0..width {
            let top = char_at(&rows, 2 * y, x);
            let bottom = char_at(&rows, 2 * y + 1, x);
            let word = art
                .word_spans
                .iter()
                .any(|(start, end)| (*start..*end).contains(&x));
            line.push(half_cell(top, bottom, x, y, art.center, word, theme));
        }
        output.push(line);
    }
    output
}

fn char_at(rows: &[Vec<char>], row: usize, column: usize) -> char {
    rows.get(row)
        .and_then(|line| line.get(column))
        .copied()
        .unwrap_or('.')
}

fn half_cell(
    top: char,
    bottom: char,
    x: usize,
    y: usize,
    center: (f32, f32),
    word: bool,
    theme: &Theme,
) -> Cell {
    let top_class = if word && class_of(top) == Class::Body {
        Class::Word
    } else {
        class_of(top)
    };
    let bottom_class = if word && class_of(bottom) == Class::Body {
        Class::Word
    } else {
        class_of(bottom)
    };
    let fx = x as f32 + 0.5;
    let top_color = pixel_color(top_class, fx, 2.0 * y as f32 + 0.5, center, theme);
    let bottom_color = pixel_color(bottom_class, fx, 2.0 * y as f32 + 1.5, center, theme);
    match (top_color, bottom_color) {
        (None, None) => Cell {
            ch: ' ',
            fg: None,
            bg: None,
        },
        (Some(color), None) => Cell {
            ch: '▀',
            fg: Some(color),
            bg: None,
        },
        (None, Some(color)) => Cell {
            ch: '▄',
            fg: Some(color),
            bg: None,
        },
        (Some(top), Some(bottom)) if top == bottom => Cell {
            ch: '█',
            fg: Some(top),
            bg: None,
        },
        (Some(top), Some(bottom)) => {
            if theme.mode == ColorMode::Ansi16 {
                let color = if top_class >= bottom_class {
                    top
                } else {
                    bottom
                };
                Cell {
                    ch: '█',
                    fg: Some(color),
                    bg: None,
                }
            } else {
                Cell {
                    ch: '▀',
                    fg: Some(top),
                    bg: Some(bottom),
                }
            }
        }
    }
}

fn cell_art_cells(art: &CellArt, theme: &Theme) -> Vec<Vec<Cell>> {
    art.chars
        .iter()
        .zip(art.classes)
        .enumerate()
        .map(|(y, (chars, classes))| {
            chars
                .chars()
                .zip(classes.chars())
                .enumerate()
                .map(|(x, (ch, class_ch))| {
                    let class = class_of(class_ch);
                    if ch == ' ' || class == Class::Empty {
                        return Cell {
                            ch: ' ',
                            fg: None,
                            bg: None,
                        };
                    }
                    Cell {
                        ch,
                        fg: pixel_color(class, x as f32 + 0.5, y as f32 + 0.5, art.center, theme),
                        bg: None,
                    }
                })
                .collect()
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/// Draw the compact header mark for candidate 1.
pub fn render(frame: &mut Frame, area: Rect, theme: &Theme) {
    render_candidate(frame, area, theme, 0, Variant::Compact);
}

/// Draw one candidate into `area`, clipping to the available rows and columns.
/// A compact mark narrower than its fifteen-cell slot falls back to plain text.
pub fn render_candidate(
    frame: &mut Frame,
    area: Rect,
    theme: &Theme,
    index: usize,
    variant: Variant,
) {
    if area.width == 0 || area.height == 0 {
        return;
    }
    let Some(lines) = cells(index, variant, theme) else {
        return;
    };
    let width = lines.first().map(Vec::len).unwrap_or(0);
    if width == 0 {
        return;
    }
    if variant == Variant::Compact && area.width < width as u16 {
        let text: String = "MOLT".chars().take(usize::from(area.width)).collect();
        frame.render_widget(Paragraph::new(Span::styled(text, theme.title())), area);
        return;
    }
    let mut rendered = Vec::with_capacity(usize::from(area.height));
    for row in lines.iter().take(usize::from(area.height)) {
        let mut spans = Vec::with_capacity(usize::from(area.width));
        for cell in row.iter().take(usize::from(area.width)) {
            if cell.ch == ' ' && cell.fg.is_none() && cell.bg.is_none() {
                spans.push(Span::raw(" "));
                continue;
            }
            let mut style = Style::new();
            if let Some(fg) = cell.fg {
                style = style.fg(fg);
            }
            if let Some(bg) = cell.bg {
                style = style.bg(bg);
            }
            spans.push(Span::styled(cell.ch.to_string(), style));
        }
        rendered.push(Line::from(spans));
    }
    frame.render_widget(Paragraph::new(rendered), area);
}

/// Size of the large art in terminal cells.
pub fn large_size(index: usize) -> (u16, u16) {
    match large_art(index) {
        Some(LargeArt::Half(art)) => (
            art.rows
                .iter()
                .map(|row| row.chars().count())
                .max()
                .unwrap_or(0) as u16,
            (art.rows.len() / 2) as u16,
        ),
        Some(LargeArt::Cells(art)) => (
            art.chars.first().map_or(0, |row| row.chars().count()) as u16,
            art.chars.len() as u16,
        ),
        None => (0, 0),
    }
}

// ---------------------------------------------------------------------------
// Non-interactive preview
// ---------------------------------------------------------------------------

/// Render one candidate as an ANSI string (no trailing reset beyond each line).
pub fn preview_ansi(index: usize, variant: Variant, mode: ColorMode) -> Result<String> {
    let theme = Theme::new(mode);
    let Some(lines) = cells(index, variant, &theme) else {
        bail!("logo candidate {} does not exist", index + 1);
    };
    let mut output = String::new();
    for row in &lines {
        let mut fg: Option<Color> = None;
        let mut bg: Option<Color> = None;
        for cell in row {
            if cell.fg != fg {
                if let Some(color) = cell.fg {
                    output.push_str(&fg_escape(mode, color));
                    fg = cell.fg;
                } else {
                    output.push_str("\x1b[39m");
                    fg = None;
                }
            }
            if cell.bg != bg {
                if let Some(color) = cell.bg {
                    output.push_str(&bg_escape(mode, color));
                    bg = cell.bg;
                } else {
                    output.push_str("\x1b[49m");
                    bg = None;
                }
            }
            output.push(cell.ch);
        }
        output.push_str("\x1b[0m\n");
    }
    Ok(output)
}

/// Print the `--logo` review sheet. `All` prints every candidate.
pub fn print(selection: Selection, mode: ColorMode) -> Result<()> {
    let theme = Theme::new(mode);
    let indices: Vec<usize> = match selection {
        Selection::All => (0..CANDIDATES.len()).collect(),
        Selection::One(number) => vec![usize::from(number.saturating_sub(1))],
    };

    let mut output = String::new();
    output.push_str(&paint(mode, theme.accent, "molt-tui · logo candidates"));
    output.push('\n');
    let _ = writeln!(
        output,
        "color={mode} · source of truth: crates/molt-tui/src/logo.rs · review assets: crates/molt-tui/assets/logo/"
    );
    output.push('\n');

    for index in indices {
        let Some(candidate) = CANDIDATES.get(index) else {
            bail!("logo candidate {} does not exist", index + 1);
        };
        output.push_str(&paint(
            mode,
            theme.accent,
            &format!("[{}] {}\n", index + 1, candidate.name),
        ));
        output.push_str(&paint(mode, theme.muted, candidate.tagline));
        output.push('\n');
        for line in preview_ansi(index, Variant::Large, mode)?.lines() {
            output.push_str("  ");
            output.push_str(line);
            output.push('\n');
        }
        output.push_str(&paint(
            mode,
            theme.muted,
            &format!("  compact {COMPACT_WIDTH}x{COMPACT_HEIGHT} (header slot)\n"),
        ));
        for line in preview_ansi(index, Variant::Compact, mode)?.lines() {
            output.push_str("  ");
            output.push_str(line);
            output.push('\n');
        }
        output.push('\n');
    }

    let mut stdout = std::io::stdout();
    stdout.write_all(output.as_bytes())?;
    stdout.flush()?;
    Ok(())
}

fn paint(mode: ColorMode, color: Color, text: &str) -> String {
    format!("{}{}\x1b[0m", fg_escape(mode, color), text)
}

fn fg_escape(mode: ColorMode, color: Color) -> String {
    if mode == ColorMode::Ansi16 {
        return format!("\x1b[{}m", basic_ansi_code(color));
    }
    match color {
        Color::Indexed(index) => format!("\x1b[38;5;{index}m"),
        Color::Rgb(red, green, blue) => format!("\x1b[38;2;{red};{green};{blue}m"),
        other => format!("\x1b[{}m", basic_ansi_code(other)),
    }
}

fn bg_escape(mode: ColorMode, color: Color) -> String {
    if mode == ColorMode::Ansi16 {
        return format!("\x1b[{}m", basic_ansi_bg_code(color));
    }
    match color {
        Color::Indexed(index) => format!("\x1b[48;5;{index}m"),
        Color::Rgb(red, green, blue) => format!("\x1b[48;2;{red};{green};{blue}m"),
        other => format!("\x1b[{}m", basic_ansi_bg_code(other)),
    }
}

const fn basic_ansi_code(color: Color) -> u8 {
    match color {
        Color::Black => 30,
        Color::Red => 31,
        Color::Green => 32,
        Color::Yellow => 33,
        Color::Blue => 34,
        Color::Magenta => 35,
        Color::Cyan => 36,
        Color::Gray => 37,
        Color::DarkGray => 90,
        Color::LightRed => 91,
        Color::LightGreen => 92,
        Color::LightYellow => 93,
        Color::LightBlue => 94,
        Color::LightMagenta => 95,
        Color::LightCyan => 96,
        Color::White => 97,
        _ => 39,
    }
}

const fn basic_ansi_bg_code(color: Color) -> u8 {
    match basic_ansi_code(color) {
        30..=37 => basic_ansi_code(color) + 10,
        90..=97 => basic_ansi_code(color) + 10,
        39 => 49,
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_logo_selection() {
        assert_eq!("all".parse::<Selection>().unwrap(), Selection::All);
        assert_eq!("1".parse::<Selection>().unwrap(), Selection::One(1));
        assert_eq!("3".parse::<Selection>().unwrap(), Selection::One(3));
        assert!("4".parse::<Selection>().is_err());
    }

    #[test]
    fn ansi16_preview_uses_basic_sgr_codes() {
        assert_eq!(basic_ansi_code(Color::Cyan), 36);
        assert_eq!(basic_ansi_code(Color::DarkGray), 90);
        assert_eq!(basic_ansi_code(Color::Rgb(1, 2, 3)), 39);
        assert_eq!(basic_ansi_bg_code(Color::Cyan), 46);
        assert_eq!(basic_ansi_bg_code(Color::DarkGray), 100);
    }

    #[test]
    fn ansi16_preview_never_emits_rgb_or_256_sequences() {
        for index in 0..CANDIDATES.len() {
            for variant in [Variant::Compact, Variant::Large] {
                let preview = preview_ansi(index, variant, ColorMode::Ansi16).unwrap();
                assert!(!preview.contains("38;5;"), "{index}: {preview}");
                assert!(!preview.contains("48;5;"), "{index}: {preview}");
                assert!(!preview.contains("38;2;"), "{index}: {preview}");
                assert!(!preview.contains("48;2;"), "{index}: {preview}");
            }
        }
    }

    #[test]
    fn ansi256_preview_never_emits_rgb_sequences() {
        for index in 0..CANDIDATES.len() {
            let preview = preview_ansi(index, Variant::Large, ColorMode::Ansi256).unwrap();
            assert!(!preview.contains("38;2;"), "{index}: {preview}");
            assert!(!preview.contains("48;2;"), "{index}: {preview}");
        }
    }

    #[test]
    fn compact_stays_inside_the_header_slot() {
        for index in 0..CANDIDATES.len() {
            let theme = Theme::new(ColorMode::Ansi256);
            let lines = cells(index, Variant::Compact, &theme).unwrap();
            assert_eq!(lines.len(), usize::from(COMPACT_HEIGHT), "{index}");
            for row in lines {
                assert_eq!(row.len(), usize::from(COMPACT_WIDTH), "{index}");
            }
        }
    }

    #[test]
    fn c3_compact_is_fifteen_by_three() {
        let theme = Theme::new(ColorMode::Ansi256);
        let lines = cells(2, Variant::Compact, &theme).unwrap();
        assert_eq!(lines.len(), 3);
        for row in &lines {
            assert_eq!(row.len(), 15);
        }
        assert_eq!(large_size(2), (38, 5));
    }

    #[test]
    fn c3_wordmark_is_five_pixels_per_letter_with_single_cell_gaps() {
        let spans = C3_LARGE.word_spans;
        assert_eq!(spans.len(), 4);
        for (start, end) in spans {
            assert_eq!(end - start, 5, "letter span {start}..{end}");
        }
        for pair in spans.windows(2) {
            assert_eq!(pair[1].0 - pair[0].1, 1, "letters need a one-cell gap");
        }
        let width = spans[3].1 - spans[0].0;
        assert_eq!(width, 23);
        assert!((20..=24).contains(&width), "wordmark is {width} cells");
        for row in C3_LARGE.rows {
            assert_eq!(row.chars().count(), spans[3].1, "art row width");
        }
    }

    #[test]
    fn c3_badge_keeps_the_w08_crest_eye_and_tongue() {
        // Columns 0-13 are the untouched 9x4 badge; only the wordmark grew.
        let crest: Vec<String> = C3_LARGE
            .rows
            .iter()
            .map(|row| row.chars().take(14).collect())
            .collect();
        assert_eq!(
            crest,
            [
                "..............",
                "...##hchc.....",
                ".####hhoh.....",
                "###..hchh.....",
                "##...hhhttt...",
                "##....##......",
                "###..###......",
                ".######.......",
                "..............",
                "..............",
            ]
        );
    }

    #[test]
    fn large_variants_are_five_to_seven_rows() {
        for index in 0..CANDIDATES.len() {
            let (width, height) = large_size(index);
            assert!((5..=7).contains(&height), "{index} is {height} rows");
            assert!((20..=40).contains(&width), "{index} is {width} cells");
        }
    }

    #[test]
    fn every_candidate_has_eye_tongue_and_crest() {
        for index in 0..CANDIDATES.len() {
            let mut eye = 0;
            let mut tongue = 0;
            let mut crest = 0;
            // Count from the raw art so a colour change cannot hide a feature.
            match large_art(index).unwrap() {
                LargeArt::Half(art) => {
                    for row in art.rows {
                        eye += row.chars().filter(|ch| *ch == 'o').count();
                        tongue += row.chars().filter(|ch| *ch == 't').count();
                        crest += row.chars().filter(|ch| *ch == 'c').count();
                    }
                }
                LargeArt::Cells(art) => {
                    for row in art.classes {
                        eye += row.chars().filter(|ch| *ch == 'o').count();
                        tongue += row.chars().filter(|ch| *ch == 't').count();
                        crest += row.chars().filter(|ch| *ch == 'c').count();
                    }
                }
            }
            assert!(eye >= 1, "candidate {index} has no eye");
            assert!(tongue >= 1, "candidate {index} has no tongue");
            assert!(crest >= 1, "candidate {index} has no crest");
        }
    }

    #[test]
    fn every_art_grid_is_rectangular() {
        for index in 0..CANDIDATES.len() {
            match large_art(index).unwrap() {
                LargeArt::Half(art) => {
                    let width = art.rows[0].chars().count();
                    for row in art.rows {
                        assert_eq!(row.chars().count(), width, "candidate {index}");
                    }
                    assert_eq!(art.rows.len() % 2, 0, "candidate {index}");
                }
                LargeArt::Cells(art) => {
                    for (chars, classes) in art.chars.iter().zip(art.classes) {
                        assert_eq!(
                            chars.chars().count(),
                            classes.chars().count(),
                            "candidate {index}"
                        );
                    }
                }
            }
            let compact = compact_art(index).unwrap();
            for (chars, classes) in compact.chars.iter().zip(compact.classes) {
                assert_eq!(chars.chars().count(), classes.chars().count());
                assert_eq!(chars.chars().count(), usize::from(COMPACT_WIDTH));
            }
            assert_eq!(
                compact.chars.len(),
                usize::from(COMPACT_HEIGHT),
                "candidate {index}"
            );
        }
    }

    #[test]
    fn preview_lists_all_three_titles() {
        let titles = CANDIDATES.map(|candidate| candidate.name);
        assert_eq!(
            titles,
            ["Ouroboros Pixel", "Braille Ring", "Terminal Crest"]
        );
    }
}
