"""Release-owned policy for legacy state writes."""

DEFAULT_BACKEND = "v1"


def v1_read_only() -> bool:
    """R is the first formal release whose default backend is the kernel."""
    return DEFAULT_BACKEND == "kernel"
