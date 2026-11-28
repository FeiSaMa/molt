//! W-09 interaction tests: tree/settings/approval/diff keys and state frames.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use molt_tui::{app, App, ColorMode, DataMode, Screen};
use ratatui::{backend::TestBackend, Terminal};
use unicode_width::UnicodeWidthStr;

fn press(application: &mut App, code: KeyCode) {
    application.handle_key(KeyEvent::new(code, KeyModifiers::NONE));
}

fn press_shift(application: &mut App, character: char) {
    application.handle_key(KeyEvent::new(KeyCode::Char(character), KeyModifiers::SHIFT));
}

fn render(application: &App, width: u16, height: u16) -> Vec<String> {
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

fn assert_rows_fit(lines: &[String], width: u16) {
    for (row, line) in lines.iter().enumerate() {
        let cells = UnicodeWidthStr::width(line.as_str());
        assert!(
            cells <= usize::from(width),
            "row {row} is {cells} cells: {line:?}"
        );
    }
}

/// A Live app whose non-Chat panels have no real backend attached.
fn live(screen: Screen) -> App {
    let mut application = App::with_screen(ColorMode::Ansi256, screen);
    application.mode = DataMode::Live;
    application
}

#[test]
fn tree_j_k_moves_the_selection() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    let start = application.tree.selected;
    press(&mut application, KeyCode::Char('j'));
    assert_eq!(application.tree.selected, start + 1);
    press(&mut application, KeyCode::Char('j'));
    assert_eq!(application.tree.selected, start + 2);
    press(&mut application, KeyCode::Char('k'));
    assert_eq!(application.tree.selected, start + 1);
}

#[test]
fn tree_f_fork_creates_a_mock_session() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    let before = application.mock.sessions.len();
    press(&mut application, KeyCode::Char('f'));
    assert_eq!(application.mock.sessions.len(), before + 1);
    let forked = application.mock.sessions.last().unwrap();
    assert_eq!(forked.parent.as_deref(), Some("demo-6"));
    assert_eq!(forked.kind, molt_tui::mock::SessionKind::Fork);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("forked")));
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("(mock)")));
}

#[test]
fn tree_r_opens_the_rollback_modal_with_compaction_warning() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    press(&mut application, KeyCode::Char('r'));
    assert!(application.tree.rollback.is_some());
    assert!(application.modal());
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("ROLLBACK"), "{text}");
    assert!(text.contains("回退后只能看到摘要"), "{text}");
    assert!(text.contains("backup"), "{text}");
}

#[test]
fn tree_rollback_files_are_disabled_without_a_checkpoint() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(2, 1);
    press(&mut application, KeyCode::Char('r'));
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(!state.files_available);
    assert!(!state.files_on);
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    assert!(!application.tree.rollback.as_ref().unwrap().files_on);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("cannot_restore_files")));
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("cannot_restore_files"), "{text}");
}

#[test]
fn tree_space_marks_nodes_and_counts_them() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    press(&mut application, KeyCode::Char(' '));
    assert_eq!(application.tree.marks.len(), 1);
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    assert_eq!(application.tree.marks.len(), 2);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("2 marked")));
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("2 marked"), "{text}");
}

#[test]
fn tree_rollback_confirm_updates_leaf_and_config() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    assert!(application.tree.rollback.as_ref().unwrap().files_on);
    press(&mut application, KeyCode::Enter);
    assert!(application.tree.rollback.is_some());
    assert!(application.tree.rollback.as_ref().unwrap().confirm_stage);
    assert_eq!(application.mock.sessions[0].leaf, "n5");
    assert_eq!(application.mock.active_config, "fast-edit");
    press(&mut application, KeyCode::Enter);
    assert!(application.tree.rollback.is_none());
    assert_eq!(application.mock.sessions[0].leaf, "n4");
    assert_eq!(application.mock.active_config, "low/full");
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("simulated rollback (mock) (files=3)")));
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("summary only")));
}

#[test]
fn tree_rollback_first_enter_stages_and_second_enter_applies() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    assert!(application.tree.rollback.as_ref().unwrap().files_on);
    press(&mut application, KeyCode::Enter);
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(state.confirm_stage);
    assert_eq!(state.checkpoint_id.as_deref(), Some("n3"));
    assert_eq!(state.files, 3);
    assert!(!application.quit);
    assert_eq!(application.mock.sessions[0].leaf, "n5");
    assert_eq!(application.mock.active_config, "fast-edit");
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("press Enter/y again to apply"), "{text}");
    assert!(text.contains("backup"), "{text}");

    press(&mut application, KeyCode::Esc);
    assert!(!application.tree.rollback.as_ref().unwrap().confirm_stage);
    assert_eq!(application.mock.sessions[0].leaf, "n5");

    press(&mut application, KeyCode::Enter);
    press(&mut application, KeyCode::Char('y'));
    assert!(application.tree.rollback.is_none());
    assert_eq!(application.mock.sessions[0].leaf, "n4");
}

#[test]
fn tree_rollback_session_only_applies_on_first_enter() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(state.session_on && !state.config_on && !state.files_on);
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("Enter apply"), "{text}");

    press(&mut application, KeyCode::Enter);
    assert!(application.tree.rollback.is_none());
    assert_eq!(application.mock.sessions[0].leaf, "n4");
    assert_eq!(application.mock.active_config, "fast-edit");
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("simulated rollback (mock) (files=0)")));
}

#[test]
fn tree_rollback_config_only_applies_on_first_enter() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    press(&mut application, KeyCode::Char(' '));
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(!state.session_on && state.config_on && !state.files_on);

    press(&mut application, KeyCode::Enter);
    assert!(application.tree.rollback.is_none());
    assert_eq!(application.mock.sessions[0].leaf, "n5");
    assert_eq!(application.mock.active_config, "low/full");
}

#[test]
fn tree_rollback_reports_a_missing_mock_config() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    application
        .mock
        .configs
        .retain(|config| config.id != "low/full");

    press(&mut application, KeyCode::Enter);
    assert!(application.tree.rollback.is_none());
    assert_eq!(application.mock.sessions[0].leaf, "n4");
    assert_eq!(application.mock.active_config, "fast-edit");
    assert_eq!(
        application.status.as_ref().unwrap().level,
        app::StatusLevel::Warning
    );
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("config not found (mock)")));
}

