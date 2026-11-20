use molt_kernel::{canonical_json, state::State};
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
#[cfg(unix)]
use std::io::Read;
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

// Publish without replacing an existing checkpoint or exposing partial bytes.
fn publish_checkpoint(output: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let (temp, mut file) = loop {
        let path = parent.join(format!(
            ".molt-checkpoint-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => break (path, file),
            Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err),
        }
    };
    let result = (|| {
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::hard_link(&temp, output)?;
        fs::File::open(parent)?.sync_all()
    })();
    drop(file);
    let cleanup = fs::remove_file(&temp).and_then(|()| fs::File::open(parent)?.sync_all());
    result?;
    cleanup
}

fn usage() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "usage: molt-kernel init --state PATH | serve --state PATH | ping --socket PATH | audit --state PATH [--checkpoint PATH] | checkpoint export --state PATH --out PATH --created-at TIMESTAMP")
}

#[cfg(unix)]
fn serve(state: &molt_kernel::state::State, path: &Path) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{FileTypeExt, OpenOptionsExt, PermissionsExt};
    use std::os::unix::net::{UnixListener, UnixStream};
    let service_lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path.parent().unwrap_or(Path::new(".")).join("SERVER_LOCK"))?;
    if !service_lock.metadata()?.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "kernel service lock path is not a regular file",
        ));
    }
    let lock_result =
        unsafe { libc::flock(service_lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if lock_result != 0 {
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "kernel server already running for this state",
            ));
        }
        return Err(error);
    }
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if !metadata.file_type().is_socket() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "kernel socket path exists and is not a socket",
            ));
        }
        match UnixStream::connect(path) {
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "kernel socket is accepting connections without the service lock",
                ));
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionRefused | io::ErrorKind::NotFound
                ) =>
            {
                fs::remove_file(path)?;
            }
            Err(error) => return Err(error),
        }
    }
    let listener = UnixListener::bind(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    state.cas_cleanup_abandoned_uploads()?;
    fn frame(stream: &mut UnixStream) -> io::Result<Value> {
        let mut len = [0; 4];
        stream.read_exact(&mut len)?;
        let n = u32::from_be_bytes(len) as usize;
        if n > 16 * 1024 * 1024 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "frame too large",
            ));
        }
        let mut bytes = vec![0; n];
        stream.read_exact(&mut bytes)?;
        serde_json::from_slice(&bytes)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid request"))
    }
    fn send(stream: &mut UnixStream, value: &Value) -> io::Result<()> {
        let bytes = canonical_json(value).map_err(io::Error::other)?;
        if bytes.len() > 16 * 1024 * 1024 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "frame too large",
            ));
        }
        stream.write_all(&(bytes.len() as u32).to_be_bytes())?;
        stream.write_all(&bytes)
    }
    #[cfg(target_os = "linux")]
    fn same_uid(stream: &UnixStream) -> io::Result<bool> {
        #[repr(C)]
        struct Ucred {
            pid: i32,
            uid: u32,
            gid: u32,
        }
        unsafe extern "C" {
            fn getsockopt(fd: i32, level: i32, name: i32, val: *mut Ucred, len: *mut u32) -> i32;
            fn geteuid() -> u32;
        }
        const SOL_SOCKET: i32 = 1;
        const SO_PEERCRED: i32 = 17;
        let mut cred = Ucred {
            pid: 0,
            uid: 0,
            gid: 0,
        };
        let mut len = std::mem::size_of::<Ucred>() as u32;
        let rc = unsafe {
            getsockopt(
                stream.as_raw_fd(),
                SOL_SOCKET,
                SO_PEERCRED,
                &mut cred,
                &mut len,
            )
        };
        if rc != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(cred.uid == unsafe { geteuid() })
    }
    #[cfg(not(target_os = "linux"))]
    fn same_uid(_stream: &UnixStream) -> io::Result<bool> {
        Ok(true)
    }
    for conn in listener.incoming() {
        let mut stream = conn?;
        if !same_uid(&stream)? {
            let _ = send(
                &mut stream,
                &json!({"jsonrpc":"2.0","id":null,"error":{"code":"permission_denied","message":"peer uid mismatch"}}),
            );
            continue;
        }
        let mut handshaken = false;
        #[cfg(unix)]
        let mut upload: Option<(String, molt_kernel::cas::CasUpload)> = None;
        #[cfg(unix)]
        let mut next_upload_id = 0u64;
        loop {
            let request = match frame(&mut stream) {
                Ok(v) => v,
                Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => break,
                Err(e) => {
                    let _ = send(
                        &mut stream,
                        &json!({"jsonrpc":"2.0","id":null,"error":{"code":"invalid_request","message":e.to_string()}}),
                    );
                    break;
                }
            };
            let id = request.get("id").cloned().unwrap_or(Value::Null);
            let method = request.get("method").and_then(Value::as_str).unwrap_or("");
            let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
            let result = (|| -> io::Result<Value> {
                if !handshaken && method != "handshake" {
                    Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "handshake required",
                    ))
                } else {
                    match method {
                        "handshake" => {
                            if request
                                .get("params")
                                .and_then(|p| p.get("protocol"))
                                .and_then(Value::as_str)
                                != Some("molt-kernel/1")
                            {
                                Err(io::Error::new(
                                    io::ErrorKind::InvalidInput,
                                    "unsupported protocol",
                                ))
                            } else {
                                handshaken = true;
                                Ok(
                                    json!({"protocol":"molt-kernel/1","state_id": state.state_id_for_ipc()?}),
                                )
                            }
                        }
                        "audit" => state.audit_report(),
                        "inspect" => state.inspect(),
                        "checkpoint.export" => {
                            state.checkpoint(params["created_at"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "created_at required")
                            })?)
                        }
                        "archive" => state.archive(params["id"].as_str().ok_or_else(|| {
                            io::Error::new(io::ErrorKind::InvalidInput, "id required")
                        })?),
                        "rollback" => state.rollback(
                            params["request_id"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "request_id required")
                            })?,
                            params["archive"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "archive required")
                            })?,
                            params["ts"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "ts required")
                            })?,
                        ),
                        "gc" => state.gc(params["dry_run"].as_bool().unwrap_or(false)),
                        "request.status" => state.request_status(
                            params["request_id"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "request_id required")
                            })?,
                        ),
                        "current" => state.current(),
                        "object.put" => {
                            let data: Vec<u8> = params["data"]
                                .as_array()
                                .ok_or_else(|| {
                                    io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "data array required",
                                    )
                                })?
                                .iter()
                                .map(|v| {
                                    v.as_u64()
                                        .filter(|n| *n <= 255)
                                        .map(|n| n as u8)
                                        .ok_or_else(|| {
                                            io::Error::new(
                                                io::ErrorKind::InvalidInput,
                                                "invalid byte",
                                            )
                                        })
                                })
                                .collect::<io::Result<_>>()?;
                            state.object_put(&data).map(|hash| json!({"hash":hash}))
                        }
                        "object.get" => state
                            .object_get(params["hash"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "hash required")
                            })?)
                            .map(|data| json!({"data":data})),
                        "manifest.create" => state
                            .manifest_create(params.get("roots").cloned().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "roots required")
                            })?)
                            .map(|hash| json!({"hash":hash})),
                        "manifest.get" => {
                            state.manifest_get(params["hash"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "hash required")
                            })?)
                        }
                        "object.upload.begin" => {
                            #[cfg(unix)]
                            {
                                if upload.is_some() {
                                    return Err(io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "upload already active on connection",
                                    ));
                                }
                                let size = params["size"].as_u64().ok_or_else(|| {
                                    io::Error::new(io::ErrorKind::InvalidInput, "size required")
                                })?;
                                let hash = params["hash"].as_str().ok_or_else(|| {
                                    io::Error::new(io::ErrorKind::InvalidInput, "hash required")
                                })?;
                                let session = format!("upload-{next_upload_id}");
                                next_upload_id += 1;
                                upload =
                                    Some((session.clone(), state.object_upload_begin(size, hash)?));
                                Ok(json!({"upload_id":session,"next_offset":0}))
                            }
                            #[cfg(not(unix))]
                            Err(io::Error::new(
                                io::ErrorKind::Unsupported,
                                "streaming upload requires Unix",
                            ))
                        }
                        "object.upload.chunk" => {
                            #[cfg(unix)]
                            {
                                let requested = params["upload_id"].as_str().ok_or_else(|| {
                                    io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "upload_id required",
                                    )
                                })?;
                                let (active_id, active) = upload
                                    .as_mut()
                                    .filter(|(id, _)| id == requested)
                                    .ok_or_else(|| {
                                        io::Error::new(
                                            io::ErrorKind::InvalidInput,
                                            "unknown upload",
                                        )
                                    })?;
                                let _ = active_id;
                                let offset = params["offset"].as_u64().ok_or_else(|| {
                                    io::Error::new(io::ErrorKind::InvalidInput, "offset required")
                                })?;
                                let bytes = params["data"]
                                    .as_array()
                                    .ok_or_else(|| {
                                        io::Error::new(
                                            io::ErrorKind::InvalidInput,
                                            "data array required",
                                        )
                                    })?
                                    .iter()
                                    .map(|v| {
                                        v.as_u64()
                                            .filter(|n| *n <= 255)
                                            .map(|n| n as u8)
                                            .ok_or_else(|| {
                                                io::Error::new(
                                                    io::ErrorKind::InvalidInput,
                                                    "invalid byte",
                                                )
                                            })
                                    })
                                    .collect::<io::Result<Vec<_>>>()?;
                                let next = active.write_chunk(offset, &bytes)?;
                                Ok(json!({"next_offset":next}))
                            }
                            #[cfg(not(unix))]
                            Err(io::Error::new(
                                io::ErrorKind::Unsupported,
                                "streaming upload requires Unix",
                            ))
                        }
                        "object.upload.finish" => {
                            #[cfg(unix)]
                            {
                                let requested = params["upload_id"].as_str().ok_or_else(|| {
                                    io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "upload_id required",
                                    )
                                })?;
                                let (active_id, _) = upload
                                    .as_ref()
                                    .filter(|(id, _)| id == requested)
                                    .ok_or_else(|| {
                                        io::Error::new(
                                            io::ErrorKind::InvalidInput,
                                            "unknown upload",
                                        )
                                    })?;
                                let _ = active_id;
                                let (_, upload_session) = upload.take().expect("active upload");
                                state
                                    .object_upload_finish(upload_session)
                                    .map(|hash| json!({"hash":hash}))
                            }
                            #[cfg(not(unix))]
                            Err(io::Error::new(
                                io::ErrorKind::Unsupported,
                                "streaming upload requires Unix",
                            ))
                        }
                        "object.upload.abort" => {
                            #[cfg(unix)]
                            {
                                let requested = params["upload_id"].as_str().ok_or_else(|| {
                                    io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "upload_id required",
                                    )
                                })?;
                                if upload.as_ref().is_some_and(|(id, _)| id == requested) {
                                    upload.take();
                                    Ok(json!({"aborted":true}))
                                } else {
                                    Err(io::Error::new(
                                        io::ErrorKind::InvalidInput,
                                        "unknown upload",
                                    ))
                                }
                            }
                            #[cfg(not(unix))]
                            Err(io::Error::new(
                                io::ErrorKind::Unsupported,
                                "streaming upload requires Unix",
                            ))
                        }
                        "record.append" => state.record_append(
                            params["kind"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "kind required")
                            })?,
                            params["ts"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "ts required")
                            })?,
                            params["payload_hash"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "payload_hash required")
                            })?,
                            params.get("meta").cloned().unwrap_or_else(|| json!({})),
                        ),
                        "generation.commit" => state.commit(
                            params["request_id"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "request_id required")
                            })?,
                            params["manifest_hash"].as_str().ok_or_else(|| {
                                io::Error::new(
                                    io::ErrorKind::InvalidInput,
                                    "manifest_hash required",
                                )
                            })?,
                            params["ts"].as_str().ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidInput, "ts required")
                            })?,
                        ),
                        _ => Err(io::Error::new(
                            io::ErrorKind::InvalidInput,
                            "unknown method",
                        )),
                    }
                }
            })();
            let response = match result {
                Ok(v) => json!({"jsonrpc":"2.0","id":id,"result":v}),
                Err(e) => {
                    let code = match e.kind() {
                        io::ErrorKind::PermissionDenied => "permission_denied",
                        io::ErrorKind::InvalidInput => "invalid_request",
                        io::ErrorKind::AlreadyExists => "request_conflict",
                        io::ErrorKind::InvalidData => "integrity_error",
                        io::ErrorKind::WouldBlock => "tx_locked",
                        _ => "io_error",
                    };
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":e.to_string()}})
                }
            };
            // A caller can lose the response after a durable commit. Keep serving
            // other connections so it can resolve the outcome by request ID.
            if send(&mut stream, &response).is_err() {
                break;
            }
        }
    }
    Ok(())
}

