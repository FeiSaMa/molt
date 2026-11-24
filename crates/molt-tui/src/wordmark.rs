//! Block-pixel `MOLT` wordmark.
//!
//! The mark is drawn with full block cells: four thick-stroke letters on a
//! 10x7 pixel grid for `M` and 8x7 for `O`/`L`/`T` (large), or a 3x2 pixel
//! grid (compact), with a one-cell drop shadow. Every stroke is two cells
//! wide, letters are separated by one blank column, and the large mark is
//! 38x8 cells including its shadow. It deliberately contains no mascot, no
//! snake, and no braille. Colours come
//! from [`crate::theme::Theme`], so the mark degrades to basic ANSI
//! white/dark-gray on a sixteen-colour terminal and never paints a background.

use ratatui::{
    layout::Rect,
    style::Style,
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use crate::theme::Theme;

/// Letter cells are seven rows tall and, except for `M`, eight columns wide.
/// `M` is ten columns wide: two-cell stems in columns 0-1 and 8-9 and a wider
/// two-cell V; `O` keeps a 4x3 square counter; `L` has an eight-cell foot;
/// `T` has a full-width bar. Letters are separated by one blank column.
const LETTER_WIDTH: usize = 8;
const M_WIDTH: usize = 10;
const LETTER_HEIGHT: usize = 7;
const LETTER_GAP: usize = 1;

const M_LARGE: [&str; LETTER_HEIGHT] = [
    "██      ██",
    "███    ███",
    "████  ████",
    "██ ████ ██",
    "██  ██  ██",
    "██      ██",
    "██      ██",
];
const O_LARGE: [&str; LETTER_HEIGHT] = [
    "████████",
    "████████",
    "██    ██",
    "██    ██",
    "██    ██",
    "████████",
    "████████",
];
const L_LARGE: [&str; LETTER_HEIGHT] = [
    "██      ",
    "██      ",
    "██      ",
    "██      ",
    "██      ",
    "████████",
    "████████",
];
const T_LARGE: [&str; LETTER_HEIGHT] = [
    "████████",
    "████████",
    "   ██   ",
    "   ██   ",
    "   ██   ",
    "   ██   ",
    "   ██   ",
];

const M_COMPACT: [&str; 2] = ["█ █", "███"];
const O_COMPACT: [&str; 2] = ["███", "█ █"];
const L_COMPACT: [&str; 2] = ["█  ", "███"];
const T_COMPACT: [&str; 2] = ["███", " █ "];

/// Large mark including the shadow cell: 37 body columns + 1 shadow, 7 body
/// rows + 1 shadow row.
const LARGE_BODY_WIDTH: usize = M_WIDTH + 3 * LETTER_WIDTH + 3 * LETTER_GAP;
pub const LARGE_WIDTH: u16 = (LARGE_BODY_WIDTH + 1) as u16;
pub const LARGE_HEIGHT: u16 = (LETTER_HEIGHT + 1) as u16;
/// Compact mark including the shadow cell.
pub const COMPACT_WIDTH: u16 = 16;
pub const COMPACT_HEIGHT: u16 = 3;

/// Size of the large mark in cells, including its one-cell drop shadow:
/// 38 columns (`M` 10 + `O`/`L`/`T` 8 each + three gaps + shadow) x 8 rows.
pub fn large_size() -> (u16, u16) {
    (LARGE_WIDTH, LARGE_HEIGHT)
}

pub fn compact_size() -> (u16, u16) {
    (COMPACT_WIDTH, COMPACT_HEIGHT)
}

/// Draw the large startup mark. Below the mark's size the widget falls back to
/// a plain `MOLT` text so the frame never clips a glyph.
pub fn render_large(frame: &mut Frame, area: Rect, theme: &Theme) {
    render_grid(frame, area, theme, &large_grid());
}

/// Draw the compact mark. The shell header is plain text, so this is retained
/// for previews and tests only.
pub fn render_compact(frame: &mut Frame, area: Rect, theme: &Theme) {
    render_grid(frame, area, theme, &compact_grid());
}

fn large_grid() -> Vec<Vec<bool>> {
    compose(
        &[&M_LARGE[..], &O_LARGE[..], &L_LARGE[..], &T_LARGE[..]],
        LETTER_GAP,
    )
}

fn compact_grid() -> Vec<Vec<bool>> {
    compose(
        &[
            &M_COMPACT[..],
            &O_COMPACT[..],
            &L_COMPACT[..],
            &T_COMPACT[..],
        ],
        1,
    )
}

/// Join fixed-width letters horizontally with `gap` blank columns.
fn compose(letters: &[&[&str]], gap: usize) -> Vec<Vec<bool>> {
    let height = letters.first().map_or(0, |letter| letter.len());
    let width = letters
        .iter()
        .map(|letter| letter.first().map_or(0, |row| row.chars().count()))
        .sum::<usize>()
        + gap * letters.len().saturating_sub(1);
    let mut grid = vec![vec![false; width]; height];
    let mut offset = 0;
    for letter in letters {
        let letter_width = letter.first().map_or(0, |row| row.chars().count());
        for (y, row) in letter.iter().enumerate() {
            for (x, character) in row.chars().enumerate() {
                if character == '█' {
                    grid[y][offset + x] = true;
                }
            }
        }
        offset += letter_width + gap;
    }
    grid
}

fn render_grid(frame: &mut Frame, area: Rect, theme: &Theme, body: &[Vec<bool>]) {
    if area.width == 0 || area.height == 0 || body.is_empty() {
        return;
    }
    let width = body[0].len();
    let height = body.len();
    let needed = (width.saturating_add(1), height.saturating_add(1));
    if usize::from(area.width) < needed.0 || usize::from(area.height) < needed.1 {
        let text: String = "MOLT".chars().take(usize::from(area.width)).collect();
        frame.render_widget(Paragraph::new(Span::styled(text, theme.title())), area);
        return;
    }

    // Drop shadow: every body pixel paints one cell to its right and one cell
    // below. Body pixels win over shadow pixels when they overlap.
    let mut shadow = vec![vec![false; width + 1]; height + 1];
    for (y, row) in body.iter().enumerate() {
        for (x, filled) in row.iter().enumerate() {
            if *filled {
                shadow[y][x + 1] = true;
                shadow[y + 1][x] = true;
            }
        }
    }

    let body_style = Style::new().fg(theme.text);
    let shadow_style = Style::new().fg(theme.muted);
    let mut lines = Vec::with_capacity(height + 1);
    for y in 0..=height {
        let mut spans = Vec::with_capacity(width + 1);
        for x in 0..=width {
            if y < height && x < width && body[y][x] {
                spans.push(Span::styled("█", body_style));
            } else if shadow[y][x] {
                spans.push(Span::styled("█", shadow_style));
            } else {
                spans.push(Span::raw(" "));
            }
        }
        lines.push(Line::from(spans));
    }
    frame.render_widget(Paragraph::new(lines), area);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme::ColorMode;
    use ratatui::{backend::TestBackend, buffer::Buffer, Terminal};

    fn render_buffer(width: u16, height: u16, variant: &str) -> Buffer {
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).expect("test backend is infallible");
        let theme = Theme::new(ColorMode::Ansi16);
        terminal
            .draw(|frame| match variant {
                "large" => render_large(frame, frame.area(), &theme),
                _ => render_compact(frame, frame.area(), &theme),
            })
            .expect("test backend is infallible");
        terminal.backend().buffer().clone()
    }

    #[test]
    fn large_mark_is_38x8_including_shadow() {
        let grid = large_grid();
        assert_eq!(grid.len(), LETTER_HEIGHT);
        assert_eq!(grid[0].len(), 37);
        assert_eq!(large_size(), (38, 8));
        assert!(
            large_size().0 <= 40,
            "large mark must stay at most 40 cells wide, got {}",
            large_size().0
        );
        let buffer = render_buffer(LARGE_WIDTH, LARGE_HEIGHT, "large");
        assert!(crate::app::buffer_overflow(&buffer).is_none());
    }

    #[test]
    fn m_is_ten_by_seven_and_the_other_letters_are_eight_by_seven() {
        assert_eq!(M_LARGE.len(), LETTER_HEIGHT);
        for row in M_LARGE {
            assert_eq!(row.chars().count(), M_WIDTH, "{row:?}");
        }
        for (name, letter) in [("O", O_LARGE), ("L", L_LARGE), ("T", T_LARGE)] {
            assert_eq!(letter.len(), LETTER_HEIGHT, "{name}");
            for row in letter {
                assert_eq!(row.chars().count(), LETTER_WIDTH, "{name}: {row:?}");
            }
        }
        assert_eq!(LARGE_BODY_WIDTH, 37); // 10 + 8 + 8 + 8 + 3 gaps
        assert_eq!(LARGE_WIDTH, 38); // 37 body columns + 1 shadow column
        assert_eq!(LARGE_HEIGHT, 8); // 7 body rows + 1 shadow row
    }

    #[test]
    fn m_stems_and_v_are_two_cells_wide() {
        // The left and right stems are two cells each, with a clear gap between
        // them: the old five-to-seven-column squeeze is gone.
        for row in [M_LARGE[0], M_LARGE[5], M_LARGE[6]] {
            let cells: Vec<char> = row.chars().collect();
            assert_eq!(&cells[..2], &['█', '█'], "{row:?}");
            assert_eq!(&cells[8..], &['█', '█'], "{row:?}");
            assert!(
                cells[2..8].iter().all(|character| *character == ' '),
                "{row:?}"
            );
        }
        // The middle V narrows through a four-cell crossing to a two-cell
        // stem at columns 4-5.
        let cells: Vec<char> = M_LARGE[3].chars().collect();
        assert_eq!(&cells[3..7], &['█', '█', '█', '█']);
        assert_eq!(cells[2], ' ');
        assert_eq!(cells[7], ' ');
        let cells: Vec<char> = M_LARGE[4].chars().collect();
        assert_eq!(&cells[4..6], &['█', '█']);
        assert!(cells[2..4].iter().all(|character| *character == ' '));
        assert!(cells[6..8].iter().all(|character| *character == ' '));
        assert!(M_LARGE[1].starts_with("███"));
        assert!(M_LARGE[1].ends_with("███"));
    }

    #[test]
    fn l_foot_meets_the_letter_gap_without_extra_blanks() {
        let grid = large_grid();
        // M(0-9) gap(10) O(11-18) gap(19) L(20-27) gap(28) T(29-36).
        for y in [5, 6] {
            assert!(
                grid[y][20..28].iter().all(|filled| *filled),
                "L foot must fill columns 20-27 on row {y}"
            );
            assert!(!grid[y][28], "L foot leaks into the letter gap on row {y}");
        }
        for (y, row) in grid.iter().enumerate() {
            assert!(!row[28], "column 28 must stay blank on row {y}");
        }
    }

    #[test]
    fn large_mark_fits_eighty_by_twenty_four() {
        let buffer = render_buffer(80, 24, "large");
        assert!(crate::app::buffer_overflow(&buffer).is_none());
        let text = crate::app::buffer_lines(&buffer).join("\n");
        assert!(text.contains('█'), "80x24 render is empty");
    }

    #[test]
    fn compact_mark_is_16x3_including_shadow() {
        let grid = compact_grid();
        assert_eq!(grid.len(), 2);
        assert_eq!(grid[0].len(), 15);
        assert_eq!(compact_size(), (16, 3));
        let buffer = render_buffer(COMPACT_WIDTH, COMPACT_HEIGHT, "compact");
        assert!(crate::app::buffer_overflow(&buffer).is_none());
    }

    #[test]
    fn letters_have_thick_strokes_and_a_square_counter() {
        // `O` is a two-cell ring around a 4x3 square counter.
        for row in &O_LARGE[..2] {
            assert!(row.chars().all(|character| character == '█'), "{row:?}");
        }
        for row in &O_LARGE[5..] {
            assert!(row.chars().all(|character| character == '█'), "{row:?}");
        }
        for row in &O_LARGE[2..5] {
            let cells: Vec<char> = row.chars().collect();
            assert_eq!(&cells[..2], &['█', '█']);
            assert_eq!(&cells[6..], &['█', '█']);
            assert!(cells[2..6].iter().all(|character| *character == ' '));
        }
        // `L` is a two-cell stem with an eight-cell foot; `T` is a full-width,
        // two-row bar over a two-cell stem.
        for row in &L_LARGE[..5] {
            let cells: Vec<char> = row.chars().collect();
            assert_eq!(&cells[..2], &['█', '█'], "{row:?}");
            assert!(cells[2..].iter().all(|character| *character == ' '));
        }
        for row in &L_LARGE[5..] {
            assert_eq!(*row, "████████", "L foot must fill every column: {row:?}");
        }
        for row in &T_LARGE[..2] {
            assert!(row.chars().all(|character| character == '█'), "{row:?}");
        }
        for row in &T_LARGE[2..] {
            let cells: Vec<char> = row.chars().collect();
            assert_eq!(&cells[3..5], &['█', '█']);
            assert!(cells[..3].iter().all(|character| *character == ' '));
            assert!(cells[5..].iter().all(|character| *character == ' '));
        }
    }

    #[test]
    fn shadow_is_offset_one_cell_right_and_down() {
        // `render_buffer` uses the sixteen-colour palette: white body, dark-gray
        // shadow, so the assertion also covers the ANSI-16 degradation path.
        let theme = Theme::new(ColorMode::Ansi16);
        assert_eq!(theme.text, ratatui::style::Color::White);
        assert_eq!(theme.muted, ratatui::style::Color::DarkGray);

        let buffer = render_buffer(LARGE_WIDTH, LARGE_HEIGHT, "large");
        let mut body = 0;
        let mut shadow = 0;
        for y in 0..buffer.area.height {
            for x in 0..buffer.area.width {
                let cell = &buffer[(x, y)];
                if cell.symbol() == "█" {
                    if cell.fg == theme.text {
                        body += 1;
                    } else if cell.fg == theme.muted {
                        shadow += 1;
                    }
                }
            }
        }
        assert!(body > 0, "body pixels are missing");
        assert!(shadow > 0, "shadow pixels are missing");
        // The right-most column and bottom row carry shadow pixels, and the
        // shadow is exactly one cell away from the body (not a full outline).
        assert!(buffer[(LARGE_WIDTH - 1, 0)].fg == theme.muted);
        assert!(buffer[(0, LARGE_HEIGHT - 1)].fg == theme.muted);
        assert_eq!(buffer[(1, 0)].fg, theme.text);
        assert_eq!(buffer[(2, 0)].fg, theme.muted);
        assert_eq!(buffer[(3, 0)].symbol(), " ");
    }

    #[test]
    fn narrow_area_falls_back_to_plain_text() {
        for variant in ["compact", "large"] {
            let backend = TestBackend::new(8, 3);
            let mut terminal = Terminal::new(backend).expect("test backend is infallible");
            let theme = Theme::new(ColorMode::Ansi16);
            terminal
                .draw(|frame| match variant {
                    "large" => render_large(frame, frame.area(), &theme),
                    _ => render_compact(frame, frame.area(), &theme),
                })
                .expect("test backend is infallible");
            let text = crate::app::buffer_lines(terminal.backend().buffer()).join("\n");
            assert!(text.contains("MOLT"), "{variant}: {text}");
            assert!(
                !text.contains('█') && !text.contains('▀') && !text.contains('▄'),
                "{variant} narrow fallback still draws blocks: {text}"
            );
        }
    }
}
