//! Regenerate the review assets under `assets/`.
//!
//! The compiled constants in `src/logo.rs` and `src/mascot.rs` remain the source
//! of truth; this example only makes the art easy to inspect with `cat`.

use std::fs;
use std::path::Path;

use molt_tui::{logo, mascot, ColorMode};

fn main() -> anyhow::Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let logo_dir = root.join("assets/logo");
    fs::create_dir_all(&logo_dir)?;
    for index in 0..logo::CANDIDATES.len() {
        let body = logo::preview_ansi(index, logo::Variant::Large, ColorMode::TrueColor)?;
        let text = format!(
            "# molt-tui logo candidate {} · truecolor ANSI · generated from src/logo.rs\n{body}",
            index + 1
        );
        fs::write(logo_dir.join(format!("candidate-{}.ansi", index + 1)), text)?;
    }

    let mascot_dir = root.join("assets/mascot");
    fs::create_dir_all(&mascot_dir)?;
    for (index, name) in mascot::FRAME_NAMES.iter().enumerate() {
        fs::write(
            mascot_dir.join(format!("{name}.txt")),
            format!("{}\n", mascot::FRAMES[index].join("\n")),
        )?;
    }

    println!(
        "wrote {} logo previews and {} mascot frames",
        logo::CANDIDATES.len(),
        mascot::FRAME_NAMES.len()
    );
    Ok(())
}
