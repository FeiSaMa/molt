#![cfg(unix)]

use molt_kernel::{canonical_json, sha256};
use serde_json::{json, Value};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static NEXT: AtomicU64 = AtomicU64::new(0);
const TS: &str = "2026-11-16T00:00:00Z";

struct Fixture {
    root: PathBuf,
    child: Option<Child>,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "molt-ipc-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let fixture = Self { root, child: None };
        fixture.ok(&["init"]);
        fixture
    }

    fn command(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_molt-kernel"))
            .args(args)
            .arg("--state")
            .arg(&self.root)
            .output()
            .unwrap()
    }

    fn ok(&self, args: &[&str]) -> Value {
        let output = self.command(args);
        assert!(output.status.success(), "{output:?}");
        serde_json::from_slice(&output.stdout).unwrap()
    }

    fn connect(&mut self) -> Client {
        self.start_server(None);
        let stream = self.wait_for_connection("server startup timeout");
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        Client { stream, id: 0 }
    }

    fn wait_for_connection(&mut self, message: &str) -> UnixStream {
        let deadline = Instant::now() + Duration::from_secs(5);
        let stream = loop {
            if let Ok(stream) = UnixStream::connect(self.root.join("kernel.sock")) {
                break stream;
            }
            assert!(self.child.as_mut().unwrap().try_wait().unwrap().is_none());
            assert!(Instant::now() < deadline, "{message}");
            std::thread::sleep(Duration::from_millis(10));
        };
        stream
    }

    fn start_server(&mut self, pause: Option<(&str, &std::path::Path)>) {
        let mut command = Command::new(env!("CARGO_BIN_EXE_molt-kernel"));
        command
            .args(["serve", "--state"])
            .arg(&self.root)
            .stdout(Stdio::null());
        if let Some((point, marker)) = pause {
            command
                .env("MOLT_KERNEL_PAUSE_AT", point)
                .env("MOLT_KERNEL_PAUSE_MARKER", marker);
        }
        self.child = Some(command.spawn().unwrap());
    }

    fn wait_for_marker(&mut self, marker: &std::path::Path) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !marker.exists() {
            assert!(self.child.as_mut().unwrap().try_wait().unwrap().is_none());
            assert!(Instant::now() < deadline, "fault point was not reached");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let _ = child.wait();
        }
        // Only this test's exclusively created temporary state is removed.
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Client {
    stream: UnixStream,
    id: u64,
}

impl Client {
    fn send_only(&mut self, method: &str, params: Value) {
        self.id += 1;
        let bytes = canonical_json(
            &json!({"jsonrpc":"2.0", "id":self.id, "method":method, "params":params}),
        )
        .unwrap();
        self.stream
            .write_all(&(bytes.len() as u32).to_be_bytes())
            .unwrap();
        self.stream.write_all(&bytes).unwrap();
    }

    fn response(&mut self, method: &str, params: Value) -> Value {
        self.id += 1;
        let bytes = canonical_json(
            &json!({"jsonrpc":"2.0", "id":self.id, "method":method, "params":params}),
        )
        .unwrap();
        self.stream
            .write_all(&(bytes.len() as u32).to_be_bytes())
            .unwrap();
        self.stream.write_all(&bytes).unwrap();
        let mut size = [0; 4];
        self.stream.read_exact(&mut size).unwrap();
        let size = u32::from_be_bytes(size) as usize;
        assert!(size <= 16 * 1024 * 1024);
        let mut bytes = vec![0; size];
        self.stream.read_exact(&mut bytes).unwrap();
        let response: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(response["id"], self.id);
        assert_eq!(response["jsonrpc"], "2.0");
        response
    }

    fn call(&mut self, method: &str, params: Value) -> Value {
        let response = self.response(method, params);
        assert!(response.get("error").is_none(), "{response}");
        response["result"].clone()
    }

    fn put(&mut self, bytes: &[u8]) -> String {
        let result = self.call("object.put", json!({"data":bytes}));
        assert_eq!(result["hash"], sha256(bytes));
        result["hash"].as_str().unwrap().to_owned()
    }

