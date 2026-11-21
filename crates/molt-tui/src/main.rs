use std::process::ExitCode;

use molt_tui::{run, Cli};

/// Exit codes are part of the CLI contract: bad usage is 2, a runtime failure
/// is 1, and a successful run (including `--help` and `--selftest`) is 0.
fn main() -> ExitCode {
    let cli = match Cli::parse(std::env::args().skip(1)) {
        Ok(cli) => cli,
        Err(error) => {
            eprintln!("{error:#}");
            return ExitCode::from(2);
        }
    };
    match run(cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error:#}");
            ExitCode::from(1)
        }
    }
}
