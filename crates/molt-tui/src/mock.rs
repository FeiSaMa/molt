//! Deterministic mock state for the v3 TUI shell.
//!
//! Nothing here talks to the kernel, a model, or the network. Data is embedded
//! so `--selftest` and the render tests run without files or daemons.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionKind {
    Root,
    Fork,
    AutoFork,
}

impl SessionKind {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Root => "root",
            Self::Fork => "fork",
            Self::AutoFork => "auto",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    User,
    Assistant,
    Tool,
    Compaction,
    Fork,
}

impl NodeKind {
    pub const fn label(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Assistant => "assistant",
            Self::Tool => "tool",
            Self::Compaction => "compaction",
            Self::Fork => "fork",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Importance {
    Normal,
    High,
}

impl Importance {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::High => "high",
        }
    }
}

/// One immutable history node. `checkpoint` marks a restorable file snapshot;
/// `backup_ts` is the mock timestamp directory under the backup root.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    pub kind: NodeKind,
    pub summary: String,
    pub importance: Importance,
    pub role: String,
    pub time: String,
    pub model: String,
    pub tokens: u32,
    pub checkpoint: bool,
    pub checkpoint_files: u32,
    pub backup_ts: Option<String>,
    pub config: String,
    /// Structural parent inside the session; `None` for the first node.
    #[serde(default)]
    pub parent_id: Option<String>,
    /// Source node id when this node is a fork boundary (points into the
    /// parent session); `None` for ordinary nodes.
    #[serde(default)]
    pub origin_node_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub kind: SessionKind,
    pub parent: Option<String>,
    /// Where the session cursor currently points.
    pub leaf: String,
    pub nodes: Vec<Node>,
}

impl Session {
    pub fn high_importance_nodes(&self) -> usize {
        self.nodes
            .iter()
            .filter(|node| node.importance == Importance::High)
            .count()
    }

    pub fn node(&self, id: &str) -> Option<&Node> {
        self.nodes.iter().find(|node| node.id == id)
    }

    /// Index of the leaf node, falling back to the last node.
    pub fn leaf_index(&self) -> usize {
        self.nodes
            .iter()
            .position(|node| node.id == self.leaf)
            .unwrap_or_else(|| self.nodes.len().saturating_sub(1))
    }

    /// Compaction nodes after `target` would be discarded by a rollback.
    pub fn compaction_after(&self, target: usize) -> bool {
        self.nodes
            .iter()
            .enumerate()
            .any(|(index, node)| index > target && node.kind == NodeKind::Compaction)
    }

