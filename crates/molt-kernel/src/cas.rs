//! Immutable, verified content-addressed objects.
use crate::sha256;
use sha2::{Digest, Sha256};
#[cfg(not(unix))]
use std::fs::OpenOptions;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(unix)]
use std::ffi::CString;
#[cfg(unix)]
use std::os::fd::{AsRawFd, FromRawFd};

#[cfg(unix)]
fn c_name(name: &str) -> io::Result<CString> {
    CString::new(name).map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid path"))
}

#[cfg(unix)]
fn open_child_dir(parent: &File, name: &str, create: bool) -> io::Result<File> {
    let name = c_name(name)?;
    let mut fd = unsafe {
        libc::openat(
            parent.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    };
    if fd < 0 && create && io::Error::last_os_error().kind() == io::ErrorKind::NotFound {
        let rc = unsafe { libc::mkdirat(parent.as_raw_fd(), name.as_ptr(), 0o700) };
        if rc != 0 {
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::AlreadyExists {
                return Err(error);
            }
        } else {
            parent.sync_all()?;
        }
        fd = unsafe {
            libc::openat(
                parent.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
    }
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

#[cfg(unix)]
fn bucket_dir(state: &Path, hex: &str, create: bool) -> io::Result<File> {
    let state = File::open(state)?;
    let objects = open_child_dir(&state, "objects", create)?;
    let sha = open_child_dir(&objects, "sha256", create)?;
    let prefix = open_child_dir(&sha, &hex[..2], create)?;
    open_child_dir(&prefix, &hex[2..4], create)
}

#[cfg(unix)]
fn openat_file(dir: &File, name: &str, flags: i32, mode: u32) -> io::Result<File> {
    let name = c_name(name)?;
    let fd = unsafe {
        libc::openat(
            dir.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            mode,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[cfg(not(unix))]
fn sync_dir(path: &Path) -> io::Result<()> {
    File::open(path)?.sync_all()
}

/// Object storage rooted at the kernel state directory.
pub struct Cas {
    state: PathBuf,
    root: PathBuf,
}

#[cfg(unix)]
pub struct CasUpload {
    state: PathBuf,
    temp_name: String,
    file: Option<File>,
    expected_size: u64,
    received: u64,
    expected_hash: String,
    hasher: Sha256,
}

#[cfg(unix)]
impl CasUpload {
    pub fn write_chunk(&mut self, offset: u64, bytes: &[u8]) -> io::Result<u64> {
        if offset != self.received || bytes.is_empty() || bytes.len() > 1024 * 1024 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid upload offset or chunk size",
            ));
        }
        let next = self
            .received
            .checked_add(bytes.len() as u64)
            .filter(|size| *size <= self.expected_size)
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidInput, "upload exceeds declared size")
            })?;
        self.file
            .as_mut()
            .expect("upload file open")
            .write_all(bytes)?;
        self.hasher.update(bytes);
        self.received = next;
        Ok(next)
    }

    pub fn finish(mut self, cas: &Cas) -> io::Result<String> {
        if self.received != self.expected_size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "upload size mismatch",
            ));
        }
        let hash = format!("sha256:{:x}", self.hasher.clone().finalize());
        if hash != self.expected_hash {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "upload hash mismatch",
            ));
        }
        self.file.take().expect("upload file open").sync_all()?;
        let hex = hash.strip_prefix("sha256:").expect("sha256 prefix");
        let target = bucket_dir(&self.state, hex, true)?;
        let state = File::open(&self.state)?;
        let source_name = c_name(&self.temp_name)?;
        let target_name = c_name(hex)?;
        let rc = unsafe {
            libc::linkat(
                state.as_raw_fd(),
                source_name.as_ptr(),
                target.as_raw_fd(),
                target_name.as_ptr(),
                0,
            )
        };
        let result = if rc == 0 {
            target.sync_all()
        } else {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::AlreadyExists {
                cas.get(&hash).map(|_| ())
            } else {
                Err(error)
            }
        };
        result?;
        let rc = unsafe { libc::unlinkat(state.as_raw_fd(), source_name.as_ptr(), 0) };
        if rc != 0 {
            return Err(io::Error::last_os_error());
        }
        state.sync_all()?;
        Ok(hash)
    }
}

#[cfg(unix)]
impl Drop for CasUpload {
    fn drop(&mut self) {
        self.file.take();
        if let Ok(state) = File::open(&self.state) {
            if let Ok(name) = c_name(&self.temp_name) {
                unsafe {
                    libc::unlinkat(state.as_raw_fd(), name.as_ptr(), 0);
                }
            }
        }
    }
}

