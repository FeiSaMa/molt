//! Application state, layout, key handling, and full-frame rendering.
//!
//! The shell is deliberately small: one startup splash plus five views, one
//! sidebar, one composer. W-09 owns tree/settings/approval interaction; this
//! module provides the frame, the shared status line, and the navigation
//! skeleton. The four work screens live in [`crate::tree`], [`crate::settings`],
//! [`crate::diff`], and [`crate::approval`].

use std::cell::Cell;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::{
    buffer::Buffer,
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph},
    Frame,
};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::approval::ApprovalUiState;
use crate::diff::DiffState;
use crate::mock::{McpStatus, Message, MessageRole, MockData, SessionKind};
use crate::provider::{self, ChatMessage, ProviderConfig, ProviderEvent, Usage};
use crate::session::{self, Session, SessionStore, SessionSummary};
use crate::settings::SettingsState;
use crate::theme::{ColorMode, Theme};
use crate::tree::TreeState;
use crate::version_workspace::VersionWorkspace;
use crate::{clipboard, logo, wordmark};

/// Status messages disappear after three seconds of wall clock time.
pub const STATUS_TTL: Duration = Duration::from_secs(3);
/// `b` is reserved for TUI file recovery (C5). The forward-commit path does
/// not exist yet, so the key must not call the CLI rollback operation.
pub const FILE_RECOVERY_DISABLED: &str =
    "TUI file recovery is disabled (C5 pending); use CLI export + forward commit";
/// The fake model turn triggered by `t` in the chat view.
pub const MOCK_STREAM_MS: u64 = 1500;

/// Where the Chat view gets its turns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataMode {
    /// Deterministic embedded data; no network and no key.
    Mock,
    /// Real DeepSeek streaming backed by a JSONL session store.
    Live,
}

/// The main content panes. The sidebar, header, footer, and composer are always
/// part of the shell frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    Startup,
    Chat,
    Tree,
    Settings,
    Diff,
    Approval,
}

impl Screen {
    /// Six screens in total: the startup splash plus the five shell views.
    pub const ALL: [Self; 6] = [
        Self::Startup,
        Self::Chat,
        Self::Tree,
        Self::Settings,
        Self::Diff,
        Self::Approval,
    ];

    pub const fn title(self) -> &'static str {
        match self {
            Self::Startup => "STARTUP",
            Self::Chat => "CHAT",
            Self::Tree => "TREE",
            Self::Settings => "SETTINGS",
            Self::Diff => "DIFF",
            Self::Approval => "APPROVAL",
        }
    }

    /// Tab only cycles the four primary views; Approval has its own key and
    /// Startup hands over to Chat.
    pub const fn next(self) -> Self {
        match self {
            Self::Startup | Self::Diff | Self::Approval => Self::Chat,
            Self::Chat => Self::Tree,
            Self::Tree => Self::Settings,
            Self::Settings => Self::Diff,
        }
    }
}