    fn manifest(&mut self, bytes: &[u8]) -> String {
        let hash = self.put(bytes);
        let roots = json!({"workspace":{"file.txt":{
            "type":"file", "mode":420, "size":bytes.len(), "sha256":hash
        }}});
        let tree_hash = sha256(&canonical_json(&roots).unwrap());
        self.put(&canonical_json(&json!({"roots":roots,"tree_hash":tree_hash})).unwrap())
    }

    fn commit(&mut self, id: &str, manifest: &str) -> Value {
        self.call(
            "generation.commit",
            json!({
                "request_id":id, "manifest_hash":manifest, "ts":TS
            }),
        )
    }
}

#[test]
fn disconnected_commit_caller_does_not_stop_server() {
    let mut fixture = Fixture::new();
    let mut caller = fixture.connect();
    caller.call("handshake", json!({"protocol":"molt-kernel/1"}));
    let manifest = caller.manifest(b"disconnected");
    caller.send_only(
        "generation.commit",
        json!({
            "request_id":"discarded-response", "manifest_hash":manifest, "ts":TS
        }),
    );
    // Shut down reads before the server responds to force a failed reply write.
    caller.stream.shutdown(std::net::Shutdown::Both).unwrap();
    drop(caller);
    let stream = fixture.wait_for_connection("reconnection timeout");
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut observer = Client { stream, id: 0 };
    observer.call("handshake", json!({"protocol":"molt-kernel/1"}));
    let status = observer.call("request.status", json!({"request_id":"discarded-response"}));
    assert_eq!(status["status"], "committed");
    assert_eq!(
        observer.commit("discarded-response", &manifest),
        status["result"]
    );
    assert_eq!(observer.call("current", json!({}))["generation"], 1);
    assert!(fixture
        .child
        .as_mut()
        .unwrap()
        .try_wait()
        .unwrap()
        .is_none());
}

