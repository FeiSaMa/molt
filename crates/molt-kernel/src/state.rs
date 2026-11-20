//! Generation commits and crash recovery over the confirmed log.
//!
//! A caller must treat an I/O error during commit as an unknown outcome and
//! retry with the same request ID. Recovery never repairs confirmed evidence.
use crate::{
    canonical_json,
    cas::Cas,
    log::{Log, WriterLock},
    manifest, sha256,
};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn encode(value: &Value) -> io::Result<Vec<u8>> {
    canonical_json(value).map_err(invalid)
}
fn read_json(path: &Path) -> io::Result<Value> {
    let bytes = fs::read(path)?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| invalid("invalid JSON"))?;
    if encode(&value)? != bytes {
        return Err(invalid("noncanonical JSON"));
    }
    Ok(value)
}
fn sync_dir(path: &Path) -> io::Result<()> {
    File::open(path)?.sync_all()
}
fn atomic_write(path: &Path, data: &[u8]) -> io::Result<()> {
    let dir = path.parent().ok_or_else(|| invalid("invalid path"))?;
    let (tmp, mut file) = loop {
        let tmp = dir.join(format!(
            ".tmp-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        match OpenOptions::new().write(true).create_new(true).open(&tmp) {
            Ok(file) => break (tmp, file),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    };
    let result = (|| {
        file.write_all(data)?;
        file.sync_all()?;
        fs::rename(&tmp, path)?;
        let label = match path.file_name().and_then(|name| name.to_str()) {
            Some("journal.json") => Some(("journal", "journal.durable")),
            Some("current.json") => Some(("current", "current.durable")),
            _ => None,
        };
        if let Some((name, _)) = label {
            crate::fault_point(&format!("{name}.renamed"));
        }
        sync_dir(dir)?;
        if let Some((_, durable)) = label {
            crate::fault_point(durable);
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}

/// Storage operations are serialized across processes by the state lock.
pub struct State {
    root: PathBuf,
}
impl State {
    pub fn new(root: &Path) -> Self {
        Self {
            root: root.to_path_buf(),
        }
    }

    /// Initialize an empty state in an existing empty directory.
    pub fn init(&self) -> io::Result<()> {
        if fs::read_dir(&self.root)?.next().is_some() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "state directory is not empty",
            ));
        }
        fs::create_dir(self.root.join("refs"))?;
        fs::create_dir(self.root.join("tx"))?;
        Log::new(&self.root).init()?;
        let mut random = [0u8; 32];
        File::open("/dev/urandom")?.read_exact(&mut random)?;
        let mut state_id = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(self.root.join("STATE_ID"))?;
        state_id.write_all(sha256(&random).as_bytes())?;
        state_id.sync_all()?;
        let mut version = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(self.root.join("VERSION"))?;
        version.write_all(b"molt-kernel-state/1\n")?;
        version.sync_all()?;
        sync_dir(&self.root)
    }
    pub fn check_version(&self) -> io::Result<()> {
        match fs::read(self.root.join("VERSION")) {
            Ok(version) if version == b"molt-kernel-state/1\n" => Ok(()),
            Ok(_) => Err(invalid("unsupported kernel state version")),
            Err(err) if err.kind() == io::ErrorKind::NotFound => {
                Err(invalid("not an initialized kernel state (missing VERSION)"))
            }
            Err(err) => Err(err),
        }
    }
    fn current_path(&self) -> PathBuf {
        self.root.join("refs/current.json")
    }
    fn archives_path(&self) -> PathBuf {
        self.root.join("refs/archive")
    }
    fn journal_path(&self) -> PathBuf {
        self.root.join("tx/journal.json")
    }
    fn read_current(&self) -> io::Result<Option<Value>> {
        match read_json(&self.current_path()) {
            Ok(value) => Ok(Some(value)),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        }
    }
    fn current_for(record: &Value) -> io::Result<Value> {
        let meta = record
            .get("meta")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("invalid commit metadata"))?;
        if meta.len() != 4 {
            return Err(invalid("invalid commit metadata"));
        }
        let generation = meta
            .get("generation")
            .and_then(Value::as_u64)
            .ok_or_else(|| invalid("invalid generation"))?;
        let manifest_hash = record
            .get("payload_hash")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("invalid manifest hash"))?;
        let seq = record
            .get("seq")
            .and_then(Value::as_u64)
            .ok_or_else(|| invalid("invalid sequence"))?;
        let hash = record
            .get("hash")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("invalid hash"))?;
        let ts = record
            .get("ts")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("invalid timestamp"))?;
        if meta
            .get("request_id")
            .and_then(Value::as_str)
            .is_none_or(|s| s.is_empty() || s.len() > 256)
            || meta.get("request_hash").and_then(Value::as_str)
                != Some(sha256(&encode(&json!({"manifest_hash":manifest_hash,"ts":ts}))?).as_str())
            || meta.get("previous").is_none()
        {
            return Err(invalid("invalid commit metadata"));
        }
        Ok(
            json!({"generation": generation, "manifest_hash": manifest_hash, "log_seq": seq, "log_hash": hash, "committed_at": ts}),
        )
    }
    fn commits(&self, records: &[Value]) -> io::Result<Vec<Value>> {
        let mut previous: Option<Value> = None;
        let mut ids = std::collections::HashSet::new();
        let cas = Cas::new(&self.root);
        let mut commits = Vec::new();
        for record in records
            .iter()
            .filter(|r| r["kind"] == "generation.committed")
        {
            let current = Self::current_for(record)?;
            if current["generation"].as_u64() != Some(commits.len() as u64 + 1)
                || record["meta"]["previous"] != previous.clone().unwrap_or(Value::Null)
                || !ids.insert(
                    record["meta"]["request_id"]
                        .as_str()
                        .ok_or_else(|| invalid("invalid request id"))?
                        .to_owned(),
                )
            {
                return Err(invalid("invalid generation history"));
            }
            manifest::get(
                current["manifest_hash"]
                    .as_str()
                    .ok_or_else(|| invalid("invalid manifest hash"))?,
                &cas,
            )?;
            previous = Some(current);
            commits.push(record.clone());
        }
        Ok(commits)
    }
    fn verify_payloads(&self, records: &[Value]) -> io::Result<()> {
        let cas = Cas::new(&self.root);
        for record in records {
            let hash = record["payload_hash"]
                .as_str()
                .ok_or_else(|| invalid("invalid payload hash"))?;
            cas.get(hash)?;
        }
        Ok(())
    }
    fn recover_locked(&self, lock: &WriterLock) -> io::Result<()> {
        self.check_version()?;
        let log = Log::new(&self.root);
        log.recover(lock)?;
        let records = log.audit()?;
        self.verify_payloads(&records)?;
        let commits = self.commits(&records)?;
        let latest = commits.last().map(Self::current_for).transpose()?;
        let current = self.read_current()?;
        match read_json(&self.journal_path()) {
            Ok(journal) => {
                let fields = journal
                    .as_object()
                    .ok_or_else(|| invalid("invalid journal"))?;
                if fields.len() != 5
                    || journal["request_id"]
                        .as_str()
                        .is_none_or(|s| s.is_empty() || s.len() > 256)
                    || journal["request_hash"].as_str().is_none()
                    || journal["manifest_hash"].as_str().is_none()
                    || journal["generation"].as_u64().is_none()
                    || !fields.contains_key("previous")
                {
                    return Err(invalid("invalid journal"));
                }
                let previous = if journal["previous"].is_null() {
                    None
                } else {
                    Some(journal["previous"].clone())
                };
                let found = commits
                    .iter()
                    .find(|r| r["meta"]["request_id"] == journal["request_id"]);
                if let Some(record) = found {
                    if record["meta"]["request_hash"] != journal["request_hash"]
                        || record["meta"]["previous"] != journal["previous"]
                        || record["payload_hash"] != journal["manifest_hash"]
                        || record["meta"]["generation"] != journal["generation"]
                        || Some(Self::current_for(record)?) != latest
                        || (current != previous && current != latest)
                    {
                        return Err(invalid("journal conflicts with confirmed history"));
                    }
                    if current != latest {
                        atomic_write(
                            &self.current_path(),
                            &encode(latest.as_ref().ok_or_else(|| invalid("missing current"))?)?,
                        )?;
                    }
                } else if current != previous || current != latest {
                    return Err(invalid("journal conflicts with current"));
                }
                fs::remove_file(self.journal_path())?;
                sync_dir(&self.root.join("tx"))?;
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                if current != latest {
                    return Err(invalid("current conflicts with confirmed history"));
                }
            }
            Err(e) => return Err(e),
        }
        Ok(())
    }
    /// Recover an interrupted transaction under the writer lock.
    pub fn recover(&self) -> io::Result<()> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)
    }
    /// Commit a manifest with a persistent, caller-supplied idempotency key.
    fn commit_locked(
        &self,
        lock: &WriterLock,
        request_id: &str,
        manifest_hash: &str,
        ts: &str,
    ) -> io::Result<Value> {
        if request_id.is_empty() || request_id.len() > 256 || ts.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid request",
            ));
        }
        let cas = Cas::new(&self.root);
        manifest::get(manifest_hash, &cas)?;
        let request_hash = sha256(&encode(&json!({"manifest_hash":manifest_hash,"ts":ts}))?);
        let log = Log::new(&self.root);
        let records = log.audit()?;
        let commits = self.commits(&records)?;
        if let Some(record) = commits
            .iter()
            .find(|r| r["meta"]["request_id"] == request_id)
        {
            if record["meta"]["request_hash"] != request_hash {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "request id conflict",
                ));
            }
            return Self::current_for(record);
        }
        let previous = self.read_current()?;
        let generation = commits.len() as u64 + 1;
        let journal = json!({"request_id":request_id,"request_hash":request_hash,"previous":previous,"generation":generation,"manifest_hash":manifest_hash});
        atomic_write(&self.journal_path(), &encode(&journal)?)?;
        crate::fault_point("journal.persisted");
        let record = log.append(lock, "generation.committed", ts, manifest_hash, json!({"request_id":request_id,"request_hash":request_hash,"previous":previous,"generation":generation}))?;
        let current = Self::current_for(&record)?;
        atomic_write(&self.current_path(), &encode(&current)?)?;
        fs::remove_file(self.journal_path())?;
        sync_dir(&self.root.join("tx"))?;
        Ok(current)
    }

    pub fn commit(&self, request_id: &str, manifest_hash: &str, ts: &str) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        self.commit_locked(&lock, request_id, manifest_hash, ts)
    }

    /// Resolve an uncertain IPC write by its durable request ID after recovery.
    pub fn request_status(&self, request_id: &str) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        self.audit_locked()?;
        let records = Log::new(&self.root).audit()?;
        for record in self.commits(&records)? {
            if record["meta"]["request_id"] == request_id {
                return Ok(json!({"status":"committed","result":Self::current_for(&record)?}));
            }
        }
        Ok(json!({"status":"not_found","retry_with_same_request_id":true}))
    }

    /// Create an immutable, named reference to the current generation.
    pub fn archive(&self, archive_id: &str) -> io::Result<Value> {
        if archive_id.is_empty()
            || archive_id.len() > 128
            || !archive_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid archive id",
            ));
        }
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        let current = self
            .read_current()?
            .ok_or_else(|| invalid("no committed generation"))?;
        fs::create_dir_all(self.archives_path())?;
        let path = self.archives_path().join(format!("{archive_id}.json"));
        if path.exists() {
            let old = read_json(&path)?;
            if old == current {
                return Ok(old);
            }
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "archive id conflict",
            ));
        }
        atomic_write(&path, &encode(&current)?)?;
        sync_dir(&self.archives_path())?;
        Ok(current)
    }

    /// Append a new generation pointing at a named archive.
    pub fn rollback(&self, request_id: &str, archive_id: &str, ts: &str) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        if archive_id.is_empty()
            || archive_id.len() > 128
            || !archive_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid archive id",
            ));
        }
        let archived = read_json(&self.archives_path().join(format!("{archive_id}.json")))?;
        if !self
            .commits(&Log::new(&self.root).audit()?)?
            .iter()
            .any(|r| Self::current_for(r).is_ok_and(|v| v == archived))
        {
            return Err(invalid("archive references unknown generation"));
        }
        let manifest_hash = archived["manifest_hash"]
            .as_str()
            .ok_or_else(|| invalid("invalid archive"))?
            .to_owned();
        self.commit_locked(&lock, request_id, &manifest_hash, ts)
    }

    /// Mark and sweep unreachable CAS objects. The plan is persisted before deletion.
    pub fn gc(&self, dry_run: bool) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        let records = Log::new(&self.root).audit()?;
        let mut reachable = std::collections::BTreeSet::new();
        let cas = Cas::new(&self.root);
        for record in &records {
            let h = record["payload_hash"]
                .as_str()
                .ok_or_else(|| invalid("invalid payload hash"))?;
            reachable.insert(h.to_owned());
        }
        let commits = self.commits(&records)?;
        let mut manifests = std::collections::BTreeSet::new();
        for record in &commits {
            manifests.insert(
                record["payload_hash"]
                    .as_str()
                    .ok_or_else(|| invalid("invalid manifest hash"))?
                    .to_owned(),
            );
        }
        let archive_dir = self.archives_path();
        match fs::read_dir(&archive_dir) {
            Ok(entries) => {
                for entry in entries {
                    let entry = entry?;
                    if !entry.file_type()?.is_file() {
                        return Err(invalid("invalid archive entry"));
                    }
                    let name = entry.file_name().to_string_lossy().to_string();
                    if !name.ends_with(".json") || name.len() <= 5 {
                        return Err(invalid("invalid archive name"));
                    }
                    let archived = read_json(&entry.path())?;
                    let hash = archived["manifest_hash"]
                        .as_str()
                        .ok_or_else(|| invalid("invalid archive"))?;
                    manifests.insert(hash.to_owned());
                }
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => (),
            Err(e) => return Err(e),
        }
        for hash in manifests {
            reachable.insert(hash.clone());
            let manifest = manifest::get(&hash, &cas)?;
            for entries in manifest["roots"]
                .as_object()
                .ok_or_else(|| invalid("invalid manifest roots"))?
                .values()
            {
                for entry in entries
                    .as_object()
                    .ok_or_else(|| invalid("invalid manifest entries"))?
                    .values()
                {
                    reachable.insert(
                        entry["sha256"]
                            .as_str()
                            .ok_or_else(|| invalid("invalid object reference"))?
                            .to_owned(),
                    );
                }
            }
        }
        let mut delete = Vec::new();
        let root = self.root.join("objects/sha256");
        for p in fs::read_dir(&root)? {
            let p = p?;
            if !p.file_type()?.is_dir() {
                return Err(invalid("invalid CAS prefix"));
            }
            for bucket in fs::read_dir(p.path())? {
                let bucket = bucket?;
                if !bucket.file_type()?.is_dir() {
                    return Err(invalid("invalid CAS bucket"));
                }
                for f in fs::read_dir(bucket.path())? {
                    let f = f?;
                    if !f.file_type()?.is_file() {
                        return Err(invalid("invalid CAS object entry"));
                    }
                    let name = f.file_name().to_string_lossy().into_owned();
                    if name.len() != 64
                        || !name
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    {
                        return Err(invalid("invalid CAS object name"));
                    }
                    let h = format!("sha256:{name}");
                    if !reachable.contains(&h) {
                        delete.push(h);
                    }
                }
            }
        }
        delete.sort();
        let plan = json!({"reachable":reachable.iter().cloned().collect::<Vec<_>>(),"delete":delete,"dry_run":dry_run});
        fs::create_dir_all(self.root.join("audit"))?;
        atomic_write(&self.root.join("audit/gc-plan.json"), &encode(&plan)?)?;
        if !dry_run {
            for h in plan["delete"].as_array().unwrap() {
                let hash = h.as_str().unwrap();
                let hex = hash.strip_prefix("sha256:").unwrap();
                let p = root.join(&hex[..2]).join(&hex[2..4]).join(hex);
                fs::remove_file(p)?;
                sync_dir(&root.join(&hex[..2]).join(&hex[2..4]))?;
            }
        }
        Ok(plan)
    }
    /// Verify a stable snapshot of the current pointer and confirmed history.
    pub fn audit(&self) -> io::Result<Option<Value>> {
        let _lock = WriterLock::acquire(&self.root)?;
        self.audit_locked()
    }
    fn audit_locked(&self) -> io::Result<Option<Value>> {
        self.check_version()?;
        self.state_id()?;
        let records = Log::new(&self.root).audit()?;
        self.verify_payloads(&records)?;
        Cas::new(&self.root).audit_all()?;
        let commits = self.commits(&records)?;
        let latest = commits.last().map(Self::current_for).transpose()?;
        if self.read_current()? != latest {
            return Err(invalid("current conflicts with confirmed history"));
        }
        if self.journal_path().exists() {
            return Err(invalid("transaction requires recovery"));
        }
        let known = commits
            .iter()
            .map(Self::current_for)
            .collect::<io::Result<Vec<_>>>()?;
        match fs::read_dir(self.archives_path()) {
            Ok(entries) => {
                for entry in entries {
                    let entry = entry?;
                    if !entry.file_type()?.is_file() {
                        return Err(invalid("invalid archive entry"));
                    }
                    let name = entry.file_name().to_string_lossy().to_string();
                    if !name.ends_with(".json") || name.len() <= 5 {
                        return Err(invalid("invalid archive name"));
                    }
                    if !known.contains(&read_json(&entry.path())?) {
                        return Err(invalid("archive references unknown generation"));
                    }
                }
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => (),
            Err(e) => return Err(e),
        }
        Ok(latest)
    }

    pub fn audit_report(&self) -> io::Result<Value> {
        let _lock = WriterLock::acquire(&self.root)?;
        let current = self.audit_locked()?.unwrap_or(Value::Null);
        let records = Log::new(&self.root).audit()?;
        Ok(
            json!({"ok":true,"state_id":self.state_id()?,"generation":current.get("generation").cloned().unwrap_or(Value::from(0)),"log_seq":records.len(),"current":current}),
        )
    }
    fn state_id(&self) -> io::Result<String> {
        let id = fs::read_to_string(self.root.join("STATE_ID"))?;
        if id.len() != 71
            || !id.starts_with("sha256:")
            || !id[7..]
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(invalid("invalid state id"));
        }
        Ok(id)
    }

    pub fn state_id_for_ipc(&self) -> io::Result<String> {
        self.state_id()
    }

    /// Minimal IPC primitives. Callers still receive the same durable semantics
    /// as the CLI; writes use the regular writer lock and recovery path.
    pub fn object_put(&self, data: &[u8]) -> io::Result<String> {
        let _lock = WriterLock::acquire(&self.root)?;
        Cas::new(&self.root).put(data)
    }

    pub fn object_get(&self, hash: &str) -> io::Result<Vec<u8>> {
        Cas::new(&self.root).get(hash)
    }

    pub fn manifest_create(&self, roots: Value) -> io::Result<String> {
        let _lock = WriterLock::acquire(&self.root)?;
        manifest::create(roots, &Cas::new(&self.root))
    }

    pub fn manifest_get(&self, hash: &str) -> io::Result<Value> {
        manifest::get(hash, &Cas::new(&self.root))
    }

    #[cfg(unix)]
    pub fn cas_cleanup_abandoned_uploads(&self) -> io::Result<usize> {
        Cas::new(&self.root).cleanup_abandoned_uploads()
    }

    #[cfg(unix)]
    pub fn object_upload_begin(&self, size: u64, hash: &str) -> io::Result<crate::cas::CasUpload> {
        Cas::new(&self.root).begin_upload(size, hash)
    }

    #[cfg(unix)]
    pub fn object_upload_finish(&self, upload: crate::cas::CasUpload) -> io::Result<String> {
        upload.finish(&Cas::new(&self.root))
    }

    pub fn current(&self) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        Ok(self.read_current()?.unwrap_or(Value::Null))
    }

    /// Inspect the durable pointer without recovery or writes, for migration dry-runs.
    pub fn inspect(&self) -> io::Result<Value> {
        let current = match fs::read(self.current_path()) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(io::Error::other)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => Value::Null,
            Err(error) => return Err(error),
        };
        Ok(json!({"state_id":self.state_id()?,"current":current}))
    }

    pub fn record_append(
        &self,
        kind: &str,
        ts: &str,
        payload_hash: &str,
        meta: Value,
    ) -> io::Result<Value> {
        let lock = WriterLock::acquire(&self.root)?;
        self.recover_locked(&lock)?;
        Cas::new(&self.root).get(payload_hash)?;
        Log::new(&self.root).append(&lock, kind, ts, payload_hash, meta)
    }

    /// Produce a checkpoint suitable for storage outside the state directory.
    pub fn checkpoint(&self, created_at: &str) -> io::Result<Value> {
        if created_at.is_empty() {
            return Err(invalid("invalid checkpoint timestamp"));
        }
        let _lock = WriterLock::acquire(&self.root)?;
        let current = self
            .audit_locked()?
            .ok_or_else(|| invalid("no committed generation"))?;
        let records = Log::new(&self.root).audit()?;
        let head = records.last().ok_or_else(|| invalid("missing log head"))?;
        Ok(
            json!({"protocol":"molt-kernel-checkpoint/1", "state_id":self.state_id()?, "generation":current["generation"], "log_seq":head["seq"], "log_hash":head["hash"], "manifest_hash":current["manifest_hash"], "created_at":created_at, "kernel_version":env!("CARGO_PKG_VERSION")}),
        )
    }

    /// Verify the checkpoint against the corresponding prefix of the current chain.
    pub fn verify_checkpoint(&self, checkpoint: &Value) -> io::Result<()> {
        let _lock = WriterLock::acquire(&self.root)?;
        self.audit_locked()?;
        let fields = checkpoint
            .as_object()
            .ok_or_else(|| invalid("invalid checkpoint"))?;
        if fields.len() != 8
            || checkpoint["protocol"] != "molt-kernel-checkpoint/1"
            || checkpoint["state_id"].as_str() != Some(self.state_id()?.as_str())
            || checkpoint["created_at"].as_str().is_none_or(str::is_empty)
            || checkpoint["kernel_version"]
                .as_str()
                .is_none_or(str::is_empty)
        {
            return Err(invalid("invalid checkpoint"));
        }
        let seq = checkpoint["log_seq"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or_else(|| invalid("invalid checkpoint sequence"))?;
        let records = Log::new(&self.root).audit()?;
        let end = usize::try_from(seq).map_err(|_| invalid("invalid checkpoint sequence"))?;
        if end > records.len() || records[end - 1]["hash"] != checkpoint["log_hash"] {
            return Err(invalid("checkpoint chain mismatch"));
        }
        let commits = self.commits(&records[..end])?;
        let checkpoint_current = commits
            .last()
            .map(Self::current_for)
            .transpose()?
            .ok_or_else(|| invalid("checkpoint has no generation"))?;
        if checkpoint_current["generation"] != checkpoint["generation"]
            || checkpoint_current["manifest_hash"] != checkpoint["manifest_hash"]
        {
            return Err(invalid("checkpoint generation mismatch"));
        }
        // The local chain may have advanced since export.  The checkpoint
        // remains valid when its exact confirmed prefix is still present;
        // rollback/forked histories fail the hash and prefix checks above.
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        path: PathBuf,
        manifest: String,
    }
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "molt-state-{}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            State::new(&path).init().unwrap();
            let manifest = manifest::create(json!({}), &Cas::new(&path)).unwrap();
            Self { path, manifest }
        }
        fn state(&self) -> State {
            State::new(&self.path)
        }
        fn journal(&self, id: &str, ts: &str, previous: Option<Value>, generation: u64) -> Value {
            json!({"request_id":id,"request_hash":sha256(&encode(&json!({"manifest_hash":self.manifest,"ts":ts})).unwrap()),"previous":previous,"generation":generation,"manifest_hash":self.manifest})
        }
        fn write_journal(&self, journal: &Value) {
            atomic_write(&self.state().journal_path(), &encode(journal).unwrap()).unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.path).unwrap();
        }
    }

    #[test]
    fn commits_are_monotonic_and_idempotent() {
        let f = Fixture::new();
        let state = f.state();
        let first = state.commit("one", &f.manifest, "t1").unwrap();
        assert_eq!(state.commit("one", &f.manifest, "t1").unwrap(), first);
        assert_eq!(
            state.commit("one", &f.manifest, "t2").unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        let second = state.commit("two", &f.manifest, "t2").unwrap();
        assert_eq!(second["generation"], 2);
        assert_eq!(state.audit().unwrap(), Some(second));
    }

    #[test]
    fn concurrent_readers_observe_only_complete_generations() {
        let f = Fixture::new();
        let path = f.path.clone();
        let readers = (0..4)
            .map(|_| {
                let path = path.clone();
                let manifest = f.manifest.clone();
                std::thread::spawn(move || {
                    let state = State::new(&path);
                    for _ in 0..250 {
                        let current = loop {
                            match state.current() {
                                Ok(current) => break current,
                                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                                    std::thread::yield_now();
                                }
                                Err(error) => panic!("unexpected reader error: {error}"),
                            }
                        };
                        if !current.is_null() {
                            assert_eq!(current["manifest_hash"], manifest);
                            assert!((1..=20).contains(&current["generation"].as_u64().unwrap()));
                            assert!(current["log_seq"].as_u64().unwrap() >= 1);
                            assert!(current["log_hash"].as_str().unwrap().starts_with("sha256:"));
                        }
                    }
                })
            })
            .collect::<Vec<_>>();
        for generation in 1..=20 {
            loop {
                match f.state().commit(
                    &format!("reader-{generation}"),
                    &f.manifest,
                    &format!("t{generation}"),
                ) {
                    Ok(_) => break,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        std::thread::yield_now();
                    }
                    Err(error) => panic!("unexpected writer error: {error}"),
                }
            }
        }
        for reader in readers {
            reader.join().unwrap();
        }
        assert_eq!(f.state().current().unwrap()["generation"], 20);
    }

    #[test]
    fn commit_metadata_must_match_its_request() {
        let f = Fixture::new();
        let state = f.state();
        state.commit("one", &f.manifest, "t1").unwrap();
        let mut record = Log::new(&f.path).audit().unwrap().remove(0);
        record["meta"]["request_hash"] = json!("sha256:wrong");
        assert!(State::current_for(&record).is_err());
        record["meta"]["request_id"] = json!("");
        assert!(State::current_for(&record).is_err());
    }

    #[test]
    fn recovery_handles_uncommitted_and_confirmed_journal() {
        let f = Fixture::new();
        let state = f.state();
        let first = state.commit("one", &f.manifest, "t1").unwrap();
        let journal = f.journal("two", "t2", Some(first.clone()), 2);
        f.write_journal(&journal);
        state.recover().unwrap();
        state.recover().unwrap();
        assert_eq!(state.audit().unwrap(), Some(first.clone()));
        f.write_journal(&journal);
        let lock = WriterLock::acquire(&f.path).unwrap();
        let record = Log::new(&f.path).append(&lock, "generation.committed", "t2", &f.manifest, json!({"request_id":"two","request_hash":journal["request_hash"],"previous":first,"generation":2})).unwrap();
        drop(lock);
        state.recover().unwrap();
        state.recover().unwrap();
        assert_eq!(
            state.audit().unwrap(),
            Some(State::current_for(&record).unwrap())
        );
        assert_eq!(
            state.commit("two", &f.manifest, "t2").unwrap(),
            State::current_for(&record).unwrap()
        );
    }

    #[test]
    fn corrupted_journal_and_pointer_fail_closed() {
        let f = Fixture::new();
        let state = f.state();
        state.commit("one", &f.manifest, "t1").unwrap();
        f.write_journal(&json!({}));
        assert!(state.recover().is_err());
        fs::remove_file(state.journal_path()).unwrap();
        atomic_write(&state.current_path(), b"{}").unwrap();
        assert!(state.audit().is_err());
        assert!(state.recover().is_err());
    }
    #[test]
    fn audit_rejects_corrupted_state_identity() {
        let f = Fixture::new();
        let state = f.state();
        state.commit("one", &f.manifest, "t1").unwrap();
        fs::write(f.path.join("STATE_ID"), b"invalid").unwrap();
        assert!(state.audit().is_err());
        assert!(state.checkpoint("t2").is_err());
    }
    #[test]
    fn audit_checks_non_generation_payloads() {
        let f = Fixture::new();
        let state = f.state();
        state.commit("one", &f.manifest, "t1").unwrap();
        let cas = Cas::new(&f.path);
        let hash = cas.put(b"ordinary evidence").unwrap();
        let lock = WriterLock::acquire(&f.path).unwrap();
        Log::new(&f.path)
            .append(&lock, "event", "t2", &hash, json!({}))
            .unwrap();
        drop(lock);
        state.audit().unwrap();
        let hex = hash.strip_prefix("sha256:").unwrap();
        fs::remove_file(
            f.path
                .join("objects/sha256")
                .join(&hex[..2])
                .join(&hex[2..4])
                .join(hex),
        )
        .unwrap();
        assert!(state.audit().is_err());
        assert!(state.recover().is_err());
    }
    #[test]
    fn confirmed_history_tampering_fails_closed() {
        for case in [
            "object_changed",
            "object_deleted",
            "log_truncated",
            "log_reordered",
            "current_changed",
            "manifest_changed",
        ] {
            let f = Fixture::new();
            let state = f.state();
            let cas = Cas::new(&f.path);
            let content = cas.put(b"evidence").unwrap();
            let roots =
                json!({"workspace":{"file":{"sha256":content,"size":8,"mode":420,"type":"file"}}});
            let manifest_hash = manifest::create(roots, &cas).unwrap();
            state.commit("one", &manifest_hash, "t1").unwrap();
            state.commit("two", &manifest_hash, "t2").unwrap();
            let object_path = |hash: &str| {
                let hex = hash.strip_prefix("sha256:").unwrap();
                f.path
                    .join("objects/sha256")
                    .join(&hex[..2])
                    .join(&hex[2..4])
                    .join(hex)
            };
            let segment = f.path.join("log/0000000000000001.seg");
            match case {
                "object_changed" => fs::write(object_path(&content), b"altered").unwrap(),
                "object_deleted" => fs::remove_file(object_path(&content)).unwrap(),
                "log_truncated" => {
                    let mut bytes = fs::read(&segment).unwrap();
                    bytes.pop();
                    fs::write(&segment, bytes).unwrap();
                }
                "log_reordered" => {
                    let bytes = fs::read(&segment).unwrap();
                    let split = bytes.iter().position(|b| *b == b'\n').unwrap() + 1;
                    fs::write(&segment, [&bytes[split..], &bytes[..split]].concat()).unwrap();
                }
                "current_changed" => atomic_write(&state.current_path(), b"{}").unwrap(),
                "manifest_changed" => fs::write(object_path(&manifest_hash), b"{}").unwrap(),
                _ => unreachable!(),
            }
            assert!(state.audit().is_err(), "{case}");
            assert!(state.recover().is_err(), "{case}");
        }
    }
    #[test]
    fn checkpoint_verifies_prefix_and_rejects_forks_and_rollback() {
        let f = Fixture::new();
        let state = f.state();
        state.commit("one", &f.manifest, "t1").unwrap();
        let checkpoint = state.checkpoint("t1").unwrap();
        state.verify_checkpoint(&checkpoint).unwrap();
        state.commit("two", &f.manifest, "t2").unwrap();
        state.verify_checkpoint(&checkpoint).unwrap();
        let mut bad = checkpoint.clone();
        bad["log_hash"] = json!("sha256:wrong");
        assert!(state.verify_checkpoint(&bad).is_err());
        bad = checkpoint.clone();
        bad["log_seq"] = json!(3);
        assert!(state.verify_checkpoint(&bad).is_err());
        bad = checkpoint;
        bad["state_id"] = json!("sha256:wrong");
        assert!(state.verify_checkpoint(&bad).is_err());
    }

    #[test]
    fn archive_rollback_and_gc_preserve_history_and_reachable_objects() {
        let f = Fixture::new();
        let state = f.state();
        let first = state.commit("one", &f.manifest, "t1").unwrap();
        state.archive("known-good").unwrap();
        let second = state.commit("two", &f.manifest, "t2").unwrap();
        let rolled = state.rollback("three", "known-good", "t3").unwrap();
        assert_eq!(rolled["generation"], 3);
        assert_eq!(rolled["manifest_hash"], first["manifest_hash"]);
        assert_eq!(state.audit().unwrap(), Some(rolled));
        assert_eq!(
            state.rollback("three", "known-good", "t3").unwrap()["generation"],
            3
        );
        assert_eq!(state.audit_report().unwrap()["log_seq"], 3);

        let orphan = Cas::new(&f.path).put(b"unreferenced").unwrap();
        let dry = state.gc(true).unwrap();
        assert!(dry["delete"].as_array().unwrap().contains(&json!(orphan)));
        assert!(Cas::new(&f.path).get(&orphan).is_ok());
        let swept = state.gc(false).unwrap();
        assert!(swept["delete"].as_array().unwrap().contains(&json!(orphan)));
        assert!(Cas::new(&f.path).get(&orphan).is_err());
        assert_eq!(state.current().unwrap(), state.audit().unwrap().unwrap());
        assert_eq!(second["generation"], 2);
    }
}