#[test]
fn tree_rollback_rejects_zero_targets() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('r'));
    press(&mut application, KeyCode::Char(' '));
    press(&mut application, KeyCode::Char('j'));
    press(&mut application, KeyCode::Char(' '));
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(!state.session_on && !state.config_on && !state.files_on);

    press(&mut application, KeyCode::Enter);
    let state = application.tree.rollback.as_ref().unwrap();
    assert!(!state.confirm_stage);
    assert_eq!(application.mock.sessions[0].leaf, "n5");
    assert_eq!(application.mock.active_config, "fast-edit");
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("select at least one target")));
}

#[test]
fn settings_a_applies_the_selected_config() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    application.select_config(1);
    press(&mut application, KeyCode::Char('a'));
    assert_eq!(application.mock.active_config, "low/full");
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("applied low/full")));
}

#[test]
fn settings_r_cycles_suggest_auto_off() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    assert_eq!(application.settings.routing.label(), "suggest");
    press(&mut application, KeyCode::Char('r'));
    assert_eq!(application.settings.routing.label(), "auto");
    press(&mut application, KeyCode::Char('r'));
    assert_eq!(application.settings.routing.label(), "off");
    press(&mut application, KeyCode::Char('r'));
    assert_eq!(application.settings.routing.label(), "suggest");
}

#[test]
fn settings_rejects_a_scenario_version_mismatch() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    application.select_config(3);
    let active = application.mock.active_config.clone();
    press(&mut application, KeyCode::Char('a'));
    assert_eq!(application.mock.active_config, active);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("scenario_mismatch")));
    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("scenario_mismatch"), "{text}");
}

#[test]
fn approval_y_once_removes_the_pending_item() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    assert_eq!(application.mock.pending_approvals().count(), 1);
    press(&mut application, KeyCode::Char('y'));
    assert_eq!(application.mock.pending_approvals().count(), 0);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("once (approved)")));
}

#[test]
fn approval_shift_a_saves_a_session_rule_for_24h() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    press_shift(&mut application, 'A');
    assert_eq!(application.mock.pending_approvals().count(), 0);
    assert_eq!(application.approvals.saved_rules.len(), 1);
    assert!(application.approvals.saved_rules[0].contains("scope=session"));
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("24h")));
}

#[test]
fn approval_x_rejects_and_removes_the_pending_item() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    press(&mut application, KeyCode::Char('x'));
    assert_eq!(application.mock.pending_approvals().count(), 0);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("rejected")));
}

#[test]
fn diff_n_and_shift_n_walk_the_three_hunks() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Diff);
    assert_eq!(application.diff.current, 0);
    press(&mut application, KeyCode::Char('n'));
    assert_eq!(application.diff.current, 1);
    press(&mut application, KeyCode::Char('n'));
    assert_eq!(application.diff.current, 2);
    press(&mut application, KeyCode::Char('n'));
    assert_eq!(application.diff.current, 0);
    press_shift(&mut application, 'N');
    assert_eq!(application.diff.current, 2);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("hunk 3/3")));
}

#[test]
fn diff_split_is_rejected_at_80_columns_and_allowed_at_120() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Diff);
    let _ = render(&application, 80, 24);
    press(&mut application, KeyCode::Char('s'));
    assert!(!application.diff.split);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("120")));

    let _ = render(&application, 120, 40);
    press(&mut application, KeyCode::Char('s'));
    assert!(application.diff.split);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("layout: split")));
}

#[test]
fn new_states_fit_80x24() {
    let mut tree = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    tree.select_tree_node(0, 3);
    let mut rollback = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    rollback.select_tree_node(0, 3);
    press(&mut rollback, KeyCode::Char('r'));
    let mut disabled = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    disabled.select_tree_node(2, 1);
    press(&mut disabled, KeyCode::Char('r'));
    let mut confirm = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    confirm.select_tree_node(0, 3);
    press(&mut confirm, KeyCode::Char('r'));
    press(&mut confirm, KeyCode::Char('j'));
    press(&mut confirm, KeyCode::Char('j'));
    press(&mut confirm, KeyCode::Char(' '));
    press(&mut confirm, KeyCode::Enter);
    let mut settings = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    settings.select_config(1);
    let mut detail = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    detail.select_config(1);
    press(&mut detail, KeyCode::Enter);
    let mut approval = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    press_shift(&mut approval, 'A');
    let mut diff = App::with_screen(ColorMode::Ansi256, Screen::Diff);
    let _ = render(&diff, 120, 40);
    press(&mut diff, KeyCode::Char('s'));

    for (name, application) in [
        ("tree-detail", &tree),
        ("rollback", &rollback),
        ("rollback-confirm", &confirm),
        ("rollback-disabled", &disabled),
        ("settings-diff", &settings),
        ("settings-detail", &detail),
        ("approval-saved-rule", &approval),
        ("diff-split", &diff),
    ] {
        for (width, height) in [(80, 24), (120, 40)] {
            let lines = render(application, width, height);
            assert_eq!(lines.len(), usize::from(height), "{name}");
            assert_rows_fit(&lines, width);
        }
    }
}

#[test]
fn composer_is_editable_in_chat_only() {
    let mut chat = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    assert!(chat.composer_focused);
    for character in "hello q".chars() {
        press(&mut chat, KeyCode::Char(character));
    }
    let text = render(&chat, 120, 40).join("\n");
    assert!(text.contains("> hello q_"), "{text}");
    assert!(!text.contains("COMPOSER · disabled"), "{text}");

    let help = {
        let mut application = App::with_screen(ColorMode::Ansi256, Screen::Chat);
        application.show_help = true;
        application
    };
    let text = render(&help, 120, 40).join("\n");
    assert!(text.contains("COMPOSER · disabled"), "{text}");

    let approval = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    let text = render(&approval, 120, 40).join("\n");
    assert!(text.contains("COMPOSER · disabled"), "{text}");
}

#[test]
fn chat_composer_hint_replaces_the_old_q_quits_footer() {
    let chat = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    let text = render(&chat, 120, 40).join("\n");
    assert!(!text.contains("q quits"), "{text}");
    assert!(text.contains("Ctrl+Q quit"), "{text}");
    assert!(text.contains("Enter send"), "{text}");
}

#[test]
fn tree_search_filters_nodes_and_esc_clears() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    press(&mut application, KeyCode::Char('/'));
    assert!(application.tree.search_input);
    for character in "compaction".chars() {
        press(&mut application, KeyCode::Char(character));
    }
    assert_eq!(application.tree.query, "compaction");
    let rows = application.tree_rows();
    let nodes: Vec<_> = rows.iter().filter(|row| row.node.is_some()).collect();
    assert!(!nodes.is_empty());
    for row in nodes {
        let node = &application.mock.sessions[row.session].nodes[row.node.unwrap()];
        assert!(
            node.kind.label() == "compaction" || node.summary.contains("compaction"),
            "unexpected filtered node {node:?}"
        );
    }
    press(&mut application, KeyCode::Esc);
    assert!(!application.tree.search_input);
    assert!(application.tree.query.is_empty());
}

