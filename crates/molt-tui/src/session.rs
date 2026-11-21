//! Append-only JSONL session storage.
//!
//! Each session is one `<id>.jsonl` file under `$HOME/.local/share/molt/sessions`
//! (or an explicit root for tests). Every line is one stored message:
//!
//! ```json
//! {"role":"user","content":"hi","created_at":"2026-11-21T19:38:12Z"}
//! ```
//!
//! Appends never rewrite history. Timestamps are ISO-8601 UTC generated with
//! `SystemTime` only (no `chrono`). Files are created `0600` and the session
//! directory `0700` on Unix.

use crate::provider::ChatMessage;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static SESSION_SEQ: AtomicU64 = AtomicU64::new(0);

/// A loaded session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Session {
    pub id: String,
    /// ISO-8601 UTC creation time (the file's mtime when loaded).
    pub created_at: String,
    pub messages: Vec<ChatMessage>,
    /// Lines skipped while loading (diagnostic only; not persisted).
    pub skipped_lines: usize,
}

/// One row of [`SessionStore::list`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionSummary {
    pub id: String,
    /// ISO-8601 UTC mtime.
    pub modified_at: String,
    /// First `user` message, whitespace-collapsed and at most 60 characters.
    pub preview: String,
}

/// Directory-backed store for JSONL sessions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionStore {
    root: PathBuf,
}

impl Default for SessionStore {
    /// `$HOME/.local/share/molt/sessions`, created best-effort with mode `0700`.
    fn default() -> Self {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        let store = Self::new(home.join(".local/share/molt/sessions"));
        // `Default` cannot surface an error; `new_session` retries and reports.
        let _ = store.ensure_root();
        store
    }
}

impl SessionStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Create the session directory (mode `0700` on Unix).
    fn ensure_root(&self) -> Result<()> {
        // Parents keep their conventional mode; only the store root is private.
        if let Some(parent) = self.root.parent() {
            if !parent.as_os_str().is_empty() && !parent.exists() {
                fs::create_dir_all(parent)
                    .with_context(|| format!("create session parent {}", parent.display()))?;
            }
        }

        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        match builder.create(&self.root) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("create session directory {}", self.root.display()))
            }
        }
        if !self.root.is_dir() {
            anyhow::bail!("session root {} is not a directory", self.root.display());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&self.root, fs::Permissions::from_mode(0o700))
                .with_context(|| format!("chmod session directory {}", self.root.display()))?;
        }
        Ok(())
    }

    /// Create a new empty session file and return its metadata.
    pub fn new_session(&self) -> Result<Session> {
        self.ensure_root()?;
        let now = SystemTime::now();
        let created_at = format_utc(now);
        let sequence = SESSION_SEQ.fetch_add(1, Ordering::Relaxed);
        let nanos = now
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.subsec_nanos())
            .unwrap_or(0);
        let id = format!(
            "{}-{:x}-{nanos:09x}-{sequence:x}",
            compact_timestamp(&created_at),
            std::process::id()
        );
        let path = self.session_path(&id);
        open_new(&path).with_context(|| format!("create session {}", path.display()))?;
        Ok(Session {
            id,
            created_at,
            messages: Vec::new(),
            skipped_lines: 0,
        })
    }

    /// Append one message as a JSON line; existing history is untouched.
    pub fn append(&self, session: &Session, message: &ChatMessage) -> Result<()> {
        if !valid_session_id(&session.id) {
            anyhow::bail!("invalid session id");
        }
        self.ensure_root()?;
        let record = StoredMessage {
            role: message.role.clone(),
            content: message.content.clone(),
            created_at: format_utc(SystemTime::now()),
        };
        let mut line = serde_json::to_string(&record).context("encode session message")?;
        line.push('\n');

        let path = self.session_path(&session.id);
        let mut file =
            open_append(&path).with_context(|| format!("open session {}", path.display()))?;
        file.write_all(line.as_bytes())
            .with_context(|| format!("append session {}", path.display()))?;
        file.flush()
            .with_context(|| format!("flush session {}", path.display()))?;
        Ok(())
    }

    /// Load the most recently modified session, or `None` for an empty store.
    pub fn load_latest(&self) -> Result<Option<Session>> {
        let Some((path, modified)) = self.session_files()?.into_iter().next() else {
            return Ok(None);
        };
        let Some(id) = file_stem(&path) else {
            return Ok(None);
        };

        let file = File::open(&path).with_context(|| format!("open session {}", path.display()))?;
        let mut reader = BufReader::new(file);
        let mut messages = Vec::new();
        let mut skipped_lines = 0_usize;
        let mut buffer = Vec::new();

        loop {
            buffer.clear();
            let read = reader
                .read_until(b'\n', &mut buffer)
                .with_context(|| format!("read session {}", path.display()))?;
            if read == 0 {
                break;
            }
            let Ok(text) = std::str::from_utf8(&buffer) else {
                skipped_lines += 1;
                continue;
            };
            let text = text.trim();
            if text.is_empty() {
                continue;
            }
            match serde_json::from_str::<StoredMessage>(text) {
                Ok(record) => messages.push(ChatMessage {
                    role: record.role,
                    content: record.content,
                }),
                Err(_) => skipped_lines += 1,
            }
        }

        Ok(Some(Session {
            id,
            created_at: format_utc(modified),
            messages,
            skipped_lines,
        }))
    }

    /// List sessions newest first, with a preview of the first user message.
    pub fn list(&self) -> Result<Vec<SessionSummary>> {
        let mut summaries = Vec::new();
        for (path, modified) in self.session_files()? {
            let Some(id) = file_stem(&path) else {
                continue;
            };
            let preview = preview_for(&path).unwrap_or_default();
            summaries.push(SessionSummary {
                id,
                modified_at: format_utc(modified),
                preview,
            });
        }
        Ok(summaries)
    }

    fn session_path(&self, id: &str) -> PathBuf {
        self.root.join(format!("{id}.jsonl"))
    }

    fn session_files(&self) -> Result<Vec<(PathBuf, SystemTime)>> {
        let entries = match fs::read_dir(&self.root) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("read session directory {}", self.root.display()))
            }
        };

        let mut files = Vec::new();
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => continue,
            };
            let path = entry.path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("jsonl") {
                continue;
            }
            if file_stem(&path).is_none() {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(metadata) if metadata.is_file() => metadata,
                _ => continue,
            };
            let modified = metadata.modified().unwrap_or(UNIX_EPOCH);
            files.push((path, modified));
        }
        files.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| right.0.cmp(&left.0)));
        Ok(files)
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct StoredMessage {
    role: String,
    content: String,
    created_at: String,
}

