//! Binary-level exit-code contract.
//!
//! Usage errors must be distinguishable from runtime failures: bad arguments
//! (including `--width/--height` misuse) exit 2, a runtime failure exits 1, and
//! `--help` / a successful `--selftest` exit 0.

use std::process::{Command, Output};

/// Prefix of a real API key, split so secret scanners do not flag the
/// leak assertion below.
const KEY_PREFIX: &str = concat!("s", "k-");

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_molt-tui"))
        .args(args)
        .output()
        .expect("molt-tui binary should run")
}

fn assert_code(output: &Output, expected: i32, context: &str) {
    assert_eq!(
        output.status.code(),
        Some(expected),
        "{context}: stdout=`{}` stderr=`{}`",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn selftest_rejects_zero_width_with_exit_2() {
    let output = run(&["--selftest", "--width", "0", "--height", "24"]);
    assert_code(&output, 2, "zero width");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("greater than 0"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn width_without_selftest_exits_2() {
    let output = run(&["--width", "80"]);
    assert_code(&output, 2, "--width without --selftest");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("--selftest"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn help_documents_live_default_and_key_sources() {
    let output = run(&["--help"]);
    assert_code(&output, 0, "--help");
    let text = String::from_utf8_lossy(&output.stdout);
    for marker in [
        "Live mode is the default",
        "--mock",
        "--model",
        "--base-url",
        "DEEPSEEK_API_KEY",
        "MOLT_API_KEY_HELPER",
        "Ctrl+C",
        "Ctrl+N",
    ] {
        assert!(text.contains(marker), "help missing `{marker}`:\n{text}");
    }
    assert!(
        !text.contains(KEY_PREFIX),
        "help must not print a key:\n{text}"
    );
}

#[test]
fn selftest_still_uses_mock_data() {
    let output = run(&["--selftest", "--width", "120", "--height", "40"]);
    assert_code(&output, 0, "selftest");
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("SELFTEST OK"), "{text}");
}

#[test]
fn mock_flag_is_accepted() {
    let output = run(&["--mock", "--help"]);
    assert_code(&output, 0, "--mock --help");
    assert!(String::from_utf8_lossy(&output.stdout).contains("Usage:"));
}

#[test]
fn selftest_with_an_explicit_size_exits_0() {
    let output = run(&["--selftest", "--width", "80", "--height", "24"]);
    assert_code(&output, 0, "explicit 80x24 selftest");
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("SELFTEST OK"),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn selftest_accepts_the_maximum_frame() {
    let output = run(&["--selftest", "--width", "500", "--height", "200"]);
    assert_code(&output, 0, "500x200 selftest");
}

#[test]
fn selftest_rejects_a_width_above_the_cap() {
    let output = run(&["--selftest", "--width", "501", "--height", "24"]);
    assert_code(&output, 2, "501 width");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("at most 500"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn selftest_rejects_a_height_above_the_cap() {
    let output = run(&["--selftest", "--width", "80", "--height", "201"]);
    assert_code(&output, 2, "201 height");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("at most 200"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn selftest_rejects_a_non_numeric_dimension() {
    let output = run(&["--selftest", "--width", "wide", "--height", "24"]);
    assert_code(&output, 2, "non-numeric width");
}