#[test]
fn tree_p_pins_and_unpins_the_selected_node() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    press(&mut application, KeyCode::Char('p'));
    assert_eq!(application.tree.pinned.len(), 1);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("pinned")));
    press(&mut application, KeyCode::Char('p'));
    assert!(application.tree.pinned.is_empty());
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("unpinned")));
}

#[test]
fn approval_q_does_not_quit_but_ctrl_q_does() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    press(&mut application, KeyCode::Char('q'));
    assert!(!application.quit);
    application.handle_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL));
    assert!(application.quit);
}

#[test]
fn ctrl_c_cancels_the_stream_without_quitting() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    application.start_mock_stream();
    assert!(application.streaming);
    application.handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL));
    assert!(!application.quit);
    assert!(!application.streaming);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("turn cancelled")));
}

#[test]
fn ctrl_c_twice_quits_and_disarms_after_three_seconds() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    let interrupt = || KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
    application.handle_key(interrupt());
    assert!(!application.quit);
    assert!(application.quit_armed);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("press Ctrl+C again to quit")));

    application.handle_key(interrupt());
    assert!(application.quit);

    let mut expired = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    expired.handle_key(interrupt());
    assert!(expired.quit_armed);
    expired.tick(expired.now + std::time::Duration::from_secs(4));
    assert!(!expired.quit_armed);
    assert!(!expired.quit);
}

#[test]
fn ctrl_c_cancel_resets_the_quit_arm() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Chat);
    let interrupt = || KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);

    application.handle_key(interrupt());
    assert!(application.quit_armed);
    application.start_mock_stream();
    assert!(application.streaming);

    application.handle_key(interrupt());
    assert!(!application.quit);
    assert!(!application.streaming);
    assert!(!application.quit_armed);
    assert!(application.quit_armed_until.is_none());

    // A third Ctrl+C is a fresh arm, not an immediate quit.
    application.handle_key(interrupt());
    assert!(!application.quit);
    assert!(application.quit_armed);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("press Ctrl+C again to quit")));
}

#[test]
fn tree_f_fork_records_boundary_parent_and_origin() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    application.select_tree_node(0, 3);
    press(&mut application, KeyCode::Char('f'));
    let forked = application.mock.sessions.last().unwrap();
    assert_eq!(forked.nodes[0].kind, molt_tui::mock::NodeKind::Fork);
    assert_eq!(forked.nodes[0].origin_node_id.as_deref(), Some("n4"));
    assert_eq!(forked.nodes[0].parent_id, None);
    assert_eq!(forked.nodes[1].parent_id.as_deref(), Some("n1"));
    assert_eq!(forked.nodes[1].origin_node_id, None);

    let forked_index = application.mock.sessions.len() - 1;
    application.select_tree_node(forked_index, 0);
    let boundary = render(&application, 120, 40).join("\n");
    assert!(boundary.contains("[boundary]"), "{boundary}");
    application.select_tree_node(forked_index, 1);
    let leaf = render(&application, 120, 40).join("\n");
    assert!(leaf.contains("[leaf]"), "{leaf}");
}

#[test]
fn diff_split_falls_back_to_unified_when_the_terminal_shrinks() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Diff);
    let _ = render(&application, 120, 40);
    press(&mut application, KeyCode::Char('s'));
    assert!(application.diff.split);
    let lines = render(&application, 80, 24);
    let text = lines.join("\n");
    assert!(text.contains("3 hunks · unified"), "{text}");
}

#[test]
fn all_screens_survive_tiny_terminals() {
    for screen in Screen::ALL {
        for (width, height) in [(40, 10), (20, 5), (10, 3)] {
            let application = App::with_screen(ColorMode::Ansi16, screen);
            let _ = render(&application, width, height);
        }
    }
}

#[test]
fn status_messages_expire_after_three_seconds() {
    let mut application = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    application.select_config(1);
    press(&mut application, KeyCode::Char('a'));
    assert!(application.status_text().is_some());
    application.tick(application.now + std::time::Duration::from_secs(4));
    assert!(application.status_text().is_none());
}

#[test]
fn live_tree_without_a_workspace_hides_demo_nodes_and_blocks_mock_actions() {
    let mut application = live(Screen::Tree);
    assert_eq!(application.unavailable_panel(), Some(Screen::Tree));
    let sessions = application.mock.sessions.len();
    let selected = application.tree.selected;

    for key in ['j', 'f', 'r', 'p', ' ', '/'] {
        press(&mut application, KeyCode::Char(key));
    }

    assert_eq!(application.mock.sessions.len(), sessions);
    assert_eq!(application.tree.selected, selected);
    assert!(application.tree.marks.is_empty());
    assert!(application.tree.pinned.is_empty());
    assert!(application.tree.rollback.is_none());
    assert!(!application.tree.search_input);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("unavailable")));

    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("TREE · unavailable"), "{text}");
    assert!(
        text.contains("no real session-tree store is connected"),
        "{text}"
    );
    assert!(!text.contains("demo-6"), "{text}");
    assert!(!text.contains("NODE n"), "{text}");
    assert!(!text.contains("f fork"), "{text}");
}

#[test]
fn live_settings_hides_the_mock_dag_and_blocks_apply() {
    let mut application = live(Screen::Settings);
    assert_eq!(application.unavailable_panel(), Some(Screen::Settings));
    let active = application.mock.active_config.clone();
    let routing = application.settings.routing;

    for key in ['j', 'a', 'd', 'r'] {
        press(&mut application, KeyCode::Char(key));
    }
    press(&mut application, KeyCode::Enter);

    assert_eq!(application.mock.active_config, active);
    assert_eq!(application.settings.routing, routing);
    assert!(!application.settings.detail_open);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("settings unavailable")));

    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("SETTINGS · unavailable"), "{text}");
    assert!(
        text.contains("no real config backend is connected"),
        "{text}"
    );
    assert!(!text.contains("CONFIG DAG"), "{text}");
    assert!(!text.contains("a apply"), "{text}");
}

#[test]
fn live_diff_hides_mock_hunks_and_blocks_patch_actions() {
    let mut application = live(Screen::Diff);
    assert_eq!(application.unavailable_panel(), Some(Screen::Diff));

    for key in ['n', 's', 'a', 'r', 'o'] {
        press(&mut application, KeyCode::Char(key));
    }

    assert_eq!(application.diff.current, 0);
    assert!(!application.diff.split);
    assert!(!application.diff.restore);

    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("DIFF · unavailable"), "{text}");
    assert!(text.contains("no real diff backend is connected"), "{text}");
    assert!(!text.contains("+++"), "{text}");
    assert!(!text.contains("@@"), "{text}");
    assert!(!text.contains("3 hunks"), "{text}");
}

