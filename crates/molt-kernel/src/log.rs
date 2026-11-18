//! Confirmed append-only hash chain. Callers must hold the state writer lock.
use crate::{canonical_json, sha256};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_HEAD_TEMP: AtomicU64 = AtomicU64::new(0);

const GENESIS: &str = "sha256:0000000000000000000000000000000000000000000000000000000000000000";

fn corrupt(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn encode(value: &Value) -> io::Result<Vec<u8>> {
    canonical_json(value).map_err(corrupt)
}
fn sync_dir(path: &Path) -> io::Result<()> {
    File::open(path)?.sync_all()
}

/// Advisory nonblocking state writer lock, held until dropped.
pub struct WriterLock {
    file: File,
    state: PathBuf,
}
impl WriterLock {
    pub fn acquire(state: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(state.join("LOCK"))?;
        file.try_lock()?;
        Ok(Self {
            file,
            state: fs::canonicalize(state)?,
        })
    }
}
impl Drop for WriterLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

/// A single segment and its durably confirmed head.
pub struct Log {
    dir: PathBuf,
}
impl Log {
    pub fn new(state: &Path) -> Self {
        Self {
            dir: state.join("log"),
        }
    }
    fn segment(&self) -> PathBuf {
        self.dir.join("0000000000000001.seg")
    }
    fn head(&self) -> PathBuf {
        self.dir.join("HEAD")
    }

    /// Initialize an empty log; existing heads are never replaced.
    pub fn init(&self) -> io::Result<()> {
        fs::create_dir_all(&self.dir)?;
        let segment = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(self.segment())?;
        segment.sync_all()?;
        let mut head = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(self.head())?;
        head.write_all(&encode(&json!({"seq": 0, "hash": GENESIS}))?)?;
        head.sync_all()?;
        sync_dir(&self.dir)?;
        sync_dir(
            self.dir
                .parent()
                .ok_or_else(|| corrupt("invalid log directory"))?,
        )
    }

    /// Verify the confirmed prefix and return its records and byte length.
    fn confirmed(&self) -> io::Result<(Vec<Value>, u64, u64)> {
        let head_bytes = fs::read(self.head())?;
        let head: Value =
            serde_json::from_slice(&head_bytes).map_err(|_| corrupt("invalid head"))?;
        if encode(&head)? != head_bytes || head.as_object().is_none_or(|x| x.len() != 2) {
            return Err(corrupt("invalid head"));
        }
        let head_seq = head["seq"]
            .as_u64()
            .ok_or_else(|| corrupt("invalid head sequence"))?;
        let head_hash = head["hash"]
            .as_str()
            .ok_or_else(|| corrupt("invalid head hash"))?;
        let mut bytes = Vec::new();
        File::open(self.segment())?.read_to_end(&mut bytes)?;
        let mut offset = 0usize;
        let mut prev = GENESIS.to_owned();
        let mut records = Vec::new();
        for seq in 1..=head_seq {
            let end = bytes[offset..]
                .iter()
                .position(|&b| b == b'\n')
                .ok_or_else(|| corrupt("confirmed log truncated"))?
                + offset;
            let raw = &bytes[offset..end];
            let record: Value =
                serde_json::from_slice(raw).map_err(|_| corrupt("invalid confirmed record"))?;
            if encode(&record)? != raw {
                return Err(corrupt("noncanonical record"));
            }
            let object = record
                .as_object()
                .ok_or_else(|| corrupt("invalid record"))?;
            if object.len() != 7
                || record["seq"].as_u64() != Some(seq)
                || record["prev"].as_str() != Some(prev.as_str())
                || record["kind"].as_str().is_none_or(str::is_empty)
                || record["ts"].as_str().is_none_or(str::is_empty)
                || record["payload_hash"].as_str().is_none_or(str::is_empty)
                || !record["meta"].is_object()
            {
                return Err(corrupt("broken log chain"));
            }
            let hash = record["hash"]
                .as_str()
                .ok_or_else(|| corrupt("missing record hash"))?
                .to_owned();
            let mut unhashed = record.clone();
            unhashed
                .as_object_mut()
                .expect("object checked")
                .remove("hash");
            if sha256(&encode(&unhashed)?) != hash {
                return Err(corrupt("record hash mismatch"));
            }
            prev = hash;
            offset = end + 1;
            records.push(record);
        }
        if prev != head_hash {
            return Err(corrupt("head mismatch"));
        }
        Ok((records, offset as u64, bytes.len() as u64))
    }

    /// Audit never repairs the evidence, and flags unconfirmed trailing bytes.
    pub fn audit(&self) -> io::Result<Vec<Value>> {
        let (records, confirmed, total) = self.confirmed()?;
        if confirmed != total {
            return Err(corrupt("unconfirmed log tail"));
        }
        Ok(records)
    }

    /// Remove only bytes beyond the confirmed HEAD, under the writer lock.
    fn recover_confirmed(&self, lock: &WriterLock) -> io::Result<Vec<Value>> {
        let state = self
            .dir
            .parent()
            .ok_or_else(|| corrupt("invalid log directory"))?;
        if fs::canonicalize(state)? != lock.state {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "wrong state lock",
            ));
        }
        let (records, confirmed, total) = self.confirmed()?;
        if total > confirmed {
            let file = OpenOptions::new().write(true).open(self.segment())?;
            file.set_len(confirmed)?;
            file.sync_all()?;
        }
        Ok(records)
    }

    pub fn recover(&self, lock: &WriterLock) -> io::Result<()> {
        self.recover_confirmed(lock).map(|_| ())
    }

    /// Confirm one record by fsyncing its segment and then atomically replacing HEAD.
    pub fn append(
        &self,
        lock: &WriterLock,
        kind: &str,
        ts: &str,
        payload_hash: &str,
        meta: Value,
    ) -> io::Result<Value> {
        let records = self.recover_confirmed(lock)?;
        let prev = records
            .last()
            .map_or(GENESIS, |r| r["hash"].as_str().expect("verified hash"));
        let seq = records.len() as u64 + 1;
        let mut record = json!({"seq":seq,"prev":prev,"kind":kind,"ts":ts,"payload_hash":payload_hash,"meta":meta});
        let hash = sha256(&encode(&record)?);
        record["hash"] = Value::String(hash.clone());
        let mut segment = OpenOptions::new().append(true).open(self.segment())?;
        segment.seek(SeekFrom::End(0))?;
        segment.write_all(&encode(&record)?)?;
        segment.write_all(b"\n")?;
        segment.sync_all()?;
        crate::fault_point("log.fsynced");
        // A crashed process can leave temporary files behind, including after PID
        // reuse. Never rely on one predetermined name being available.
        let (tmp, mut temp) = loop {
            let candidate = self.dir.join(format!(
                ".HEAD-{}-{}",
                std::process::id(),
                NEXT_HEAD_TEMP.fetch_add(1, Ordering::Relaxed)
            ));
            match OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&candidate)
            {
                Ok(file) => break (candidate, file),
                Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err),
            }
        };
        let result: io::Result<()> = (|| {
            temp.write_all(&encode(&json!({"seq":seq,"hash":hash}))?)?;
            temp.sync_all()?;
            fs::rename(&tmp, self.head())?;
            crate::fault_point("head.renamed");
            sync_dir(&self.dir)?;
            crate::fault_point("head.durable");
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(tmp);
        }
        result?;
        Ok(record)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn fixture() -> (PathBuf, Log) {
        let dir = std::env::temp_dir().join(format!(
            "molt-log-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&dir).unwrap();
        let log = Log::new(&dir);
        log.init().unwrap();
        (dir, log)
    }
    #[test]
    fn chain_recovery_and_tampering() {
        let (dir, log) = fixture();
        let lock = WriterLock::acquire(&dir).unwrap();
        assert!(WriterLock::acquire(&dir).is_err());
        log.append(
            &lock,
            "event",
            "2026-01-01T00:00:00Z",
            "sha256:x",
            json!({}),
        )
        .unwrap();
        log.append(
            &lock,
            "event",
            "2026-01-01T00:00:01Z",
            "sha256:y",
            json!({}),
        )
        .unwrap();
        assert_eq!(log.audit().unwrap().len(), 2);
        OpenOptions::new()
            .append(true)
            .open(log.segment())
            .unwrap()
            .write_all(b"partial")
            .unwrap();
        assert!(log.audit().is_err());
        log.recover(&lock).unwrap();
        assert_eq!(log.audit().unwrap().len(), 2);
        let mut bytes = fs::read(log.segment()).unwrap();
        bytes[10] ^= 1;
        fs::write(log.segment(), bytes).unwrap();
        assert!(log.audit().is_err());
        assert!(log.recover(&lock).is_err());
        drop(lock);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn stale_head_temporary_file_does_not_block_append() {
        let (dir, log) = fixture();
        let lock = WriterLock::acquire(&dir).unwrap();
        let next = NEXT_HEAD_TEMP.load(Ordering::Relaxed);
        // Pre-create several names so the test remains effective if another
        // parallel test consumes one of the counters.
        for n in next..next + 32 {
            fs::write(
                log.dir.join(format!(".HEAD-{}-{n}", std::process::id())),
                b"stale",
            )
            .unwrap();
        }
        log.append(
            &lock,
            "event",
            "2026-01-01T00:00:00Z",
            "sha256:x",
            json!({}),
        )
        .unwrap();
        assert_eq!(log.audit().unwrap().len(), 1);
        drop(lock);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn lock_from_another_state_cannot_authorize_a_write() {
        let (first_dir, first_log) = fixture();
        let (second_dir, second_log) = fixture();
        let lock = WriterLock::acquire(&first_dir).unwrap();
        assert_eq!(
            second_log.recover(&lock).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert!(second_log
            .append(&lock, "event", "now", "sha256:x", json!({}))
            .is_err());
        assert!(first_log.audit().unwrap().is_empty());
        assert!(second_log.audit().unwrap().is_empty());
        drop(lock);
        fs::remove_dir_all(first_dir).unwrap();
        fs::remove_dir_all(second_dir).unwrap();
    }
}