impl Cas {
    pub fn new(state: &Path) -> Self {
        Self {
            state: state.to_path_buf(),
            root: state.join("objects/sha256"),
        }
    }

    #[cfg(unix)]
    pub fn cleanup_abandoned_uploads(&self) -> io::Result<usize> {
        let state = File::open(&self.state)?;
        let mut removed = 0;
        for entry in fs::read_dir(&self.state)? {
            let entry = entry?;
            let name = entry.file_name();
            let Some(name) = name.to_str().filter(|name| name.starts_with(".upload-")) else {
                continue;
            };
            let name = c_name(name)?;
            let rc = unsafe { libc::unlinkat(state.as_raw_fd(), name.as_ptr(), 0) };
            if rc == 0 {
                removed += 1;
            } else {
                let error = io::Error::last_os_error();
                if error.kind() != io::ErrorKind::NotFound {
                    return Err(error);
                }
            }
        }
        if removed > 0 {
            state.sync_all()?;
        }
        Ok(removed)
    }

    #[cfg(unix)]
    pub fn begin_upload(&self, expected_size: u64, expected_hash: &str) -> io::Result<CasUpload> {
        if expected_hash.strip_prefix("sha256:").is_none_or(|hex| {
            hex.len() != 64
                || !hex
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        }) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid expected hash",
            ));
        }
        let state = File::open(&self.state)?;
        let (temp_name, file) = loop {
            let name = format!(
                ".upload-{}-{}",
                std::process::id(),
                NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
            );
            match openat_file(
                &state,
                &name,
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
                0o600,
            ) {
                Ok(file) => break (name, file),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        };
        Ok(CasUpload {
            state: self.state.clone(),
            temp_name,
            file: Some(file),
            expected_size,
            received: 0,
            expected_hash: expected_hash.to_owned(),
            hasher: Sha256::new(),
        })
    }

    #[cfg(any(not(unix), test))]
    fn path(&self, hash: &str) -> io::Result<PathBuf> {
        let hex = hash
            .strip_prefix("sha256:")
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid hash"))?;
        if hex.len() != 64
            || !hex
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid hash"));
        }
        Ok(self.root.join(&hex[..2]).join(&hex[2..4]).join(hex))
    }

    fn check_directory(path: &Path) -> io::Result<()> {
        match fs::symlink_metadata(path) {
            Ok(meta) if meta.file_type().is_dir() && !meta.file_type().is_symlink() => Ok(()),
            Ok(_) => Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "CAS path component is not a real directory",
            )),
            Err(error) => Err(error),
        }
    }

    fn check_parent_chain(&self, path: &Path) -> io::Result<()> {
        let relative = path
            .strip_prefix(&self.state)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid CAS path"))?;
        Self::check_directory(&self.state)?;
        let mut current = self.state.clone();
        let components = relative.components().collect::<Vec<_>>();
        for component in components.iter().take(components.len().saturating_sub(1)) {
            current.push(component.as_os_str());
            Self::check_directory(&current)?;
        }
        Ok(())
    }

    #[cfg(not(unix))]
    fn ensure_parent_chain(&self, path: &Path) -> io::Result<()> {
        Self::check_directory(&self.state)?;
        let relative = path
            .strip_prefix(&self.state)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid CAS path"))?;
        let components = relative.components().collect::<Vec<_>>();
        let mut current = self.state.clone();
        for component in components.iter().take(components.len().saturating_sub(1)) {
            current.push(component.as_os_str());
            match fs::symlink_metadata(&current) {
                Ok(meta) if meta.file_type().is_dir() && !meta.file_type().is_symlink() => {}
                Ok(_) => {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "CAS path component is not a real directory",
                    ));
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    fs::create_dir(&current)?;
                    sync_dir(current.parent().expect("created directory has parent"))?;
                }
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    /// Store bytes without replacing an existing object; verify any existing copy.
    pub fn put(&self, data: &[u8]) -> io::Result<String> {
        let hash = sha256(data);
        #[cfg(unix)]
        {
            let hex = hash.strip_prefix("sha256:").expect("sha256 prefix");
            let dir = bucket_dir(&self.state, hex, true)?;
            let temp_name = loop {
                let name = format!(
                    ".tmp-{}-{}",
                    std::process::id(),
                    NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
                );
                match openat_file(
                    &dir,
                    &name,
                    libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
                    0o600,
                ) {
                    Ok(mut file) => {
                        let written = file.write_all(data).and_then(|()| file.sync_all());
                        drop(file);
                        if let Err(error) = written {
                            if let Ok(temp) = c_name(&name) {
                                unsafe { libc::unlinkat(dir.as_raw_fd(), temp.as_ptr(), 0) };
                                let _ = dir.sync_all();
                            }
                            return Err(error);
                        }
                        break name;
                    }
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(error),
                }
            };
            let object = c_name(hex)?;
            let temp = c_name(&temp_name)?;
            let rc = unsafe {
                libc::linkat(
                    dir.as_raw_fd(),
                    temp.as_ptr(),
                    dir.as_raw_fd(),
                    object.as_ptr(),
                    0,
                )
            };
            let result = if rc == 0 {
                dir.sync_all()
            } else {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::AlreadyExists {
                    self.get(&hash).map(|_| ())
                } else {
                    Err(error)
                }
            };
            unsafe { libc::unlinkat(dir.as_raw_fd(), temp.as_ptr(), 0) };
            dir.sync_all()?;
            result?;
            Ok(hash)
        }
        #[cfg(not(unix))]
        {
            let path = self.path(&hash)?;
            let dir = path.parent().expect("object path has parent");
            self.ensure_parent_chain(&path)?;
            self.check_parent_chain(&path)?;
            // Persist newly created directory entries from the state down to the leaf.
            let objects = self.root.parent().expect("CAS root has parent");
            sync_dir(objects.parent().expect("objects has parent"))?;
            sync_dir(objects)?;
            sync_dir(&self.root)?;
            sync_dir(dir.parent().expect("leaf has parent"))?;
            sync_dir(dir)?;
            let mut temp;
            let temp_path;
            loop {
                let name = format!(
                    ".tmp-{}-{}",
                    std::process::id(),
                    NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
                );
                let candidate = dir.join(name);
                match OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&candidate)
                {
                    Ok(file) => {
                        temp = file;
                        temp_path = candidate;
                        break;
                    }
                    Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
                    Err(err) => return Err(err),
                }
            }
            let result = (|| {
                temp.write_all(data)?;
                temp.sync_all()?;
                match fs::hard_link(&temp_path, &path) {
                    Ok(()) => sync_dir(dir)?,
                    Err(err) if err.kind() == io::ErrorKind::AlreadyExists => {
                        self.get(&hash)?;
                    }
                    Err(err) => return Err(err),
                }
                Ok(())
            })();
            drop(temp);
            let cleanup = fs::remove_file(&temp_path).and_then(|()| sync_dir(dir));
            result?;
            cleanup?;
            Ok(hash)
        }
    }

    /// Read an object and check its digest every time.
    pub fn get(&self, hash: &str) -> io::Result<Vec<u8>> {
        #[cfg(unix)]
        {
            let hex = hash
                .strip_prefix("sha256:")
                .filter(|hex| {
                    hex.len() == 64
                        && hex
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                })
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid hash"))?;
            let dir = bucket_dir(&self.state, hex, false)?;
            let mut file = openat_file(&dir, hex, libc::O_RDONLY, 0)?;
            if !file.metadata()?.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "object is not a regular file",
                ));
            }
            let mut data = Vec::new();
            file.read_to_end(&mut data)?;
            if sha256(&data) != hash {
                return Err(io::Error::new(io::ErrorKind::InvalidData, "hash mismatch"));
            }
            Ok(data)
        }
        #[cfg(not(unix))]
        {
            let path = self.path(hash)?;
            self.check_parent_chain(&path)?;
            let mut data = Vec::new();
            let mut options = OpenOptions::new();
            options.read(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                // Reject a replaced final path component. The digest check below
                // also detects content races; no path lookup is trusted as proof.
                options.custom_flags(libc_o_nofollow());
            }
            let mut file = options.open(&path)?;
            if !file.metadata()?.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "object is not a regular file",
                ));
            }
            file.read_to_end(&mut data)?;
            if sha256(&data) != hash {
                return Err(io::Error::new(io::ErrorKind::InvalidData, "hash mismatch"));
            }
            Ok(data)
        }
    }

    /// Verify every entry in the CAS, including objects that are currently
    /// unreachable.  An audit must never silently ignore a tampered orphan.
    pub fn audit_all(&self) -> io::Result<usize> {
        Self::check_directory(&self.state)?;
        match fs::symlink_metadata(self.state.join("objects")) {
            Ok(meta) if meta.file_type().is_dir() && !meta.file_type().is_symlink() => {}
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "CAS path component is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(0),
            Err(error) => return Err(error),
        }
        match fs::symlink_metadata(&self.root) {
            Ok(meta) if meta.file_type().is_dir() && !meta.file_type().is_symlink() => {}
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "CAS path component is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(0),
            Err(error) => return Err(error),
        }
        self.check_parent_chain(&self.root.join("entry"))?;
        let mut count = 0;
        let prefixes = match fs::read_dir(&self.root) {
            Ok(entries) => entries,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(0),
            Err(e) => return Err(e),
        };
        for prefix in prefixes {
            let prefix = prefix?;
            if !prefix.file_type()?.is_dir() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid CAS prefix",
                ));
            }
            let name = prefix.file_name().to_string_lossy().into_owned();
            if name.len() != 2
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid CAS prefix",
                ));
            }
            for bucket in fs::read_dir(prefix.path())? {
                let bucket = bucket?;
                if !bucket.file_type()?.is_dir() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "invalid CAS bucket",
                    ));
                }
                let bucket_name = bucket.file_name().to_string_lossy().into_owned();
                if bucket_name.len() != 2
                    || !bucket_name
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "invalid CAS bucket",
                    ));
                }
                for entry in fs::read_dir(bucket.path())? {
                    let entry = entry?;
                    if !entry.file_type()?.is_file() {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "invalid CAS object entry",
                        ));
                    }
                    let object = entry.file_name().to_string_lossy().into_owned();
                    if object.len() != 64
                        || !object
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "invalid CAS object name",
                        ));
                    }
                    let hash = format!("sha256:{object}");
                    self.get(&hash)?;
                    count += 1;
                }
            }
        }
        Ok(count)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn object_is_immutable_and_verified() {
        let dir = std::env::temp_dir().join(format!(
            "molt-cas-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&dir).unwrap();
        let cas = Cas::new(&dir);
        let hash = cas.put(b"first").unwrap();
        assert_eq!(cas.get(&hash).unwrap(), b"first");
        assert_eq!(cas.put(b"first").unwrap(), hash);
        fs::write(cas.path(&hash).unwrap(), b"tampered").unwrap();
        assert_eq!(
            cas.get(&hash).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert_eq!(
            cas.put(b"first").unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert!(cas.get("sha256:../../etc/passwd").is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn parent_directory_replacement_with_symlink_is_rejected() {
        use std::os::unix::fs::symlink;
        let dir = std::env::temp_dir().join(format!(
            "molt-cas-symlink-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        let outside = dir.join("outside");
        fs::create_dir_all(&outside).unwrap();
        let cas = Cas::new(&dir);
        let hash = cas.put(b"symlink-check").unwrap();
        let object = cas.path(&hash).unwrap();
        let prefix = object.parent().unwrap().parent().unwrap();
        fs::remove_dir_all(prefix).unwrap();
        symlink(&outside, prefix).unwrap();
        assert!(cas.get(&hash).is_err());
        assert!(cas.put(b"symlink-check").is_err());
        assert_eq!(fs::read_dir(outside).unwrap().count(), 0);
        fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn concurrent_bucket_replacement_never_writes_through_symlink() {
        use std::os::unix::fs::symlink;
        use std::sync::atomic::AtomicBool;
        let dir = std::env::temp_dir().join(format!(
            "molt-cas-race-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        let outside = dir.join("outside");
        fs::create_dir_all(&outside).unwrap();
        let cas = std::sync::Arc::new(Cas::new(&dir));
        let bytes = b"race-target";
        let hash = cas.put(bytes).unwrap();
        let bucket = cas.path(&hash).unwrap().parent().unwrap().to_path_buf();
        let moved = bucket.with_extension("moved");
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let swapper = {
            let stop = stop.clone();
            let outside_for_thread = outside.clone();
            std::thread::spawn(move || {
                for _ in 0..500 {
                    if stop.load(Ordering::Relaxed) {
                        break;
                    }
                    if fs::rename(&bucket, &moved).is_err() {
                        continue;
                    }
                    if symlink(&outside_for_thread, &bucket).is_ok() {
                        std::thread::yield_now();
                        let _ = fs::remove_file(&bucket);
                    }
                    let _ = fs::rename(&moved, &bucket);
                }
            })
        };
        for _ in 0..500 {
            if let Ok(value) = cas.get(&hash) {
                assert_eq!(value, bytes);
            }
            let _ = cas.put(bytes);
        }
        stop.store(true, Ordering::Relaxed);
        swapper.join().unwrap();
        assert_eq!(fs::read_dir(&outside).unwrap().count(), 0);
        fs::remove_dir_all(dir).unwrap();
    }
}