#[test]
fn live_approval_hides_the_mock_request_and_blocks_decisions() {
    let mut application = live(Screen::Approval);
    assert_eq!(application.unavailable_panel(), Some(Screen::Approval));
    let pending = application.mock.pending_approvals().count();

    press(&mut application, KeyCode::Char('y'));
    press_shift(&mut application, 'A');
    press(&mut application, KeyCode::Char('x'));

    assert_eq!(application.mock.pending_approvals().count(), pending);
    assert!(application.approvals.saved_rules.is_empty());
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("approval unavailable")));

    let text = render(&application, 120, 40).join("\n");
    assert!(text.contains("APPROVAL · unavailable"), "{text}");
    assert!(
        text.contains("no real permission engine is connected"),
        "{text}"
    );
    assert!(!text.contains("proc.exec"), "{text}");
    assert!(!text.contains("cargo test"), "{text}");
    assert!(!text.contains("once (approved)"), "{text}");

    press(&mut application, KeyCode::Esc);
    assert_eq!(application.screen, Screen::Chat);
    assert!(application.composer_focused);
}

#[test]
fn live_chat_keeps_the_composer_and_opens_only_the_unavailable_approval_screen() {
    let mut application = live(Screen::Chat);
    assert!(application.unavailable_panel().is_none());

    press(&mut application, KeyCode::Char('h'));
    press(&mut application, KeyCode::Char('i'));
    assert_eq!(application.input, "hi");

    // `a` opens the panel, but the panel has no real backend and refuses
    // every decision below.
    press(&mut application, KeyCode::Esc);
    press(&mut application, KeyCode::Char('a'));
    assert_eq!(application.screen, Screen::Approval);
    assert_eq!(application.unavailable_panel(), Some(Screen::Approval));

    let pending = application.mock.pending_approvals().count();
    press(&mut application, KeyCode::Char('y'));
    assert_eq!(application.mock.pending_approvals().count(), pending);
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("approval unavailable")));

    press(&mut application, KeyCode::Esc);
    assert_eq!(application.screen, Screen::Chat);
    assert!(application.composer_focused);
    assert_eq!(application.input, "hi");
    assert!(!application.quit);
}

#[test]
fn mock_mode_still_runs_the_demo_actions() {
    let mut tree = App::with_screen(ColorMode::Ansi256, Screen::Tree);
    assert!(tree.unavailable_panel().is_none());
    let sessions = tree.mock.sessions.len();
    press(&mut tree, KeyCode::Char('f'));
    assert_eq!(tree.mock.sessions.len(), sessions + 1);

    let mut settings = App::with_screen(ColorMode::Ansi256, Screen::Settings);
    settings.select_config(1);
    press(&mut settings, KeyCode::Char('a'));
    assert_eq!(settings.mock.active_config, "low/full");

    let mut approval = App::with_screen(ColorMode::Ansi256, Screen::Approval);
    press(&mut approval, KeyCode::Char('y'));
    assert_eq!(approval.mock.pending_approvals().count(), 0);

    let mut diff = App::with_screen(ColorMode::Ansi256, Screen::Diff);
    press(&mut diff, KeyCode::Char('a'));
    assert!(diff
        .status_text()
        .is_some_and(|status| status.contains("patch applied")));
}

// ---------------------------------------------------------------------------
// C5 file recovery: a fake `molt` CLI drives the rejection matrix, the
// exported tree, the manifest and the exact `commit-files` argv.
// ---------------------------------------------------------------------------

use molt_tui::version_workspace::{
    FileRecoveryOutcome, FileRecoveryReport, VersionHistory, VersionWorkspace,
};
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

const STATE_ID: &str = "state-1";
const FROM: &str = "1111111111111111111111111111111111111111111111111111111111111111";
const TO: &str = "2222222222222222222222222222222222222222222222222222222222222222";
const NEW: &str = "3333333333333333333333333333333333333333333333333333333333333333";
const SIDE: &str = "9999999999999999999999999999999999999999999999999999999999999999";
const BEFORE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HASH: &str = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const EVENT_1: &str = "sha256:4444444444444444444444444444444444444444444444444444444444444444";
const EVENT_2: &str = "sha256:5555555555555555555555555555555555555555555555555555555555555555";
const EVENT_3: &str = "sha256:6666666666666666666666666666666666666666666666666666666666666666";
const EVENT_4: &str = "sha256:7777777777777777777777777777777777777777777777777777777777777777";
const REQUEST_ID: &str = "synthetic-recovery-1234";

/// Linux hands every open descriptor to a `vfork` child, so rewriting an
/// executable while another thread forks can make a concurrent `execve` fail
/// with ETXTBSY. Script writes and the `b` spawn sequence take one lock.
static EXEC_LOCK: Mutex<()> = Mutex::new(());

fn exec_lock() -> std::sync::MutexGuard<'static, ()> {
    EXEC_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn press_b(application: &mut App) {
    let _guard = exec_lock();
    application.handle_key(KeyEvent::new(KeyCode::Char('b'), KeyModifiers::NONE));
}

struct FakeMolt {
    directory: PathBuf,
    binary: PathBuf,
    workspace: PathBuf,
}

