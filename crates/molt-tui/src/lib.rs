//! `molt-tui` — the v3 terminal shell.
//!
//! The interactive loop, layout, theme, and selftest are real. The default
//! Chat is a live DeepSeek stream backed by JSONL sessions ([`provider`] and
//! [`session`]); `--mock` keeps the deterministic embedded demo, and
//! `--selftest` always renders against mock data. W-21 still leaves tools,
//! approvals, MCP and tree rollback on mock data.

pub mod app;
pub mod approval;
pub mod clipboard;
pub mod diff;
pub mod logo;
pub mod mascot;
pub mod mock;
pub mod provider;
pub mod session;
pub mod settings;
pub mod theme;
pub mod tree;
pub mod version_workspace;
pub mod wordmark;

use anyhow::{bail, Context, Result};
use ratatui::{backend::TestBackend, Terminal};
use std::io::IsTerminal;

pub use app::{App, DataMode, Screen};
pub use theme::ColorMode;

/// Default DeepSeek model for the live Chat.
pub const DEFAULT_MODEL: &str = "deepseek-flash";

/// Parsed command-line options.
#[derive(Debug, Clone)]
pub struct Cli {
    /// Force the deterministic mock shell; the default is live DeepSeek.
    pub mock: bool,
    /// DeepSeek model id used by live mode.
    pub model: String,
    /// OpenAI-compatible endpoint root used by live mode.
    pub base_url: String,
    pub selftest: bool,
    /// Explicit selftest width; requires `--selftest` and `--height`.
    pub width: Option<u16>,
    /// Explicit selftest height; requires `--selftest` and `--width`.
    pub height: Option<u16>,
    pub logo: Option<logo::Selection>,
    pub color: ColorMode,
    /// Explicit alias for the default reset background. The shell never paints
    /// an opaque background, so the flag is accepted for intent/clarity only.
    pub transparent: bool,
    /// Explicit local molt version workspace. No workspace is accessed by default.
    pub version_workspace: Option<std::path::PathBuf>,
    /// Path to the existing molt CLI used for version transactions.
    pub molt_bin: String,
    pub help: bool,
}

impl Default for Cli {
    fn default() -> Self {
        Self {
            mock: false,
            model: DEFAULT_MODEL.to_string(),
            base_url: provider::DEFAULT_BASE_URL.to_string(),
            selftest: false,
            width: None,
            height: None,
            logo: None,
            color: ColorMode::default(),
            transparent: false,
            version_workspace: None,
            molt_bin: "molt".to_string(),
            help: false,
        }
    }
}

impl Cli {
    /// Parse arguments without a CLI framework. Live mode is the default;
    /// `--mock` opts back into the embedded demo data.
    pub fn parse<I>(args: I) -> Result<Self>
    where
        I: IntoIterator<Item = String>,
    {
        let mut cli = Self::default();
        let mut args = args.into_iter().peekable();
        while let Some(argument) = args.next() {
            match argument.as_str() {
                "--mock" => cli.mock = true,
                "--model" => {
                    let value = args.next().context("--model requires a model id")?;
                    if value.trim().is_empty() {
                        bail!("--model requires a non-empty model id");
                    }
                    cli.model = value;
                }
                "--base-url" => {
                    let value = args.next().context("--base-url requires an http(s) URL")?;
                    cli.base_url = value;
                }
                "--selftest" => cli.selftest = true,
                "--transparent" => cli.transparent = true,
                "--version-workspace" => {
                    cli.version_workspace = Some(
                        args.next()
                            .context("--version-workspace requires a path")?
                            .into(),
                    );
                }
                "--molt-bin" => {
                    cli.molt_bin = args
                        .next()
                        .context("--molt-bin requires an executable path")?;
                }
                "--help" | "-h" => cli.help = true,
                "--logo" => {
                    // `--logo` on its own selects candidate 3; a following
                    // `all` / `1` / `2` / `3` still overrides it.
                    let selection = match args.peek() {
                        Some(value) if matches!(value.as_str(), "all" | "1" | "2" | "3") => {
                            let value = args.next().expect("peeked value is present");
                            value.parse()?
                        }
                        _ => logo::DEFAULT_SELECTION,
                    };
                    cli.logo = Some(selection);
                }
                "--color" => {
                    let value = args
                        .next()
                        .context("--color requires one of: 16, 256, truecolor")?;
                    cli.color = value.parse()?;
                }
                "--width" => {
                    let value = args.next().context("--width requires a u16 value")?;
                    cli.width = Some(dimension("--width", &value, MAX_WIDTH)?);
                }
                "--height" => {
                    let value = args.next().context("--height requires a u16 value")?;
                    cli.height = Some(dimension("--height", &value, MAX_HEIGHT)?);
                }
                other => bail!("unknown argument `{other}`\n\n{}", usage()),
            }
        }
        if !cli.selftest && (cli.width.is_some() || cli.height.is_some()) {
            bail!(
                "--width/--height are only valid with --selftest\n\n{}",
                usage()
            );
        }
        if cli.width.is_some() != cli.height.is_some() {
            bail!("--width and --height must be used together\n\n{}", usage());
        }
        Ok(cli)
    }
}

