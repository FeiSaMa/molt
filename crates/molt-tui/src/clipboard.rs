//! Clipboard helpers without a new dependency.
//!
//! `y` in the chat view copies the last assistant answer with the OSC 52
//! terminal sequence. Encoding is a small, self-contained base64 encoder so the
//! crate keeps its dependency list unchanged.

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Standard base64 with `=` padding (RFC 4648 §4).
pub fn base64(input: &[u8]) -> String {
    let mut output = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let first = u32::from(chunk[0]);
        let second = chunk.get(1).copied().map_or(0, u32::from);
        let third = chunk.get(2).copied().map_or(0, u32::from);
        let triple = (first << 16) | (second << 8) | third;
        output.push(ALPHABET[((triple >> 18) & 0x3f) as usize] as char);
        output.push(ALPHABET[((triple >> 12) & 0x3f) as usize] as char);
        if chunk.len() > 1 {
            output.push(ALPHABET[((triple >> 6) & 0x3f) as usize] as char);
        } else {
            output.push('=');
        }
        if chunk.len() > 2 {
            output.push(ALPHABET[(triple & 0x3f) as usize] as char);
        } else {
            output.push('=');
        }
    }
    output
}

/// OSC 52 sequence that asks the terminal to put `text` on the system
/// clipboard (`c` = clipboard selection).
pub fn osc52(text: &str) -> String {
    format!("\x1b]52;c;{}\x07", base64(text.as_bytes()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_rfc4648_vectors() {
        for (input, expected) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foob", "Zm9vYg=="),
            ("fooba", "Zm9vYmE="),
            ("foobar", "Zm9vYmFy"),
            ("hello world", "aGVsbG8gd29ybGQ="),
        ] {
            assert_eq!(base64(input.as_bytes()), expected, "input {input:?}");
        }
    }

    #[test]
    fn base64_handles_utf8_bytes() {
        assert_eq!(base64("中".as_bytes()), "5Lit");
        assert_eq!(base64("MOLT".as_bytes()), "TU9MVA==");
    }

    #[test]
    fn osc52_wraps_the_payload_in_the_clipboard_sequence() {
        assert_eq!(osc52("MOLT"), "\x1b]52;c;TU9MVA==\x07");
    }
}
