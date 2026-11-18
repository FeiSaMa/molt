"""Shared canonical JSON vectors for the future kernel client."""

import json
from pathlib import Path


def test_canonical_vectors():
    path = Path(__file__).parent / "fixtures" / "kernel" / "canonical.json"
    for vector in json.loads(path.read_text(encoding="utf-8")):
        encoded = json.dumps(
            vector["value"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        assert encoded == vector["canonical"]