/// Upper bounds for `--selftest --width/--height`. A larger frame is not a
/// realistic terminal and only wastes memory in the off-screen backend.
const MAX_WIDTH: u16 = 500;
const MAX_HEIGHT: u16 = 200;

fn dimension(flag: &str, value: &str, max: u16) -> Result<u16> {
    match value.parse::<u16>() {
        Ok(0) => bail!("{flag} must be greater than 0"),
        Ok(value) if value > max => bail!("{flag} must be at most {max}, got {value}"),
        Ok(value) => Ok(value),
        Err(_) => bail!("{flag} expects a u16, got `{value}`"),
    }
}

pub const fn usage() -> &'static str {
    "molt-tui · terminal shell (v3, live DeepSeek)

Usage:
  molt-tui [--version-workspace <path>] [--molt-bin <path>] [--color 16|256|truecolor]
  molt-tui --mock [--color 16|256|truecolor] [--transparent]
  molt-tui --selftest [--width <u16> --height <u16>]

  Live mode is the default: Chat streams from DeepSeek and persists the
  conversation as JSONL under ~/.local/share/molt/sessions.
  --mock         run entirely on deterministic mock data (no network, no key)
  --version-workspace <path>
                 connect the Versions panel to an initialized local molt workspace
  --molt-bin <path>  molt CLI executable (default: molt)
  --model <id>   DeepSeek model id (default deepseek-flash)
  --base-url <url>
                 OpenAI-compatible endpoint root (default https://api.deepseek.com)
  --color <mode> 16 | 256 | truecolor (default 256)
  --transparent  explicit alias for the default: the shell paints no background,
                 so the terminal's own background colour shows through
  --selftest     render every screen and state off-screen; exits non-zero on failure
  --width <u16>  selftest only: render this width (1-500; needs --height; default 80 and 120)
  --height <u16> selftest only: render this height (1-200; needs --width)

  Legacy logo preview (not shown in the shell, always mock):
  --logo 1|2|3   print one legacy candidate preview and exit
  --logo all     print the three-candidate review sheet and exit

Environment:
  DEEPSEEK_API_KEY       API key for live mode
  MOLT_API_KEY_HELPER    shell command that prints the API key
  Neither value is ever printed; a missing key leaves Chat usable but unable
  to send, with the error shown as a system message.

Background:
  All regions use the terminal default background (Color::Reset). kitty's
  existing opacity/blur shows through; no terminal configuration is required.

Keys:
  Ctrl+Q   quit
  Tab      Chat -> Tree -> Settings -> Diff
  a        approval modal (normal mode)
  ? / Esc  toggle help
  Enter    send the Chat composer (live unless --mock)
  Ctrl+C   cancel the live stream; outside a stream, press twice to quit
  Ctrl+N   new live session (live mode)
  Ctrl+O   browse and reopen live sessions (live mode)
  Ctrl+V   open an explicitly configured local molt version workspace
  Esc      leave the composer, or return to it
  y        copy the last answer via OSC 52 (Chat, normal mode)

Screen keys:
  Chat     type to compose · Ctrl+N new · Ctrl+O sessions · a approval
           (--mock: t streams a 1.5s mock turn)
  Tree     j/k move · h/l fold · f fork · r rollback · p pin
           Space mark · / search
  Settings j/k select · a apply · d diff · r routing · Enter detail
  Diff     n/N hunk · s split (>=120) · a apply · r restore · o $PAGER
  Approval y once · A always · x reject · Esc later
"
}

/// Dispatch the parsed CLI.
pub fn run(cli: Cli) -> Result<()> {
    if cli.help {
        print!("{}", usage());
        return Ok(());
    }
    if cli.selftest {
        return match (cli.width, cli.height) {
            (Some(width), Some(height)) => selftest_at(width, height),
            _ => selftest(),
        };
    }
    if let Some(selection) = cli.logo {
        match selection {
            logo::Selection::All => return logo::print(selection, cli.color),
            logo::Selection::One(candidate) => {
                let index = usize::from(candidate.saturating_sub(1));
                if std::io::stdout().is_terminal() {
                    return app::run_with(cli.color, index, cli.transparent);
                }
                // Non-interactive runs keep the review path usable in scripts.
                return logo::print(selection, cli.color);
            }
        }
    }
    if cli.mock {
        return app::run_with_workspace(
            cli.color,
            logo::DEFAULT_CANDIDATE,
            cli.transparent,
            cli.version_workspace,
            &cli.molt_bin,
        );
    }
    app::run_live(
        cli.color,
        &cli.model,
        &cli.base_url,
        cli.transparent,
        cli.version_workspace,
        &cli.molt_bin,
    )
}