/// Text that must be present in a rendered frame. Shared by `--selftest` and
/// integration tests so a layout regression cannot silently pass.
pub const fn screen_markers(screen: Screen) -> &'static [&'static str] {
    match screen {
        Screen::Startup => &["MOLT", "v3"],
        Screen::Chat => &["molt", "SESSIONS", "MCP", "context", ">"],
        Screen::Tree => &["TREE", "demo-6", "auto/config-B", "NODE"],
        Screen::Settings => &["SETTINGS", "base", "low/full", "fast-edit", "merge"],
        Screen::Diff => &["DIFF", "+++", "@@", "hunk"],
        Screen::Approval => &[
            "APPROVAL",
            "proc.exec",
            "cargo test",
            "sandbox",
            "once",
            "always",
            "reject",
        ],
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StatusLevel {
    Info,
    Success,
    Warning,
    Danger,
}

#[derive(Debug, Clone)]
pub struct StatusMessage {
    pub text: String,
    pub until: Instant,
    pub level: StatusLevel,
}

#[derive(Debug, Clone)]
struct SessionPickerState {
    sessions: Vec<SessionSummary>,
    selected: usize,
}

#[derive(Debug)]
pub struct App {
    pub mock: MockData,
    /// Mock (embedded demo data) or Live (DeepSeek + JSONL session store).
    pub mode: DataMode,
    pub screen: Screen,
    pub theme: Theme,
    pub show_help: bool,
    session_picker: Option<SessionPickerState>,
    pub version_workspace: Option<VersionWorkspace>,
    pub version_panel_open: bool,
    version_receipt_scroll: Option<usize>,
    pub scroll: u16,
    pub quit: bool,
    /// First Ctrl+C in a non-streaming state arms quit; the second one exits.
    pub quit_armed: bool,
    pub quit_armed_until: Option<Instant>,
    /// Legacy `--logo` candidate index. The shell itself always draws the
    /// block wordmark; the field only keeps the old preview path addressable.
    pub logo_candidate: usize,
    /// Composer buffer. Only the Chat view accepts typed characters.
    pub input: String,
    /// True while the Chat composer owns the keyboard. Every other screen and
    /// every modal leaves it false.
    pub composer_focused: bool,
    /// Last assistant body, copied by `y` through OSC 52.
    pub last_assistant: String,
    /// OSC 52 sequence queued by `y`; the event loop flushes it after drawing.
    pub pending_osc: Option<String>,
    /// While true the composer shows streaming.
    pub streaming: bool,
    /// Injected clock. The event loop advances it every tick; tests can freeze
    /// it for deterministic status expiry.
    pub now: Instant,
    pub status: Option<StatusMessage>,
    pub stream_until: Option<Instant>,
    /// Last drawn terminal size. Split diff uses it; tests can drive it by
    /// rendering once at the desired size first.
    pub viewport: Cell<(u16, u16)>,
    /// Resolved provider settings; `None` when key resolution failed.
    pub provider: Option<ProviderConfig>,
    /// Human-readable key-resolution failure; never contains the key.
    pub provider_error: Option<String>,
    /// JSONL store used by live sessions; `None` only for mock apps.
    pub store: Option<SessionStore>,
    /// Currently loaded live session (messages are rendered in Chat).
    pub session: Option<Session>,
    /// Events from the worker thread while a live stream is in flight.
    pub live_rx: Option<Receiver<ProviderEvent>>,
    /// Shared cancellation flag handed to the worker thread.
    pub live_cancel: Option<Arc<AtomicBool>>,
    /// Assistant text accumulated for the in-flight stream.
    pub streaming_text: String,
    /// Chain-of-thought text accumulated for the in-flight stream.
    pub streaming_reasoning: String,
    /// Final token usage reported by the provider, shown in the footer.
    pub usage: Option<Usage>,
    pub tree: TreeState,
    pub settings: SettingsState,
    pub diff: DiffState,
    pub approvals: ApprovalUiState,
}

impl App {
    pub fn new(mode: ColorMode) -> Self {
        Self::with_options(mode, logo::DEFAULT_CANDIDATE)
    }

    pub fn with_screen(mode: ColorMode, screen: Screen) -> Self {
        let mut app = Self::with_options(mode, logo::DEFAULT_CANDIDATE);
        app.screen = screen;
        app.composer_focused = screen == Screen::Chat;
        app
    }

    /// Build the app with an explicit legacy logo candidate.
    pub fn with_options(mode: ColorMode, logo_candidate: usize) -> Self {
        Self::with_options_theme(mode, logo_candidate, false)
    }

    /// Build the app with the legacy candidate and the `--transparent` intent.
    /// The background is already reset in every mode; the flag is an alias.
    pub fn with_options_theme(mode: ColorMode, logo_candidate: usize, transparent: bool) -> Self {
        let mock = MockData::demo();
        let last_assistant = mock
            .messages
            .iter()
            .rev()
            .find(|message| message.role == MessageRole::Assistant)
            .map(|message| message.body.clone())
            .unwrap_or_default();
        let tree = TreeState::new(&mock);
        let settings = SettingsState::new(&mock);
        let theme = if transparent {
            Theme::new(mode).with_transparent()
        } else {
            Theme::new(mode)
        };
        Self {
            mock,
            mode: DataMode::Mock,
            screen: Screen::Startup,
            theme,
            show_help: false,
            session_picker: None,
            version_workspace: None,
            version_panel_open: false,
            version_receipt_scroll: None,
            scroll: 0,
            quit: false,
            quit_armed: false,
            quit_armed_until: None,
            logo_candidate: logo_candidate.min(logo::CANDIDATES.len() - 1),
            input: String::new(),
            composer_focused: false,
            last_assistant,
            pending_osc: None,
            streaming: false,
            now: Instant::now(),
            status: None,
            stream_until: None,
            viewport: Cell::new((120, 40)),
            provider: None,
            provider_error: None,
            store: None,
            session: None,
            live_rx: None,
            live_cancel: None,
            streaming_text: String::new(),
            streaming_reasoning: String::new(),
            usage: None,
            tree,
            settings,
            diff: DiffState::default(),
            approvals: ApprovalUiState::default(),
        }
    }

    /// Build a live app: Chat is the initial screen, the latest JSONL session
    /// is loaded (or a fresh one created), and a key-resolution failure is
    /// surfaced as a system message instead of aborting the shell.
    pub fn new_live(
        mode: ColorMode,
        provider_result: Result<ProviderConfig, String>,
        store: SessionStore,
    ) -> Self {
        let mut app = Self::with_options_theme(mode, logo::DEFAULT_CANDIDATE, false);
        app.mode = DataMode::Live;
        app.screen = Screen::Chat;
        app.composer_focused = true;
        app.scroll = 0;
        app.last_assistant.clear();
        app.store = Some(store.clone());

        match store.load_latest() {
            Ok(Some(session)) => {
                app.last_assistant = last_assistant_text(&session);
                app.session = Some(session);
            }
            Ok(None) => match store.new_session() {
                Ok(session) => app.session = Some(session),
                Err(error) => {
                    app.set_status(StatusLevel::Danger, format!("session unavailable: {error}"));
                }
            },
            Err(error) => {
                app.set_status(StatusLevel::Danger, format!("session load failed: {error}"));
                match store.new_session() {
                    Ok(session) => app.session = Some(session),
                    Err(error) => {
                        app.set_status(
                            StatusLevel::Danger,
                            format!("session unavailable: {error}"),
                        );
                    }
                }
            }
        }

        match provider_result {
            Ok(config) => app.provider = Some(config),
            Err(message) => {
                app.provider_error = Some(message.clone());
                if let Some(session) = app.session.as_mut() {
                    session.messages.push(ChatMessage {
                        role: "system".to_string(),
                        content: format!("provider unavailable: {message}"),
                    });
                }
                app.set_status(
                    StatusLevel::Danger,
                    format!("provider unavailable: {message}"),
                );
            }
        }
        app.scroll_chat_to_bottom();
        app
    }

    /// A modal is any overlay that must take the keyboard away from the
    /// composer and show `COMPOSER · disabled`. The approval screen itself is a
    /// modal surface.
    pub fn modal(&self) -> bool {
        self.show_help
            || self.session_picker.is_some()
            || self.version_panel_open
            || self.screen == Screen::Approval
            || self.tree.rollback.is_some()
            || self.settings.detail_open
            || self.diff.restore
    }

    /// Publish a transient status line.
    pub fn set_status(&mut self, level: StatusLevel, text: impl Into<String>) {
        self.status = Some(StatusMessage {
            text: text.into(),
            until: self.now + STATUS_TTL,
            level,
        });
    }

    /// The status text, or `None` once three seconds have elapsed.
    pub fn status_text(&self) -> Option<&str> {
        self.status
            .as_ref()
            .filter(|status| self.now < status.until)
            .map(|status| status.text.as_str())
    }

    /// Inject time and expire transient state. The event loop calls this on
    /// every iteration; tests call it with a fixed instant.
    pub fn tick(&mut self, now: Instant) {
        self.now = now;
        self.pump_live_events();
        if let Some(until) = self.stream_until {
            if now >= until {
                self.streaming = false;
                self.stream_until = None;
                self.set_status(StatusLevel::Success, "mock stream finished");
            }
        }
        if self
            .status
            .as_ref()
            .is_some_and(|status| now >= status.until)
        {
            self.status = None;
        }
        if let Some(until) = self.quit_armed_until {
            if now >= until {
                self.quit_armed = false;
                self.quit_armed_until = None;
            }
        }
    }

    pub fn start_mock_stream(&mut self) {
        self.streaming = true;
        self.stream_until = Some(self.now + Duration::from_millis(MOCK_STREAM_MS));
        self.scroll_chat_to_bottom();
        self.set_status(StatusLevel::Info, "mock stream started (1.5s)");
    }

    /// Drain any events the provider thread has already produced. Called from
    /// [`App::tick`] on every event-loop iteration.
    fn pump_live_events(&mut self) {
        loop {
            let event = match self.live_rx.as_ref() {
                Some(receiver) => receiver.try_recv(),
                None => return,
            };
            match event {
                Ok(event) => self.handle_live_event(event),
                Err(TryRecvError::Empty) => return,
                Err(TryRecvError::Disconnected) => {
                    if self.streaming {
                        self.finish_live_error("stream ended without a provider event".to_string());
                    }
                    self.live_rx = None;
                    self.live_cancel = None;
                    return;
                }
            }
        }
    }

    /// Apply one [`ProviderEvent`]. Public to the crate so unit tests can inject
    /// events without a network call.
    pub(crate) fn handle_live_event(&mut self, event: ProviderEvent) {
        match event {
            ProviderEvent::Delta(text) => {
                if self.streaming {
                    self.streaming_text.push_str(&text);
                    self.scroll_chat_to_bottom();
                }
            }
            ProviderEvent::Reasoning(text) => {
                if self.streaming {
                    self.streaming_reasoning.push_str(&text);
                    self.scroll_chat_to_bottom();
                }
            }
            ProviderEvent::Usage(usage) => self.usage = usage,
            ProviderEvent::Done => self.finish_live_done(),
            ProviderEvent::Error(message) => self.finish_live_error(message),
        }
    }

    fn finish_live_done(&mut self) {
        self.live_rx = None;
        self.live_cancel = None;
        if !self.streaming {
            return;
        }
        let text = std::mem::take(&mut self.streaming_text);
        if !text.is_empty() {
            self.append_assistant(text, None);
        }
        self.streaming = false;
        self.streaming_reasoning.clear();
        self.set_status(StatusLevel::Success, "stream complete");
        self.scroll_chat_to_bottom();
    }

    fn finish_live_error(&mut self, message: String) {
        self.live_rx = None;
        self.live_cancel = None;
        if !self.streaming {
            return;
        }
        let partial = std::mem::take(&mut self.streaming_text);
        if !partial.is_empty() {
            // Keep the partial answer but mark it so it is not mistaken for a
            // complete turn. The marker is part of the stored assistant body.
            self.append_assistant(partial, Some(&format!("[interrupted: {message}]")));
        }
        self.streaming = false;
        self.streaming_reasoning.clear();
        if message == "cancelled" {
            self.set_status(StatusLevel::Warning, "turn cancelled (interrupted)");
        } else {
            self.set_status(StatusLevel::Danger, format!("stream error: {message}"));
        }
        self.scroll_chat_to_bottom();
    }

    fn append_assistant(&mut self, text: String, suffix: Option<&str>) {
        let content = match suffix {
            Some(suffix) => format!("{text}\n\n{suffix}"),
            None => text,
        };
        let message = ChatMessage {
            role: "assistant".to_string(),
            content: content.clone(),
        };
        if let Some(session) = self.session.as_mut() {
            session.messages.push(message.clone());
        }
        let append_error = match (self.store.as_ref(), self.session.as_ref()) {
            (Some(store), Some(session)) => store.append(session, &message).err(),
            _ => None,
        };
        self.last_assistant = content;
        if let Some(error) = append_error {
            self.set_status(
                StatusLevel::Warning,
                format!("session save failed: {error}"),
            );
        }
    }

    /// Start a fresh live session and clear the Chat pane.
    fn new_live_session(&mut self) {
        if self.mode != DataMode::Live {
            return;
        }
        if self.streaming {
            self.set_status(StatusLevel::Warning, "cancel the stream first");
            return;
        }
        let Some(store) = self.store.clone() else {
            self.set_status(StatusLevel::Danger, "session store unavailable");
            return;
        };
        match store.new_session() {
            Ok(session) => {
                self.session = Some(session);
                self.last_assistant.clear();
                self.streaming_text.clear();
                self.streaming_reasoning.clear();
                self.usage = None;
                self.scroll = 0;
                self.set_status(StatusLevel::Success, "new session");
            }
            Err(error) => {
                self.set_status(StatusLevel::Danger, format!("new session failed: {error}"));
            }
        }
    }

    fn open_session_picker(&mut self) {
        if self.mode != DataMode::Live {
            self.set_status(
                StatusLevel::Info,
                "saved sessions are available in live mode",
            );
            return;
        }
        if self.streaming {
            self.set_status(
                StatusLevel::Warning,
                "cancel the stream before opening a session",
            );
            return;
        }
        let Some(store) = self.store.as_ref() else {
            self.set_status(StatusLevel::Danger, "session store unavailable");
            return;
        };
        match store.list() {
            Ok(sessions) if sessions.is_empty() => {
                self.set_status(StatusLevel::Info, "no saved sessions");
            }
            Ok(sessions) => {
                let selected = self
                    .session
                    .as_ref()
                    .and_then(|current| sessions.iter().position(|item| item.id == current.id))
                    .unwrap_or(0);
                self.session_picker = Some(SessionPickerState { sessions, selected });
            }
            Err(error) => {
                self.set_status(StatusLevel::Danger, format!("session list failed: {error}"))
            }
        }
    }

    fn handle_session_picker_key(&mut self, key: KeyEvent) {
        let Some(picker) = self.session_picker.as_mut() else {
            return;
        };
        match key.code {
            KeyCode::Esc => self.session_picker = None,
            KeyCode::Char('j') | KeyCode::Down => {
                picker.selected = (picker.selected + 1).min(picker.sessions.len() - 1);
            }
            KeyCode::Char('k') | KeyCode::Up => {
                picker.selected = picker.selected.saturating_sub(1);
            }
            KeyCode::Enter => {
                let id = picker.sessions[picker.selected].id.clone();
                let result = self.store.as_ref().map(|store| store.load_by_id(&id));
                match result {
                    Some(Ok(session)) => {
                        let skipped_lines = session.skipped_lines;
                        self.last_assistant = last_assistant_text(&session);
                        self.session = Some(session);
                        self.screen = Screen::Chat;
                        self.composer_focused = true;
                        self.streaming_text.clear();
                        self.streaming_reasoning.clear();
                        self.usage = None;
                        self.session_picker = None;
                        self.scroll_chat_to_bottom();
                        if skipped_lines == 0 {
                            self.set_status(StatusLevel::Success, format!("loaded session {id}"));
                        } else {
                            self.set_status(
                                StatusLevel::Warning,
                                format!(
                                    "loaded session {id} · skipped {skipped_lines} malformed lines"
                                ),
                            );
                        }
                    }
                    Some(Err(error)) => self
                        .set_status(StatusLevel::Danger, format!("session load failed: {error}")),
                    None => self.set_status(StatusLevel::Danger, "session store unavailable"),
                }
            }
            _ => {}
        }
    }

    /// Handle one key press. `Ctrl+Q` is the only quit key; `q` is ordinary
    /// text whenever the Chat composer is focused. Tab and `?` stay global so
    /// they work while typing.
    pub fn handle_key(&mut self, key: KeyEvent) {
        if key.kind != KeyEventKind::Press {
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('q') | KeyCode::Char('Q'))
        {
            self.quit = true;
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('C'))
        {
            self.handle_interrupt();
            return;
        }
        if self.session_picker.is_some() {
            self.handle_session_picker_key(key);
            return;
        }
        if self.version_panel_open {
            self.handle_version_panel_key(key);
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('n') | KeyCode::Char('N'))
        {
            self.new_live_session();
            return;
        }
        if self.show_help {
            match key.code {
                KeyCode::Char('?') | KeyCode::Esc => self.show_help = false,
                _ => {}
            }
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('o') | KeyCode::Char('O'))
        {
            self.open_session_picker();
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('v') | KeyCode::Char('V'))
        {
            if self.version_workspace.is_some() {
                self.version_panel_open = true;
                self.refresh_version_status();
            } else {
                self.set_status(
                    StatusLevel::Warning,
                    "start with --version-workspace to connect molt",
                );
            }
            return;
        }
        if self.screen == Screen::Startup {
            match key.code {
                KeyCode::Char('?') => self.show_help = true,
                KeyCode::Char(character)
                    if !key.modifiers.contains(KeyModifiers::CONTROL)
                        && !key.modifiers.contains(KeyModifiers::ALT) =>
                {
                    self.screen = Screen::Chat;
                    self.composer_focused = true;
                    self.scroll = 0;
                    // A printable key that dismisses the splash is also the
                    // first character of the message, mirroring a real shell.
                    self.input.push(character);
                }
                _ => {
                    self.screen = Screen::Chat;
                    self.composer_focused = true;
                    self.scroll = 0;
                }
            }
            return;
        }
        if self.mode == DataMode::Mock
            && self.screen == Screen::Approval
            && self.handle_approval_key(key)
        {
            return;
        }
        if key.code == KeyCode::Tab {
            self.screen = self.screen.next();
            self.composer_focused = self.screen == Screen::Chat;
            self.scroll = 0;
            self.show_help = false;
            return;
        }
        if key.code == KeyCode::Char('?') {
            self.show_help = true;
            return;
        }
        if self.screen == Screen::Chat && self.composer_focused && !self.modal() {
            self.handle_composer_key(key);
            return;
        }
        if self.screen == Screen::Tree && self.uses_managed_tree() {
            self.handle_version_tree_key(key);
            return;
        }
        // Live mode has no real backend for the demo-only panels. Reject the
        // action at dispatch time so a visual label alone cannot be bypassed.
        if self.unavailable_panel().is_some() {
            self.reject_unavailable_action(key);
            return;
        }
        let consumed = match self.screen {
            Screen::Tree => self.handle_tree_key(key),
            Screen::Settings => self.handle_settings_key(key),
            Screen::Diff => self.handle_diff_key(key),
            Screen::Chat => self.handle_chat_key(key),
            Screen::Startup | Screen::Approval => false,
        };
        if consumed {
            return;
        }
        match key.code {
            KeyCode::Char('?') => self.show_help = true,
            KeyCode::Char('a') => {
                self.screen = Screen::Approval;
                self.composer_focused = false;
                self.scroll = 0;
            }
            KeyCode::Up => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down => self.scroll = self.scroll.saturating_add(1),
            KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::PageDown => self.scroll = self.scroll.saturating_add(10),
            _ => {}
        }
    }

    fn handle_version_tree_key(&mut self, key: KeyEvent) {
        let Some(workspace) = self.version_workspace.as_mut() else {
            return;
        };
        match key.code {
            KeyCode::Char('j') | KeyCode::Down => workspace.move_reader_selection(1),
            KeyCode::Char('k') | KeyCode::Up => workspace.move_reader_selection(-1),
            KeyCode::PageDown => {
                for _ in 0..8 {
                    workspace.move_reader_selection(1);
                }
            }
            KeyCode::PageUp => {
                for _ in 0..8 {
                    workspace.move_reader_selection(-1);
                }
            }
            _ => {}
        }
    }

    fn uses_managed_tree(&self) -> bool {
        self.version_workspace.is_some()
    }

    /// The current screen when Live mode has no real backend for it. `None`
    /// means the screen is Mock (where the demo is explicit) or has a real
    /// backend (Chat, or Tree with a connected Versions workspace).
    pub fn unavailable_panel(&self) -> Option<Screen> {
        if self.mode != DataMode::Live {
            return None;
        }
        match self.screen {
            Screen::Tree if !self.uses_managed_tree() => Some(Screen::Tree),
            Screen::Settings | Screen::Diff | Screen::Approval => Some(self.screen),
            _ => None,
        }
    }

    /// One-line reason for an unavailable panel.
    fn unavailable_detail(screen: Screen) -> &'static str {
        match screen {
            Screen::Tree => "no real session-tree store is connected",
            Screen::Settings => "no real config backend is connected",
            Screen::Diff => "no real diff backend is connected",
            Screen::Approval => "no real permission engine is connected",
            _ => "no real backend is connected",
        }
    }

    /// Keep navigation keys working but refuse every demo action in Live mode.
    fn reject_unavailable_action(&mut self, key: KeyEvent) {
        if self.screen == Screen::Approval && key.code == KeyCode::Esc {
            self.screen = Screen::Chat;
            self.composer_focused = true;
            self.set_status(StatusLevel::Info, "returned to live chat");
            return;
        }
        let panel = self.screen.title().to_ascii_lowercase();
        self.set_status(
            StatusLevel::Warning,
            format!("{panel} unavailable in live · no real data source connected"),
        );
    }

    fn handle_version_panel_key(&mut self, key: KeyEvent) {
        let Some(workspace) = self.version_workspace.as_mut() else {
            self.version_panel_open = false;
            self.version_receipt_scroll = None;
            return;
        };
        if let Some(scroll) = self.version_receipt_scroll {
            let (width, height) = self.viewport.get();
            let panel = Self::version_panel_rect(Rect::new(0, 0, width, height));
            let lines = Self::save_receipt_lines(workspace, panel.width.saturating_sub(2));
            let max_scroll = lines
                .len()
                .saturating_sub(usize::from(panel.height.saturating_sub(2)));
            let scroll = scroll.min(max_scroll);
            self.version_receipt_scroll = match key.code {
                KeyCode::Esc | KeyCode::Char('q') => None,
                KeyCode::Up | KeyCode::Char('k') => Some(scroll.saturating_sub(1)),
                KeyCode::Down | KeyCode::Char('j') => Some((scroll + 1).min(max_scroll)),
                _ => Some(scroll),
            };
            return;
        }
        if workspace.editing {
            workspace.handle_editor_key(key);
            return;
        }
        if workspace.history_filter_draft().is_some() {
            workspace.handle_history_filter_key(key);
            return;
        }
        if key.code == KeyCode::Char('t') {
            self.version_panel_open = false;
            self.screen = Screen::Tree;
            self.composer_focused = false;
            return;
        }
        let mut file_recovery_disabled = false;
        let result = match key.code {
            KeyCode::Esc | KeyCode::Char('q') => {
                self.version_panel_open = false;
                return;
            }
            KeyCode::Char('r') => workspace.refresh(),
            KeyCode::Char('s') => workspace.commit_synthetic(),
            KeyCode::Char('v') if key.modifiers.is_empty() => {
                self.version_receipt_scroll = Some(0);
                return;
            }
            KeyCode::Char('e') => {
                workspace.begin_edit();
                return;
            }
            KeyCode::Char('p') => workspace.pin_reader(),
            KeyCode::Char('l') => workspace.read_pinned(),
            KeyCode::Char('h') => workspace.history(),
            KeyCode::Char('/') => {
                workspace.begin_history_filter();
                return;
            }
            KeyCode::Char('c') => {
                workspace.clear_history_filter();
                return;
            }
            KeyCode::Char('j') | KeyCode::Down => {
                workspace.move_history_selection(1);
                return;
            }
            KeyCode::Char('k') | KeyCode::Up => {
                workspace.move_history_selection(-1);
                return;
            }
            KeyCode::PageDown | KeyCode::Char(']') => {
                workspace.page_history(1);
                return;
            }
            KeyCode::PageUp | KeyCode::Char('[') => {
                workspace.page_history(-1);
                return;
            }
            KeyCode::Char('a') => workspace.audit_last(),
            KeyCode::Char('u') => workspace.recover().and_then(|()| workspace.refresh()),
            KeyCode::Char('b') => {
                workspace
                    .detail
                    .insert(0, FILE_RECOVERY_DISABLED.to_owned());
                file_recovery_disabled = true;
                Ok(())
            }
            _ => return,
        };
        if file_recovery_disabled {
            self.set_status(StatusLevel::Warning, FILE_RECOVERY_DISABLED);
            return;
        }
        if let Err(error) = result {
            let message = format!("operation failed: {error}");
            if key.code == KeyCode::Char('s') {
                workspace.detail = vec![
                    message,
                    format!("draft bytes {}", workspace.draft.len()),
                    workspace.draft.clone(),
                ];
            } else {
                workspace.detail.push(message);
            }
            self.set_status(StatusLevel::Danger, format!("molt workspace: {error}"));
        } else {
            self.set_status(StatusLevel::Success, "molt workspace updated");
        }
    }

    fn refresh_version_status(&mut self) {
        if let Some(workspace) = self.version_workspace.as_mut() {
            if let Err(error) = workspace.refresh() {
                self.set_status(StatusLevel::Danger, format!("molt workspace: {error}"));
            }
        }
    }

    fn handle_interrupt(&mut self) {
        if self.streaming && self.mode == DataMode::Live {
            if let Some(cancel) = &self.live_cancel {
                cancel.store(true, Ordering::Relaxed);
            }
            // Keep `streaming` true until the worker reports `Error("cancelled")`,
            // so the partial answer is still appended as interrupted.
            self.set_status(StatusLevel::Info, "cancelling…");
            return;
        }
        if self.streaming {
            self.streaming = false;
            self.stream_until = None;
            // Cancelling a turn must not leave a stale quit arm behind: the
            // next Ctrl+C is a fresh first press, not an immediate quit.
            self.quit_armed = false;
            self.quit_armed_until = None;
            self.set_status(StatusLevel::Info, "turn cancelled");
            return;
        }
        if self.quit_armed {
            self.quit = true;
            return;
        }
        self.quit_armed = true;
        self.quit_armed_until = Some(self.now + STATUS_TTL);
        self.set_status(StatusLevel::Warning, "press Ctrl+C again to quit");
    }

    fn handle_composer_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Char(character)
                if !key.modifiers.contains(KeyModifiers::CONTROL)
                    && !key.modifiers.contains(KeyModifiers::ALT) =>
            {
                self.input.push(character);
            }
            KeyCode::Backspace => {
                self.input.pop();
            }
            KeyCode::Enter => self.submit_composer(),
            KeyCode::Esc => {
                self.composer_focused = false;
                self.set_status(StatusLevel::Info, "composer normal mode");
            }
            _ => {}
        }
    }

    /// Enter in the Chat composer. Mock keeps the embedded demo; Live appends
    /// the user turn, persists it and starts a provider stream.
    fn submit_composer(&mut self) {
        if self.mode == DataMode::Live {
            self.submit_live();
            return;
        }
        if self.input.trim().is_empty() {
            self.set_status(StatusLevel::Warning, "empty message (mock)");
            return;
        }
        let body = std::mem::take(&mut self.input);
        self.mock.messages.push(Message {
            role: MessageRole::User,
            body,
        });
        self.scroll_chat_to_bottom();
        self.set_status(StatusLevel::Success, "sent (mock)");
    }

    fn submit_live(&mut self) {
        if self.streaming {
            self.set_status(StatusLevel::Warning, "wait for stream");
            return;
        }
        if self.input.trim().is_empty() {
            self.set_status(StatusLevel::Warning, "empty message");
            return;
        }
        let Some(config) = self.provider.clone() else {
            let error = self
                .provider_error
                .clone()
                .unwrap_or_else(|| "provider unavailable".to_string());
            self.set_status(StatusLevel::Danger, format!("not sent: {error}"));
            return;
        };
        if self.session.is_none() {
            self.set_status(StatusLevel::Danger, "not sent: session unavailable");
            return;
        }

        let body = std::mem::take(&mut self.input);
        let user = ChatMessage {
            role: "user".to_string(),
            content: body,
        };
        let mut history = Vec::new();
        if let Some(session) = self.session.as_mut() {
            session.messages.push(user.clone());
            history = session.messages.clone();
        }
        let append_error = match (self.store.as_ref(), self.session.as_ref()) {
            (Some(store), Some(session)) => store.append(session, &user).err(),
            _ => None,
        };

        let cancel = Arc::new(AtomicBool::new(false));
        let (tx, rx) = mpsc::channel();
        let thread_cancel = Arc::clone(&cancel);
        let thread_config = config.clone();
        std::thread::spawn(move || {
            // `stream_chat` reports failures through the channel; the return
            // value is only a convenience for non-TUI callers.
            let _ = provider::stream_chat(&thread_config, &history, thread_cancel, tx);
        });

        self.streaming = true;
        self.streaming_text.clear();
        self.streaming_reasoning.clear();
        self.usage = None;
        self.live_cancel = Some(cancel);
        self.live_rx = Some(rx);
        self.scroll_chat_to_bottom();
        match append_error {
            Some(error) => self.set_status(
                StatusLevel::Danger,
                format!("sent · session save failed: {error}"),
            ),
            None => self.set_status(
                StatusLevel::Info,
                format!("sent · streaming {}", config.model),
            ),
        }
    }

    /// Copy the last assistant answer with OSC 52. The sequence is queued and
    /// written by the event loop after the next draw.
    fn copy_last_answer(&mut self) {
        let mode = if self.mode == DataMode::Live {
            "live"
        } else {
            "mock"
        };
        if self.last_assistant.is_empty() {
            self.set_status(StatusLevel::Warning, format!("nothing to copy ({mode})"));
            return;
        }
        self.pending_osc = Some(clipboard::osc52(&self.last_assistant));
        self.set_status(
            StatusLevel::Success,
            format!("copied last answer (OSC52, {mode})"),
        );
    }

    fn handle_chat_key(&mut self, key: KeyEvent) -> bool {
        match key.code {
            KeyCode::Char('t') if self.mode == DataMode::Mock => {
                self.start_mock_stream();
                true
            }
            KeyCode::Char('a') => {
                self.screen = Screen::Approval;
                self.composer_focused = false;
                self.scroll = 0;
                true
            }
            KeyCode::Char('y') => {
                self.copy_last_answer();
                true
            }
            KeyCode::Esc => {
                self.composer_focused = true;
                true
            }
            _ => false,
        }
    }

    fn key_hint(&self) -> &'static str {
        match self.screen {
            Screen::Startup => "",
            Screen::Chat => {
                if self.mode == DataMode::Live {
                    "type · Enter send · Ctrl+V versions · Ctrl+O sessions · Ctrl+N new · Ctrl+C cancel · y copy · ? help"
                } else {
                    "type to compose · Esc normal · t mock stream · y copy · Tab views · ? help"
                }
            }
            Screen::Tree if self.uses_managed_tree() => {
                "j/k browse · PgUp/PgDn · Ctrl+V Versions · read-only"
            }
            Screen::Tree | Screen::Settings | Screen::Diff if self.mode == DataMode::Live => {
                "unavailable in live · Tab next · ? help"
            }
            Screen::Approval if self.mode == DataMode::Live => {
                "unavailable in live · Esc chat · Tab next · ? help"
            }
            Screen::Tree => {
                "j/k move · h/l fold · f fork · r rollback · p pin · Space mark · / search"
            }
            Screen::Settings => "j/k select · a apply · d diff · r routing · Enter detail",
            Screen::Diff => "n/N hunk · s split · a apply · r restore · o $PAGER",
            Screen::Approval => "y once · A always · x reject · Esc later",
        }
    }

    /// Render a complete frame. Every region is painted on every call; the
    /// terminal backend handles the diff.
    pub fn draw(&self, frame: &mut Frame) {
        let area = frame.area();
        self.viewport.set((area.width, area.height));
        frame.render_widget(Block::default().style(self.theme.base()), area);

        let shell = ShellLayout::new(area);
        self.render_header(frame, shell.header);
        if self.screen == Screen::Startup {
            let body = Rect::new(area.x, shell.main.y, area.width, shell.main.height);
            self.render_startup(frame, body);
        } else {
            if let Some(sidebar) = shell.sidebar {
                self.render_sidebar(frame, sidebar);
            }
            self.render_main(frame, shell.main);
        }
        self.render_footer(frame, shell.footer);
        self.render_composer(frame, shell.composer);
        if self.tree.rollback.is_some() {
            self.render_rollback(frame, area);
        }
        if self.settings.detail_open {
            self.render_settings_detail_overlay(frame, area);
        }
        if self.diff.restore {
            self.render_restore(frame, area);
        }
        if self.show_help {
            self.render_help(frame, area);
        }
        if self.session_picker.is_some() {
            self.render_session_picker(frame, area);
        }
        if self.version_panel_open {
            self.render_version_panel(frame, area);
        }
    }

    fn version_panel_rect(area: Rect) -> Rect {
        let width = area.width.saturating_sub(8).clamp(1, 88);
        let height = area.height.saturating_sub(6).clamp(1, 16);
        Rect::new(
            area.x + area.width.saturating_sub(width) / 2,
            area.y + area.height.saturating_sub(height) / 2,
            width,
            height,
        )
    }

    fn save_receipt_lines(workspace: &VersionWorkspace, width: u16) -> Vec<Line<'static>> {
        let fields = match &workspace.last_save_receipt {
            Some(receipt) => vec![
                "Esc back · j/k scroll".to_owned(),
                format!("generation {}", receipt.generation),
                "snapshot ID".to_owned(),
                receipt.snapshot.clone(),
                "event ID".to_owned(),
                receipt.event.clone(),
                "request ID".to_owned(),
                receipt.request_id.clone(),
            ],
            None => vec![
                "Esc back".to_owned(),
                "No successful save in this session".to_owned(),
            ],
        };
        fields
            .iter()
            .flat_map(|field| wrap_text(field, width))
            .map(Line::from)
            .collect()
    }

    fn render_version_panel(&self, frame: &mut Frame, area: Rect) {
        use ratatui::widgets::{Clear, Wrap};
        let panel = Self::version_panel_rect(area);
        let workspace = self.version_workspace.as_ref();
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(self.theme.border())
            .style(self.theme.base());
        frame.render_widget(Clear, panel);
        if let (Some(scroll), Some(workspace)) = (self.version_receipt_scroll, workspace) {
            let inner = block.inner(panel);
            let lines = Self::save_receipt_lines(workspace, inner.width);
            let offset = scroll.min(lines.len().saturating_sub(usize::from(inner.height)));
            let widget = Paragraph::new(lines.into_iter().skip(offset).collect::<Vec<_>>())
                .block(block.title(" LAST SUCCESSFUL SAVE "));
            frame.render_widget(widget, panel);
            return;
        }
        let mut lines = vec![
            Line::from("Ctrl+V · local workspace · v save receipt · / filter · c clear"),
            Line::from(
                "e edit · s save · p pin reader · l read pinned · t tree · b file recovery unavailable",
            ),
            Line::from("u recover · h history · j/k move · PgUp/PgDn · a audit · Esc close"),
        ];
        if let Some(workspace) = workspace {
            if let Some(draft) = workspace.history_filter_draft() {
                lines.push(Line::from("event ID, generation (gN), or action"));
                lines.push(Line::from("Enter apply · Esc cancel · Backspace edit"));
                lines.push(Line::from(format!("/ {draft}")));
            } else {
                if !workspace.history_filter().is_empty() {
                    let text = format!("filter {}", workspace.history_filter());
                    lines.extend(
                        wrap_text(&text, panel.width.saturating_sub(2))
                            .into_iter()
                            .take(2)
                            .map(Line::from),
                    );
                }
                lines.extend(workspace.detail.iter().cloned().map(Line::from));
            }
        }
        let widget = Paragraph::new(lines)
            .wrap(Wrap { trim: true })
            .block(block.title(" VERSIONS "));
        frame.render_widget(widget, panel);
    }

    fn render_header(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        // W-19: the header is a single plain-text line. No compact block
        // wordmark and no icon glyphs; narrow terminals simply truncate it.
        let mode = match (self.mode, self.screen, self.version_workspace.is_some()) {
            (DataMode::Live, Screen::Chat, true) => "live chat · molt connected",
            (DataMode::Live, Screen::Chat, false) => "live chat · demo panels unavailable",
            (DataMode::Live, _, _) => "live · demo panels unavailable",
            (DataMode::Mock, _, true) => "molt connected · mock panels",
            (DataMode::Mock, _, false) => "mock",
        };
        let label = fit(&format!(" molt · v3 · {mode}"), area.width);
        frame.render_widget(
            Paragraph::new(Span::styled(label, self.theme.accent())),
            Rect::new(area.x, area.y, area.width, 1),
        );
    }

    /// Startup splash: the large block wordmark, one tagline, one hint. No
    /// mascot, no ouroboros, and no legacy logo candidate. Below the mark's 38
    /// columns or eight rows the art is replaced by plain `MOLT` text, never
    /// block glyphs.
    fn render_startup(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let tagline = "v3 · mock";
        let hint = "MOLT · press any key · Ctrl+Q quits · ? keys";
        let (art_width, art_height) = wordmark::large_size();
        if area.width < art_width || area.height < art_height {
            self.render_startup_fallback(frame, area, tagline, hint);
            return;
        }

        let block_height = art_height.saturating_add(3);
        let top = area.y + area.height.saturating_sub(block_height) / 2;
        let art_area = Rect::new(
            area.x + (area.width - art_width) / 2,
            top,
            art_width,
            art_height,
        );
        wordmark::render_large(frame, art_area, &self.theme);

        let bottom = area.y.saturating_add(area.height);
        let tag_y = top.saturating_add(art_height).saturating_add(1);
        if tag_y < bottom {
            frame.render_widget(
                Paragraph::new(Span::styled(fit(tagline, area.width), self.theme.muted()))
                    .alignment(Alignment::Center),
                Rect::new(area.x, tag_y, area.width, 1),
            );
        }
        let hint_y = tag_y.saturating_add(1);
        if hint_y < bottom {
            frame.render_widget(
                Paragraph::new(Span::styled(fit(hint, area.width), self.theme.text()))
                    .alignment(Alignment::Center),
                Rect::new(area.x, hint_y, area.width, 1),
            );
        }
    }

    fn render_startup_fallback(&self, frame: &mut Frame, area: Rect, tagline: &str, hint: &str) {
        let lines = [
            ("MOLT", self.theme.title()),
            (tagline, self.theme.muted()),
            (hint, self.theme.text()),
        ];
        let height = (lines.len() as u16).min(area.height);
        let top = area.y + (area.height - height) / 2;
        for (offset, (text, style)) in lines.iter().take(usize::from(height)).enumerate() {
            frame.render_widget(
                Paragraph::new(Span::styled(fit(text, area.width), *style))
                    .alignment(Alignment::Center),
                Rect::new(area.x, top.saturating_add(offset as u16), area.width, 1),
            );
        }
    }

    fn render_sidebar(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let block = Block::default()
            .borders(Borders::RIGHT)
            .border_style(self.theme.border())
            .style(self.theme.base());
        let inner = block.inner(area);
        frame.render_widget(block, area);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let lines = self.sidebar_lines(inner.width);
        frame.render_widget(Paragraph::new(lines).style(self.theme.base()), inner);
    }

    fn sidebar_lines(&self, width: u16) -> Vec<Line<'static>> {
        let mut lines = Vec::new();
        let theme = self.theme;

        let demo = self.mode == DataMode::Live;
        let compact = width < 22;
        lines.push(styled_line("SESSIONS", width, theme.title()));
        if self.mode == DataMode::Live {
            match &self.session {
                Some(session) => {
                    let label = if compact {
                        format!("▾ {}", session.id)
                    } else {
                        format!("▾ {} · {} msgs", session.id, session.messages.len())
                    };
                    lines.push(styled_line(label, width, theme.accent()));
                    if !compact {
                        let preview = session
                            .messages
                            .iter()
                            .find(|message| message.role == "user")
                            .map(|message| session::summarize(&message.content))
                            .unwrap_or_default();
                        if !preview.is_empty() {
                            lines.push(styled_line(format!("  {preview}"), width, theme.muted()));
                        }
                    }
                }
                None => lines.push(styled_line("(no session)", width, theme.danger())),
            }
        } else {
            if let Some(primary) = self.mock.sessions.first() {
                let label = if compact {
                    format!("▾ {} · !{}", primary.id, primary.high_importance_nodes())
                } else {
                    format!(
                        "▾ {} · {}n · !{}",
                        primary.id,
                        primary.nodes.len(),
                        primary.high_importance_nodes()
                    )
                };
                lines.push(styled_line(label, width, theme.accent()));
            }
            for (index, session) in self.mock.sessions.iter().skip(1).enumerate() {
                let connector = if index + 2 == self.mock.sessions.len() {
                    "└"
                } else {
                    "├"
                };
                let kind = match session.kind {
                    SessionKind::AutoFork => "auto",
                    SessionKind::Fork => "fork",
                    SessionKind::Root => "root",
                };
                let label = if compact {
                    format!("{connector} {}", session.id)
                } else {
                    format!("{connector} {} · {kind}", session.id)
                };
                lines.push(styled_line(label, width, theme.accent()));
            }
        }

        lines.push(styled_line(
            if demo { "MCP · demo only" } else { "MCP" },
            width,
            theme.title(),
        ));
        for server in &self.mock.mcp {
            let (text, style) = match server.status {
                McpStatus::Connected => (format!("● {} ok", server.name), theme.success()),
                McpStatus::Error => (format!("● {} err", server.name), theme.danger()),
            };
            lines.push(styled_line(text, width, style));
        }
        if let Some(error) = self
            .mock
            .mcp
            .iter()
            .find(|server| server.status == McpStatus::Error)
        {
            if let Some(message) = &error.message {
                lines.push(styled_line(format!("  {message}"), width, theme.muted()));
            }
        }

        lines.push(styled_line(
            if demo { "TODOS · demo only" } else { "TODOS" },
            width,
            theme.title(),
        ));
        for todo in &self.mock.todos {
            let mark = if todo.done { "x" } else { " " };
            let style = if todo.done {
                theme.muted()
            } else {
                theme.text()
            };
            lines.push(styled_line(
                format!("[{mark}] {}", todo.title),
                width,
                style,
            ));
        }

        lines.push(styled_line(
            if demo {
                "CONFIG · demo only"
            } else {
                "CONFIG"
            },
            width,
            theme.title(),
        ));
        if let Some(config) = self.mock.active_config() {
            if compact {
                lines.push(styled_line(config.id.clone(), width, theme.info()));
                lines.push(styled_line(
                    config.permissions.clone(),
                    width,
                    theme.muted(),
                ));
            } else {
                lines.push(styled_line(
                    format!("{} · {}", config.id, config.permissions),
                    width,
                    theme.info(),
                ));
                lines.push(styled_line(config.model.clone(), width, theme.muted()));
            }
        }
        lines
    }

    fn render_main(&self, frame: &mut Frame, area: Rect) {
        if area.width < 2 || area.height < 2 {
            let text = self.screen.title();
            frame.render_widget(
                Paragraph::new(fit(text, area.width)).style(self.theme.base()),
                area,
            );
            return;
        }
        let unavailable = self.unavailable_panel();
        let context = if unavailable.is_some() {
            "unavailable".to_owned()
        } else {
            match self.screen {
                Screen::Tree if self.uses_managed_tree() => self
                    .version_workspace
                    .as_ref()
                    .and_then(|workspace| workspace.reader_snapshot.clone())
                    .unwrap_or_else(|| "no tree".to_owned()),
                Screen::Tree => self.tree_context_id(),
                Screen::Settings => self.mock.active_config.clone(),
                Screen::Diff => format!("{} hunks", self.mock.hunk_count()),
                Screen::Approval => format!("{} pending", self.mock.pending_approvals().count()),
                Screen::Chat | Screen::Startup => {
                    if self.mode == DataMode::Live {
                        self.session
                            .as_ref()
                            .map(|session| session.id.clone())
                            .unwrap_or_else(|| "live".to_owned())
                    } else {
                        self.mock
                            .sessions
                            .first()
                            .map(|session| session.id.clone())
                            .unwrap_or_else(|| "mock".to_owned())
                    }
                }
            }
        };
        let title = format!(" {} · {context} ", self.screen.title());
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(self.theme.border())
            .style(self.theme.base())
            .title(Span::styled(title, self.theme.title()));
        let inner = block.inner(area);
        frame.render_widget(block, area);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let status_height = u16::from(inner.height > 1);
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(status_height), Constraint::Min(0)])
            .split(inner);
        self.render_status(frame, rows[0]);
        let body = rows[1];
        if body.width == 0 || body.height == 0 {
            return;
        }
        if let Some(panel) = unavailable {
            self.render_unavailable(frame, body, panel);
            return;
        }
        match self.screen {
            Screen::Startup => {}
            Screen::Chat => self.render_chat(frame, body),
            Screen::Tree => {
                if self.uses_managed_tree() {
                    self.render_managed_tree(frame, body);
                } else {
                    self.render_tree(frame, body);
                }
            }
            Screen::Settings => self.render_settings(frame, body),
            Screen::Diff => self.render_diff(frame, body),
            Screen::Approval => self.render_approval(frame, body),
        }
    }

    /// Explicit empty state for a Live screen whose real backend does not
    /// exist. The embedded demo is never painted here, so mock approvals,
    /// diffs or configs cannot be mistaken for real state.
    fn render_unavailable(&self, frame: &mut Frame, area: Rect, screen: Screen) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let width = area.width;
        let mut lines = vec![styled_line(
            format!("UNAVAILABLE · {}", screen.title()),
            width,
            self.theme.warning().add_modifier(Modifier::BOLD),
        )];
        for text in [
            "live mode · not connected to a real data source",
            Self::unavailable_detail(screen),
            "nothing on this screen reflects real state",
        ] {
            for line in wrap_text(text, width) {
                lines.push(styled_line(line, width, self.theme.text()));
            }
        }
        lines.push(Line::default());
        for text in [
            "demo data is hidden · demo actions are disabled",
            "Chat, sessions and Versions stay available · --mock keeps the demo",
        ] {
            for line in wrap_text(text, width) {
                lines.push(styled_line(line, width, self.theme.muted()));
            }
        }
        if screen == Screen::Tree {
            for line in wrap_text(
                "start with --version-workspace for the read-only Versions tree",
                width,
            ) {
                lines.push(styled_line(line, width, self.theme.muted()));
            }
        }
        frame.render_widget(Paragraph::new(lines).style(self.theme.base()), area);
    }

    fn render_status(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let (text, style) = match self
            .status
            .as_ref()
            .filter(|status| self.now < status.until)
        {
            Some(status) => {
                let style = match status.level {
                    StatusLevel::Info => self.theme.info(),
                    StatusLevel::Success => self.theme.success(),
                    StatusLevel::Warning => self.theme.warning(),
                    StatusLevel::Danger => self.theme.danger(),
                };
                (status.text.clone(), style)
            }
            None => (self.key_hint().to_owned(), self.theme.muted()),
        };
        frame.render_widget(
            Paragraph::new(styled_line(text, area.width, style)).style(self.theme.base()),
            area,
        );
    }

    fn render_chat(&self, frame: &mut Frame, area: Rect) {
        let lines = self.chat_frame_lines(area.width);
        // A stale scroll offset after a resize would otherwise blank the pane.
        let max = lines
            .len()
            .saturating_sub(usize::from(area.height))
            .min(usize::from(u16::MAX)) as u16;
        frame.render_widget(
            Paragraph::new(lines)
                .style(self.theme.base())
                .scroll((self.scroll.min(max), 0)),
            area,
        );
    }

    /// Chat body plus the in-flight turn. Shared by rendering and the
    /// scroll-to-bottom calculation so both agree on the line count.
    fn chat_frame_lines(&self, width: u16) -> Vec<Line<'static>> {
        let mut lines = self.chat_lines(width);
        if !self.streaming {
            return lines;
        }
        if self.mode == DataMode::Mock {
            lines.push(Line::default());
            lines.push(styled_line(
                "molt · streaming… (mock turn)",
                width,
                self.theme.accent().add_modifier(Modifier::BOLD),
            ));
            return lines;
        }

        lines.push(Line::default());
        if !self.streaming_reasoning.is_empty() {
            lines.push(styled_line(
                "[THINK]",
                width,
                self.theme.muted().add_modifier(Modifier::BOLD),
            ));
            for line in wrap_text(&self.streaming_reasoning, width) {
                lines.push(styled_line(line, width, self.theme.muted()));
            }
            lines.push(Line::default());
        }
        lines.push(styled_line(
            "[MOLT]",
            width,
            self.theme.title().add_modifier(Modifier::BOLD),
        ));
        let mut streamed = self.streaming_text.clone();
        streamed.push('▌');
        for line in wrap_text(&streamed, width) {
            lines.push(styled_line(line, width, self.theme.text()));
        }
        lines
    }

    fn chat_lines(&self, width: u16) -> Vec<Line<'static>> {
        if self.mode == DataMode::Live {
            return self.live_chat_lines(width);
        }
        let theme = self.theme;
        let mut lines = Vec::new();
        for (index, message) in self.mock.messages.iter().enumerate() {
            if index > 0 {
                lines.push(Line::default());
            }
            let (label_style, body_style) = match message.role {
                MessageRole::User => (theme.accent(), theme.text()),
                MessageRole::Assistant => (theme.title(), theme.text()),
                MessageRole::ToolCall => (theme.info(), theme.info()),
                MessageRole::ToolResult => (theme.success(), theme.success()),
            };
            lines.push(styled_line(
                format!("[{}]", message.role.label()),
                width,
                label_style.add_modifier(Modifier::BOLD),
            ));
            for line in wrap_text(&message.body, width) {
                lines.push(styled_line(line, width, body_style));
            }
        }
        lines
    }

    fn live_chat_lines(&self, width: u16) -> Vec<Line<'static>> {
        let theme = self.theme;
        let mut lines = Vec::new();
        let Some(session) = &self.session else {
            if let Some(error) = &self.provider_error {
                lines.push(styled_line(
                    "[SYSTEM]",
                    width,
                    theme.warning().add_modifier(Modifier::BOLD),
                ));
                for line in wrap_text(&format!("provider unavailable: {error}"), width) {
                    lines.push(styled_line(line, width, theme.muted()));
                }
            }
            return lines;
        };

        for (index, message) in session.messages.iter().enumerate() {
            if index > 0 {
                lines.push(Line::default());
            }
            let (label, label_style, body_style): (String, Style, Style) =
                match message.role.as_str() {
                    "user" => ("[YOU]".to_string(), theme.accent(), theme.text()),
                    "assistant" => ("[MOLT]".to_string(), theme.title(), theme.text()),
                    "system" => ("[SYSTEM]".to_string(), theme.warning(), theme.muted()),
                    role => (
                        format!("[{}]", role.to_uppercase()),
                        theme.info(),
                        theme.muted(),
                    ),
                };
            lines.push(styled_line(
                label,
                width,
                label_style.add_modifier(Modifier::BOLD),
            ));
            for line in wrap_text(&message.content, width) {
                lines.push(styled_line(line, width, body_style));
            }
        }
        lines
    }

    /// Screen rows available to the Chat body, mirroring [`Self::render_main`].
    fn chat_body_size(&self) -> (u16, u16) {
        let (width, height) = self.viewport.get();
        let shell = ShellLayout::new(Rect::new(0, 0, width, height));
        if shell.main.width < 2 || shell.main.height < 2 {
            return (0, 0);
        }
        let inner = Block::default().borders(Borders::ALL).inner(shell.main);
        let status_height = u16::from(inner.height > 1);
        (inner.width, inner.height.saturating_sub(status_height))
    }

    /// Scroll the Chat pane so the newest line is visible. Called after every
    /// send and every streamed delta so the answer follows the cursor.
    pub fn scroll_chat_to_bottom(&mut self) {
        let (width, height) = self.chat_body_size();
        if width == 0 || height == 0 {
            return;
        }
        let line_count = self.chat_frame_lines(width).len();
        let max = line_count.saturating_sub(usize::from(height));
        self.scroll = max.min(usize::from(u16::MAX)) as u16;
    }

    fn tree_context_id(&self) -> String {
        let rows = self.tree_rows();
        rows.get(self.tree.selected.min(rows.len().saturating_sub(1)))
            .and_then(|row| self.mock.sessions.get(row.session))
            .map(|session| session.id.clone())
            .unwrap_or_else(|| "tree".to_owned())
    }

    fn render_footer(&self, frame: &mut Frame, area: Rect) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let text = if self.mode == DataMode::Live {
            let model = self
                .provider
                .as_ref()
                .map(|provider| provider.model.as_str())
                .unwrap_or("deepseek");
            let tokens = match self.usage {
                Some(Usage {
                    input_tokens,
                    output_tokens,
                    ..
                }) => format!(
                    "tokens {}/{}",
                    input_tokens
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "?".to_string()),
                    output_tokens
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "?".to_string())
                ),
                None => "tokens —".to_string(),
            };
            let session = self
                .session
                .as_ref()
                .map(|session| session.id.as_str())
                .unwrap_or("no session");
            format!("{model} · live · {tokens} · {session} · Ctrl+Q quit")
        } else {
            let config = self.mock.active_config();
            let model = config.map(|config| config.model.as_str()).unwrap_or("mock");
            let provider = config
                .map(|config| config.provider.as_str())
                .unwrap_or("mock");
            let permissions = config
                .map(|config| config.permissions.as_str())
                .unwrap_or("ask");
            format!(
                "{model} · {provider} · context 42% · $0.18 · feat/v3-agent-tui · {permissions} · routing {}",
                self.settings.routing.label()
            )
        };
        frame.render_widget(
            Paragraph::new(styled_line(text, area.width, self.theme.muted()))
                .style(self.theme.base()),
            area,
        );
    }

    fn render_composer(&self, frame: &mut Frame, area: Rect) {
        if area.width < 2 || area.height < 2 {
            return;
        }
        let hint = " Enter send · Ctrl+Q quit · Esc normal ";
        let (border, title, body, body_style, bottom_hint) = if self.modal() {
            (
                self.theme.danger(),
                " COMPOSER · disabled ",
                "composer disabled · modal open · finish or Esc".to_owned(),
                self.theme.muted(),
                None,
            )
        } else if self.streaming {
            let body = if self.mode == DataMode::Live {
                "molt is streaming… (live · DeepSeek)".to_owned()
            } else {
                "molt is streaming… (mock · 1.5s)".to_owned()
            };
            (
                self.theme.warning(),
                " COMPOSER · streaming ",
                body,
                self.theme.warning(),
                Some(" Ctrl+C cancel "),
            )
        } else if self.composer_focused {
            (
                self.theme.accent(),
                " COMPOSER ",
                format!("> {}_", self.input),
                self.theme.text(),
                Some(hint),
            )
        } else {
            (
                self.theme.border(),
                " COMPOSER · Esc to type ",
                "Enter send · Ctrl+Q quit · Esc normal".to_owned(),
                self.theme.muted(),
                None,
            )
        };
        let mut block = Block::default()
            .borders(Borders::ALL)
            .border_style(border)
            .style(self.theme.base())
            .title(Span::styled(title, border));
        if let Some(text) = bottom_hint {
            block = block.title_bottom(Span::styled(text, self.theme.muted()));
        }
        let inner = block.inner(area);
        frame.render_widget(block, area);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        frame.render_widget(
            Paragraph::new(styled_line(body, inner.width, body_style)).style(self.theme.base()),
            inner,
        );
    }

    fn render_help(&self, frame: &mut Frame, area: Rect) {
        if area.width < 24 || area.height < 7 {
            return;
        }
        let width = area.width.min(64);
        let height = area.height.min(16);
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
            .title(Span::styled(" HELP ", self.theme.title()));
        let inner = block.inner(rect);
        frame.render_widget(block, rect);
        let mut lines = vec![
            styled_line("Ctrl+Q   quit", inner.width, self.theme.text()),
            styled_line(
                "Tab      Chat -> Tree -> Settings -> Diff",
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                if self.mode == DataMode::Live {
                    "a        unavailable in live (no approval engine)"
                } else {
                    "a        approval screen (modal)"
                },
                inner.width,
                self.theme.text(),
            ),
            styled_line("? / Esc  close help", inner.width, self.theme.text()),
            styled_line(
                "Enter    send composer (live or mock)",
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                "Ctrl+C   cancel stream / press twice to quit",
                inner.width,
                self.theme.text(),
            ),
            styled_line("Ctrl+N   new live session", inner.width, self.theme.text()),
            styled_line(
                "Ctrl+O   browse live sessions",
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                "Esc      leave / return to the composer",
                inner.width,
                self.theme.text(),
            ),
            styled_line(
                "y        copy last answer (OSC 52)",
                inner.width,
                self.theme.text(),
            ),
            styled_line("Up/Down  scroll main pane", inner.width, self.theme.text()),
        ];
        if self.mode == DataMode::Live {
            lines.push(styled_line(
                "Ctrl+V   Versions workspace (if connected)",
                inner.width,
                self.theme.text(),
            ));
            lines.push(styled_line(
                "Tree/Settings/Diff/Approval unavailable in live",
                inner.width,
                self.theme.text(),
            ));
        } else {
            lines.push(styled_line(
                "Tree     j/k · h/l · f · r · p · Space · /",
                inner.width,
                self.theme.text(),
            ));
            lines.push(styled_line(
                "Settings j/k · a apply · d diff · r routing",
                inner.width,
                self.theme.text(),
            ));
            lines.push(styled_line(
                "Diff     n/N hunk · s split · a apply · r restore",
                inner.width,
                self.theme.text(),
            ));
        }
        frame.render_widget(Paragraph::new(lines).style(self.theme.panel()), inner);
    }

    fn render_session_picker(&self, frame: &mut Frame, area: Rect) {
        if area.width < 24 || area.height < 7 {
            return;
        }
        let picker = self.session_picker.as_ref().expect("picker checked above");
        let width = area.width.saturating_sub(2).min(74);
        let height = area.height.saturating_sub(2).min(20);
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
            .title(Span::styled(
                " SESSIONS · j/k select · Enter open · Esc close ",
                self.theme.title(),
            ));
        let inner = block.inner(rect);
        frame.render_widget(block, rect);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let mut lines = Vec::with_capacity(picker.sessions.len() * 2);
        for (index, session) in picker.sessions.iter().enumerate() {
            let selected = index == picker.selected;
            let marker = if selected { "▸ " } else { "  " };
            let style = if selected {
                self.theme.accent().add_modifier(Modifier::BOLD)
            } else {
                self.theme.text()
            };
            lines.push(styled_line(
                format!("{marker}{} · {}", session.id, session.modified_at),
                inner.width,
                style,
            ));
            if !session.preview.is_empty() {
                lines.push(styled_line(
                    format!("    {}", session.preview),
                    inner.width,
                    self.theme.muted(),
                ));
            }
        }
        let selected_line = picker.sessions[..picker.selected]
            .iter()
            .map(|session| 1 + usize::from(!session.preview.is_empty()))
            .sum::<usize>();
        let scroll = selected_line.saturating_sub(usize::from(inner.height.saturating_sub(1)));
        frame.render_widget(
            Paragraph::new(lines)
                .style(self.theme.panel())
                .scroll((scroll.min(u16::MAX as usize) as u16, 0)),
            inner,
        );
    }
}