    /// Nearest checkpoint at or before `target`.
    pub fn checkpoint_at_or_before(&self, target: usize) -> Option<&Node> {
        self.nodes[..=target.min(self.nodes.len().saturating_sub(1))]
            .iter()
            .rev()
            .find(|node| node.checkpoint)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Config {
    pub id: String,
    pub model: String,
    pub provider: String,
    pub thinking: String,
    pub tools: String,
    pub mcp: String,
    pub permissions: String,
    pub prompt: String,
    /// DAG parents; empty for the root config.
    pub parents: Vec<String>,
    /// Mock scenario version used for compatibility gating.
    pub scenario_version: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum McpStatus {
    Connected,
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpServer {
    pub name: String,
    pub status: McpStatus,
    pub message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Todo {
    pub title: String,
    pub done: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    User,
    Assistant,
    ToolCall,
    ToolResult,
}

impl MessageRole {
    pub const fn label(self) -> &'static str {
        match self {
            Self::User => "YOU",
            Self::Assistant => "MOLT",
            Self::ToolCall => "TOOL.CALL",
            Self::ToolResult => "TOOL.RESULT",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Message {
    pub role: MessageRole,
    pub body: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalState {
    Pending,
    Approved,
    Rejected,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Approval {
    pub id: String,
    pub action: String,
    pub kind: String,
    pub resource: String,
    pub command: String,
    pub cwd: String,
    pub tier: String,
    pub sandbox: String,
    pub risk: String,
    pub diff_preview: Option<String>,
    pub reason: String,
    pub options: Vec<String>,
    pub state: ApprovalState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiffKind {
    Context,
    Add,
    Del,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiffLine {
    pub kind: DiffKind,
    pub old: Option<u32>,
    pub new: Option<u32>,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Hunk {
    pub header: String,
    pub lines: Vec<DiffLine>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiffFile {
    pub path: String,
    pub hunks: Vec<Hunk>,
}

/// Placeholder for a file too large to inline.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LargeFile {
    pub path: String,
    pub bytes: u64,
}

impl LargeFile {
    pub fn human_size(&self) -> String {
        format!("{:.1} MiB", self.bytes as f64 / (1024.0 * 1024.0))
    }
}

/// Everything the mock shell renders.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MockData {
    pub sessions: Vec<Session>,
    pub configs: Vec<Config>,
    pub active_config: String,
    pub scenario_version: u32,
    pub mcp: Vec<McpServer>,
    pub todos: Vec<Todo>,
    pub messages: Vec<Message>,
    pub approvals: Vec<Approval>,
    pub diffs: Vec<DiffFile>,
    pub large_file: LargeFile,
}

impl MockData {
    /// The fixed demo state shared by the TUI, `--selftest`, and tests.
    pub fn demo() -> Self {
        Self {
            sessions: vec![
                Session {
                    id: "demo-6".to_owned(),
                    kind: SessionKind::Root,
                    parent: None,
                    leaf: "n5".to_owned(),
                    nodes: vec![
                        Node {
                            id: "n1".to_owned(),
                            kind: NodeKind::User,
                            summary: "为 TUI 增加 mock shell".to_owned(),
                            importance: Importance::Normal,
                            role: "user".to_owned(),
                            time: "08:40".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 120,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "base".to_owned(),
                            parent_id: None,
                            origin_node_id: None,
                        },
                        Node {
                            id: "n2".to_owned(),
                            kind: NodeKind::Assistant,
                            summary: "已拆出 crate，先渲染静态帧。".to_owned(),
                            importance: Importance::Normal,
                            role: "assistant".to_owned(),
                            time: "08:41".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 480,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "base".to_owned(),
                            parent_id: Some("n1".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n3".to_owned(),
                            kind: NodeKind::Tool,
                            summary: "write crates/molt-tui/src/app.rs".to_owned(),
                            importance: Importance::Normal,
                            role: "tool".to_owned(),
                            time: "08:42".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 210,
                            checkpoint: true,
                            checkpoint_files: 3,
                            backup_ts: Some("20261121T0842Z".to_owned()),
                            config: "base".to_owned(),
                            parent_id: Some("n2".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n4".to_owned(),
                            kind: NodeKind::Assistant,
                            summary: "布局骨架完成，等待编译。".to_owned(),
                            importance: Importance::High,
                            role: "assistant".to_owned(),
                            time: "08:44".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 920,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "low/full".to_owned(),
                            parent_id: Some("n3".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n5".to_owned(),
                            kind: NodeKind::Compaction,
                            summary: "compacted 12 messages into 4 nodes".to_owned(),
                            importance: Importance::Normal,
                            role: "system".to_owned(),
                            time: "08:46".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 64,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "low/full".to_owned(),
                            parent_id: Some("n4".to_owned()),
                            origin_node_id: None,
                        },
                    ],
                },
                Session {
                    id: "feat/tui".to_owned(),
                    kind: SessionKind::Fork,
                    parent: Some("demo-6".to_owned()),
                    leaf: "n4".to_owned(),
                    nodes: vec![
                        Node {
                            id: "n1".to_owned(),
                            kind: NodeKind::Fork,
                            summary: "fork from demo-6 @ n4".to_owned(),
                            importance: Importance::Normal,
                            role: "system".to_owned(),
                            time: "08:47".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 32,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "base".to_owned(),
                            parent_id: None,
                            origin_node_id: Some("n4".to_owned()),
                        },
                        Node {
                            id: "n2".to_owned(),
                            kind: NodeKind::User,
                            summary: "切到 feat/tui 分支".to_owned(),
                            importance: Importance::Normal,
                            role: "user".to_owned(),
                            time: "08:48".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 96,
                            checkpoint: true,
                            checkpoint_files: 2,
                            backup_ts: Some("20261121T0848Z".to_owned()),
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n1".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n3".to_owned(),
                            kind: NodeKind::Assistant,
                            summary: "准备 120x40 与 80x24 两档帧。".to_owned(),
                            importance: Importance::Normal,
                            role: "assistant".to_owned(),
                            time: "08:49".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 640,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n2".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n4".to_owned(),
                            kind: NodeKind::Tool,
                            summary: "cargo test -p molt-tui".to_owned(),
                            importance: Importance::Normal,
                            role: "tool".to_owned(),
                            time: "08:50".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 180,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n3".to_owned()),
                            origin_node_id: None,
                        },
                    ],
                },
                Session {
                    id: "auto/config-B".to_owned(),
                    kind: SessionKind::AutoFork,
                    parent: Some("demo-6".to_owned()),
                    leaf: "n4".to_owned(),
                    nodes: vec![
                        Node {
                            id: "n1".to_owned(),
                            kind: NodeKind::Fork,
                            summary: "auto fork from demo-6".to_owned(),
                            importance: Importance::Normal,
                            role: "system".to_owned(),
                            time: "08:51".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 32,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "base".to_owned(),
                            parent_id: None,
                            origin_node_id: Some("n4".to_owned()),
                        },
                        Node {
                            id: "n2".to_owned(),
                            kind: NodeKind::User,
                            summary: "对比 fast-edit 配置".to_owned(),
                            importance: Importance::Normal,
                            role: "user".to_owned(),
                            time: "08:52".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 88,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n1".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n3".to_owned(),
                            kind: NodeKind::Assistant,
                            summary: "模型保持 deepseek-flash。".to_owned(),
                            importance: Importance::Normal,
                            role: "assistant".to_owned(),
                            time: "08:53".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 520,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n2".to_owned()),
                            origin_node_id: None,
                        },
                        Node {
                            id: "n4".to_owned(),
                            kind: NodeKind::Compaction,
                            summary: "context 42% -> 18%".to_owned(),
                            importance: Importance::Normal,
                            role: "system".to_owned(),
                            time: "08:54".to_owned(),
                            model: "deepseek-flash".to_owned(),
                            tokens: 64,
                            checkpoint: false,
                            checkpoint_files: 0,
                            backup_ts: None,
                            config: "fast-edit".to_owned(),
                            parent_id: Some("n3".to_owned()),
                            origin_node_id: None,
                        },
                    ],
                },
            ],
            configs: vec![
                Config {
                    id: "base".to_owned(),
                    model: "deepseek-flash".to_owned(),
                    provider: "deepseek".to_owned(),
                    thinking: "high".to_owned(),
                    tools: "read,grep,edit".to_owned(),
                    mcp: "github,context7".to_owned(),
                    permissions: "ask".to_owned(),
                    prompt: "You are molt. Keep the kernel boundary; explain before writing."
                        .to_owned(),
                    parents: Vec::new(),
                    scenario_version: 2,
                },
                Config {
                    id: "low/full".to_owned(),
                    model: "deepseek-flash".to_owned(),
                    provider: "deepseek".to_owned(),
                    thinking: "low".to_owned(),
                    tools: "read,grep,edit,shell".to_owned(),
                    mcp: "github,context7,playwright".to_owned(),
                    permissions: "ask".to_owned(),
                    prompt: "Low thinking, full tool surface. Use shell only for tests."
                        .to_owned(),
                    parents: vec!["base".to_owned()],
                    scenario_version: 2,
                },
                Config {
                    id: "fast-edit".to_owned(),
                    model: "deepseek-flash".to_owned(),
                    provider: "deepseek".to_owned(),
                    thinking: "low".to_owned(),
                    tools: "read,edit".to_owned(),
                    mcp: "github".to_owned(),
                    permissions: "allow-edit".to_owned(),
                    prompt: "Edit-first config for tight loops. No shell by default.".to_owned(),
                    parents: vec!["base".to_owned()],
                    scenario_version: 2,
                },
                Config {
                    id: "merge".to_owned(),
                    model: "deepseek-flash".to_owned(),
                    provider: "deepseek".to_owned(),
                    thinking: "high".to_owned(),
                    tools: "read,grep,edit,shell".to_owned(),
                    mcp: "github,context7,playwright".to_owned(),
                    permissions: "ask".to_owned(),
                    prompt: "Merge of low/full and fast-edit. Kept from an older scenario."
                        .to_owned(),
                    parents: vec!["low/full".to_owned(), "fast-edit".to_owned()],
                    scenario_version: 1,
                },
            ],
            active_config: "fast-edit".to_owned(),
            scenario_version: 2,
            mcp: vec![
                McpServer {
                    name: "github".to_owned(),
                    status: McpStatus::Connected,
                    message: None,
                },
                McpServer {
                    name: "context7".to_owned(),
                    status: McpStatus::Connected,
                    message: None,
                },
                McpServer {
                    name: "playwright".to_owned(),
                    status: McpStatus::Error,
                    message: Some("spawn failed".to_owned()),
                },
            ],
            todos: vec![
                Todo {
                    title: "核对 CJK 宽度".to_owned(),
                    done: false,
                },
                Todo {
                    title: "补整帧渲染测试".to_owned(),
                    done: true,
                },
                Todo {
                    title: "接 W-08 logo".to_owned(),
                    done: false,
                },
            ],
            messages: vec![
                Message {
                    role: MessageRole::User,
                    body: "先做一个能跑的 mock shell，别接 kernel。".to_owned(),
                },
                Message {
                    role: MessageRole::Assistant,
                    body: "好。布局固定为 header / sidebar / main / composer / footer，先渲染 120x40 与 80x24。".to_owned(),
                },
                Message {
                    role: MessageRole::ToolCall,
                    body: "shell · cargo test -p molt-tui".to_owned(),
                },
                Message {
                    role: MessageRole::ToolResult,
                    body: "ok · 4 passed; 0 failed".to_owned(),
                },
                Message {
                    role: MessageRole::Assistant,
                    body: "mock 帧已通过，CJK 宽度用 unicode-width 截断。".to_owned(),
                },
            ],
            approvals: vec![Approval {
                id: "apr-1".to_owned(),
                action: "proc.exec".to_owned(),
                kind: "proc".to_owned(),
                resource: "cmd:cargo test -p molt-kernel".to_owned(),
                command: "cargo test -p molt-kernel".to_owned(),
                cwd: "/home/kevin/projects/molt-v3".to_owned(),
                tier: "T2".to_owned(),
                sandbox: "bwrap (net off)".to_owned(),
                risk: "会写入 target/（工作区内）".to_owned(),
                diff_preview: Some(
                    "+ target/debug/deps/molt_kernel.rmeta\n+ target/debug/.fingerprint/molt-kernel"
                        .to_owned(),
                ),
                reason: "workspace test run requested by the agent".to_owned(),
                options: vec!["once".to_owned(), "always".to_owned(), "reject".to_owned()],
                state: ApprovalState::Pending,
            }],
            diffs: demo_diffs(),
            large_file: LargeFile {
                path: "assets/generated/registry.bin".to_owned(),
                bytes: 2_516_582,
            },
        }
    }

    pub fn session(&self, id: &str) -> Option<&Session> {
        self.sessions.iter().find(|session| session.id == id)
    }

    pub fn config(&self, id: &str) -> Option<&Config> {
        self.configs.iter().find(|config| config.id == id)
    }

    pub fn active_config(&self) -> Option<&Config> {
        self.config(&self.active_config)
    }

    pub fn pending_approvals(&self) -> impl Iterator<Item = &Approval> {
        self.approvals
            .iter()
            .filter(|approval| approval.state == ApprovalState::Pending)
    }

    pub fn hunk_count(&self) -> usize {
        self.diffs.iter().map(|file| file.hunks.len()).sum()
    }

    /// Flattened `(file, hunk)` indexes in screen order.
    pub fn hunk_indexes(&self) -> Vec<(usize, usize)> {
        self.diffs
            .iter()
            .enumerate()
            .flat_map(|(file, diff)| (0..diff.hunks.len()).map(move |hunk| (file, hunk)))
            .collect()
    }

    /// Serialize the mock state. Used by `--selftest` as a cheap schema check.
    pub fn to_json(&self) -> serde_json::Result<String> {
        serde_json::to_string_pretty(self)
    }

    /// Parse mock state that was produced by [`MockData::to_json`].
    pub fn from_json(json: &str) -> serde_json::Result<Self> {
        serde_json::from_str(json)
    }
}

fn diff_line(kind: DiffKind, old: Option<u32>, new: Option<u32>, text: &str) -> DiffLine {
    DiffLine {
        kind,
        old,
        new,
        text: text.to_owned(),
    }
}

fn demo_diffs() -> Vec<DiffFile> {
    vec![
        DiffFile {
            path: "crates/molt-tui/src/app.rs".to_owned(),
            hunks: vec![
                Hunk {
                    header: "@@ -501,6 +501,9 @@ fn main_lines".to_owned(),
                    lines: vec![
                        diff_line(
                            DiffKind::Context,
                            Some(501),
                            Some(501),
                            "match self.screen {",
                        ),
                        diff_line(
                            DiffKind::Del,
                            Some(502),
                            None,
                            "    Screen::Tree => self.tree_lines(width),",
                        ),
                        diff_line(
                            DiffKind::Add,
                            None,
                            Some(502),
                            "    Screen::Tree => self.render_tree(frame, inner),",
                        ),
                        diff_line(
                            DiffKind::Add,
                            None,
                            Some(503),
                            "    Screen::Settings => self.render_settings(frame, inner),",
                        ),
                        diff_line(
                            DiffKind::Context,
                            Some(503),
                            Some(504),
                            "    Screen::Diff => self.diff_lines(width),",
                        ),
                        diff_line(DiffKind::Context, Some(504), Some(505), "}"),
                    ],
                },
                Hunk {
                    header: "@@ -884,4 +887,6 @@ pub fn buffer_lines".to_owned(),
                    lines: vec![
                        diff_line(
                            DiffKind::Context,
                            Some(884),
                            Some(887),
                            "pub fn buffer_lines(buffer: &Buffer) -> Vec<String> {",
                        ),
                        diff_line(
                            DiffKind::Add,
                            None,
                            Some(888),
                            "    if buffer.area.width == 0 { return Vec::new(); }",
                        ),
                        diff_line(
                            DiffKind::Context,
                            Some(885),
                            Some(889),
                            "    let area = buffer.area;",
                        ),
                        diff_line(DiffKind::Context, Some(886), Some(890), "}"),
                    ],
                },
            ],
        },
        DiffFile {
            path: "crates/molt-kernel/src/state.rs".to_owned(),
            hunks: vec![Hunk {
                header: "@@ -42,5 +42,7 @@ fn commit".to_owned(),
                lines: vec![
                    diff_line(
                        DiffKind::Context,
                        Some(42),
                        Some(42),
                        "let record = Record {",
                    ),
                    diff_line(
                        DiffKind::Add,
                        None,
                        Some(43),
                        "    checkpoint: Some(checkpoint_id.clone()),",
                    ),
                    diff_line(DiffKind::Context, Some(43), Some(44), "    ..record"),
                    diff_line(DiffKind::Context, Some(44), Some(45), "};"),
                    diff_line(
                        DiffKind::Context,
                        Some(45),
                        Some(46),
                        "journal.append(record)?;",
                    ),
                ],
            }],
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn demo_matches_the_mock_contract() {
        let demo = MockData::demo();
        assert_eq!(demo.sessions.len(), 3);
        assert_eq!(
            demo.sessions
                .iter()
                .map(|session| session.id.as_str())
                .collect::<Vec<_>>(),
            ["demo-6", "feat/tui", "auto/config-B"]
        );
        assert!(demo
            .sessions
            .iter()
            .all(|session| (3..=5).contains(&session.nodes.len())));
        assert_eq!(
            demo.sessions
                .iter()
                .flat_map(|session| &session.nodes)
                .filter(|node| node.importance == Importance::High)
                .count(),
            1
        );

        let kinds: Vec<NodeKind> = demo
            .sessions
            .iter()
            .flat_map(|session| &session.nodes)
            .map(|node| node.kind)
            .collect();
        for expected in [
            NodeKind::User,
            NodeKind::Assistant,
            NodeKind::Tool,
            NodeKind::Compaction,
            NodeKind::Fork,
        ] {
            assert!(kinds.contains(&expected), "missing node kind {expected:?}");
        }
        assert!(demo
            .sessions
            .iter()
            .any(|session| session.kind == SessionKind::AutoFork));

        assert_eq!(demo.configs.len(), 4);
        assert_eq!(
            demo.configs
                .iter()
                .map(|config| config.id.as_str())
                .collect::<Vec<_>>(),
            ["base", "low/full", "fast-edit", "merge"]
        );
        assert_eq!(
            demo.mcp
                .iter()
                .filter(|server| server.status == McpStatus::Connected)
                .count(),
            2
        );
        assert!(demo
            .mcp
            .iter()
            .any(|server| server.status == McpStatus::Error && server.message.is_some()));
        assert_eq!(demo.pending_approvals().count(), 1);

        let roles: Vec<MessageRole> = demo.messages.iter().map(|message| message.role).collect();
        assert_eq!(
            roles,
            [
                MessageRole::User,
                MessageRole::Assistant,
                MessageRole::ToolCall,
                MessageRole::ToolResult,
                MessageRole::Assistant,
            ]
        );
    }

    #[test]
    fn demo_has_checkpoints_compaction_and_three_hunks() {
        let demo = MockData::demo();
        let demo6 = demo.session("demo-6").unwrap();
        assert_eq!(demo6.leaf_index(), 4);
        assert!(demo6.checkpoint_at_or_before(3).is_some());
        assert!(demo6.checkpoint_at_or_before(1).is_none());
        assert!(demo6.compaction_after(3));
        assert!(!demo6.compaction_after(4));

        let auto = demo.session("auto/config-B").unwrap();
        assert!(auto.checkpoint_at_or_before(2).is_none());

        assert_eq!(demo.diffs.len(), 2);
        assert_eq!(demo.hunk_count(), 3);
        assert!(demo.large_file.bytes > 2 * 1024 * 1024);
        assert_eq!(demo.large_file.human_size(), "2.4 MiB");
        assert!(demo.config("merge").unwrap().scenario_version != demo.scenario_version);
    }
}
