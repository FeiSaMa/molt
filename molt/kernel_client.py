"""Small dependency-free client for the molt-kernel Unix socket protocol."""
import hashlib
import json
import socket
import struct
from typing import Any

MAX_FRAME = 16 * 1024 * 1024


class KernelError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class KernelClient:
    def __init__(self, path: str, timeout: float | None = None):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if timeout is not None:
            self.sock.settimeout(timeout)
        self.sock.connect(path)
        self._id = 0
        self.handshake = self.call("handshake", {"protocol": "molt-kernel/1"})

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _read(self, n: int) -> bytes:
        chunks = []
        while n:
            chunk = self.sock.recv(n)
            if not chunk:
                raise ConnectionError("kernel disconnected; commit outcome may be unknown")
            chunks.append(chunk)
            n -= len(chunk)
        return b"".join(chunks)

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        payload = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode()
        if len(payload) > MAX_FRAME:
            raise ValueError("request frame too large")
        self.sock.sendall(struct.pack(">I", len(payload)) + payload)
        size = struct.unpack(">I", self._read(4))[0]
        if size > MAX_FRAME:
            raise ValueError("response frame too large")
        response = json.loads(self._read(size))
        if "error" in response:
            error = response["error"]
            raise KernelError(error.get("code", "io_error"), error.get("message", ""))
        return response.get("result")

    def request_status(self, request_id: str) -> Any:
        return self.call("request.status", {"request_id": request_id})

    def current(self) -> Any:
        return self.call("current")

    def inspect(self) -> Any:
        return self.call("inspect")

    def create_manifest(self, roots: dict[str, Any]) -> str:
        return self.call("manifest.create", {"roots": roots})["hash"]

    def get_manifest(self, digest: str) -> dict[str, Any]:
        return self.call("manifest.get", {"hash": digest})

    def get_object(self, digest: str) -> bytes:
        return bytes(self.call("object.get", {"hash": digest})["data"])

    def upload_object(self, data: bytes, *, chunk_size: int = 1024 * 1024) -> str:
        """Upload an immutable CAS object in bounded frames; reconnect and restart after disconnect."""
        if not 1 <= chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be between 1 and 1048576")
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        session = self.call(
            "object.upload.begin", {"size": len(data), "hash": digest}
        )["upload_id"]
        offset = 0
        try:
            while offset < len(data):
                chunk = data[offset : offset + chunk_size]
                result = self.call(
                    "object.upload.chunk",
                    {"upload_id": session, "offset": offset, "data": list(chunk)},
                )
                offset = result["next_offset"]
            result = self.call("object.upload.finish", {"upload_id": session})
            if result["hash"] != digest:
                raise KernelError("integrity_error", "uploaded object hash mismatch")
            return digest
        except BaseException:
            try:
                self.call("object.upload.abort", {"upload_id": session})
            except (OSError, KernelError, ValueError):
                self.close()
            raise