/// Last assistant body in a loaded session, used as the `y`-copy target.
fn last_assistant_text(session: &Session) -> String {
    session
        .messages
        .iter()
        .rev()
        .find(|message| message.role == "assistant")
        .map(|message| message.content.clone())
        .unwrap_or_default()
}

/// Fixed shell regions for a given terminal area.
#[derive(Debug, Clone, Copy)]
pub struct ShellLayout {
    pub header: Rect,
    pub sidebar: Option<Rect>,
    pub main: Rect,
    pub footer: Rect,
    pub composer: Rect,
}

impl ShellLayout {
    pub fn new(area: Rect) -> Self {
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(3),
                Constraint::Min(1),
                Constraint::Length(3),
                Constraint::Length(1),
            ])
            .split(area);

        let (sidebar, main) = if area.width >= 72 {
            let sidebar_width = if area.width >= 100 { 24 } else { 20 };
            let columns = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([Constraint::Length(sidebar_width), Constraint::Min(1)])
                .split(rows[1]);
            (Some(columns[0]), columns[1])
        } else {
            (None, rows[1])
        };

        Self {
            header: rows[0],
            sidebar,
            main,
            footer: rows[3],
            composer: rows[2],
        }
    }
}

/// Trim and pad a string to exactly `width` display cells. A wide character is
/// dropped rather than split across the boundary.
pub fn fit(text: &str, width: u16) -> String {
    let width = usize::from(width);
    let mut output = String::new();
    let mut used = 0;
    for character in text.chars() {
        let character_width = UnicodeWidthChar::width(character).unwrap_or(0);
        if character_width == 0 {
            continue;
        }
        if used + character_width > width {
            break;
        }
        output.push(character);
        used += character_width;
    }
    output.push_str(&" ".repeat(width.saturating_sub(used)));
    output
}

