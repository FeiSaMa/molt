//! Validate domain-independent manifests before accepting their references.
use crate::cas::Cas;
use crate::{canonical_json, sha256};
use serde_json::{json, Value};
use std::io;
use std::path::{Component, Path};

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn valid_path(path: &str) -> bool {
    !path.is_empty()
        && path.len() <= 4096
        && !path.contains('\0')
        && !path.contains('\\')
        && path
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != ".." && part.len() <= 255)
        && Path::new(path)
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
}

/// Validate file entries and verify every referenced CAS object and byte size.
/// Roots and paths are logical names; the kernel never follows them on disk.
pub fn validate(manifest: &Value, cas: &Cas) -> io::Result<()> {
    let object = manifest
        .as_object()
        .ok_or_else(|| invalid("invalid manifest"))?;
    if object.len() != 2 {
        return Err(invalid("invalid manifest fields"));
    }
    let roots = manifest
        .get("roots")
        .and_then(Value::as_object)
        .ok_or_else(|| invalid("invalid manifest roots"))?;
    let roots_bytes = canonical_json(&Value::Object(roots.clone()))
        .map_err(|_| invalid("invalid manifest roots"))?;
    if manifest.get("tree_hash").and_then(Value::as_str) != Some(sha256(&roots_bytes).as_str()) {
        return Err(invalid("tree hash mismatch"));
    }
    for (root, entries) in roots {
        if !valid_path(root) || root.contains('/') {
            return Err(invalid("invalid root key"));
        }
        let entries = entries
            .as_object()
            .ok_or_else(|| invalid("invalid root entries"))?;
        for (path, entry) in entries {
            if !valid_path(path) {
                return Err(invalid("invalid manifest path"));
            }
            if path
                .match_indices('/')
                .any(|(index, _)| entries.contains_key(&path[..index]))
            {
                return Err(invalid("manifest file is an ancestor of another file"));
            }
            let entry = entry
                .as_object()
                .ok_or_else(|| invalid("invalid manifest entry"))?;
            if entry.len() != 4 || entry.get("type").and_then(Value::as_str) != Some("file") {
                return Err(invalid("unsupported manifest entry"));
            }
            let hash = entry
                .get("sha256")
                .and_then(Value::as_str)
                .ok_or_else(|| invalid("missing object hash"))?;
            let size = entry
                .get("size")
                .and_then(Value::as_u64)
                .ok_or_else(|| invalid("invalid object size"))?;
            let mode = entry
                .get("mode")
                .and_then(Value::as_u64)
                .ok_or_else(|| invalid("invalid mode"))?;
            if mode > 0o777 {
                return Err(invalid("invalid mode"));
            }
            if cas.get(hash)?.len() as u64 != size {
                return Err(invalid("object size mismatch"));
            }
        }
    }
    Ok(())
}

/// Create an immutable manifest object after verifying all referenced objects.
pub fn create(roots: Value, cas: &Cas) -> io::Result<String> {
    if !roots.is_object() {
        return Err(invalid("invalid manifest roots"));
    }
    let encoded_roots = canonical_json(&roots).map_err(|_| invalid("invalid manifest roots"))?;
    let manifest = json!({"roots": roots, "tree_hash": sha256(&encoded_roots)});
    validate(&manifest, cas)?;
    let bytes = canonical_json(&manifest).map_err(|_| invalid("invalid manifest"))?;
    cas.put(&bytes)
}

/// Read and verify the manifest object, its tree hash, and its file objects.
pub fn get(hash: &str, cas: &Cas) -> io::Result<Value> {
    let bytes = cas.get(hash)?;
    let manifest: Value =
        serde_json::from_slice(&bytes).map_err(|_| invalid("invalid manifest"))?;
    if canonical_json(&manifest).map_err(|_| invalid("invalid manifest"))? != bytes {
        return Err(invalid("noncanonical manifest"));
    }
    validate(&manifest, cas)?;
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;
    #[test]
    fn paths_and_objects_are_checked() {
        let dir = std::env::temp_dir().join(format!("molt-manifest-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let cas = Cas::new(&dir);
        let hash = cas.put(b"data").unwrap();
        let entry = json!({"sha256":hash,"size":4,"mode":420,"type":"file"});
        let roots = json!({"workspace":{"file":entry}});
        let manifest_hash = create(roots, &cas).unwrap();
        let manifest = get(&manifest_hash, &cas).unwrap();
        assert_eq!(manifest["roots"]["workspace"]["file"], entry);
        let mut tampered = manifest;
        tampered["tree_hash"] = json!("sha256:bad");
        assert!(validate(&tampered, &cas).is_err());
        for path in ["../file", "/file", "a/../b", "a//b", "a\\b", "a\0b"] {
            assert!(create(json!({"workspace":{path:entry}}), &cas).is_err());
        }
        assert!(create(json!({"workspace":{"a":entry,"a/b":entry}}), &cas).is_err());
        assert!(create(
            json!({"workspace":{"link":{"sha256":hash,"size":4,"mode":420,"type":"symlink"}}}),
            &cas
        )
        .is_err());
        fs::remove_file(cas_path_for_test(&dir, &hash)).unwrap();
        assert!(get(&manifest_hash, &cas).is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    fn cas_path_for_test(dir: &Path, hash: &str) -> std::path::PathBuf {
        let hex = hash.strip_prefix("sha256:").unwrap();
        dir.join("objects/sha256")
            .join(&hex[..2])
            .join(&hex[2..4])
            .join(hex)
    }
}
