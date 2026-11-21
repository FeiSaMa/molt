//! Full-frame rendering tests using Ratatui's `TestBackend`.

use molt_tui::theme::Theme;
use molt_tui::{app, logo, mascot, App, ColorMode, Screen};
use ratatui::{backend::TestBackend, style::Color, Terminal};
use std::process::Command;
use unicode_width::UnicodeWidthStr;

fn render_app(application: &App, width: u16, height: u16) -> Vec<String> {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::new(backend).expect("test backend is infallible");
    terminal
        .draw(|frame| application.draw(frame))
        .expect("test backend is infallible");
    let buffer = terminal.backend().buffer();
    assert!(
        app::buffer_overflow(buffer).is_none(),
        "{} frame at {width}x{height} drew past the right edge",
        application.screen.title()
    );
    app::buffer_lines(buffer)
}

fn render(screen: Screen, width: u16, height: u16) -> Vec<String> {
    let application = App::with_screen(ColorMode::Ansi256, screen);
    render_app(&application, width, height)
}

fn run_binary(args: &[&str]) -> String {
    let output = Command::new(env!("CARGO_BIN_EXE_molt-tui"))
        .args(args)
        .output()
        .expect("molt-tui binary should run");
    assert!(
        output.status.success(),
        "molt-tui {args:?} failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).expect("stdout is utf-8")
}

#[test]
fn chat_frame_at_120x40_has_shell_and_sidebar() {
    let lines = render(Screen::Chat, 120, 40);
    let text = lines.join("\n");
    for marker in ["molt", "SESSIONS", "MCP", "context", ">"] {
        assert!(text.contains(marker), "missing `{marker}` in:\n{text}");
    }
    assert!(
        text.contains("auto/config-B"),
        "sidebar fork missing:\n{text}"
    );
    assert!(
        text.contains("cargo test -p molt-tui"),
        "mock chat missing:\n{text}"
    );
}

#[test]
fn every_screen_at_80x24_stays_within_80_columns() {
    for screen in Screen::ALL {
        let lines = render(screen, 80, 24);
        assert_eq!(lines.len(), 24);
        for (row, line) in lines.iter().enumerate() {
            let width = UnicodeWidthStr::width(line.as_str());
            assert!(
                width <= 80,
                "{} row {row} is {width} cells wide: {line:?}",
                screen.title()
            );
        }
    }
}

#[test]
fn each_main_screen_renders_its_title_and_mock_content() {
    for screen in [
        Screen::Tree,
        Screen::Settings,
        Screen::Diff,
        Screen::Approval,
    ] {
        let lines = render(screen, 120, 40);
        let text = lines.join("\n");
        for marker in app::screen_markers(screen) {
            assert!(
                text.contains(marker),
                "{} missing `{marker}` in:\n{text}",
                screen.title()
            );
        }
    }
}

#[test]
fn startup_splash_renders_large_art_at_both_sizes() {
    // 38 columns is the exact boundary: the 38-wide mark (shadow included)
    // must still draw there, and 37 columns must fall back.
    for (width, height) in [(120, 40), (80, 24), (38, 24)] {
        let lines = render(Screen::Startup, width, height);
        let text = lines.join("\n");
        for marker in app::screen_markers(Screen::Startup) {
            assert!(
                text.contains(marker),
                "startup {width}x{height} missing `{marker}` in:\n{text}"
            );
        }
        assert!(
            text.contains('█'),
            "startup {width}x{height} lost the large block wordmark:\n{text}"
        );
        assert!(
            text.contains("press any key"),
            "startup hint missing:\n{text}"
        );
    }
}

#[test]
fn narrow_startup_falls_back_to_plain_text() {
    for (width, height) in [(37, 24), (40, 10), (30, 20)] {
        let text = render(Screen::Startup, width, height).join("\n");
        assert!(text.contains("MOLT"), "{width}x{height}:\n{text}");
        assert!(
            !text.contains('█') && !text.contains('▀') && !text.contains('▄'),
            "startup {width}x{height} still draws blocks:\n{text}"
        );
    }
}

#[test]
fn startup_has_no_ouroboros_mascot_or_legacy_candidates() {
    let application = App::new(ColorMode::Ansi256);
    for (width, height) in [(120, 40), (80, 24), (40, 10)] {
        let text = render_app(&application, width, height).join("\n");
        for banned in [
            "ouroboros",
            "Ouroboros",
            "Terminal Crest",
            "Braille Ring",
            "╭",
            "╰",
        ] {
            assert!(
                !text.contains(banned),
                "startup {width}x{height} still shows `{banned}`:\n{text}"
            );
        }
        assert!(text.contains("v3 · mock"), "{text}");
        assert!(text.contains("MOLT"), "{text}");
    }
}

#[test]
fn logo_candidate_flag_does_not_change_the_shell() {
    // `--logo` only feeds the legacy preview; the startup mark is fixed.
    let first = render_app(&App::with_options(ColorMode::Ansi256, 0), 120, 40).join("\n");
    let last = render_app(&App::with_options(ColorMode::Ansi256, 2), 120, 40).join("\n");
    assert_eq!(first, last);
}

#[test]
fn every_candidate_renders_in_every_colour_mode() {
    for mode in [ColorMode::Ansi16, ColorMode::Ansi256, ColorMode::TrueColor] {
        for index in 0..logo::CANDIDATES.len() {
            for variant in [logo::Variant::Compact, logo::Variant::Large] {
                let theme = Theme::new(mode);
                let (width, height) = match variant {
                    logo::Variant::Compact => (logo::COMPACT_WIDTH, logo::COMPACT_HEIGHT),
                    logo::Variant::Large => logo::large_size(index),
                };
                let backend = TestBackend::new(width, height);
                let mut terminal = Terminal::new(backend).expect("test backend is infallible");
                terminal
                    .draw(|frame| {
                        logo::render_candidate(frame, frame.area(), &theme, index, variant);
                    })
                    .expect("test backend is infallible");
                let buffer = terminal.backend().buffer();
                assert!(
                    app::buffer_overflow(buffer).is_none(),
                    "candidate {index} {variant:?} {mode} overflowed {width}x{height}"
                );
                if mode != ColorMode::TrueColor {
                    assert_no_rgb(buffer, "logo");
                }
            }
        }
    }
}

#[test]
fn compact_mark_fits_the_fifteen_by_three_header_slot() {
    let theme = Theme::new(ColorMode::Ansi256);
    for index in 0..logo::CANDIDATES.len() {
        let backend = TestBackend::new(15, 3);
        let mut terminal = Terminal::new(backend).expect("test backend is infallible");
        terminal
            .draw(|frame| {
                logo::render_candidate(frame, frame.area(), &theme, index, logo::Variant::Compact);
            })
            .expect("test backend is infallible");
        let buffer = terminal.backend().buffer();
        assert!(app::buffer_overflow(buffer).is_none(), "candidate {index}");
    }

    // Narrower than the mark: fall back to a plain wordmark instead of clipping
    // the art mid-glyph.
    let backend = TestBackend::new(8, 3);
    let mut terminal = Terminal::new(backend).expect("test backend is infallible");
    terminal
        .draw(|frame| {
            logo::render_candidate(frame, frame.area(), &theme, 0, logo::Variant::Compact);
        })
        .expect("test backend is infallible");
    let text = app::buffer_lines(terminal.backend().buffer()).join("\n");
    assert!(text.contains("MOLT"), "compact fallback missing: {text}");
}

#[test]
fn header_is_plain_text_without_block_glyphs() {
    for width in [40, 79, 80, 99, 120] {
        let lines = render(Screen::Chat, width, 24);
        let header = &lines[0];
        assert!(
            header.contains("molt"),
            "header at {width} columns lost the label: {header:?}"
        );
        assert!(
            !header.contains('█') && !header.contains('▀') && !header.contains('▄'),
            "header at {width} columns still draws block art: {header:?}"
        );
    }
}

#[test]
fn header_truncates_instead_of_drawing_an_icon() {
    for width in [20, 12, 10] {
        let lines = render(Screen::Chat, width, 24);
        let header = &lines[0];
        assert!(
            header.contains("molt"),
            "header at {width} columns lost the label: {header:?}"
        );
        assert!(
            !header.contains('█') && !header.contains('▀') && !header.contains('▄'),
            "header at {width} columns still draws block art: {header:?}"
        );
    }
}

#[test]
fn ansi16_logo_never_uses_rgb_or_indexed_cells() {
    let theme = Theme::new(ColorMode::Ansi16);
    for index in 0..logo::CANDIDATES.len() {
        let (width, height) = logo::large_size(index);
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).expect("test backend is infallible");
        terminal
            .draw(|frame| {
                logo::render_candidate(frame, frame.area(), &theme, index, logo::Variant::Large);
            })
            .expect("test backend is infallible");
        let buffer = terminal.backend().buffer();
        for y in 0..buffer.area.height {
            for x in 0..buffer.area.width {
                let cell = &buffer[(x, y)];
                assert!(
                    !matches!(cell.fg, Color::Rgb(..) | Color::Indexed(..)),
                    "candidate {index} cell ({x},{y}) fg {:?}",
                    cell.fg
                );
                assert!(
                    !matches!(cell.bg, Color::Rgb(..) | Color::Indexed(..)),
                    "candidate {index} cell ({x},{y}) bg {:?}",
                    cell.bg
                );
            }
        }
    }
}

