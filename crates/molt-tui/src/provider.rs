//! Blocking DeepSeek chat-completions client with SSE streaming.
//!
//! The TUI is synchronous, so this module uses `ureq` (blocking + rustls)
//! instead of an async runtime. Callers run [`stream_chat`] on a worker thread
//! and consume [`ProviderEvent`]s from an `mpsc` channel.
//!
//! Secrets: the API key is never logged, never included in an error string and
//! is redacted from [`ProviderConfig`]'s `Debug` output. Response bodies and
//! the `Authorization` header are not printed either.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::time::Duration;

/// DeepSeek's OpenAI-compatible endpoint root (no `/v1`).
pub const DEFAULT_BASE_URL: &str = "https://api.deepseek.com";
/// Environment variable that holds the API key directly.
pub const API_KEY_ENV: &str = "DEEPSEEK_API_KEY";
/// Environment variable that holds a shell command printing the API key.
pub const API_KEY_HELPER_ENV: &str = "MOLT_API_KEY_HELPER";
/// Upper bound for helper stdout (after trimming), in bytes.
pub const HELPER_OUTPUT_LIMIT: usize = 4096;

const MISSING_KEY_MESSAGE: &str = "set DEEPSEEK_API_KEY or MOLT_API_KEY_HELPER";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);

/// Connection settings for one provider request.
///
/// `api_key` is deliberately hidden from `Debug`; print the struct freely
/// without leaking the secret.
#[derive(Clone, PartialEq, Eq)]
pub struct ProviderConfig {
    pub base_url: String,
    pub model: String,
    pub api_key: String,
}

impl fmt::Debug for ProviderConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProviderConfig")
            .field("base_url", &self.base_url)
            .field("model", &self.model)
            .field("api_key", &"***")
            .finish()
    }
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            base_url: DEFAULT_BASE_URL.to_string(),
            model: "deepseek-chat".to_string(),
            api_key: String::new(),
        }
    }
}

/// One chat turn, shared with [`crate::session`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

/// Token accounting from the final SSE chunk.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Usage {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub total_tokens: Option<u64>,
}

/// Events produced while a stream is consumed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProviderEvent {
    /// Answer text (`delta.content`).
    Delta(String),
    /// Chain-of-thought text (`delta.reasoning_content`) when the model sends it.
    Reasoning(String),
    /// Token usage, emitted when the stream carries a `usage` object.
    Usage(Option<Usage>),
    /// The stream completed (`data: [DONE]` or clean EOF).
    Done,
    /// Transport / HTTP / parse failure, or `"cancelled"`.
    Error(String),
}

/// Resolve the API key from `DEEPSEEK_API_KEY`, then from the helper command.
///
/// The helper runs through `sh -c`, its stdout is trimmed and limited to
/// [`HELPER_OUTPUT_LIMIT`] bytes. Error messages never contain the key, the
/// helper's stdout or its stderr.
pub fn resolve_api_key() -> Result<String, String> {
    if let Some(key) = env_value(API_KEY_ENV) {
        return Ok(key);
    }

    let helper = match std::env::var(API_KEY_HELPER_ENV) {
        Ok(value) if !value.trim().is_empty() => value,
        _ => return Err(MISSING_KEY_MESSAGE.to_string()),
    };

    let output = Command::new("sh")
        .arg("-c")
        .arg(&helper)
        .output()
        .map_err(|error| format!("MOLT_API_KEY_HELPER could not be executed: {error}"))?;

    if !output.status.success() {
        let status = match output.status.code() {
            Some(code) => format!("exit status {code}"),
            None => "a signal".to_string(),
        };
        return Err(format!(
            "MOLT_API_KEY_HELPER failed ({status}); its output was discarded"
        ));
    }

    // Trim before measuring so a trailing newline does not trip the limit.
    let stdout = String::from_utf8_lossy(&output.stdout);
    let key = stdout.trim();
    if key.is_empty() {
        return Err("MOLT_API_KEY_HELPER produced an empty key".to_string());
    }
    if key.len() > HELPER_OUTPUT_LIMIT {
        return Err(format!(
            "MOLT_API_KEY_HELPER output exceeds {HELPER_OUTPUT_LIMIT} bytes"
        ));
    }
    Ok(key.to_string())
}

