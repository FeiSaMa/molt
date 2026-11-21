# Mascot review assets

Plain-text copies of the three 6x3 mascot frames. They are a **visual review
aid only**: the compiled truth is `../../src/mascot.rs` (`IDLE`, `BLINK`,
`TONGUE`). `cargo test -p molt-tui` asserts these files match the constants byte
for byte.

| file | frame | trigger |
|---|---|---|
| `idle.txt` | idle | default, also the static fallback |
| `blink.txt` | blink | 4 fps cycle |
| `tongue.txt` | tongue | 4 fps cycle |

Frames advance every `FRAME_MS` = 250 ms (4 fps) and only when the header is
≥80 columns, the terminal is not `TERM=dumb`, `NO_COLOR` is unset, the colour
mode is 256/truecolor, no modal is open, and no stream is running. Otherwise
the mascot freezes on `idle.txt` or is hidden entirely (`mascot::policy`).

Colours come from three semantic tokens in `../src/theme.rs`:
`mascot.body`, `mascot.eye`, `mascot.tongue`.

Regenerate (writes the same bytes):

```bash
cargo run -p molt-tui --example gen_assets
```