/// Render every screen at 120x40 and 80x24, check required text and row widths,
/// exercise the new interactive states, then print a machine-readable result.
/// No TTY is required.
pub fn selftest() -> Result<()> {
    selftest_sizes(&[(120, 40), (80, 24)])
}

/// Render every screen and state frame at one explicit size.
pub fn selftest_at(width: u16, height: u16) -> Result<()> {
    selftest_sizes(&[(width, height)])
}

fn selftest_sizes(sizes: &[(u16, u16)]) -> Result<()> {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    let mut frames = 0_usize;

    for &(width, height) in sizes {
        for screen in Screen::ALL {
            let application = App::with_screen(ColorMode::Ansi256, screen);
            let backend = TestBackend::new(width, height);
            let mut terminal = Terminal::new(backend)?;
            terminal.draw(|frame| application.draw(frame))?;
            let buffer = terminal.backend().buffer();
            if let Some((row, excess)) = app::buffer_overflow(buffer) {
                bail!(
                    "selftest overflow: {} {}x{} row {row} exceeds by {excess}",
                    screen.title(),
                    width,
                    height
                );
            }
            let text = app::buffer_lines(buffer).join("\n");
            for marker in app::screen_markers(screen) {
                if !text.contains(marker) {
                    bail!(
                        "selftest missing marker `{marker}` in {} {}x{}",
                        screen.title(),
                        width,
                        height
                    );
                }
            }
            frames += 1;
        }
    }

    // New W-09 states: these overlays are not extra `Screen` variants, so the
    // frames/screens counters above stay at 6 x 2 = 12.
    let mut tree_detail = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    tree_detail.select_tree_node(0, 3);
    let mut settings_diff = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    settings_diff.select_config(1);
    let mut rollback = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    rollback.select_tree_node(0, 3);
    rollback.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::NONE));
    let mut rollback_disabled = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    rollback_disabled.select_tree_node(2, 1);
    rollback_disabled.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::NONE));
    let approval = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    let states = [
        ("tree-detail", tree_detail, &["NODE", "role", "tokens"][..]),
        ("settings-diff", settings_diff, &["diff vs", "+ tools"][..]),
        (
            "rollback",
            rollback,
            &["ROLLBACK", "回退后只能看到摘要", "backup"][..],
        ),
        (
            "rollback-disabled",
            rollback_disabled,
            &["ROLLBACK", "cannot_restore_files"][..],
        ),
        (
            "approval-modal",
            approval,
            &["proc.exec", "sandbox", "bwrap (net off)"][..],
        ),
    ];
    for &(width, height) in sizes {
        for (name, application, markers) in &states {
            let backend = TestBackend::new(width, height);
            let mut terminal = Terminal::new(backend)?;
            terminal.draw(|frame| application.draw(frame))?;
            let buffer = terminal.backend().buffer();
            if let Some((row, excess)) = app::buffer_overflow(buffer) {
                bail!(
                    "selftest overflow: state {name} {width}x{height} row {row} exceeds by {excess}"
                );
            }
            let text = app::buffer_lines(buffer).join("\n");
            for marker in *markers {
                if !text.contains(marker) {
                    bail!(
                        "selftest missing marker `{marker}` in state {name} {width}x{height}\n{text}"
                    );
                }
            }
        }
    }

    let demo = mock::MockData::demo();
    let json = demo.to_json().context("serialize mock data")?;
    let decoded = mock::MockData::from_json(&json).context("parse mock data")?;
    if decoded != demo {
        bail!("mock data did not survive a JSON round trip");
    }

    println!(
        "SELFTEST OK screens={} frames={frames} sizes={} states={} shell=sidebar",
        Screen::ALL.len(),
        sizes.len(),
        states.len(),
    );
    println!(
        "# count: screens={} (Startup + Chat/Tree/Settings/Diff/Approval) x sizes={} = frames={}; states={}",
        Screen::ALL.len(),
        sizes.len(),
        Screen::ALL.len() * sizes.len(),
        states.len(),
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_defaults_to_live() {
        let cli = Cli::parse(std::iter::empty()).unwrap();
        assert!(!cli.mock);
        assert_eq!(cli.model, DEFAULT_MODEL);
        assert_eq!(cli.base_url, provider::DEFAULT_BASE_URL);
        assert_eq!(cli.color, ColorMode::Ansi256);
        assert!(cli.logo.is_none());
        assert!(!cli.selftest);
    }

    #[test]
    fn cli_parses_flags() {
        let cli = Cli::parse(
            [
                "--mock",
                "--selftest",
                "--color",
                "truecolor",
                "--model",
                "deepseek-chat",
                "--base-url",
                "http://127.0.0.1:9/v1",
            ]
            .into_iter()
            .map(str::to_owned),
        )
        .unwrap();
        assert!(cli.mock);
        assert!(cli.selftest);
        assert_eq!(cli.color, ColorMode::TrueColor);
        assert_eq!(cli.model, "deepseek-chat");
        assert_eq!(cli.base_url, "http://127.0.0.1:9/v1");
    }

    #[test]
    fn cli_rejects_missing_model_and_base_url_values() {
        for args in [
            &["--model"][..],
            &["--base-url"][..],
            &["--model", "  "][..],
        ] {
            let parsed = Cli::parse(args.iter().map(|value| (*value).to_owned()));
            assert!(parsed.is_err(), "expected failure for {args:?}");
        }
    }

    #[test]
    fn cli_parses_the_transparent_alias() {
        let cli = Cli::parse(["--transparent".to_owned()]).unwrap();
        assert!(cli.transparent);
        assert!(!Cli::default().transparent);
        assert_eq!(
            Cli::parse(
                ["--transparent", "--color", "16"]
                    .into_iter()
                    .map(str::to_owned)
            )
            .unwrap()
            .color,
            ColorMode::Ansi16
        );
    }

    #[test]
    fn cli_rejects_unknown_flags() {
        assert!(Cli::parse(["--nope".to_owned()]).is_err());
        assert!(Cli::parse(["--color".to_owned()]).is_err());
    }

    #[test]
    fn cli_selftest_accepts_explicit_size() {
        let cli = Cli::parse(
            ["--selftest", "--width", "80", "--height", "24"]
                .into_iter()
                .map(str::to_owned),
        )
        .unwrap();
        assert!(cli.selftest);
        assert_eq!(cli.width, Some(80));
        assert_eq!(cli.height, Some(24));
    }

    #[test]
    fn cli_rejects_size_without_selftest() {
        let error = Cli::parse(
            ["--width", "80", "--height", "24"]
                .into_iter()
                .map(str::to_owned),
        )
        .expect_err("--width/--height without --selftest must fail");
        assert!(error.to_string().contains("--selftest"), "{error}");
    }

    #[test]
    fn cli_rejects_zero_or_incomplete_size() {
        for args in [
            &["--selftest", "--width", "0", "--height", "24"][..],
            &["--selftest", "--width", "80", "--height", "0"][..],
            &["--selftest", "--width", "abc", "--height", "24"][..],
            &["--selftest", "--width", "80"][..],
            &["--selftest", "--height", "24"][..],
        ] {
            let parsed = Cli::parse(args.iter().map(|value| (*value).to_owned()));
            assert!(parsed.is_err(), "expected failure for {args:?}");
        }
    }

    #[test]
    fn cli_accepts_the_largest_supported_frame() {
        let cli = Cli::parse(
            ["--selftest", "--width", "500", "--height", "200"]
                .into_iter()
                .map(str::to_owned),
        )
        .unwrap();
        assert_eq!(cli.width, Some(MAX_WIDTH));
        assert_eq!(cli.height, Some(MAX_HEIGHT));
    }

    #[test]
    fn cli_rejects_sizes_above_the_caps() {
        for args in [
            &["--selftest", "--width", "501", "--height", "24"][..],
            &["--selftest", "--width", "80", "--height", "201"][..],
        ] {
            let error = Cli::parse(args.iter().map(|value| (*value).to_owned()))
                .expect_err("sizes above the caps must fail");
            assert!(error.to_string().contains("at most"), "{error}");
        }
    }

    #[test]
    fn logo_flag_without_value_defaults_to_candidate_three() {
        let cli = Cli::parse(["--logo".to_owned()]).unwrap();
        assert_eq!(cli.logo, Some(logo::Selection::One(3)));

        let cli = Cli::parse(["--logo", "--color", "16"].into_iter().map(str::to_owned)).unwrap();
        assert_eq!(cli.logo, Some(logo::Selection::One(3)));
        assert_eq!(cli.color, ColorMode::Ansi16);

        let cli = Cli::parse(["--logo", "2"].into_iter().map(str::to_owned)).unwrap();
        assert_eq!(cli.logo, Some(logo::Selection::One(2)));

        let cli = Cli::parse(["--logo", "all"].into_iter().map(str::to_owned)).unwrap();
        assert_eq!(cli.logo, Some(logo::Selection::All));
    }
}