/// Wrap by display width. Character-based wrapping keeps CJK text inside the
/// pane without a word-segmentation dependency; English may break mid-word.
pub fn wrap_text(text: &str, width: u16) -> Vec<String> {
    let width = usize::from(width);
    if width == 0 {
        return Vec::new();
    }
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        let mut current = String::new();
        let mut used = 0;
        for character in paragraph.chars() {
            let character_width = UnicodeWidthChar::width(character).unwrap_or(0);
            if character_width == 0 || character_width > width {
                continue;
            }
            if used + character_width > width && !current.is_empty() {
                lines.push(std::mem::take(&mut current));
                used = 0;
            }
            current.push(character);
            used += character_width;
        }
        lines.push(current);
    }
    lines
}

pub(crate) fn styled_line(text: impl Into<String>, width: u16, style: Style) -> Line<'static> {
    Line::from(Span::styled(fit(&text.into(), width), style))
}

/// Join coloured spans and pad the row to exactly `width` cells.
pub(crate) fn spans_line(mut spans: Vec<Span<'static>>, width: u16) -> Line<'static> {
    let used: usize = spans
        .iter()
        .map(|span| UnicodeWidthStr::width(span.content.as_ref()))
        .sum();
    let pad = usize::from(width).saturating_sub(used);
    if pad > 0 {
        spans.push(Span::raw(" ".repeat(pad)));
    }
    Line::from(spans)
}

/// Extract visible rows from a backend buffer. Wide-character continuation
/// cells are skipped so the result reads like the terminal output.
pub fn buffer_lines(buffer: &Buffer) -> Vec<String> {
    let area = buffer.area;
    (0..area.height)
        .map(|y| {
            let mut text = String::new();
            let mut x = 0;
            while x < area.width {
                let symbol = buffer[(x, y)].symbol();
                text.push_str(symbol);
                let width = UnicodeWidthStr::width(symbol).max(1) as u16;
                x = x.saturating_add(width);
            }
            text
        })
        .collect()
}

/// Return the first row whose drawing exceeds the buffer width. A wide glyph
/// placed in the final column is an overflow because its second cell is clipped.
pub fn buffer_overflow(buffer: &Buffer) -> Option<(u16, u16)> {
    let area = buffer.area;
    for y in 0..area.height {
        let mut x = 0;
        while x < area.width {
            let width = UnicodeWidthStr::width(buffer[(x, y)].symbol()).max(1) as u16;
            x = x.saturating_add(width);
        }
        if x > area.width {
            return Some((y, x - area.width));
        }
    }
    None
}

/// Restores the terminal even when setup fails after raw mode is enabled.
struct TerminalRestore;

impl Drop for TerminalRestore {
    fn drop(&mut self) {
        use crossterm::{
            cursor::Show,
            execute,
            terminal::{disable_raw_mode, LeaveAlternateScreen},
        };
        let _ = disable_raw_mode();
        let _ = execute!(std::io::stdout(), LeaveAlternateScreen, Show);
    }
}

/// Run the interactive terminal loop in mock mode.
pub fn run(mode: ColorMode, transparent: bool) -> Result<()> {
    run_with(mode, logo::DEFAULT_CANDIDATE, transparent)
}

/// Run the interactive loop with an explicit legacy candidate and the
/// `--transparent` intent.
pub fn run_with(mode: ColorMode, logo_candidate: usize, transparent: bool) -> Result<()> {
    run_with_workspace(mode, logo_candidate, transparent, None, "molt")
}

pub fn run_with_workspace(
    mode: ColorMode,
    logo_candidate: usize,
    transparent: bool,
    workspace: Option<std::path::PathBuf>,
    molt_bin: &str,
) -> Result<()> {
    if !terminal_available() {
        eprintln!("molt-tui requires a TTY; use --selftest");
        return Ok(());
    }
    let mut app = App::with_options_theme(mode, logo_candidate, transparent);
    if let Some(workspace) = workspace {
        app.version_workspace = Some(VersionWorkspace::connect(molt_bin, workspace)?);
    }
    run_terminal(app)
}

/// Run the interactive loop against the real DeepSeek endpoint. Key resolution
/// happens before the raw-mode setup but after the TTY check; a missing key is
/// handed to [`App::new_live`] as a system message, not an abort.
pub fn run_live(
    mode: ColorMode,
    model: &str,
    base_url: &str,
    transparent: bool,
    version_workspace: Option<std::path::PathBuf>,
    molt_bin: &str,
) -> Result<()> {
    if !terminal_available() {
        eprintln!("molt-tui requires a TTY; use --selftest");
        return Ok(());
    }
    let provider_result = provider::resolve_api_key().map(|api_key| ProviderConfig {
        base_url: provider::normalize_base_url(base_url),
        model: model.to_string(),
        api_key,
    });
    let store = SessionStore::default();
    let mut app = App::new_live(mode, provider_result, store);
    if let Some(workspace) = version_workspace {
        app.version_workspace = Some(VersionWorkspace::connect(molt_bin, workspace)?);
    }
    if transparent {
        app.theme = Theme::new(mode).with_transparent();
    }
    run_terminal(app)
}

fn terminal_available() -> bool {
    use std::io::IsTerminal;
    let dumb_terminal = std::env::var("TERM").is_ok_and(|term| term == "dumb");
    std::io::stdout().is_terminal() && !dumb_terminal
}

fn run_terminal(mut app: App) -> Result<()> {
    use crossterm::{
        cursor::Hide,
        execute,
        terminal::{enable_raw_mode, EnterAlternateScreen},
    };
    use ratatui::backend::CrosstermBackend;
    use ratatui::Terminal;
    use std::io::stdout;

    enable_raw_mode()?;
    let _restore = TerminalRestore;
    let mut output = stdout();
    execute!(output, EnterAlternateScreen, Hide)?;
    let backend = CrosstermBackend::new(output);
    let mut terminal = Terminal::new(backend)?;

    loop {
        app.tick(std::time::Instant::now());
        terminal.draw(|frame| app.draw(frame))?;
        // OSC 52 is a raw control sequence; send it after the frame so it
        // cannot interfere with the buffered cell diff.
        if let Some(sequence) = app.pending_osc.take() {
            crossterm::execute!(std::io::stdout(), crossterm::style::Print(sequence))?;
        }
        if crossterm::event::poll(std::time::Duration::from_millis(200))? {
            if let crossterm::event::Event::Key(key) = crossterm::event::read()? {
                app.handle_key(key);
            }
        }
        if app.quit {
            break;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Prefix of a real API key, split so secret scanners do not flag the
    /// leak assertions below.
    const KEY_PREFIX: &str = concat!("s", "k-");

    #[test]
    fn fit_never_exceeds_width() {
        assert_eq!(fit("abcdef", 3), "abc");
        assert_eq!(fit("中文字", 4), "中文");
        assert_eq!(fit("中文字", 5), "中文 ");
        assert_eq!(fit("abc", 0), "");
    }

    #[test]
    fn wrap_keeps_cjk_within_width() {
        let lines = wrap_text("中文中文中文", 4);
        assert!(lines
            .iter()
            .all(|line| UnicodeWidthStr::width(line.as_str()) <= 4));
        assert!(lines.len() >= 3);
    }

    #[test]
    fn tab_skips_approval() {
        assert_eq!(Screen::Startup.next(), Screen::Chat);
        assert_eq!(Screen::Chat.next(), Screen::Tree);
        assert_eq!(Screen::Diff.next(), Screen::Chat);
        assert_eq!(Screen::Approval.next(), Screen::Chat);
    }

    fn press(app: &mut App, code: KeyCode) {
        app.handle_key(KeyEvent::new(code, KeyModifiers::NONE));
    }

    fn press_control(app: &mut App, character: char) {
        app.handle_key(KeyEvent::new(
            KeyCode::Char(character),
            KeyModifiers::CONTROL,
        ));
    }

    #[test]
    fn version_panel_keys_move_history_selection() {
        use crate::version_workspace::{HistoryEvent, VersionHistory};

        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        let mut history_view = VersionHistory::default();
        history_view.replace_events(vec![
            HistoryEvent {
                event: "event-new".to_owned(),
                generation: 2,
                action: "commit".to_owned(),
            },
            HistoryEvent {
                event: "event-old".to_owned(),
                generation: 1,
                action: "commit".to_owned(),
            },
        ]);
        app.version_workspace = Some(VersionWorkspace {
            binary: "molt".into(),
            workspace: "/tmp/synthetic".into(),
            state_id: "synthetic-state".to_owned(),
            generation: 2,
            active_snapshot: None,
            last_event: Some("event-new".to_owned()),
            history_view,
            pending_request: None,
            last_save_receipt: None,
            detail: vec![
                "> g2 commit event-new".to_owned(),
                "  g1 commit event-old".to_owned(),
            ],
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        });
        app.version_panel_open = true;

        press(&mut app, KeyCode::Char('j'));

        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.detail[2], "> g1 commit event-old");
    }

    #[test]
    fn version_panel_page_keys_select_older_history() {
        use crate::version_workspace::{HistoryEvent, VersionHistory};

        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        let mut history_view = VersionHistory::default();
        history_view.replace_events(
            (0..17)
                .map(|index| HistoryEvent {
                    event: format!("event-{index}"),
                    generation: 17 - index,
                    action: "commit".to_owned(),
                })
                .collect(),
        );
        app.version_workspace = Some(VersionWorkspace {
            binary: "molt".into(),
            workspace: "/tmp/synthetic".into(),
            state_id: "synthetic-state".to_owned(),
            generation: 17,
            active_snapshot: None,
            last_event: Some("event-0".to_owned()),
            history_view,
            pending_request: None,
            last_save_receipt: None,
            detail: Vec::new(),
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        });
        app.version_panel_open = true;

        press(&mut app, KeyCode::PageDown);

        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.detail[0], "history 9-16 of 17");
        assert_eq!(workspace.detail[1], "> g9 commit event-8");

        press(&mut app, KeyCode::PageUp);

        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.detail[0], "history 1-8 of 17");
        assert_eq!(workspace.detail[1], "> g17 commit event-0");

        press(&mut app, KeyCode::Char('/'));
        press(&mut app, KeyCode::Char('g'));
        press(&mut app, KeyCode::Char('9'));
        press(&mut app, KeyCode::Enter);
        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.history_filter(), "g9");
        assert_eq!(workspace.detail[0], "history 1-1 of 1");
        assert_eq!(workspace.detail[1], "> g9 commit event-8");

        press(&mut app, KeyCode::Char('/'));
        press(&mut app, KeyCode::Char('a'));
        press(&mut app, KeyCode::Esc);
        assert!(app.version_panel_open);
        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.history_filter(), "g9");
        assert_eq!(workspace.detail[1], "> g9 commit event-8");

        press(&mut app, KeyCode::Char('c'));
        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.history_filter(), "");
        assert_eq!(workspace.detail[0], "history 1-8 of 17");
    }

    #[test]
    fn version_panel_b_reports_file_recovery_disabled_without_calling_molt() {
        use crate::version_workspace::VersionHistory;
        use std::os::unix::fs::PermissionsExt;

        let directory = std::env::temp_dir().join(format!(
            "molt-tui-b-disabled-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&directory).unwrap();
        let binary = directory.join("molt");
        let argv_log = directory.join("argv.log");
        std::fs::write(
            &binary,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{}'\nexit 1\n",
                argv_log.display()
            ),
        )
        .unwrap();
        let mut permissions = std::fs::metadata(&binary).unwrap().permissions();
        permissions.set_mode(0o755);
        std::fs::set_permissions(&binary, permissions).unwrap();

        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        app.version_workspace = Some(VersionWorkspace {
            binary,
            workspace: "/tmp/synthetic".into(),
            state_id: "synthetic-state".to_owned(),
            generation: 2,
            active_snapshot: Some("snapshot-b".to_owned()),
            last_event: Some("event-b".to_owned()),
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
            detail: Vec::new(),
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        });
        app.version_panel_open = true;

        press(&mut app, KeyCode::Char('b'));

        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.generation, 2);
        assert_eq!(workspace.active_snapshot.as_deref(), Some("snapshot-b"));
        assert_eq!(workspace.detail[0], FILE_RECOVERY_DISABLED);
        assert!(app
            .status_text()
            .is_some_and(|status| status.contains(FILE_RECOVERY_DISABLED)));
        assert!(
            !argv_log.exists(),
            "b must not invoke the molt CLI: {:?}",
            std::fs::read_to_string(&argv_log)
        );
        drop(app);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn managed_tree_keys_browse_exports_without_running_mock_actions() {
        use crate::version_workspace::VersionHistory;
        use std::path::PathBuf;

        let tree = std::env::temp_dir().join(format!(
            "molt-tui-tree-keys-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&tree).unwrap();
        std::fs::write(tree.join("a.txt"), "alpha").unwrap();
        std::fs::write(tree.join("b.txt"), "bravo").unwrap();

        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        app.mode = DataMode::Live;
        app.version_workspace = Some(VersionWorkspace {
            binary: "molt".into(),
            workspace: "/tmp/synthetic".into(),
            state_id: "synthetic-state".to_owned(),
            generation: 2,
            active_snapshot: None,
            last_event: None,
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
            detail: Vec::new(),
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: Some(tree.clone()),
            reader_snapshot: Some("snapshot-b".to_owned()),
            reader_files: vec![PathBuf::from("a.txt"), PathBuf::from("b.txt")],
            reader_selected: 0,
            reader_preview: "alpha".to_owned(),
            retired_reader_parents: Vec::new(),
        });
        assert!(app.unavailable_panel().is_none());
        let mock_selected = app.tree.selected;
        let mock_session_count = app.mock.sessions.len();

        press(&mut app, KeyCode::Char('j'));
        press(&mut app, KeyCode::Char('f'));
        press(&mut app, KeyCode::Char('r'));
        press(&mut app, KeyCode::Char('p'));
        press(&mut app, KeyCode::Char(' '));

        let workspace = app.version_workspace.as_ref().unwrap();
        assert_eq!(workspace.reader_selected, 1);
        assert_eq!(workspace.reader_preview, "bravo");
        assert_eq!(app.tree.selected, mock_selected);
        assert!(app.tree.marks.is_empty());
        assert!(app.tree.pinned.is_empty());
        assert!(app.tree.rollback.is_none());
        assert_eq!(app.mock.sessions.len(), mock_session_count);
        drop(app);
        std::fs::remove_dir_all(tree).unwrap();
    }

    #[test]
    fn versions_panel_opens_the_managed_tree() {
        use crate::version_workspace::VersionHistory;

        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        app.mode = DataMode::Live;
        app.version_workspace = Some(VersionWorkspace {
            binary: "molt".into(),
            workspace: "/tmp/synthetic".into(),
            state_id: "synthetic-state".to_owned(),
            generation: 0,
            active_snapshot: None,
            last_event: None,
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
            detail: Vec::new(),
            draft: String::new(),
            editing: false,
            pinned_reader: None,
            reader_parent: None,
            reader_tree: None,
            reader_snapshot: None,
            reader_files: Vec::new(),
            reader_selected: 0,
            reader_preview: String::new(),
            retired_reader_parents: Vec::new(),
        });

        press_control(&mut app, 'v');
        press(&mut app, KeyCode::Char('t'));

        assert!(!app.version_panel_open);
        assert_eq!(app.screen, Screen::Tree);
    }

    #[test]
    fn startup_is_the_initial_screen() {
        let app = App::new(ColorMode::Ansi256);
        assert_eq!(app.screen, Screen::Startup);
        assert_eq!(app.mode, DataMode::Mock);
        assert_eq!(app.logo_candidate, logo::DEFAULT_CANDIDATE);
        assert!(!app.composer_focused);
        assert!(app.input.is_empty());
        assert!(!app.last_assistant.is_empty());
        assert!(!app.streaming);
    }

    #[test]
    fn live_demo_only_panels_report_unavailable() {
        for screen in [
            Screen::Tree,
            Screen::Settings,
            Screen::Diff,
            Screen::Approval,
        ] {
            let mut app = App::with_screen(ColorMode::Ansi256, screen);
            app.mode = DataMode::Live;
            assert_eq!(app.unavailable_panel(), Some(screen));
        }

        let mut chat = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        chat.mode = DataMode::Live;
        assert_eq!(chat.unavailable_panel(), None);

        let mock = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        assert_eq!(mock.unavailable_panel(), None);
    }

    #[test]
    fn any_key_leaves_startup() {
        let mut app = App::new(ColorMode::Ansi256);
        app.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
        assert_eq!(app.screen, Screen::Chat);
        assert!(app.composer_focused);
    }

    #[test]
    fn startup_q_becomes_the_first_composer_character() {
        let mut app = App::new(ColorMode::Ansi256);
        press(&mut app, KeyCode::Char('q'));
        assert_eq!(app.screen, Screen::Chat);
        assert!(app.composer_focused);
        assert_eq!(app.input, "q");
        assert!(!app.quit);
    }

    #[test]
    fn ctrl_q_quits_and_q_does_not() {
        for screen in Screen::ALL {
            let mut app = App::with_screen(ColorMode::Ansi256, screen);
            press(&mut app, KeyCode::Char('q'));
            assert!(!app.quit, "plain q quit from {}", screen.title());
        }
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        app.handle_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL));
        assert!(app.quit);
    }

    #[test]
    fn composer_types_q_and_enter_appends_a_mock_message() {
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        assert!(app.composer_focused);
        let before = app.mock.messages.len();

        press(&mut app, KeyCode::Char('q'));
        assert_eq!(app.input, "q");
        assert!(!app.quit);

        press(&mut app, KeyCode::Char('a'));
        press(&mut app, KeyCode::Char('i'));
        assert_eq!(app.input, "qai");

        press(&mut app, KeyCode::Enter);
        assert_eq!(app.mock.messages.len(), before + 1);
        assert_eq!(app.mock.messages.last().unwrap().role, MessageRole::User);
        assert_eq!(app.mock.messages.last().unwrap().body, "qai");
        assert!(app.input.is_empty());
        assert_eq!(app.status_text(), Some("sent (mock)"));
    }

    #[test]
    fn escape_leaves_the_composer_and_q_never_quits() {
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        press(&mut app, KeyCode::Esc);
        assert!(!app.composer_focused);
        press(&mut app, KeyCode::Char('q'));
        assert!(!app.quit);
        assert!(app.input.is_empty());

        press(&mut app, KeyCode::Esc);
        assert!(app.composer_focused);
    }

    #[test]
    fn y_copies_the_last_assistant_answer() {
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        press(&mut app, KeyCode::Esc);
        press(&mut app, KeyCode::Char('y'));
        let sequence = app.pending_osc.as_deref().unwrap_or_default();
        assert!(sequence.starts_with("\x1b]52;c;"), "{sequence:?}");
        assert!(sequence.ends_with('\x07'), "{sequence:?}");
        assert_eq!(app.status_text(), Some("copied last answer (OSC52, mock)"));
    }

    #[test]
    fn tab_returns_to_chat_with_focus() {
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.screen, Screen::Tree);
        assert!(!app.composer_focused);
        press(&mut app, KeyCode::Tab);
        press(&mut app, KeyCode::Tab);
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.screen, Screen::Chat);
        assert!(app.composer_focused);
    }

    #[test]
    fn help_freezes_the_composer_and_ctrl_q_still_quits() {
        let mut app = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        app.handle_key(KeyEvent::new(KeyCode::Char('?'), KeyModifiers::SHIFT));
        assert!(app.show_help);
        press(&mut app, KeyCode::Char('q'));
        assert!(!app.quit);
        assert!(app.input.is_empty());
        app.handle_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL));
        assert!(app.quit);
    }

    #[test]
    fn status_expires_after_three_seconds() {
        let mut app = App::new(ColorMode::Ansi256);
        app.set_status(StatusLevel::Success, "applied base");
        assert_eq!(app.status_text(), Some("applied base"));
        app.tick(app.now + Duration::from_secs(4));
        assert_eq!(app.status_text(), None);
    }

    // ---- W-21b: live Chat, streaming, cancellation and session loading ----

    use crate::provider::ChatMessage;
    use crate::session::SessionStore;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::AtomicU64;

    static TEMP_SEQ: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "molt-tui-app-{tag}-{}-{}",
                std::process::id(),
                TEMP_SEQ.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("create temp dir");
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn temp_store(tag: &str) -> (TempDir, SessionStore) {
        let temp = TempDir::new(tag);
        let store = SessionStore::new(temp.0.join("sessions"));
        (temp, store)
    }

    fn test_config() -> ProviderConfig {
        ProviderConfig {
            base_url: "http://127.0.0.1:9".to_string(),
            model: "deepseek-flash".to_string(),
            api_key: "test-key".to_string(),
        }
    }

    fn render_app(app: &App, width: u16, height: u16) -> Vec<String> {
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).expect("test backend");
        terminal
            .draw(|frame| app.draw(frame))
            .expect("test backend");
        buffer_lines(terminal.backend().buffer())
    }

    #[test]
    fn live_provider_error_blocks_send_and_renders_a_system_message() {
        let (_temp, store) = temp_store("provider-error");
        let mut app = App::new_live(
            ColorMode::Ansi256,
            Err("set DEEPSEEK_API_KEY or MOLT_API_KEY_HELPER".to_string()),
            store,
        );
        assert_eq!(app.mode, DataMode::Live);
        assert_eq!(app.screen, Screen::Chat);
        assert!(app.provider.is_none());
        assert!(app
            .provider_error
            .as_deref()
            .is_some_and(|error| error.contains("DEEPSEEK_API_KEY")));

        let before = app.session.as_ref().unwrap().messages.len();
        for character in "hello".chars() {
            press(&mut app, KeyCode::Char(character));
        }
        press(&mut app, KeyCode::Enter);
        assert_eq!(app.session.as_ref().unwrap().messages.len(), before);
        assert!(!app.streaming);
        let status = app.status_text().unwrap_or_default();
        assert!(status.contains("DEEPSEEK_API_KEY"), "{status}");
        assert!(!status.contains(KEY_PREFIX), "{status}");

        let text = render_app(&app, 120, 40).join("\n");
        assert!(text.contains("[SYSTEM]"), "{text}");
        assert!(text.contains("provider unavailable"), "{text}");
        assert!(!text.contains(KEY_PREFIX), "{text}");
    }

    #[test]
    fn live_session_picker_reopens_a_saved_conversation() {
        let (_temp, store) = temp_store("session-picker");
        let prior = store.new_session().expect("prior session");
        store
            .append(
                &prior,
                &ChatMessage {
                    role: "user".to_string(),
                    content: "older prompt".to_string(),
                },
            )
            .expect("append prior user message");
        store
            .append(
                &prior,
                &ChatMessage {
                    role: "assistant".to_string(),
                    content: "older answer".to_string(),
                },
            )
            .expect("append prior assistant message");
        let current = store.new_session().expect("current session");
        store
            .append(
                &current,
                &ChatMessage {
                    role: "user".to_string(),
                    content: "current prompt".to_string(),
                },
            )
            .expect("append current prompt");

        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store.clone());
        app.input = "unsent draft".to_string();
        assert_eq!(app.session.as_ref().unwrap().id, current.id);
        press_control(&mut app, 'o');
        assert!(app.modal());
        assert!(app.session_picker.is_some());

        let text = render_app(&app, 80, 24).join("\n");
        assert!(text.contains("SESSIONS"), "{text}");
        assert!(text.contains("older prompt"), "{text}");
        let target = app
            .session_picker
            .as_ref()
            .unwrap()
            .sessions
            .iter()
            .position(|summary| summary.id == prior.id)
            .expect("prior session is listed");
        for _ in 0..target {
            press(&mut app, KeyCode::Char('j'));
        }
        press(&mut app, KeyCode::Enter);

        assert!(app.session_picker.is_none());
        let loaded = app.session.as_ref().unwrap();
        assert_eq!(loaded.id, prior.id);
        assert_eq!(loaded.messages.len(), 2);
        assert_eq!(app.last_assistant, "older answer");
        assert_eq!(app.screen, Screen::Chat);
        assert_eq!(app.input, "unsent draft");
        assert!(app
            .status_text()
            .is_some_and(|status| status == format!("loaded session {}", prior.id)));
    }

    #[test]
    fn live_session_picker_does_not_open_during_a_stream() {
        let (_temp, store) = temp_store("picker-streaming");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        app.streaming = true;

        press_control(&mut app, 'o');

        assert!(app.session_picker.is_none());
        assert!(app
            .status_text()
            .is_some_and(|status| status.contains("cancel the stream")));
    }

    #[test]
    fn live_delta_then_done_appends_and_persists_the_assistant_turn() {
        let (_temp, store) = temp_store("done");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store.clone());
        let (tx, rx) = mpsc::channel();
        app.live_rx = Some(rx);
        app.streaming = true;
        app.streaming_text.clear();

        tx.send(ProviderEvent::Delta("Hel".to_string())).unwrap();
        app.tick(app.now);
        assert_eq!(app.streaming_text, "Hel");
        assert!(app.streaming);

        tx.send(ProviderEvent::Delta("lo".to_string())).unwrap();
        tx.send(ProviderEvent::Usage(Some(Usage {
            input_tokens: Some(3),
            output_tokens: Some(2),
            total_tokens: Some(5),
        })))
        .unwrap();
        tx.send(ProviderEvent::Done).unwrap();
        app.tick(app.now);

        assert!(!app.streaming);
        assert_eq!(app.last_assistant, "Hello");
        assert!(app.usage.is_some());
        let messages = &app.session.as_ref().unwrap().messages;
        assert_eq!(messages.last().unwrap().role, "assistant");
        assert_eq!(messages.last().unwrap().content, "Hello");
        assert_eq!(app.status_text(), Some("stream complete"));

        let loaded = store
            .load_latest()
            .expect("load latest")
            .expect("one session");
        assert!(loaded
            .messages
            .iter()
            .any(|message| message.role == "assistant" && message.content == "Hello"));
    }

    #[test]
    fn live_error_keeps_a_partial_answer_marked_interrupted() {
        let (_temp, store) = temp_store("partial-error");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        app.streaming = true;
        app.handle_live_event(ProviderEvent::Delta("partial".to_string()));
        app.handle_live_event(ProviderEvent::Error("boom".to_string()));

        assert!(!app.streaming);
        let last = app.session.as_ref().unwrap().messages.last().unwrap();
        assert_eq!(last.role, "assistant");
        assert!(last.content.starts_with("partial"), "{}", last.content);
        assert!(last.content.contains("interrupted"), "{}", last.content);
        assert!(app
            .status_text()
            .is_some_and(|status| status.contains("boom")));
    }

    #[test]
    fn live_error_without_a_partial_answer_appends_nothing() {
        let (_temp, store) = temp_store("empty-error");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        let before = app.session.as_ref().unwrap().messages.len();
        app.streaming = true;
        app.handle_live_event(ProviderEvent::Error("cancelled".to_string()));

        assert!(!app.streaming);
        assert_eq!(app.session.as_ref().unwrap().messages.len(), before);
        assert!(app
            .status_text()
            .is_some_and(|status| status.contains("cancelled")));
    }

    #[test]
    fn live_ctrl_c_sets_the_cancel_flag_and_waits_for_the_interrupt() {
        let (_temp, store) = temp_store("cancel");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        let cancel = Arc::new(AtomicBool::new(false));
        app.live_cancel = Some(Arc::clone(&cancel));
        app.streaming = true;

        app.handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL));
        assert!(cancel.load(Ordering::Relaxed));
        assert!(app.streaming, "the worker still owns the partial turn");
        assert_eq!(app.status_text(), Some("cancelling…"));
        assert!(!app.quit);

        app.handle_live_event(ProviderEvent::Error("cancelled".to_string()));
        assert!(!app.streaming);
        assert!(!app.quit);
        assert!(app
            .status_text()
            .is_some_and(|status| status.contains("cancelled")));
    }

    #[test]
    fn live_startup_loads_the_latest_session_into_chat() {
        let (_temp, store) = temp_store("load");
        let session = store.new_session().expect("new session");
        store
            .append(
                &session,
                &ChatMessage {
                    role: "user".to_string(),
                    content: "earlier question".to_string(),
                },
            )
            .expect("append user");
        store
            .append(
                &session,
                &ChatMessage {
                    role: "assistant".to_string(),
                    content: "earlier answer".to_string(),
                },
            )
            .expect("append assistant");

        let app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        assert_eq!(app.screen, Screen::Chat);
        assert_eq!(app.session.as_ref().unwrap().id, session.id);
        assert_eq!(app.session.as_ref().unwrap().messages.len(), 2);
        assert_eq!(app.last_assistant, "earlier answer");

        let text = render_app(&app, 120, 40).join("\n");
        assert!(text.contains("earlier question"), "{text}");
        assert!(text.contains("earlier answer"), "{text}");
        assert!(text.contains("[YOU]"), "{text}");
        assert!(text.contains("[MOLT]"), "{text}");
    }

    #[test]
    fn live_streaming_renders_the_delta_with_a_cursor() {
        let (_temp, store) = temp_store("render-stream");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        app.streaming = true;
        app.handle_live_event(ProviderEvent::Delta("half".to_string()));
        app.handle_live_event(ProviderEvent::Reasoning("thinking".to_string()));

        let text = render_app(&app, 120, 40).join("\n");
        assert!(text.contains("half▌"), "{text}");
        assert!(text.contains("[THINK]"), "{text}");
        assert!(text.contains("thinking"), "{text}");
    }

    #[test]
    fn live_ctrl_n_creates_a_fresh_session() {
        let (_temp, store) = temp_store("ctrl-n");
        let mut app = App::new_live(ColorMode::Ansi256, Ok(test_config()), store);
        let before = app.session.as_ref().unwrap().id.clone();
        app.handle_key(KeyEvent::new(KeyCode::Char('n'), KeyModifiers::CONTROL));
        let after = app.session.as_ref().unwrap().id.clone();
        assert_ne!(before, after);
        assert!(app.session.as_ref().unwrap().messages.is_empty());
        assert_eq!(app.status_text(), Some("new session"));
    }
}
