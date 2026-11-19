"""Release-owned policy for legacy state writes."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

DEFAULT_BACKEND = "kernel"
_kernel_projection_write = ContextVar("kernel_projection_write", default=False)


def v1_read_only() -> bool:
    """Whether the formal release must keep legacy v1 state read-only."""
    return DEFAULT_BACKEND == "kernel"


def projection_writes_allowed() -> bool:
    return _kernel_projection_write.get()


@contextmanager
def kernel_projection() -> Iterator[None]:
    """Allow v1 storage primitives only inside an ephemeral kernel projection."""
    token = _kernel_projection_write.set(True)
    try:
        yield
    finally:
        _kernel_projection_write.reset(token)