#[cfg(unix)]
fn open_new(path: &Path) -> std::io::Result<File> {
    use std::os::unix::fs::OpenOptionsExt;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
}

#[cfg(not(unix))]
fn open_new(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}

fn open_append(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(file)
}

fn file_stem(path: &Path) -> Option<String> {
    let stem = path.file_stem().and_then(|stem| stem.to_str())?;
    if valid_session_id(stem) {
        Some(stem.to_string())
    } else {
        None
    }
}

/// Session ids are file stems, so only the characters produced by
/// `new_session` are accepted. This also keeps a crafted `Session` from
/// escaping the store root through `append`.
fn valid_session_id(id: &str) -> bool {
    !id.is_empty()
        && id != "."
        && id != ".."
        && id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
}

fn preview_for(path: &Path) -> Result<String> {
    let file = File::open(path)?;
    let mut reader = BufReader::new(file);
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        if reader.read_until(b'\n', &mut buffer)? == 0 {
            break;
        }
        let Ok(text) = std::str::from_utf8(&buffer) else {
            continue;
        };
        let text = text.trim();
        if text.is_empty() {
            continue;
        }
        if let Ok(record) = serde_json::from_str::<StoredMessage>(text) {
            if record.role == "user" {
                return Ok(summarize(&record.content));
            }
        }
    }
    Ok(String::new())
}

/// Collapse whitespace and cap the preview at 60 characters.
pub(crate) fn summarize(content: &str) -> String {
    let collapsed = content.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut characters = collapsed.chars();
    let prefix: String = characters.by_ref().take(60).collect();
    if characters.next().is_some() {
        let mut truncated: String = prefix.chars().take(59).collect();
        truncated.push('…');
        truncated
    } else {
        prefix
    }
}

/// Format a `SystemTime` as `YYYY-MM-DDTHH:MM:SSZ` (UTC).
pub fn format_utc(time: SystemTime) -> String {
    let seconds = time
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let days = (seconds / 86_400) as i64;
    let remainder = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        remainder / 3_600,
        (remainder % 3_600) / 60,
        remainder % 60
    )
}

fn compact_timestamp(iso: &str) -> String {
    iso.chars()
        .filter(|character| *character != '-' && *character != ':')
        .collect()
}

