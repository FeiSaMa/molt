# Logo review assets

These are **rendered snapshots for visual review**, not the source of truth.
The shape data and the colour maths live in `../../src/logo.rs`; `cargo test -p
molt-tui` locks the compact slot at 15x3 and the colour degradation.

| file | candidate | variant | colour |
|---|---|---|---|
| `candidate-1.ansi` | Ouroboros Pixel | large (startup) | truecolor SGR |
| `candidate-2.ansi` | Braille Ring | large (startup) | truecolor SGR |
| `candidate-3.ansi` | Terminal Crest | large (startup) | truecolor SGR |

Sizes: candidates 1 and 2 keep 12x3 compact art inside the wider 15-cell
header slot. Candidate 3's compact wordmark is 15x3. Its large art is 38x5:
the unchanged 9x4 crest badge plus a 23-cell wordmark drawn with five pixels
per letter and a one-pixel gap.

The files contain real ANSI escape sequences (`ESC[...m`). View them with a
truecolor terminal:

```bash
cat crates/molt-tui/assets/logo/candidate-1.ansi
# or inspect the raw bytes:
cat -v crates/molt-tui/assets/logo/candidate-1.ansi
```

For the authoritative, terminal-native preview (including 16-colour and
256-colour degradation):

```bash
cargo run -p molt-tui -- --logo all --color 16
cargo run -p molt-tui -- --logo all --color 256
cargo run -p molt-tui -- --logo all --color truecolor
```

Design rules shared by all three candidates:

- self-drawn pixel maps only, no third-party art;
- the `O` of `MOLT` is an ouroboros: a one-to-two cell ring, hollow centre,
  head at the upper right with a one-pixel eye, a crest/jaw, and a 2-3 cell
  tongue;
- the body colour sweeps the polar angle in truecolor and 256-colour modes;
  16-colour terminals get two fixed ANSI colours instead.

Candidate 2 draws its ring with Braille patterns (U+2800–28FF). A monospace
font without Braille glyphs will show replacement boxes; candidate 1 and 3 use
half blocks only.

Regenerate the snapshots (also rewrites the mascot frames):

```bash
cargo run -p molt-tui --example gen_assets
```