#[test]
fn mascot_renders_three_distinct_frames_without_overflow() {
    let theme = Theme::new(ColorMode::Ansi256);
    let mut rendered = Vec::new();
    for index in 0..mascot::FRAME_COUNT {
        let backend = TestBackend::new(mascot::WIDTH, mascot::HEIGHT);
        let mut terminal = Terminal::new(backend).expect("test backend is infallible");
        terminal
            .draw(|frame| mascot::render_frame(frame, frame.area(), &theme, index))
            .expect("test backend is infallible");
        let buffer = terminal.backend().buffer();
        assert!(app::buffer_overflow(buffer).is_none(), "frame {index}");
        rendered.push(app::buffer_lines(buffer));
    }
    assert_ne!(rendered[0], rendered[1], "blink frame must differ");
    assert_ne!(rendered[0], rendered[2], "tongue frame must differ");
    assert_eq!(rendered[0].len(), usize::from(mascot::HEIGHT));
    assert_eq!(
        UnicodeWidthStr::width(rendered[0][0].as_str()),
        usize::from(mascot::WIDTH)
    );
}

#[test]
fn mascot_policy_hides_on_narrow_or_dumb_terminals() {
    let env = mascot::Environment::default();
    assert_eq!(
        mascot::policy(70, ColorMode::TrueColor, env, false, false),
        mascot::MascotMode::Hidden
    );
    assert_eq!(
        mascot::policy(
            120,
            ColorMode::TrueColor,
            mascot::Environment {
                dumb: true,
                no_color: false
            },
            false,
            false
        ),
        mascot::MascotMode::Hidden
    );
    assert_eq!(
        mascot::policy(120, ColorMode::Ansi16, env, false, false),
        mascot::MascotMode::Static
    );
    assert_eq!(
        mascot::policy(120, ColorMode::TrueColor, env, false, false),
        mascot::MascotMode::Animate
    );
}

