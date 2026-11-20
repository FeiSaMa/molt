"""Project-level editing and pinned OpenCode jobs over the version broker."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from molt.core.fsutil import atomic_write_bytes, exclusive_lock
from molt.core.hash import canonical_json_bytes, sha256_bytes
from molt.version_files import capture, publish_tree, validate_paths
from molt.versioning import RecoveryRequired, VersionConflict, VersionSandbox


def add_project_parser(commands) -> None:
    project = commands.add_parser("project", help="编辑和保存受管项目，固定 OpenCode 作业版本")
    project.add_argument("--config", type=Path, required=True)
    actions = project.add_subparsers(dest="project_command", required=True)
    begin = actions.add_parser("begin", help="从分支头创建新的编辑目录")
    begin.add_argument("--branch", default="main")
    begin.add_argument("--keep-old", action="store_true", help="保留旧编辑目录及其改动并开始新一轮")
    actions.add_parser("preview", help="列出相对于编辑基线的路径变更")
    save = actions.add_parser("save", help="以编辑时的 generation 和分支头提交")
    save.add_argument("--reason", required=True)
    save.add_argument("--reason-source", default="local operator")
    actions.add_parser("recover", help="查询或恢复原 request ID")
    actions.add_parser("switch", help="激活指定分支头").add_argument("branch")
    actions.add_parser("rollback", help="回退活动快照").add_argument("snapshot")
    actions.add_parser("read-tree", help="固定活动快照并输出其只读目录")
    opencode = actions.add_parser("opencode", help="在固定目录运行前台 OpenCode 作业")
    opencode.add_argument("arguments", nargs=argparse.REMAINDER)


def _fingerprints(contents: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256_bytes(data) for name, data in contents.items()}


def _changes(before: dict, after: dict) -> list[dict]:
    return [
        {
            "path": name,
            "operation": "add" if name not in before else "delete" if name not in after else "modify",
        }
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    ]


class Project:
    def __init__(self, config_path: Path, config: dict):
        if set(config) != {"workspace", "edits", "exports", "files"}:
            raise ValueError("项目配置需要 workspace、edits、exports 和 files")
        paths = []
        for key in ("workspace", "edits", "exports"):
            if not isinstance(config[key], str) or not config[key]:
                raise ValueError(f"{key} 必须是目录路径")
            paths.append((config_path.absolute().parent / config[key]).resolve(strict=True))
        for i, path in enumerate(paths):
            if any(path.is_relative_to(other) or other.is_relative_to(path) for other in paths[:i]):
                raise ValueError("workspace、edits 和 exports 必须是互相独立的目录")
        workspace, self.edits, self.exports = paths
        self.box = VersionSandbox(workspace)
        self.files = config["files"]
        if not isinstance(self.files, list) or not self.files:
            raise ValueError("files 必须是非空相对路径数组")
        validate_paths(self.files)
        self.identity = sha256_bytes(canonical_json_bytes([str(self.edits), str(self.exports), sorted(self.files)]))
        self.state = workspace / "project-session.json"
        self.lock = workspace / "project-session.lock"

    def _load(self) -> dict | None:
        if self.state.is_symlink() or self.lock.is_symlink():
            raise ValueError("项目会话必须保存在工作区内")
        if not self.state.exists():
            return None
        session = json.loads(self.state.read_bytes())
        if session["project"] != self.identity:
            raise ValueError("项目配置与已有编辑会话不符，请恢复原配置")
        return session

    def _write(self, session: dict) -> None:
        atomic_write_bytes(self.state, canonical_json_bytes(session))

    def _capture(self, session: dict) -> dict[str, bytes]:
        return capture(Path(session["edit"]), self.files, allow_missing=True)

    def _check_edit(self, session: dict | None, *, keep_old: bool = False) -> None:
        if session and session.get("pending"):
            raise RecoveryRequired(f"请先恢复原请求 {session['pending']['request_id']}")
        if session and not keep_old and _fingerprints(self._capture(session)) != session["baseline"]:
            raise VersionConflict(f"编辑目录有未保存的改动：{session['edit']}")

    def _status(self) -> dict:
        status = self.box.workspace_status()
        if status["pending_request"]:
            raise RecoveryRequired(f"请先恢复原请求 {status['pending_request']}")
        return status

    def begin(self, session: dict | None, branch: str, *, keep_old: bool) -> dict:
        self._check_edit(session, keep_old=keep_old)
        status = self._status()
        head = status["branches"].get(branch)
        if head is None and (status["generation"] or branch != "main"):
            raise ValueError(f"分支不存在：{branch}")
        if head is None:
            tree, baseline = str(publish_tree(self.edits, {}, writable=True)), {}
        else:
            version = self.box.show(head)
            if "files" not in version:
                raise ValueError("项目编辑需要受管文件快照")
            if version["files"].keys() - set(self.files):
                raise ValueError("项目清单遗漏分支头中的受管文件")
            baseline = {name: item["sha256"].removeprefix("sha256:") for name, item in version["files"].items()}
            tree = self.box.export_tree(self.edits, snapshot=head, writable=True)["tree"]
        current = {
            "project": self.identity, "edit": tree, "branch": branch, "head": head,
            "generation": status["generation"], "baseline": baseline,
        }
        self._write(current)
        return {
            "edit": tree, "branch": branch, "head": head, "generation": status["generation"],
            "previous_edit": session["edit"] if session else None,
        }

    def save(self, session: dict, reason: str, source: str) -> dict:
        if session.get("pending"):
            raise RecoveryRequired(f"请先恢复原请求 {session['pending']['request_id']}")
        contents = self._capture(session)
        baseline = _fingerprints(contents)
        if not contents and session["head"] is None:
            if self._status()["generation"] != session["generation"]:
                raise VersionConflict("stale generation; start a new edit")
            return {"changed": False, "snapshot": None, "event": None}

        def prepare(request_id, receipt):
            self._write({**session, "pending": {"request_id": request_id, "baseline": baseline, **receipt}})

        result = self.box.commit_file_contents(
            session["branch"], contents, expected=session["generation"], expected_head=session["head"],
            activate=True, reason=reason, source=source, prepare=prepare,
        )
        if result["changed"]:
            self._settle(session, result, baseline)
        return result

    def _settle(self, session: dict, result: dict, baseline: dict) -> None:
        settled = {key: value for key, value in session.items() if key != "pending"}
        self._write(settled | {
            "head": result["snapshot"], "generation": result["current"]["generation"], "baseline": baseline,
        })

    def recover(self, session: dict | None) -> dict:
        pending = session.get("pending") if session else None
        if pending is None:
            return {"current": self.box.recover()}
        request_id = pending["request_id"]
        current = self.box.recover(request_id=request_id)
        if current is None:
            self._write({key: value for key, value in session.items() if key != "pending"})
            return {"request_id": request_id, "status": "not_found"}
        result = {"current": current, "snapshot": pending["snapshot"], "event": pending["event"]}
        self._settle(session, result, pending["baseline"])
        return {"request_id": request_id, "status": "committed", **result}

    def dispatch(self, args: argparse.Namespace, session: dict | None) -> dict:
        match args.project_command:
            case "begin":
                return self.begin(session, args.branch, keep_old=args.keep_old)
            case "preview" | "save":
                if session is None:
                    raise ValueError("请先运行 project begin")
                if args.project_command == "save":
                    return self.save(session, args.reason, args.reason_source)
                return {
                    "edit": session["edit"], "branch": session["branch"], "head": session["head"],
                    "generation": session["generation"], "pending": session.get("pending", {}).get("request_id"),
                    "changes": _changes(session["baseline"], _fingerprints(self._capture(session))),
                }
            case "recover":
                return self.recover(session)
            case "switch" | "rollback":
                self._check_edit(session)
                status = self._status()
                if args.project_command == "rollback":
                    return self.box.rollback(args.snapshot, expected=status["generation"])
                head = status["branches"].get(args.branch)
                if head is None:
                    raise ValueError(f"分支不存在：{args.branch}")
                return self.box.switch(args.branch, expected=status["generation"], expected_head=head)
            case "read-tree" | "opencode":
                return self.box.export_tree(self.exports)
        raise AssertionError(f"unexpected project command {args.project_command}")

    def execute(self, args: argparse.Namespace) -> dict:
        if self.lock.is_symlink():
            raise ValueError("项目会话锁必须保存在工作区内")
        with exclusive_lock(self.lock):
            return self.dispatch(args, self._load())


def opencode_arguments(arguments: list[str]) -> list[str]:
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if arguments[:2] in (["debug", "config"], ["debug", "agents"]) and len(arguments) == 2:
        return arguments
    if arguments[:1] != ["run"]:
        raise ValueError("请指定 -- run …、-- debug config 或 -- debug agents")
    forbidden = {"--server", "--attach", "--dir", "--cwd", "--worktree", "--continue", "--session", "--fork"}
    for argument in arguments[1:]:
        if argument == "--":
            break
        if argument.split("=", 1)[0] in forbidden or re.fullmatch(r"-[wcs].*", argument):
            raise ValueError(f"固定版本的新作业不支持该选项：{argument}")
    return arguments


def run_opencode(tree: Path, arguments: list[str]) -> None:
    home = Path(tempfile.mkdtemp(prefix="opencode-", dir=tree.parent))
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENCODE_")}
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"), "XDG_STATE_HOME": str(home / "state"),
        "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true",
    })
    os.chdir(tree)
    os.execvpe("opencode", ["opencode", "--standalone", *arguments], env)