impl FakeMolt {
    fn new(label: &str) -> Self {
        let directory = std::env::temp_dir().join(format!(
            "molt-tui-fake-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let workspace = directory.join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        fs::create_dir_all(directory.join("tree")).unwrap();
        let _guard = exec_lock();
        let binary = directory.join("molt");
        let script = format!(
            r#"#!/bin/sh
DIR='{dir}'
printf '%s\n' "$*" >> "$DIR/argv.log"
cmd="$4"
resp=""
codefile=""
errfile=""
case "$cmd" in
  status)
    n=0
    if [ -f "$DIR/status.count" ]; then n=$(cat "$DIR/status.count"); fi
    n=$((n+1))
    printf '%s' "$n" > "$DIR/status.count"
    resp="$DIR/status-$n.json"
    codefile="$DIR/status-$n.exit"
    errfile="$DIR/status-$n.err"
    if [ ! -f "$resp" ]; then resp="$DIR/status.json"; fi
    if [ ! -f "$codefile" ]; then codefile="$DIR/status.exit"; fi
    if [ ! -f "$errfile" ]; then errfile="$DIR/status.err"; fi
    ;;
  history)
    resp="$DIR/history.json"; codefile="$DIR/history.exit"; errfile="$DIR/history.err" ;;
  show)
    resp="$DIR/show-$5.json"; codefile="$DIR/show.exit"; errfile="$DIR/show.err"
    if [ ! -f "$resp" ]; then resp="$DIR/show.json"; fi
    ;;
  export)
    if [ -f "$DIR/export.json" ]; then
      resp="$DIR/export.json"; codefile="$DIR/export.exit"; errfile="$DIR/export.err"
    else
      into=""
      prev=""
      for a in "$@"; do
        if [ "$prev" = "--into" ]; then into="$a"; fi
        prev="$a"
      done
      tree="$into/molt-tree-fixture/project"
      mkdir -p "$tree"
      chmod 700 "$into/molt-tree-fixture"
      cp -R "$DIR/tree/." "$tree/"
      find "$tree" -type d -exec chmod 500 {{}} +
      find "$tree" -type f -exec chmod 400 {{}} +
      printf '{{"snapshot":"%s","tree":"%s"}}\n' "$5" "$tree"
      exit 0
    fi
    ;;
  commit-files)
    resp="$DIR/commit.json"; codefile="$DIR/commit.exit"; errfile="$DIR/commit.err"
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--manifest" ]; then cp "$a" "$DIR/manifest.json"; fi
      if [ "$prev" = "--root" ]; then printf '%s' "$a" > "$DIR/root.txt"; fi
      prev="$a"
    done
    ;;
  *)
    resp="$DIR/other.json"; codefile="$DIR/other.exit"; errfile="$DIR/other.err" ;;
esac
if [ "$cmd" = "commit-files" ] && [ -f "$DIR/signal" ]; then kill -TERM $$; sleep 1; fi
if [ -f "$errfile" ]; then cat "$errfile" >&2; fi
if [ -f "$resp" ]; then cat "$resp"; fi
if [ -f "$codefile" ]; then exit "$(cat "$codefile")"; fi
exit 0
"#,
            dir = directory.display()
        );
        let staging = directory.join("molt.staging");
        {
            use std::io::Write;
            use std::os::unix::fs::OpenOptionsExt;
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o755)
                .open(&staging)
                .unwrap();
            file.write_all(script.as_bytes()).unwrap();
            file.sync_all().unwrap();
        }
        fs::rename(&staging, &binary).unwrap();
        Self {
            directory,
            binary,
            workspace,
        }
    }

    fn respond(&self, name: &str, body: &str) {
        fs::write(self.directory.join(format!("{name}.json")), body).unwrap();
    }

    fn exit(&self, name: &str, code: i32) {
        fs::write(
            self.directory.join(format!("{name}.exit")),
            code.to_string(),
        )
        .unwrap();
    }

    fn stderr(&self, name: &str, text: &str) {
        fs::write(self.directory.join(format!("{name}.err")), text).unwrap();
    }

    fn signal(&self) {
        fs::write(self.directory.join("signal"), "term").unwrap();
    }

    fn tree_file(&self, path: &str, contents: &str) {
        let path = self.directory.join("tree").join(path);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, contents).unwrap();
    }

    fn argv(&self) -> String {
        fs::read_to_string(self.directory.join("argv.log")).unwrap_or_default()
    }

    fn manifest(&self) -> String {
        fs::read_to_string(self.directory.join("manifest.json")).unwrap_or_default()
    }

    fn exported_root(&self) -> PathBuf {
        PathBuf::from(
            fs::read_to_string(self.directory.join("root.txt"))
                .expect("commit-files received --root"),
        )
    }

    fn app(&self, generation: u64, active: &str, main_head: &str, last_event: &str) -> App {
        let mut application = App::with_screen(ColorMode::Ansi256, Screen::Tree);
        application.mode = DataMode::Live;
        application.version_workspace = Some(VersionWorkspace {
            binary: self.binary.clone(),
            workspace: self.workspace.clone(),
            state_id: STATE_ID.to_owned(),
            generation,
            active_snapshot: Some(active.to_owned()),
            last_event: Some(last_event.to_owned()),
            main_head: Some(main_head.to_owned()),
            history_view: VersionHistory::default(),
            pending_request: None,
            last_save_receipt: None,
            last_file_recovery: None,
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
        application.version_panel_open = true;
        application
    }
}

impl Drop for FakeMolt {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}

fn status_json(generation: u64, active: &str, main: &str, last_event: &str) -> String {
    format!(
        r#"{{"state_id":"{STATE_ID}","generation":{generation},"current":{{"generation":{generation}}},"active":"{active}","last_event":"{last_event}","branches":{{"main":"{main}"}},"pending_request":null}}"#
    )
}

fn initial_status_json() -> String {
    format!(
        r#"{{"state_id":"{STATE_ID}","generation":0,"current":null,"active":null,"last_event":null,"branches":{{}},"pending_request":null}}"#
    )
}

#[allow(clippy::too_many_arguments)]
fn event(
    event: &str,
    generation: u64,
    action: &str,
    from: Option<&str>,
    to: &str,
    active_after: &str,
    branch: &str,
    previous_head: Option<&str>,
    parents: &[&str],
) -> String {
    let from = from
        .map(|value| format!("\"{value}\""))
        .unwrap_or_else(|| "null".to_owned());
    let previous_head = previous_head
        .map(|value| format!("\"{value}\""))
        .unwrap_or_else(|| "null".to_owned());
    let parents = parents
        .iter()
        .map(|parent| format!("\"{parent}\""))
        .collect::<Vec<_>>()
        .join(",");
    let base = if generation == 1 {
        "null".to_owned()
    } else {
        format!(r#"{{"generation":{}}}"#, generation - 1)
    };
    format!(
        r#"{{"event":"{event}","generation":{generation},"action":"{action}","from":{from},"to":"{to}","active_after":"{active_after}","branch":"{branch}","previous_head":{previous_head},"parents":[{parents}],"base":{base}}}"#
    )
}

fn history_json(events: &[String]) -> String {
    format!("[{}]", events.join(","))
}

fn two_commit_history() -> String {
    history_json(&[
        event(EVENT_1, 1, "commit", None, FROM, FROM, "main", None, &[]),
        event(
            EVENT_2,
            2,
            "commit",
            Some(FROM),
            TO,
            TO,
            "main",
            Some(FROM),
            &[],
        ),
    ])
}

fn show_json(snapshot: &str, parents: &[&str], files: &[(&str, u64)]) -> String {
    let parents = parents
        .iter()
        .map(|parent| format!("\"{parent}\""))
        .collect::<Vec<_>>()
        .join(",");
    let files = files
        .iter()
        .map(|(path, size)| format!(r#""{path}":{{"sha256":"{HASH}","size":{size}}}"#))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        r#"{{"snapshot":"{snapshot}","descriptor":{{"parents":[{parents}]}},"files":{{{files}}}}}"#
    )
}

fn success_receipt(generation: u64, snapshot: &str, event: &str) -> String {
    format!(
        r#"{{"changed":true,"current":{{"generation":{generation}}},"snapshot":"{snapshot}","event":"{event}","request_id":"{REQUEST_ID}"}}"#
    )
}

fn standard_target(fake: &FakeMolt) {
    fake.respond("status", &status_json(2, TO, TO, EVENT_2));
    fake.respond("history", &two_commit_history());
    fake.respond(
        &format!("show-{FROM}"),
        &show_json(FROM, &[BEFORE], &[("config.json", 5), ("old.txt", 3)]),
    );
    fake.respond(
        &format!("show-{TO}"),
        &show_json(TO, &[FROM], &[("config.json", 5)]),
    );
    fake.tree_file("config.json", "hello");
    fake.tree_file("old.txt", "old");
}

fn report(application: &App) -> &FileRecoveryReport {
    application
        .version_workspace
        .as_ref()
        .unwrap()
        .last_file_recovery
        .as_ref()
        .expect("b records a report")
}

fn assert_outcome(application: &App, expected: FileRecoveryOutcome) {
    let report = report(application);
    assert_eq!(report.outcome, expected, "{:?}", report.lines);
}

fn commit_invocations(fake: &FakeMolt) -> Vec<String> {
    fake.argv()
        .lines()
        .filter(|line| line.contains("commit-files"))
        .map(str::to_owned)
        .collect()
}

#[test]
fn file_recovery_commits_the_previous_main_tree_once() {
    let fake = FakeMolt::new("restore");
    standard_target(&fake);
    fake.respond("commit", &success_receipt(3, NEW, EVENT_3));
    fake.respond("status-3", &status_json(3, NEW, NEW, EVENT_3));

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Restored);
    let workspace = application.version_workspace.as_ref().unwrap();
    assert_eq!(workspace.generation, 3);
    assert_eq!(workspace.main_head.as_deref(), Some(NEW));
    assert!(workspace
        .detail
        .iter()
        .any(|line| line.contains("request ID") && line.contains(REQUEST_ID)));
    assert!(application
        .status_text()
        .is_some_and(|status| status.contains("committed · generation 3")));

    let commits = commit_invocations(&fake);
    assert_eq!(commits.len(), 1, "{}", fake.argv());
    let commit = &commits[0];
    for token in [
        "version --workspace",
        "commit-files",
        "--root",
        "molt-tree-fixture/project",
        "--manifest",
        "--branch main",
        "--expect-generation 2",
        "--expect-head 2222222222222222222222222222222222222222222222222222222222222222",
        "--activate",
        "--reason",
        "--reason-source",
        "--include-request-id",
    ] {
        assert!(commit.contains(token), "missing {token}: {commit}");
    }
    assert!(!fake.argv().contains("rollback"), "{}", fake.argv());
    assert_eq!(fake.manifest(), r#"["config.json","old.txt"]"#);
    let parent = fake
        .exported_root()
        .parent()
        .and_then(|slot| slot.parent())
        .map(std::path::Path::to_path_buf)
        .unwrap();
    assert!(!parent.exists(), "recovery tree was not cleaned up");
}

#[test]
fn file_recovery_rejects_a_stale_panel_baseline_before_history() {
    let fake = FakeMolt::new("baseline");
    fake.respond("status", &status_json(5, TO, TO, EVENT_2));

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application).lines[2].contains("generation 5"));
    assert!(fake.argv().contains("status"), "{}", fake.argv());
    assert!(!fake.argv().contains("history"), "{}", fake.argv());
    assert!(commit_invocations(&fake).is_empty());
    assert_eq!(
        application.version_workspace.as_ref().unwrap().generation,
        2
    );
}

#[test]
fn file_recovery_refuses_to_run_while_a_pending_request_is_cached() {
    let fake = FakeMolt::new("pending");
    let mut application = fake.app(2, TO, TO, EVENT_2);
    application
        .version_workspace
        .as_mut()
        .unwrap()
        .pending_request = Some("synthetic-other".to_owned());

    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application).lines[2].contains("another request is pending"));
    assert!(fake.argv().is_empty(), "{}", fake.argv());
}

