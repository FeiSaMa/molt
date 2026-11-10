"""Pi 1.5.0 argv 构造与校验。

强制会话四件套；禁止 ``--no-session`` / ``--continue`` / ``--resume``；
no-tools 旗标永不生成。tools_mode 的旗标由 shell 产出，这里只做合并与透传。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from molt.core.errors import ValidationError

FORBIDDEN_FLAGS: tuple[str, ...] = (
    "--no-session",
    "--continue",
    "-c",
    "--resume",
    "-r",
    "--no-" + "tools",
    "-nt",
)
REQUIRED_FLAGS: tuple[str, ...] = (
    "-p",
    "--mode",
    "json",
    "--approve",
    "--session-id",
    "--session-dir",
)
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
BUILTIN_READ_TOOLS: tuple[str, ...] = ("read", "grep", "find", "ls")


def safe_session_id(value: str) -> str:
    """生成 Pi 接受的 session id：首尾字母/数字，只含字母数字 . _ -。"""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", str(value)).strip("._-")
    if not cleaned:
        cleaned = "molt"
    if not cleaned[0].isalnum():
        cleaned = "molt-" + cleaned
    if not cleaned[-1].isalnum():
        cleaned += "0"
    if not SESSION_ID_RE.match(cleaned):
        raise ValidationError(f"无法规范化 session id: {value!r}")
    return cleaned


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _extract_tools(
    tools_args: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """返回 ``(allowlist, denylist, passthrough)``，并拒绝禁用旗标。"""
    allow: list[str] = []
    deny: list[str] = []
    passthrough: list[str] = []
    index = 0
    while index < len(tools_args):
        token = tools_args[index]
        if token in FORBIDDEN_FLAGS:
            raise ValidationError(f"禁止的 Pi 旗标: {token}")
        if token in ("--exclude-tools", "-xt"):
            if index + 1 >= len(tools_args):
                raise ValidationError("--exclude-tools 缺少值")
            deny.extend(_split(tools_args[index + 1]))
            index += 2
            continue
        if token in ("--tools", "-t"):
            if index + 1 >= len(tools_args):
                raise ValidationError("--tools 缺少值")
            allow.extend(_split(tools_args[index + 1]))
            index += 2
            continue
        passthrough.append(token)
        index += 1
    return allow, deny, passthrough


def build_pi_argv(
    *,
    model: str,
    thinking: str,
    session_id: str,
    session_dir: str,
    tools_args: Sequence[str] = (),
    builtin_excludes: Iterable[str] = (),
    prompt: str | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """拼出一次 ``pi -p --mode json`` 调用；不含 key 等敏感信息。"""
    if not model:
        raise ValidationError("model 不得为空")
    if not session_dir:
        raise ValidationError("session_dir 不得为空")
    safe_id = safe_session_id(session_id)
    allow, deny, passthrough = _extract_tools(tools_args)
    for item in builtin_excludes:
        if item not in deny:
            deny.append(item)
    for token in extra:
        if token in FORBIDDEN_FLAGS:
            raise ValidationError(f"禁止的 Pi 旗标: {token}")

    argv = [
        "pi",
        "-p",
        "--mode",
        "json",
        "--approve",
        "--session-id",
        safe_id,
        "--session-dir",
        str(session_dir),
        "--model",
        model,
        "--thinking",
        thinking,
    ]
    if allow:
        argv.extend(["--tools", ",".join(dict.fromkeys(allow))])
    if deny:
        argv.extend(["--exclude-tools", ",".join(dict.fromkeys(deny))])
    argv.extend(passthrough)
    argv.extend(extra)
    if prompt is not None:
        if prompt.startswith("-"):
            argv.append("--")
        argv.append(prompt)
    return argv


def assert_required_flags(argv: Sequence[str]) -> None:
    """测试用：断言强制旗标存在且禁用旗标不存在。"""
    for required in ("-p", "--approve"):
        if required not in argv:
            raise ValidationError(f"缺少必需旗标: {required}")
    if "--mode" not in argv or argv[argv.index("--mode") + 1] != "json":
        raise ValidationError("缺少 --mode json")
    if "--session-id" not in argv or "--session-dir" not in argv:
        raise ValidationError("缺少 --session-id / --session-dir")
    for token in argv:
        if token in FORBIDDEN_FLAGS:
            raise ValidationError(f"出现禁用旗标: {token}")
