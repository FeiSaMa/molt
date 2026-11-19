"""Run legacy CLI behavior tests without inheriting the release default."""

from __future__ import annotations

import sys

from molt.cli import main
from molt.core import write_policy

write_policy.DEFAULT_BACKEND = "v1"
sys.exit(main(sys.argv[1:]))