#[test]
fn file_recovery_rejects_a_switch_back_to_main() {
    let fake = FakeMolt::new("switch");
    fake.respond("status", &status_json(4, FROM, FROM, EVENT_4));
    fake.respond(
        "history",
        &history_json(&[
            event(EVENT_1, 1, "commit", None, FROM, FROM, "main", None, &[]),
            event(
                EVENT_2,
                2,
                "create-branch",
                Some(FROM),
                SIDE,
                FROM,
                "side",
                Some(FROM),
                &[FROM],
            ),
            event(
                EVENT_3,
                3,
                "switch",
                Some(FROM),
                SIDE,
                SIDE,
                "side",
                Some(SIDE),
                &[],
            ),
            event(
                EVENT_4,
                4,
                "switch",
                Some(SIDE),
                FROM,
                FROM,
                "main",
                Some(FROM),
                &[],
            ),
        ]),
    );

    let mut application = fake.app(4, FROM, FROM, EVENT_4);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(
        report(&application)
            .lines
            .iter()
            .any(|line| line.contains("was `switch`")),
        "{:?}",
        report(&application).lines
    );
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_a_side_branch_write_that_activated_main() {
    let fake = FakeMolt::new("side");
    fake.respond("status", &status_json(4, TO, TO, EVENT_4));
    fake.respond(
        "history",
        &history_json(&[
            event(EVENT_1, 1, "commit", None, FROM, FROM, "main", None, &[]),
            event(
                EVENT_2,
                2,
                "create-branch",
                Some(FROM),
                SIDE,
                FROM,
                "side",
                Some(FROM),
                &[FROM],
            ),
            event(
                EVENT_3,
                3,
                "commit",
                Some(FROM),
                SIDE,
                SIDE,
                "side",
                Some(SIDE),
                &[FROM],
            ),
            event(
                EVENT_4,
                4,
                "commit",
                Some(SIDE),
                TO,
                TO,
                "main",
                Some(FROM),
                &[FROM],
            ),
        ]),
    );
    fake.respond(
        &format!("show-{TO}"),
        &show_json(TO, &[FROM], &[("config.json", 5)]),
    );

    let mut application = fake.app(4, TO, TO, EVENT_4);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(commit_invocations(&fake).is_empty());
    assert!(!fake.argv().contains("export"), "{}", fake.argv());
}

#[test]
fn file_recovery_rejects_an_empty_initial_parent() {
    let fake = FakeMolt::new("initial");
    fake.respond("status", &status_json(1, FROM, FROM, EVENT_1));
    fake.respond(
        "history",
        &history_json(&[event(
            EVENT_1,
            1,
            "commit",
            None,
            FROM,
            FROM,
            "main",
            None,
            &[],
        )]),
    );

    let mut application = fake.app(1, FROM, FROM, EVENT_1);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("no snapshot parent")));
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_an_echo_target() {
    let fake = FakeMolt::new("echo");
    standard_target(&fake);
    fake.respond(
        &format!("show-{FROM}"),
        &format!(r#"{{"snapshot":"{FROM}","descriptor":{{"parents":[]}}}}"#),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("no managed files")));
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_invalid_paths_sizes_and_extra_tree_files() {
    for (label, files, extra) in [
        ("traversal", vec![("../evil", 1u64)], false),
        ("oversize", vec![("big.bin", 300_000u64)], false),
        ("extra", vec![("config.json", 5u64)], true),
    ] {
        let fake = FakeMolt::new(label);
        standard_target(&fake);
        fake.respond(&format!("show-{FROM}"), &show_json(FROM, &[BEFORE], &files));
        if extra {
            fake.tree_file("unexpected.txt", "extra");
        }
        let mut application = fake.app(2, TO, TO, EVENT_2);
        press_b(&mut application);
        assert_outcome(&application, FileRecoveryOutcome::Rejected);
        assert!(commit_invocations(&fake).is_empty(), "{label}");
        assert!(
            !fake.argv().contains("commit-files"),
            "{label}: {}",
            fake.argv()
        );
    }
}

#[test]
fn file_recovery_rejects_an_export_tree_outside_the_private_parent() {
    let fake = FakeMolt::new("export-path");
    standard_target(&fake);
    fake.respond(
        "export",
        &format!(r#"{{"snapshot":"{FROM}","tree":"/tmp/not-the-export-parent/project"}}"#),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application).lines[1].contains("export tree"));
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_a_status_drift_after_the_target_is_prepared() {
    let fake = FakeMolt::new("confirm");
    standard_target(&fake);
    fake.respond("status-2", &status_json(3, TO, TO, EVENT_3));

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application).lines[1].contains("confirm"));
    assert!(commit_invocations(&fake).is_empty());
    assert!(!fake.argv().contains("rollback"), "{}", fake.argv());
}

#[test]
fn file_recovery_reports_no_effect_for_the_current_head_tree() {
    let fake = FakeMolt::new("no-effect");
    standard_target(&fake);
    fake.respond(
        "commit",
        &format!(
            r#"{{"changed":false,"current":{{"generation":2}},"snapshot":"{TO}","event":null}}"#
        ),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::NoEffect);
    assert!(report(&application).lines[0].contains("no effect"));
    assert_eq!(
        application.version_workspace.as_ref().unwrap().generation,
        2
    );
    assert_eq!(commit_invocations(&fake).len(), 1);
    assert!(!fake.argv().contains("rollback"), "{}", fake.argv());
}

#[test]
fn file_recovery_classifies_a_stale_conflict_without_retrying() {
    let fake = FakeMolt::new("stale");
    standard_target(&fake);
    fake.exit("commit", 3);
    fake.stderr("commit", "error: stale generation 2\n");

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(
        report(&application).lines[0].contains("stale main"),
        "{:?}",
        report(&application).lines
    );
    assert_eq!(commit_invocations(&fake).len(), 1);
    assert!(!fake.argv().contains("rollback"), "{}", fake.argv());
}

#[test]
fn file_recovery_does_not_guess_an_unclassified_exit_3() {
    let fake = FakeMolt::new("exit3");
    standard_target(&fake);
    fake.exit("commit", 3);
    fake.stderr("commit", "error: resource temporarily unavailable\n");

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Unknown);
    assert!(report(&application).lines[0].contains("result unknown"));
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("not classified")));
    assert_eq!(commit_invocations(&fake).len(), 1);
}

#[test]
fn file_recovery_reports_runtime_failures_and_signals_as_unknown() {
    let fake = FakeMolt::new("runtime");
    standard_target(&fake);
    fake.exit("commit", 1);
    fake.stderr("commit", "error: kernel unavailable\n");

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Unknown);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("do not retry")));
    assert_eq!(commit_invocations(&fake).len(), 1);
    drop(application);

    let signaling = FakeMolt::new("signal");
    standard_target(&signaling);
    signaling.signal();

    let mut application = signaling.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Unknown);
    assert!(
        report(&application)
            .lines
            .iter()
            .any(|line| line.contains("signal 15")),
        "{:?}",
        report(&application).lines
    );
    assert_eq!(commit_invocations(&signaling).len(), 1);
}

