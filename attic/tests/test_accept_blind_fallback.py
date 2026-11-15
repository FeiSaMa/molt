"""blind 验收在 /tmp state 缺失时回退到 tracked trace。"""

from __future__ import annotations

from pathlib import Path

from probes.accept import stage_blind


def test_blind_falls_back_to_tracked_trace(tmp_path: Path) -> None:
    ok, reason, code = stage_blind(tmp_path / "missing-blind-state")
    assert ok is True
    assert code == 0
    assert "tracked-trace" in reason