/// Howard Hinnant's `civil_from_days`, valid for non-negative day counts.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = (shifted - era * 146_097) as u64;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era as i64 + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_prime + 2) / 5 + 1) as u32;
    let month = if month_prime < 10 {
        month_prime + 3
    } else {
        month_prime - 9
    } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicU64;
    use std::time::Duration;

    static TEMP_SEQ: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "molt-tui-session-{tag}-{}-{}",
                std::process::id(),
                TEMP_SEQ.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("create temp dir");
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn message(role: &str, content: &str) -> ChatMessage {
        ChatMessage {
            role: role.to_string(),
            content: content.to_string(),
        }
    }

    #[test]
    fn utc_formatting_matches_known_instants() {
        assert_eq!(format_utc(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        assert_eq!(
            format_utc(UNIX_EPOCH + Duration::from_secs(1_704_067_200)),
            "2024-01-01T00:00:00Z"
        );
        assert_eq!(
            format_utc(UNIX_EPOCH + Duration::from_secs(1_709_164_800)),
            "2024-02-29T00:00:00Z"
        );
    }

    #[test]
    fn new_session_append_and_load_round_trip() {
        let temp = TempDir::new("round-trip");
        let store = SessionStore::new(temp.path().join("sessions"));

        let session = store.new_session().expect("new session");
        store
            .append(&session, &message("user", "hello"))
            .expect("append user");
        store
            .append(&session, &message("assistant", "hi there"))
            .expect("append assistant");

        let loaded = store
            .load_latest()
            .expect("load latest")
            .expect("one session");
        assert_eq!(loaded.id, session.id);
        assert_eq!(loaded.created_at, session.created_at);
        assert_eq!(
            loaded.messages,
            vec![message("user", "hello"), message("assistant", "hi there")]
        );
        assert_eq!(loaded.skipped_lines, 0);
    }

    #[test]
    fn bad_lines_are_skipped_and_counted() {
        let temp = TempDir::new("bad-lines");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = store.new_session().expect("new session");

        let path = store.root().join(format!("{}.jsonl", session.id));
        let mut file = OpenOptions::new()
            .append(true)
            .open(&path)
            .expect("open session file");
        writeln!(file, "not json").expect("write bad line");
        writeln!(
            file,
            "{{\"role\":\"user\",\"content\":\"kept\",\"created_at\":\"2026-11-21T00:00:00Z\"}}"
        )
        .expect("write good line");
        writeln!(file, "{{}}").expect("write incomplete line");

        let loaded = store
            .load_latest()
            .expect("load latest")
            .expect("one session");
        assert_eq!(loaded.messages, vec![message("user", "kept")]);
        assert_eq!(loaded.skipped_lines, 2);
    }

    #[test]
    fn empty_directory_has_no_latest_session() {
        let temp = TempDir::new("empty");
        let store = SessionStore::new(temp.path().join("missing"));

        assert!(store.load_latest().expect("load latest").is_none());
        assert!(store.list().expect("list").is_empty());
    }

    #[test]
    fn an_empty_session_file_loads_with_no_messages() {
        let temp = TempDir::new("empty-file");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = store.new_session().expect("new session");

        let loaded = store
            .load_latest()
            .expect("load latest")
            .expect("one session");
        assert_eq!(loaded.id, session.id);
        assert!(loaded.messages.is_empty());
        assert_eq!(loaded.skipped_lines, 0);
    }

    #[cfg(unix)]
    #[test]
    fn session_directory_and_files_are_private() {
        use std::os::unix::fs::PermissionsExt;

        let temp = TempDir::new("permissions");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = store.new_session().expect("new session");
        store
            .append(&session, &message("user", "secret"))
            .expect("append");

        let directory_mode = fs::metadata(store.root())
            .expect("directory metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(directory_mode, 0o700);

        let path = store.root().join(format!("{}.jsonl", session.id));
        let file_mode = fs::metadata(path)
            .expect("file metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(file_mode, 0o600);
    }

    #[test]
    fn list_previews_the_first_user_message_and_truncates_at_sixty() {
        let temp = TempDir::new("preview");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = store.new_session().expect("new session");
        store
            .append(&session, &message("assistant", "not this one"))
            .expect("append assistant");
        let long = "字".repeat(100);
        store
            .append(&session, &message("user", &long))
            .expect("append user");

        let summaries = store.list().expect("list");
        assert_eq!(summaries.len(), 1);
        assert_eq!(summaries[0].id, session.id);
        assert_eq!(summaries[0].preview.chars().count(), 60);
        assert!(summaries[0].preview.ends_with('…'));
        assert!(summaries[0].preview.starts_with('字'));
        assert!(!summaries[0].modified_at.is_empty());
    }

    #[test]
    fn preview_collapses_whitespace() {
        assert_eq!(summarize("  hello\n\tworld  "), "hello world");
        assert_eq!(summarize("short"), "short");
    }

    #[test]
    fn append_rejects_ids_that_escape_the_store() {
        let temp = TempDir::new("traversal");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = Session {
            id: "../escape".to_string(),
            created_at: "2026-11-21T00:00:00Z".to_string(),
            messages: Vec::new(),
            skipped_lines: 0,
        };

        let error = store
            .append(&session, &message("user", "nope"))
            .expect_err("traversal id must be rejected");
        assert!(error.to_string().contains("invalid session id"), "{error}");
        assert!(!temp.path().join("escape.jsonl").exists());
    }

    #[test]
    fn sessions_are_appended_not_rewritten() {
        let temp = TempDir::new("append-only");
        let store = SessionStore::new(temp.path().join("sessions"));
        let session = store.new_session().expect("new session");
        store
            .append(&session, &message("user", "one"))
            .expect("append one");
        store
            .append(&session, &message("user", "two"))
            .expect("append two");

        let path = store.root().join(format!("{}.jsonl", session.id));
        let contents = fs::read_to_string(&path).expect("read session");
        assert_eq!(contents.lines().count(), 2);
        assert!(contents.lines().next().unwrap().contains("\"one\""));
        assert!(contents.lines().nth(1).unwrap().contains("\"two\""));
    }
}