#[test]
fn file_recovery_distinguishes_foreign_pending_from_its_own_recovery() {
    let foreign = FakeMolt::new("foreign-pending");
    standard_target(&foreign);
    foreign.exit("commit", 4);
    foreign.stderr(
        "commit",
        "error: pending request synthetic-other; query request-status or run recover\n",
    );
    let mut application = foreign.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert_eq!(
        report(&application).request_id.as_deref(),
        Some("synthetic-other")
    );
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("not submitted by b")));
    assert!(!report(&application)
        .lines
        .iter()
        .any(|line| line.contains("request ID synthetic-other")));
    drop(application);

    let own = FakeMolt::new("own-recovery");
    standard_target(&own);
    own.exit("commit", 4);
    own.stderr(
        "commit",
        &format!("error: request {REQUEST_ID} needs recovery\n"),
    );
    let mut application = own.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Unknown);
    assert_eq!(report(&application).request_id.as_deref(), Some(REQUEST_ID));
    assert!(report(&application).lines[0].contains("do not retry"));
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("request-status")));
}

#[test]
fn file_recovery_treats_an_incomplete_receipt_as_unknown() {
    let fake = FakeMolt::new("receipt");
    standard_target(&fake);
    fake.respond(
        "commit",
        &format!(
            r#"{{"changed":true,"current":{{"generation":3}},"snapshot":"{NEW}","event":"{EVENT_3}"}}"#
        ),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Unknown);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("request ID")));
    assert_eq!(commit_invocations(&fake).len(), 1);
}

