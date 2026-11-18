//! Domain-independent primitives for the molt storage kernel.

pub mod cas;
pub mod log;
pub mod manifest;
pub mod state;

#[cfg(feature = "fault-injection")]
pub(crate) fn fault_point(name: &str) {
    use std::{fs::OpenOptions, io::Write, path::Path, thread, time::Duration};
    let Ok(target) = std::env::var("MOLT_KERNEL_PAUSE_AT") else {
        return;
    };
    if target != name {
        return;
    }
    let marker = std::env::var_os("MOLT_KERNEL_PAUSE_MARKER").expect("pause marker required");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(Path::new(&marker))
        .expect("pause marker must be unique");
    writeln!(file, "{name}").expect("write pause marker");
    file.sync_all().expect("sync pause marker");
    loop {
        thread::sleep(Duration::from_secs(60));
    }
}

#[cfg(not(feature = "fault-injection"))]
#[inline(always)]
pub(crate) fn fault_point(_name: &str) {}

use serde_json::Value;
use sha2::{Digest, Sha256};

/// Canonical JSON for kernel envelopes. Floating-point values are deliberately
/// excluded until a cross-language numeric encoding has been specified.
pub fn canonical_json(value: &Value) -> Result<Vec<u8>, &'static str> {
    fn validate(value: &Value) -> Result<(), &'static str> {
        match value {
            Value::Number(n) if !n.is_i64() && !n.is_u64() => Err("non-integer number"),
            Value::Array(values) => values.iter().try_for_each(validate),
            Value::Object(values) => values.values().try_for_each(validate),
            _ => Ok(()),
        }
    }
    validate(value)?;
    serde_json::to_vec(value).map_err(|_| "invalid JSON")
}

/// SHA-256 digest in the kernel's prefixed representation.
pub fn sha256(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    format!("sha256:{digest:x}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_sort_unicode_and_integer() {
        let value = json!({"z": [true, null, -2], "a": "中文\n"});
        assert_eq!(
            canonical_json(&value).unwrap(),
            "{\"a\":\"中文\\n\",\"z\":[true,null,-2]}".as_bytes()
        );
        assert!(canonical_json(&json!({"x": 1.5})).is_err());
    }

    #[test]
    fn shared_golden_vectors() {
        let vectors: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/kernel/canonical.json"
        ))
        .unwrap();
        for vector in vectors.as_array().unwrap() {
            assert_eq!(
                canonical_json(&vector["value"]).unwrap(),
                vector["canonical"].as_str().unwrap().as_bytes()
            );
        }
    }

    #[test]
    fn known_sha256() {
        assert_eq!(
            sha256(b"abc"),
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