#[cfg(unix)]
fn ping(path: &Path) -> io::Result<Value> {
    use std::os::unix::net::UnixStream;

    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let mut stream = loop {
        match UnixStream::connect(path) {
            Ok(stream) => break stream,
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::NotFound | io::ErrorKind::ConnectionRefused
                ) && std::time::Instant::now() < deadline =>
            {
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(error) => return Err(error),
        }
    };
    stream.set_read_timeout(Some(std::time::Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(std::time::Duration::from_secs(5)))?;
    let request = json!({
        "jsonrpc":"2.0",
        "id":1,
        "method":"handshake",
        "params":{"protocol":"molt-kernel/1"}
    });
    let bytes = canonical_json(&request).map_err(io::Error::other)?;
    stream.write_all(&(bytes.len() as u32).to_be_bytes())?;
    stream.write_all(&bytes)?;
    let mut size = [0; 4];
    stream.read_exact(&mut size)?;
    let size = u32::from_be_bytes(size) as usize;
    if size > 16 * 1024 * 1024 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "kernel ping response frame too large",
        ));
    }
    let mut response = vec![0; size];
    stream.read_exact(&mut response)?;
    let response: Value = serde_json::from_slice(&response).map_err(io::Error::other)?;
    if response["id"] != 1
        || response["result"]["protocol"] != "molt-kernel/1"
        || response["result"]["state_id"].as_str().is_none()
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "kernel handshake failed or protocol is incompatible",
        ));
    }
    Ok(json!({"ok":true,"state_id":response["result"]["state_id"]}))
}