#[test]
fn file_recovery_empty_target_submits_an_empty_manifest() {
    let fake = FakeMolt::new("empty");
    fake.respond("status", &status_json(2, TO, TO, EVENT_2));
    fake.respond("history", &two_commit_history());
    fake.respond(&format!("show-{FROM}"), &show_json(FROM, &[BEFORE], &[]));
    fake.respond(
        &format!("show-{TO}"),
        &show_json(TO, &[FROM], &[("config.json", 5)]),
    );
    fake.respond("commit", &success_receipt(3, NEW, EVENT_3));
    fake.respond("status-3", &status_json(3, NEW, NEW, EVENT_3));

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Restored);
    assert_eq!(fake.manifest(), "[]");
    assert_eq!(commit_invocations(&fake).len(), 1);
}

#[test]
fn file_recovery_rejects_a_generation_zero_workspace() {
    let fake = FakeMolt::new("zero");
    fake.respond("status", &initial_status_json());
    fake.respond("history", "[]");

    let mut application = fake.app(0, FROM, FROM, EVENT_1);
    application
        .version_workspace
        .as_mut()
        .unwrap()
        .active_snapshot = None;
    application.version_workspace.as_mut().unwrap().last_event = None;
    application.version_workspace.as_mut().unwrap().main_head = None;

    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(
        report(&application)
            .lines
            .iter()
            .any(|line| line.contains("no committed generation")),
        "{:?}",
        report(&application).lines
    );
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_history_without_an_active_change() {
    let fake = FakeMolt::new("no-target");
    fake.respond("status", &status_json(1, FROM, FROM, EVENT_1));
    fake.respond(
        "history",
        &history_json(&[event(
            EVENT_1,
            1,
            "commit",
            Some(FROM),
            FROM,
            FROM,
            "main",
            None,
            &[],
        )]),
    );

    let mut application = fake.app(1, FROM, FROM, EVENT_1);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("no event changed the active snapshot")));
    assert!(commit_invocations(&fake).is_empty());
}

fn assert_no_recovery_side_effects(fake: &FakeMolt, application: &App, expected: &str) {
    assert_outcome(application, FileRecoveryOutcome::Rejected);
    assert!(
        report(application)
            .lines
            .iter()
            .any(|line| line.contains(expected)),
        "{:?}",
        report(application).lines
    );
    let argv = fake.argv();
    assert!(
        !argv
            .lines()
            .any(|line| line.split_whitespace().nth(3) == Some("export")),
        "{argv}"
    );
    assert!(commit_invocations(fake).is_empty(), "{argv}");
}

#[test]
fn file_recovery_rejects_a_selected_event_whose_from_breaks_the_predecessor_link() {
    let fake = FakeMolt::new("broken-link");
    fake.respond("status", &status_json(2, TO, TO, EVENT_2));
    fake.respond(
        "history",
        &history_json(&[
            event(EVENT_1, 1, "commit", None, FROM, BEFORE, "main", None, &[]),
            event(
                EVENT_2,
                2,
                "commit",
                Some(FROM),
                TO,
                TO,
                "main",
                Some(FROM),
                &[],
            ),
        ]),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_no_recovery_side_effects(
        &fake,
        &application,
        "does not start from the snapshot the previous event left active",
    );
}

#[test]
fn file_recovery_rejects_a_selected_event_that_omits_its_from_link() {
    let fake = FakeMolt::new("missing-link");
    fake.respond("status", &status_json(2, TO, TO, EVENT_2));
    fake.respond(
        "history",
        &history_json(&[
            event(EVENT_1, 1, "commit", None, FROM, FROM, "main", None, &[]),
            event(EVENT_2, 2, "commit", None, TO, TO, "main", Some(FROM), &[]),
        ]),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_no_recovery_side_effects(
        &fake,
        &application,
        "does not start from the snapshot the previous event left active",
    );
}

#[test]
fn file_recovery_rejects_workspace_replacement_and_history_binding_drift() {
    let replaced = FakeMolt::new("replaced");
    replaced.respond(
        "status",
        &format!(
            r#"{{"state_id":"state-2","generation":2,"current":{{"generation":2}},"active":"{TO}","last_event":"{EVENT_2}","branches":{{"main":"{TO}"}},"pending_request":null}}"#
        ),
    );
    let mut application = replaced.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("state_id state-2")));
    assert!(commit_invocations(&replaced).is_empty());
    drop(application);
    drop(replaced);

    let unbound = FakeMolt::new("unbound");
    unbound.respond("status", &status_json(2, TO, TO, EVENT_2));
    unbound.respond(
        "history",
        &history_json(&[
            event(EVENT_1, 1, "commit", None, FROM, FROM, "main", None, &[]),
            event(
                EVENT_2,
                2,
                "commit",
                Some(FROM),
                TO,
                FROM,
                "main",
                Some(FROM),
                &[],
            ),
        ]),
    );
    let mut application = unbound.app(2, TO, TO, EVENT_2);
    press_b(&mut application);
    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("does not end at the status active")));
    assert!(commit_invocations(&unbound).is_empty());
}

#[test]
fn file_recovery_rejects_a_status_whose_current_generation_disagrees() {
    let fake = FakeMolt::new("current");
    fake.respond(
        "status",
        &format!(
            r#"{{"state_id":"{STATE_ID}","generation":2,"current":{{"generation":1}},"active":"{TO}","last_event":"{EVENT_2}","branches":{{"main":"{TO}"}},"pending_request":null}}"#
        ),
    );

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert_eq!(report(&application).lines[1], "stage read status");
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("current generation")));
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_rejects_an_export_without_a_tree_path() {
    let fake = FakeMolt::new("export-missing");
    standard_target(&fake);
    fake.respond("export", &format!(r#"{{"snapshot":"{FROM}"}}"#));

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application)
        .lines
        .iter()
        .any(|line| line.contains("export omitted the tree path")));
    assert!(commit_invocations(&fake).is_empty());
}

#[test]
fn file_recovery_reports_exit_2_as_an_input_rejection() {
    let fake = FakeMolt::new("exit2");
    standard_target(&fake);
    fake.exit("commit", 2);
    fake.stderr("commit", "error: invalid managed path: ../evil\n");

    let mut application = fake.app(2, TO, TO, EVENT_2);
    press_b(&mut application);

    assert_outcome(&application, FileRecoveryOutcome::Rejected);
    assert!(report(&application).lines[0].contains("input or workspace refused"));
    assert_eq!(commit_invocations(&fake).len(), 1);
    assert!(!fake.argv().contains("rollback"), "{}", fake.argv());
}