#[test]
fn help_overlay_renders_over_chat() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    application.show_help = true;
    let lines = render_app(&application, 120, 40);
    let text = lines.join("\n");
    for marker in ["HELP", "quit", "approval screen"] {
        assert!(text.contains(marker), "missing `{marker}` in:\n{text}");
    }
}

#[test]
fn default_theme_uses_reset_backgrounds_in_every_state() {
    for screen in Screen::ALL {
        assert_reset_background(&App::with_screen(ColorMode::TrueColor, screen), 120, 40);
    }

    let mut help = App::with_screen(ColorMode::TrueColor, Screen::Chat);
    help.show_help = true;
    assert_reset_background(&help, 120, 40);

    let mut detail = App::with_screen(ColorMode::TrueColor, Screen::Settings);
    detail.select_config(1);
    detail.settings.detail_open = true;
    assert_reset_background(&detail, 120, 40);
}

#[test]
fn explicit_transparent_flag_matches_the_default_palette() {
    let default = Theme::new(ColorMode::TrueColor);
    let explicit = Theme::new(ColorMode::TrueColor).with_transparent();
    assert_eq!(default.bg, Color::Reset);
    assert_eq!(explicit.bg, Color::Reset);
    assert_eq!(default.panel, explicit.panel);
    let application = App::with_options_theme(ColorMode::TrueColor, 2, true);
    assert_reset_background(&application, 120, 40);
}

#[test]
fn help_documents_transparency_without_kitty_config_edits() {
    let output = run_binary(&["--help"]);
    assert!(output.contains("--transparent"), "{output}");
    assert!(output.contains("Color::Reset"), "{output}");
    assert!(output.contains("kitty"), "{output}");
    assert!(output.contains("Ctrl+Q"), "{output}");
    for unwanted in ["background_opacity", "background_blur", "copy_on_select"] {
        assert!(
            !output.contains(unwanted),
            "help still asks the user to edit kitty config: {unwanted}"
        );
    }
}

