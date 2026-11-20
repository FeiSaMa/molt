"""Isolated JSON workers for the synthetic version broker."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

_BWRAP = Path("/usr/bin/bwrap")
_MAX_OUTPUT = 64 * 1024
_MAX_REQUEST = 64 * 1024
_SHORT_TEXT = {"type": "string", "maxLength": 200}
_TEXT_LIST = {"type": "array", "maxItems": 4, "items": _SHORT_TEXT}

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "config": {
            "type": "object",
            "properties": {"reply_format": {"enum": ["plain", "json"]},
                           "prefix": {"type": "string", "maxLength": 120}},
            "required": ["reply_format", "prefix"],
            "additionalProperties": False,
        },
        "tool": {
            "type": "object",
            "properties": {"tool": {"const": "echo"}, "output_format": {"enum": ["plain", "json", "config"]}},
            "required": ["tool", "output_format"],
            "additionalProperties": False,
        },
        "judgment": {
            "type": "object",
            "properties": {
                "reason": _SHORT_TEXT,
                "evidence": _TEXT_LIST,
                "assumptions": _TEXT_LIST,
            },
            "required": ["reason", "evidence", "assumptions"],
            "additionalProperties": False,
        },
    },
    "required": ["config", "tool", "judgment"],
    "additionalProperties": False,
}

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"enum": ["inference", "missing", "conflict"]},
                    "text": _SHORT_TEXT,
                    "evidence": _TEXT_LIST,
                },
                "required": ["kind", "text", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def _launch(command: list[str], mounts: list[str], request: bytes, timeout: int) -> bytes:
    if len(request) > _MAX_REQUEST:
        raise ValueError("worker input is too large")
    # Build a fresh root. These system mounts are runtime files on the supported
    # Arch Linux host. No home, repository, host /tmp, socket or credentials.
    args = [
        str(_BWRAP), "--unshare-all", "--unshare-user", "--unshare-net", "--disable-userns",
        "--die-with-parent",
        "--new-session", "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/usr/bin",
        "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
        "--dev", "/dev", "--proc", "/proc", "--size", str(16 * 1024 * 1024),
        "--tmpfs", "/tmp", "--chdir", "/tmp", *mounts, "--", *command,
    ]
    with tempfile.TemporaryFile() as source:
        source.write(request)
        source.seek(0)
        try:
            proc = subprocess.Popen(args, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError("isolated worker could not start") from exc
        with proc, selectors.DefaultSelector() as ready:
            ready.register(proc.stdout, selectors.EVENT_READ)
            output = bytearray()
            deadline = time.monotonic() + timeout
            try:
                while ready.select(max(0, deadline - time.monotonic())):
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        if proc.wait(timeout=max(0, deadline - time.monotonic())):
                            raise RuntimeError("isolated worker failed")
                        return bytes(output)
                    output.extend(chunk)
                    if len(output) > _MAX_OUTPUT:
                        raise ValueError("worker output is too large")
                    if time.monotonic() >= deadline:
                        break
                raise TimeoutError("isolated worker timed out")
            finally:
                if proc.poll() is None:
                    proc.kill()


def _decode(output: bytes) -> dict:
    try:
        value = json.loads(output)
    except (ValueError, RecursionError) as exc:
        raise ValueError("worker did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("worker must return a JSON object")
    return value


@dataclass(frozen=True)
class ScriptWorker:
    """A Python Agent entry point with a JSON request on stdin."""

    script: Path
    identity: ClassVar[str] = "isolated-python"
    model_id: ClassVar[str] = "none"

    def generate(self, purpose: str, request: dict) -> dict:
        script = self.script.resolve(strict=True)
        if not script.is_file():
            raise ValueError("expected a worker script file")
        data = json.dumps({"purpose": purpose, **request}).encode()
        return _decode(_launch(
            ["/usr/bin/python3", "-I", "-B", "/agent.py"], ["--ro-bind", str(script), "/agent.py"],
            data, 20,
        ))


@dataclass(frozen=True)
class LlamaWorker:
    """Run one local GGUF inference in a fresh namespace, with no network."""

    model: Path
    identity: ClassVar[str] = "llama-completion"
    model_id: str = ""

    def generate(self, purpose: str, request: dict) -> dict:
        model = self.model.resolve(strict=True)
        if not model.is_file() or model.suffix.lower() != ".gguf" or not self.model_id.strip():
            raise ValueError("expected an installed GGUF file and its model version")
        schema, instruction = {
            "propose": (
                PROPOSAL_SCHEMA,
                "Edit the JSON config and tool according to task. Return config.reply_format and config.prefix "
                "explicitly. The echo engine prepends config.prefix verbatim; use an empty string if no prefix "
                "is wanted. tool.output_format supports plain, json, or config (follow config.reply_format). "
                "Use one short sentence for judgment.reason, exact keys from evidence, and list any unverified assumptions.",
            ),
            "digest": (
                DIGEST_SCHEMA,
                "Return exactly one short finding under 24 words. Name the operation exactly as recorded in action. "
                "For a change summary use kind inference and evidence [\"changes\"]. "
                "For an absent operator reason use kind missing and evidence [\"reason_recorded\"]. "
                "For an unverified assumption cite judgment. For a source conflict cite two distinct source IDs. "
                "Use literal field names once as citations. Do not invent a reason.",
            ),
        }[purpose]
        schema = deepcopy(schema)
        citations = {"type": "array", "maxItems": 4}
        if request["evidence"]:
            citations["items"] = {"enum": list(request["evidence"])}
        else:
            citations["maxItems"] = 0
        if purpose == "propose":
            schema["properties"]["judgment"]["properties"]["evidence"] = citations
        else:
            citations["minItems"] = 1
            citations["maxItems"] = 2
            claims = schema["properties"]["claims"]
            claims["maxItems"] = 1
            claims["items"]["properties"]["evidence"] = citations
        data = json.dumps(request, ensure_ascii=False)
        prompt = (
            f"<|im_start|>user\n{instruction}\n{data}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        ).encode()
        output = _launch([
            "/usr/bin/llama-completion", "-m", "/model.gguf", "-ngl", "0", "-t", "6",
            "-c", "4096", "-n", "512", "-no-cnv", "--no-display-prompt", "--simple-io",
            "--no-warmup", "--temp", "0", "-f", "/proc/self/fd/0", "-j", json.dumps(schema),
        ], ["--ro-bind", str(model), "/model.gguf"], prompt, 240)
        value = _decode(output.strip().removesuffix(b" [end of text]").strip())
        # The model schema requires an explicit prefix, while older configs omit an empty prefix.
        if purpose == "propose" and "prefix" not in request["config"] and value.get("config", {}).get("prefix") == "":
            del value["config"]["prefix"]
        return value
