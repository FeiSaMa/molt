//! W-09 interaction tests: tree/settings/approval/diff keys and state frames.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use molt_tui::{app, App, ColorMode, Screen};
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