#[test]
fn tiny_terminals_do_not_panic() {
    for screen in [Screen::Startup, Screen::Chat] {
        for (width, height) in [(40, 10), (20, 5), (10, 3)] {
            let application = App::with_screen(ColorMode::Ansi16, screen);
            let _ = render_app(&application, width, height);
        }
    }
}

#[test]
fn selftest_returns_ok() {
    molt_tui::selftest().expect("selftest should render all frames");
}

#[test]
fn selftest_reports_six_screens_and_twelve_frames() {
    let output = run_binary(&["--selftest"]);
    assert!(output.contains("screens=6"), "{output}");
    assert!(output.contains("frames=12"), "{output}");
    assert!(output.contains("sizes=2"), "{output}");
    assert!(output.contains("states=5"), "{output}");
}

#[test]
fn selftest_honours_an_explicit_size() {
    let output = run_binary(&["--selftest", "--width", "80", "--height", "24"]);
    assert!(output.contains("screens=6"), "{output}");
    assert!(output.contains("frames=6"), "{output}");
    assert!(output.contains("sizes=1"), "{output}");
    assert!(output.contains("states=5"), "{output}");
}

#[test]
fn selftest_rejects_a_zero_size() {
    let output = Command::new(env!("CARGO_BIN_EXE_molt-tui"))
        .args(["--selftest", "--width", "0", "--height", "24"])
        .output()
        .expect("molt-tui binary should run");
    assert!(!output.status.success(), "zero width should fail");
    assert_eq!(output.status.code(), Some(2), "bad usage must exit 2");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("greater than 0"), "{stderr}");
}

#[test]
fn width_without_selftest_is_rejected() {
    let output = Command::new(env!("CARGO_BIN_EXE_molt-tui"))
        .args(["--width", "80", "--height", "24"])
        .output()
        .expect("molt-tui binary should run");
    assert!(
        !output.status.success(),
        "size without selftest should fail"
    );
    assert_eq!(output.status.code(), Some(2), "bad usage must exit 2");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--selftest"), "{stderr}");
}

#[test]
fn logo_all_prints_three_titles_in_every_colour_mode() {
    for mode in ["16", "256", "truecolor"] {
        let output = run_binary(&["--logo", "all", "--color", mode]);
        for title in ["Ouroboros Pixel", "Braille Ring", "Terminal Crest"] {
            assert!(output.contains(title), "mode {mode} missing {title}");
        }
        if mode == "16" {
            assert!(!output.contains("38;2;"), "16-colour output has RGB");
            assert!(
                !output.contains("38;5;"),
                "16-colour output has 256-indexed"
            );
        }
        if mode == "256" {
            assert!(!output.contains("38;2;"), "256-colour output has RGB");
        }
        if mode == "truecolor" {
            assert!(output.contains("38;2;"), "truecolor output has no RGB");
        }
    }
}

#[test]
fn logo_one_preview_lists_only_that_candidate() {
    // Non-TTY stdout falls back to the review sheet, which keeps the single
    // candidate path testable in CI.
    let output = run_binary(&["--logo", "2", "--color", "16"]);
    assert!(output.contains("[2] Braille Ring"), "{output}");
    assert!(!output.contains("[1] Ouroboros Pixel"), "{output}");
    assert!(!output.contains("[3] Terminal Crest"), "{output}");
}

fn assert_reset_background(application: &App, width: u16, height: u16) {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::new(backend).expect("test backend is infallible");
    terminal
        .draw(|frame| application.draw(frame))
        .expect("test backend is infallible");
    let buffer = terminal.backend().buffer();
    for y in 0..buffer.area.height {
        for x in 0..buffer.area.width {
            assert_eq!(
                buffer[(x, y)].bg,
                Color::Reset,
                "{} cell ({x},{y}) paints a background",
                application.screen.title()
            );
        }
    }
}

fn assert_no_rgb(buffer: &ratatui::buffer::Buffer, context: &str) {
    for y in 0..buffer.area.height {
        for x in 0..buffer.area.width {
            let cell = &buffer[(x, y)];
            assert!(
                !matches!(cell.fg, Color::Rgb(..)),
                "{context} cell ({x},{y}) uses RGB fg"
            );
            assert!(
                !matches!(cell.bg, Color::Rgb(..)),
                "{context} cell ({x},{y}) uses RGB bg"
            );
        }
    }
}
