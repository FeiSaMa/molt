#!/usr/bin/env bash
# Verify the installed wheel and bundled kernel outside the source checkout.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
bundle=$(cd "${1:?usage: verify-bundle.sh EXTRACTED_BUNDLE NEW_WORK_DIR}" && pwd)
work=${2:?usage: verify-bundle.sh EXTRACTED_BUNDLE NEW_WORK_DIR}
work=$(realpath -m "$work")
case "$work" in
  "$repo_root"|"$repo_root"/*)
    echo "verification work directory must be outside the source checkout" >&2
    exit 2
    ;;
esac
mkdir "$work"
work=$(cd "$work" && pwd)
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME MOLT_KERNEL_BACKEND MOLT_KERNEL_SOCKET

for doc in v2-backup-restore.md v2-release-checklist.md v2-release-1.1.0.md molt-kernel-systemd.md; do
  test -s "$bundle/share/molt/docs/operations/$doc"
done
uv export --project "$repo_root" --locked --extra dev --no-emit-project --no-hashes \
  --output-file "$work/requirements.txt"
uv venv --python 3.12.13 "$work/venv"
uv pip install --python "$work/venv/bin/python" -r "$work/requirements.txt" \
  "$bundle"/wheels/molt-*.whl
cp "$repo_root/tests/test_kernel_client.py" "$work/test_kernel_client.py"
cd "$work"
# A successful recovery test must not silently import the checkout's Python package.
"$work/venv/bin/python" -I -c \
  'import molt, pathlib, sys; p = pathlib.Path(molt.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()), p; print("installed molt:", p)'
"$work/venv/bin/molt" --root "$bundle/share/molt" schema validate --all
"$work/venv/bin/molt" --root "$bundle/share/molt" scenario validate \
  --scenario "$bundle/share/molt/scenarios/demo/scenario.json"
"$bundle/bin/molt-kernel" init --state "$work/empty-state"
"$bundle/bin/molt-kernel" audit --state "$work/empty-state"
# Verify a concrete unit without depending on a preinstalled ~/.local/bin binary.
"$work/venv/bin/python" -I - "$bundle" "$work" <<'PY'
import pathlib
import sys

bundle, work = map(pathlib.Path, sys.argv[1:])
unit = (bundle / "molt-kernel.service").read_text()
unit = unit.replace("%h/.local/bin/molt-kernel", str(bundle / "bin/molt-kernel"))
unit = unit.replace("%h/.local/share/molt/kernel", str(work / "empty-state"))
(work / "molt-kernel.service").write_text(unit)
PY
systemd-analyze verify "$work/molt-kernel.service"
MOLT_TEST_DATA_ROOT="$bundle/share/molt" MOLT_KERNEL_BIN="$bundle/bin/molt-kernel" \
  "$work/venv/bin/python" -I -m pytest -c /dev/null -p no:cacheprovider \
  -o 'markers=integration: real Unix socket integration' -q \
  "$work/test_kernel_client.py::test_kernel_backup_restores_replay_evidence"