fn env_value(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

/// Strip a trailing slash and one optional OpenAI-style `/v1` suffix.
///
/// An empty value falls back to [`DEFAULT_BASE_URL`]. This keeps
/// `https://api.deepseek.com`, `https://api.deepseek.com/` and
/// `https://api.deepseek.com/v1` all pointing at the same endpoint.
pub fn normalize_base_url(base_url: &str) -> String {
    let trimmed = base_url.trim().trim_end_matches('/');
    let normalized = trimmed.strip_suffix("/v1").unwrap_or(trimmed);
    if normalized.is_empty() {
        DEFAULT_BASE_URL.to_string()
    } else {
        normalized.to_string()
    }
}

/// Full URL of the streaming endpoint for `base_url`.
pub fn chat_completions_url(base_url: &str) -> String {
    format!("{}/chat/completions", normalize_base_url(base_url))
}

/// POST a streaming chat completion and forward SSE events to `tx`.
///
/// Blocking: run it on a worker thread. The stream is checked for cancellation
/// before every read; once `cancel` is true the function stops reading and
/// sends [`ProviderEvent::Error`]`("cancelled")`, then returns `Ok(())`.
///
/// Transport, HTTP and I/O failures send [`ProviderEvent::Error`] and also
/// return `Err(message)` so the caller can log the outcome. Malformed SSE
/// lines are skipped without panicking.
pub fn stream_chat(
    config: &ProviderConfig,
    messages: &[ChatMessage],
    cancel: Arc<AtomicBool>,
    tx: Sender<ProviderEvent>,
) -> Result<(), String> {
    if cancel.load(Ordering::Relaxed) {
        let _ = tx.send(ProviderEvent::Error("cancelled".to_string()));
        return Ok(());
    }
    if config.api_key.trim().is_empty() {
        return Err(fail(
            &tx,
            "missing API key: set DEEPSEEK_API_KEY or MOLT_API_KEY_HELPER".to_string(),
        ));
    }
    if config.model.trim().is_empty() {
        return Err(fail(&tx, "missing model".to_string()));
    }

    let url = chat_completions_url(&config.base_url);
    let messages: Vec<_> = messages
        .iter()
        .map(|message| {
            serde_json::json!({
                "role": message.role,
                "content": message.content,
            })
        })
        .collect();
    let body = serde_json::json!({
        "model": config.model,
        "messages": messages,
        "stream": true,
        "stream_options": { "include_usage": true },
    });
    let body = match serde_json::to_string(&body) {
        Ok(body) => body,
        Err(error) => return Err(fail(&tx, format!("failed to encode request: {error}"))),
    };

    let agent = ureq::Agent::config_builder()
        .timeout_connect(Some(CONNECT_TIMEOUT))
        .build()
        .new_agent();

    let mut response = match agent
        .post(url.as_str())
        .header("Authorization", format!("Bearer {}", config.api_key.trim()))
        .header("Content-Type", "application/json")
        .header("Accept", "text/event-stream")
        .send(body.as_str())
    {
        Ok(response) => response,
        Err(error) => {
            return Err(fail(
                &tx,
                format!("chat completion request to {url} failed: {error}"),
            ))
        }
    };

    let reader = BufReader::new(response.body_mut().as_reader());
    run_sse(reader, &cancel, &tx)
}

fn fail(tx: &Sender<ProviderEvent>, message: String) -> String {
    let _ = tx.send(ProviderEvent::Error(message.clone()));
    message
}

fn emit(tx: &Sender<ProviderEvent>, event: ProviderEvent) -> bool {
    tx.send(event).is_ok()
}

enum LineOutcome {
    Continue,
    Done,
    ReceiverClosed,
}

/// Read SSE lines until `[DONE]`, EOF, cancellation or a read error.
fn run_sse<R: BufRead>(
    mut reader: R,
    cancel: &AtomicBool,
    tx: &Sender<ProviderEvent>,
) -> Result<(), String> {
    let mut line = Vec::new();
    loop {
        if cancel.load(Ordering::Relaxed) {
            let _ = tx.send(ProviderEvent::Error("cancelled".to_string()));
            return Ok(());
        }

        line.clear();
        match reader.read_until(b'\n', &mut line) {
            Ok(0) => {
                // A clean close without `[DONE]` is a truncated answer, not a
                // successful completion; surface it instead of pretending the
                // stream finished.
                let message = "stream ended without [DONE]".to_string();
                return Err(fail(tx, message));
            }
            Ok(_) => {}
            Err(error) => return Err(fail(tx, format!("failed to read stream: {error}"))),
        }

        // SSE is UTF-8 by spec. A corrupted line is skipped instead of being
        // decoded lossily, so no replacement characters reach the UI.
        let Ok(text) = std::str::from_utf8(&line) else {
            continue;
        };
        match handle_line(text, tx) {
            LineOutcome::Continue => {}
            LineOutcome::Done => return Ok(()),
            LineOutcome::ReceiverClosed => return Ok(()),
        }
    }
}

fn handle_line(raw: &str, tx: &Sender<ProviderEvent>) -> LineOutcome {
    let line = raw.trim_end_matches(['\r', '\n']);
    if line.is_empty() || line.starts_with(':') {
        return LineOutcome::Continue;
    }
    let data = match line.strip_prefix("data:") {
        Some(data) => data.trim(),
        None => return LineOutcome::Continue,
    };
    if data.is_empty() {
        return LineOutcome::Continue;
    }
    if data == "[DONE]" {
        return if emit(tx, ProviderEvent::Done) {
            LineOutcome::Done
        } else {
            LineOutcome::ReceiverClosed
        };
    }

    let chunk: StreamChunk = match serde_json::from_str(data) {
        Ok(chunk) => chunk,
        Err(_) => return LineOutcome::Continue,
    };

    for choice in chunk.choices.unwrap_or_default() {
        let delta = choice.delta.unwrap_or_default();
        if let Some(reasoning) = delta.reasoning_content.filter(|text| !text.is_empty()) {
            if !emit(tx, ProviderEvent::Reasoning(reasoning)) {
                return LineOutcome::ReceiverClosed;
            }
        }
        if let Some(content) = delta.content.filter(|text| !text.is_empty()) {
            if !emit(tx, ProviderEvent::Delta(content)) {
                return LineOutcome::ReceiverClosed;
            }
        }
    }

    if let Some(usage) = chunk.usage {
        let usage = Usage {
            input_tokens: usage.input_tokens,
            output_tokens: usage.output_tokens,
            total_tokens: usage.total_tokens,
        };
        if !emit(tx, ProviderEvent::Usage(Some(usage))) {
            return LineOutcome::ReceiverClosed;
        }
    }

    LineOutcome::Continue
}

#[derive(Deserialize)]
struct StreamChunk {
    #[serde(default)]
    choices: Option<Vec<StreamChoice>>,
    #[serde(default)]
    usage: Option<RawUsage>,
}

#[derive(Deserialize)]
struct StreamChoice {
    #[serde(default)]
    delta: Option<StreamDelta>,
}

#[derive(Default, Deserialize)]
struct StreamDelta {
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    reasoning_content: Option<String>,
}

#[derive(Deserialize)]
struct RawUsage {
    #[serde(default)]
    input_tokens: Option<u64>,
    #[serde(default)]
    output_tokens: Option<u64>,
    #[serde(default)]
    total_tokens: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Read, Write};
    use std::net::TcpListener;
    use std::str;
    use std::sync::mpsc;
    use std::sync::Mutex;
    use std::thread;

    /// Environment variables are process-global, so key-resolution tests run
    /// one at a time.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Prefix of a real API key, split so secret scanners do not flag the
    /// leak assertions below.
    const KEY_PREFIX: &str = concat!("s", "k-");

    fn clear_key_env() {
        std::env::remove_var(API_KEY_ENV);
        std::env::remove_var(API_KEY_HELPER_ENV);
    }

    fn collect(reader: Cursor<Vec<u8>>, cancel: Arc<AtomicBool>) -> Vec<ProviderEvent> {
        let (tx, rx) = mpsc::channel();
        run_sse(reader, &cancel, &tx).expect("sse loop");
        drop(tx);
        rx.iter().collect()
    }

    fn pump(input: &str) -> Vec<ProviderEvent> {
        collect(
            Cursor::new(input.as_bytes().to_vec()),
            Arc::new(AtomicBool::new(false)),
        )
    }

    #[test]
    fn resolve_api_key_prefers_the_environment() {
        let _guard = ENV_LOCK.lock().unwrap();
        clear_key_env();
        std::env::set_var(API_KEY_ENV, "  test-env-key  ");
        std::env::set_var(API_KEY_HELPER_ENV, "printf 'test-helper-key'");

        assert_eq!(resolve_api_key().unwrap(), "test-env-key");

        clear_key_env();
    }

    #[test]
    fn resolve_api_key_falls_back_to_the_helper() {
        let _guard = ENV_LOCK.lock().unwrap();
        clear_key_env();
        std::env::set_var(API_KEY_ENV, "   ");
        std::env::set_var(API_KEY_HELPER_ENV, "printf '  test-helper-key\\n'");

        assert_eq!(resolve_api_key().unwrap(), "test-helper-key");

        clear_key_env();
    }

    #[test]
    fn resolve_api_key_reports_the_missing_configuration() {
        let _guard = ENV_LOCK.lock().unwrap();
        clear_key_env();

        let error = resolve_api_key().expect_err("no key configured");
        assert_eq!(error, MISSING_KEY_MESSAGE);
    }

    #[test]
    fn resolve_api_key_helper_failure_is_readable_and_leaks_nothing() {
        let _guard = ENV_LOCK.lock().unwrap();
        clear_key_env();
        std::env::set_var(
            API_KEY_HELPER_ENV,
            "printf 'test-super-secret\\n' >&2; exit 7",
        );

        let error = resolve_api_key().expect_err("helper must fail");
        assert!(error.contains("exit status 7"), "{error}");
        assert!(!error.contains("test-super-secret"), "{error}");
        assert!(!error.contains(KEY_PREFIX), "{error}");

        clear_key_env();
    }

    #[test]
    fn resolve_api_key_rejects_empty_and_oversized_helper_output() {
        let _guard = ENV_LOCK.lock().unwrap();
        clear_key_env();
        std::env::set_var(API_KEY_HELPER_ENV, "true");
        let error = resolve_api_key().expect_err("empty output");
        assert!(error.contains("empty"), "{error}");

        std::env::set_var(API_KEY_HELPER_ENV, "printf '%05000d' 0");
        let error = resolve_api_key().expect_err("oversized output");
        assert!(error.contains("4096"), "{error}");

        clear_key_env();
    }

    #[test]
    fn base_url_normalization_strips_one_v1_suffix() {
        assert_eq!(
            normalize_base_url("https://api.deepseek.com"),
            "https://api.deepseek.com"
        );
        assert_eq!(
            normalize_base_url("https://api.deepseek.com/"),
            "https://api.deepseek.com"
        );
        assert_eq!(
            normalize_base_url(" https://api.deepseek.com/v1 "),
            "https://api.deepseek.com"
        );
        assert_eq!(
            normalize_base_url("https://api.deepseek.com/v1/"),
            "https://api.deepseek.com"
        );
        assert_eq!(normalize_base_url(""), DEFAULT_BASE_URL);
        assert_eq!(
            chat_completions_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/chat/completions"
        );
    }

    #[test]
    fn fixture_stream_emits_deltas_usage_and_done_in_order() {
        let fixture = concat!(
            "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\",\"content\":\"\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n",
            "data: {\"choices\":[],\"usage\":{\"input_tokens\":3,\"output_tokens\":2,\"total_tokens\":5}}\n\n",
            "data: [DONE]\n\n",
        );

        assert_eq!(
            pump(fixture),
            vec![
                ProviderEvent::Delta("Hel".to_string()),
                ProviderEvent::Delta("lo".to_string()),
                ProviderEvent::Usage(Some(Usage {
                    input_tokens: Some(3),
                    output_tokens: Some(2),
                    total_tokens: Some(5),
                })),
                ProviderEvent::Done,
            ]
        );
    }

    #[test]
    fn reasoning_content_is_preserved_as_a_separate_event() {
        let fixture = concat!(
            "data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"think\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"answer\"}}]}\n\n",
            "data: [DONE]\n\n",
        );

        assert_eq!(
            pump(fixture),
            vec![
                ProviderEvent::Reasoning("think".to_string()),
                ProviderEvent::Delta("answer".to_string()),
                ProviderEvent::Done,
            ]
        );
    }

    #[test]
    fn malformed_lines_and_unknown_events_do_not_panic() {
        let fixture = concat!(
            "\n",
            ": keep-alive comment\n",
            "event: ping\n",
            "data: not json\n",
            "data: {\"choices\":[{\"delta\":{\"content\":null}}]}\n",
            "data: {\"choices\":null,\"usage\":null}\n",
            "id: 42\n",
            "data: [DONE]\n",
        );

        assert_eq!(pump(fixture), vec![ProviderEvent::Done]);
    }

    #[test]
    fn invalid_utf8_in_a_data_line_is_skipped() {
        let mut bytes = b"data: {\"choices\":[{\"delta\":{\"content\":\"".to_vec();
        bytes.push(0xff);
        bytes.extend_from_slice(b"\"}}]}\n\ndata: [DONE]\n\n");

        assert_eq!(
            collect(Cursor::new(bytes), Arc::new(AtomicBool::new(false))),
            vec![ProviderEvent::Done]
        );
    }

    #[test]
    fn cancellation_before_the_first_read_stops_the_stream() {
        let fixture = concat!(
            "data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n",
            "data: [DONE]\n\n",
        );

        assert_eq!(
            collect(
                Cursor::new(fixture.as_bytes().to_vec()),
                Arc::new(AtomicBool::new(true))
            ),
            vec![ProviderEvent::Error("cancelled".to_string())]
        );
    }

    #[test]
    fn a_stream_without_done_is_reported_as_truncated() {
        let fixture = "data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n";
        let (tx, rx) = mpsc::channel();
        let result = run_sse(
            Cursor::new(fixture.as_bytes().to_vec()),
            &AtomicBool::new(false),
            &tx,
        );
        assert!(result.is_err(), "missing [DONE] must not look successful");
        drop(tx);

        let events: Vec<_> = rx.iter().collect();
        assert_eq!(events.len(), 2, "{events:?}");
        assert_eq!(events[0], ProviderEvent::Delta("partial".to_string()));
        assert!(
            matches!(&events[1], ProviderEvent::Error(message) if message.contains("[DONE]")),
            "{events:?}"
        );
    }

    #[test]
    fn local_provider_streams_deltas_and_sends_a_well_formed_request() {
        let response_body = concat!(
            "data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n",
            "data: [DONE]\n\n",
        );
        let (base_url, server) = fake_server("200 OK", response_body);

        let config = ProviderConfig {
            base_url,
            model: "deepseek-chat".to_string(),
            api_key: "test-key".to_string(),
        };
        let messages = [ChatMessage {
            role: "user".to_string(),
            content: "say hello".to_string(),
        }];

        let (tx, rx) = mpsc::channel();
        stream_chat(&config, &messages, Arc::new(AtomicBool::new(false)), tx)
            .expect("stream should succeed");
        let events: Vec<_> = rx.iter().collect();

        assert_eq!(
            events,
            vec![
                ProviderEvent::Delta("Hel".to_string()),
                ProviderEvent::Delta("lo".to_string()),
                ProviderEvent::Done,
            ]
        );

        let request = server.join().expect("server thread");
        let lower = request.to_ascii_lowercase();
        assert!(
            lower.starts_with("post /chat/completions http/1.1"),
            "{request}"
        );
        assert!(
            lower.contains("authorization: bearer test-key"),
            "{request}"
        );
        assert!(request.contains("\"stream\":true"), "{request}");
        assert!(request.contains("\"include_usage\":true"), "{request}");
        assert!(request.contains("\"model\":\"deepseek-chat\""), "{request}");
        assert!(request.contains("\"role\":\"user\""), "{request}");
        assert!(request.contains("\"content\":\"say hello\""), "{request}");
    }

    #[test]
    fn local_provider_http_error_becomes_an_event() {
        let (base_url, server) = fake_server("401 Unauthorized", "{\"error\":\"bad key\"}");

        let config = ProviderConfig {
            base_url,
            model: "deepseek-chat".to_string(),
            api_key: "wrong".to_string(),
        };
        let (tx, rx) = mpsc::channel();
        let result = stream_chat(&config, &[], Arc::new(AtomicBool::new(false)), tx);
        let error = result.expect_err("401 must fail");
        assert!(error.contains("401"), "{error}");

        let events: Vec<_> = rx.iter().collect();
        assert!(
            matches!(events.as_slice(), [ProviderEvent::Error(_)]),
            "{events:?}"
        );

        server.join().expect("server thread");
    }

    #[test]
    fn provider_config_debug_redacts_the_key() {
        let config = ProviderConfig {
            base_url: DEFAULT_BASE_URL.to_string(),
            model: "deepseek-chat".to_string(),
            api_key: "test-must-not-appear".to_string(),
        };
        let debug = format!("{config:?}");
        assert!(!debug.contains("test-must-not-appear"), "{debug}");
        assert!(debug.contains("***"), "{debug}");
    }

    /// Bind a one-shot HTTP server on 127.0.0.1, return its base URL and a
    /// handle that yields the raw request bytes after the response is sent.
    fn fake_server(
        status: &'static str,
        body: &'static str,
    ) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let address = listener.local_addr().expect("local address");
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            let header_end = loop {
                let read = stream.read(&mut buffer).expect("read request");
                if read == 0 {
                    break request.len();
                }
                request.extend_from_slice(&buffer[..read]);
                if let Some(position) = request.windows(4).position(|window| window == b"\r\n\r\n")
                {
                    break position + 4;
                }
            };

            let headers = str::from_utf8(&request[..header_end]).unwrap_or("");
            let content_length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    if name.eq_ignore_ascii_case("content-length") {
                        value.trim().parse::<usize>().ok()
                    } else {
                        None
                    }
                })
                .unwrap_or(0);
            while request.len() < header_end + content_length {
                let read = stream.read(&mut buffer).expect("read body");
                if read == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..read]);
            }

            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n{body}"
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
            stream.flush().expect("flush response");

            str::from_utf8(&request).expect("utf-8 request").to_string()
        });
        (format!("http://{address}"), handle)
    }
}