#[test]
fn process_ipc_checkpoint_archive_rollback_and_gc() {
    let mut fixture = Fixture::new();
    let mut client = fixture.connect();
    assert_eq!(
        fs::metadata(fixture.root.join("kernel.sock"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    assert_eq!(
        client.response("current", json!({}))["error"]["code"],
        "permission_denied"
    );
    assert_eq!(
        client.response("handshake", json!({"protocol":"wrong"}))["error"]["code"],
        "invalid_request"
    );
    let hello = client.call("handshake", json!({"protocol":"molt-kernel/1"}));
    assert_eq!(hello["protocol"], "molt-kernel/1");
    assert_eq!(client.call("current", json!({})), Value::Null);

    let first_hash = client.manifest(b"first");
    let record = client.call(
        "record.append",
        json!({
            "kind":"test.payload", "ts":TS, "payload_hash":first_hash, "meta":{}
        }),
    );
    assert_eq!(record["kind"], "test.payload");
    let first = client.commit("first", &first_hash);
    assert_eq!(client.commit("first", &first_hash), first);
    assert_eq!(client.call("current", json!({})), first);
    let status = client.call("request.status", json!({"request_id":"first"}));
    assert_eq!(status["status"], "committed");
    assert_eq!(client.call("archive", json!({"id":"good"})), first);

    let checkpoint_path = fixture.root.join("checkpoint.json");
    let checkpoint = fixture.ok(&[
        "checkpoint",
        "export",
        "--out",
        checkpoint_path.to_str().unwrap(),
        "--created-at",
        TS,
    ])["checkpoint"]
        .clone();
    assert_eq!(
        checkpoint,
        client.call("checkpoint.export", json!({"created_at":TS}))
    );
    let checkpoint_bytes = fs::read(&checkpoint_path).unwrap();
    assert!(!fixture
        .command(&[
            "checkpoint",
            "export",
            "--out",
            checkpoint_path.to_str().unwrap(),
            "--created-at",
            TS,
        ])
        .status
        .success());
    assert_eq!(fs::read(&checkpoint_path).unwrap(), checkpoint_bytes);

    let second_hash = client.manifest(b"second");
    client.commit("second", &second_hash);
    assert_eq!(
        client.response(
            "generation.commit",
            json!({
                "request_id":"first", "manifest_hash":second_hash, "ts":TS
            })
        )["error"]["code"],
        "request_conflict"
    );
    let rolled = fixture.ok(&[
        "rollback",
        "--request-id",
        "rollback",
        "--archive",
        "good",
        "--ts",
        TS,
    ])["current"]
        .clone();
    assert_eq!(rolled["manifest_hash"], first_hash);
    assert_ne!(rolled["generation"], first["generation"]);
    assert_eq!(
        client.call(
            "rollback",
            json!({
                "request_id":"rollback", "archive":"good", "ts":TS
            })
        ),
        rolled
    );
    assert_eq!(
        fixture.ok(&["archive", "--id", "after-rollback"])["archive"],
        rolled
    );
    assert_eq!(
        fixture.ok(&["audit", "--checkpoint", checkpoint_path.to_str().unwrap(),])
            ["checkpoint_verified"],
        true
    );
    client.call("audit", json!({}));

    let orphan = client.put(b"unreferenced");
    let before = fixture.ok(&["audit"]);
    let plan = fixture.ok(&["gc", "--dry-run"]);
    assert_eq!(plan["dry_run"], true);
    assert!(plan["delete"].as_array().unwrap().contains(&json!(orphan)));
    assert!(!plan["delete"]
        .as_array()
        .unwrap()
        .contains(&json!(first_hash)));
    assert_eq!(fixture.ok(&["audit"]), before);
    assert_eq!(client.call("gc", json!({"dry_run":true})), plan);
    assert_eq!(client.call("current", json!({})), rolled);
}

#[test]
fn process_ipc_streaming_upload_checks_offsets_hash_and_disconnect_cleanup() {
    let mut fixture = Fixture::new();
    fs::write(fixture.root.join(".upload-crashed-session"), b"partial").unwrap();
    let mut client = fixture.connect();
    client.call("handshake", json!({"protocol":"molt-kernel/1"}));
    assert!(!fixture.root.join(".upload-crashed-session").exists());
    let bytes = vec![0x5a; 2 * 1024 * 1024 + 17];
    let hash = sha256(&bytes);
    let begin = client.call(
        "object.upload.begin",
        json!({"size":bytes.len(),"hash":hash}),
    );
    let id = begin["upload_id"].as_str().unwrap();
    let bad_offset = client.response(
        "object.upload.chunk",
        json!({"upload_id":id,"offset":1,"data":[1]}),
    );
    assert_eq!(bad_offset["error"]["code"], "invalid_request");
    for (index, chunk) in bytes.chunks(1024 * 1024).enumerate() {
        let offset = index * 1024 * 1024;
        let result = client.call(
            "object.upload.chunk",
            json!({"upload_id":id,"offset":offset,"data":chunk}),
        );
        assert_eq!(result["next_offset"], offset + chunk.len());
    }
    assert_eq!(
        client.call("object.upload.finish", json!({"upload_id":id}))["hash"],
        hash
    );

    client.call(
        "object.upload.begin",
        json!({"size":4096,"hash":sha256(&vec![7;4096])}),
    );
    drop(client);
    let stream = UnixStream::connect(fixture.root.join("kernel.sock")).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut client = Client { stream, id: 0 };
    client.call("handshake", json!({"protocol":"molt-kernel/1"}));
    assert!(!fs::read_dir(&fixture.root).unwrap().any(|entry| {
        entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".upload-")
    }));

    let wrong = client.call(
        "object.upload.begin",
        json!({"size":1,"hash":sha256(b"wrong")}),
    );
    let wrong_id = wrong["upload_id"].as_str().unwrap();
    client.call(
        "object.upload.chunk",
        json!({"upload_id":wrong_id,"offset":0,"data":[1]}),
    );
    let finish = client.response("object.upload.finish", json!({"upload_id":wrong_id}));
    assert_eq!(finish["error"]["code"], "integrity_error");
    assert!(!fs::read_dir(&fixture.root).unwrap().any(|entry| entry
        .unwrap()
        .file_name()
        .to_string_lossy()
        .starts_with(".upload-")));
}

#[test]
fn killed_server_preserves_confirmed_request_and_rejects_tampered_object() {
    let mut fixture = Fixture::new();
    let mut client = fixture.connect();
    client.call("handshake", json!({"protocol":"molt-kernel/1"}));
    let manifest = client.manifest(b"durable");
    let committed = client.commit("durable-request", &manifest);
    let checkpoint_path = fixture.root.join("external-checkpoint.json");
    fixture.ok(&[
        "checkpoint",
        "export",
        "--out",
        checkpoint_path.to_str().unwrap(),
        "--created-at",
        TS,
    ]);

    // Child::kill is SIGKILL on Unix: no graceful shutdown can flush state.
    let mut child = fixture.child.take().unwrap();
    child.kill().unwrap();
    assert!(!child.wait().unwrap().success());
    drop(client);
    fixture.ok(&["recover"]);
    fixture.ok(&["recover"]);
    assert_eq!(
        fixture.ok(&["audit", "--checkpoint", checkpoint_path.to_str().unwrap(),])
            ["checkpoint_verified"],
        true
    );

    // A subsequent service start recovers the stale socket after taking the service lock.
    let mut client = fixture.connect();
    client.call("handshake", json!({"protocol":"molt-kernel/1"}));
    assert_eq!(client.call("current", json!({})), committed);
    assert_eq!(client.commit("durable-request", &manifest), committed);
    assert_eq!(
        client.call(
            "request.status",
            json!({
                "request_id":"missing"
            })
        ),
        json!({"status":"not_found", "retry_with_same_request_id":true})
    );

    let hex = sha256(b"durable");
    let hex = hex.strip_prefix("sha256:").unwrap();
    let path = fixture
        .root
        .join("objects/sha256")
        .join(&hex[..2])
        .join(&hex[2..4])
        .join(hex);
    fs::write(path, b"changed").unwrap();
    let output = fixture.command(&["audit", "--checkpoint", checkpoint_path.to_str().unwrap()]);
    assert!(!output.status.success());
    let error: Value = serde_json::from_slice(&output.stderr).unwrap();
    assert_eq!(error["error"]["code"], "integrity_error");
    assert_eq!(
        client.response("audit", json!({}))["error"]["code"],
        "integrity_error"
    );
}

#[cfg(feature = "fault-injection")]
#[test]
fn independent_process_crash_matrix_recovers_old_or_confirmed_generation() {
    for (point, committed) in [
        ("journal.durable", false),
        ("log.fsynced", false),
        ("head.renamed", true),
        ("head.durable", true),
        ("current.renamed", true),
        ("current.durable", true),
    ] {
        let mut fixture = Fixture::new();
        let mut client = fixture.connect();
        client.call("handshake", json!({"protocol":"molt-kernel/1"}));
        let manifest = client.manifest(format!("payload-{point}").as_bytes());
        let child = fixture.child.take().unwrap();
        drop(client);
        let mut child = child;
        child.kill().unwrap();
        let _ = child.wait().unwrap();
        let marker = fixture.root.join("pause.marker");
        fixture.start_server(Some((point, &marker)));
        let mut client = Client {
            stream: fixture.wait_for_connection(&format!("server startup timeout at {point}")),
            id: 0,
        };
        client.call("handshake", json!({"protocol":"molt-kernel/1"}));
        let request = json!({"request_id":"matrix-request","manifest_hash":manifest,"ts":TS});
        client.send_only("generation.commit", request.clone());
        fixture.wait_for_marker(&marker);
        let mut child = fixture.child.take().unwrap();
        child.kill().unwrap();
        let _ = child.wait().unwrap();
        drop(client);
        fixture.ok(&["recover"]);
        fixture.ok(&["recover"]);
        fixture.start_server(None);
        let mut recovered = Client {
            stream: fixture.wait_for_connection("recovery server startup timeout"),
            id: 0,
        };
        recovered.call("handshake", json!({"protocol":"molt-kernel/1"}));
        let current = recovered.call("current", json!({}));
        assert_eq!(current.is_object(), committed, "{point}");
        if committed {
            assert_eq!(current["manifest_hash"], manifest, "{point}");
            assert_eq!(
                recovered.call("generation.commit", request),
                current,
                "{point}"
            );
        }
        fixture.ok(&["audit"]);
        drop(recovered);
    }
}