fn option(args: &[String], name: &str) -> io::Result<String> {
    let mut found = args.iter().enumerate().filter(|(_, arg)| *arg == name);
    let (index, _) = found.next().ok_or_else(usage)?;
    if found.next().is_some() {
        return Err(usage());
    }
    args.get(index + 1)
        .filter(|s| !s.starts_with("--"))
        .cloned()
        .ok_or_else(usage)
}
fn only_options(args: &[String], allowed: &[&str]) -> io::Result<()> {
    let mut i = 1;
    while i < args.len() {
        let arg = args[i].as_str();
        if !allowed.contains(&arg) {
            return Err(usage());
        }
        if arg == "--state"
            || arg == "--id"
            || arg == "--request-id"
            || arg == "--archive"
            || arg == "--ts"
            || arg == "--checkpoint"
            || arg == "--created-at"
            || arg == "--out"
            || arg == "--socket"
        {
            i += 1;
            if i >= args.len() || args[i].starts_with("--") {
                return Err(usage());
            }
        }
        i += 1;
    }
    Ok(())
}
fn run(args: &[String]) -> io::Result<Value> {
    let command = args.first().ok_or_else(usage)?;
    if command == "ping" {
        only_options(args, &["--socket"])?;
        if args.len() != 3 {
            return Err(usage());
        }
        #[cfg(unix)]
        {
            return ping(Path::new(&option(args, "--socket")?));
        }
        #[cfg(not(unix))]
        {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "unix IPC unavailable",
            ));
        }
    }
    let state_path = option(args, "--state")?;
    let path = Path::new(&state_path);
    if !path.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "state path must be absolute",
        ));
    }
    let state = State::new(path);
    if command != "init" {
        state.check_version()?;
    }
    match command.as_str() {
        "init" if args.len() == 3 => {
            fs::create_dir_all(path)?;
            state.init()?;
            Ok(json!({"ok":true}))
        }
        "serve" if args.len() == 3 => {
            #[cfg(unix)]
            {
                serve(&state, &path.join("kernel.sock")).map(|_| Value::Null)
            }
            #[cfg(not(unix))]
            {
                Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "unix IPC unavailable",
                ))
            }
        }
        "audit" => {
            only_options(args, &["--state", "--checkpoint"])?;
            if args.len() != 3 && args.len() != 5 {
                return Err(usage());
            }
            let mut checkpoint_verified = false;
            if let Some(checkpoint_path) = args.iter().position(|x| x == "--checkpoint") {
                let filename = args.get(checkpoint_path + 1).ok_or_else(usage)?;
                let bytes = fs::read(filename)?;
                let checkpoint: Value = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
                if canonical_json(&checkpoint).map_err(io::Error::other)? != bytes {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "noncanonical checkpoint",
                    ));
                }
                state.verify_checkpoint(&checkpoint)?;
                checkpoint_verified = true;
            }
            let mut report = state.audit_report()?;
            if checkpoint_verified {
                report["checkpoint_verified"] = Value::Bool(true);
            }
            Ok(report)
        }
        "recover" if args.len() == 3 => {
            state.recover()?;
            Ok(json!({"ok":true}))
        }
        "archive" if args.len() == 5 => {
            Ok(json!({"archive":state.archive(&option(args,"--id")?)?}))
        }
        "rollback" if args.len() == 9 => Ok(
            json!({"current":state.rollback(&option(args,"--request-id")?, &option(args,"--archive")?, &option(args,"--ts")?)?}),
        ),
        "gc" => {
            only_options(args, &["--state", "--dry-run"])?;
            if args.len() != 3 && args.len() != 4 {
                return Err(usage());
            }
            Ok(state.gc(args.iter().any(|x| x == "--dry-run"))?)
        }
        "checkpoint" if args.get(1).is_some_and(|x| x == "export") && args.len() == 8 => {
            let checkpoint = state.checkpoint(&option(args, "--created-at")?)?;
            let bytes = canonical_json(&checkpoint).map_err(io::Error::other)?;
            let output = option(args, "--out")?;
            publish_checkpoint(Path::new(&output), &bytes)?;
            Ok(json!({"ok":true,"checkpoint":checkpoint}))
        }
        _ => Err(usage()),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(result) => println!(
            "{}",
            String::from_utf8(canonical_json(&result).expect("valid result")).expect("UTF-8")
        ),
        Err(err) => {
            let code = match err.kind() {
                io::ErrorKind::InvalidInput => "invalid_request",
                io::ErrorKind::AlreadyExists => "request_conflict",
                io::ErrorKind::WouldBlock => "tx_locked",
                io::ErrorKind::InvalidData => "integrity_error",
                _ => "io_error",
            };
            eprintln!(
                "{}",
                json!({"ok":false,"error":{"code":code,"message":err.to_string()}})
            );
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkpoint_publication_is_complete_and_never_overwrites() {
        let dir = std::env::temp_dir().join(format!(
            "molt-checkpoint-test-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&dir).unwrap();
        let target = dir.join("checkpoint.json");
        publish_checkpoint(&target, b"first").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"first");
        assert_eq!(
            publish_checkpoint(&target, b"second").unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(fs::read(&target).unwrap(), b"first");
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 1);
        fs::remove_dir_all(dir).unwrap();
    }
}
