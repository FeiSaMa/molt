#!/usr/bin/env bash
# Verify the installed wheel and bundled kernel outside the source checkout.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
bundle=$(cd "${1:?usage: verify-bundle.sh EXTRACTED_BUNDLE NEW_WORK_DIR SDIST}" && pwd)
work=${2:?usage: verify-bundle.sh EXTRACTED_BUNDLE NEW_WORK_DIR SDIST}
sdist=${3:?usage: verify-bundle.sh EXTRACTED_BUNDLE NEW_WORK_DIR SDIST}
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
unset PYTHONPATH PYTHONHOME MOLT_KERNEL_BACKEND MOLT_KERNEL_SOCKET MOLT_KERNEL_BIN

test -s "$bundle/share/molt/README.md"
for doc in kernel-cli.md version-cli.md v2-backup-restore.md v2-release-checklist.md v2-release-1.1.0.md molt-kernel-systemd.md; do
  test -s "$bundle/share/molt/docs/operations/$doc"
done
# Reject accidental reintroduction of competition modules or duplicate import roots.
uv run --no-project --python 3.12.13 python - "$bundle" "$sdist" <<'PYCODE'
import pathlib
import sys
import tarfile
import zipfile

bundle = pathlib.Path(sys.argv[1])
license_text = (bundle / "LICENSE").read_bytes()
assert license_text.startswith(b"MIT License\n")
for name in ("molt", "attic", "bench", "shells", "scenarios", "schemas", "tests"):
    assert not (bundle / "share/molt" / name).exists(), name
runtime = {
    "molt/__init__.py", "molt/__main__.py", "molt/cli.py", "molt/kernel_client.py",
    "molt/project_cli.py", "molt/version_cli.py", "molt/version_files.py",
    "molt/version_workers.py", "molt/versioning.py", "molt/core/__init__.py",
    "molt/core/errors.py", "molt/core/fsutil.py", "molt/core/hash.py",
    "molt/core/write_policy.py",
}
wheels = list((bundle / "wheels").glob("molt-*.whl"))
assert len(wheels) == 1, wheels
with zipfile.ZipFile(wheels[0]) as wheel:
    wheel_licenses = [name for name in wheel.namelist() if name.endswith(".dist-info/licenses/LICENSE")]
    assert len(wheel_licenses) == 1, wheel_licenses
    assert wheel.read(wheel_licenses[0]) == license_text
    names = {name for name in wheel.namelist() if ".dist-info/" not in name}
    assert names == runtime, (names - runtime, runtime - names)
with tarfile.open(sys.argv[2], "r:gz") as archive:
    filename = "THIRD-PARTY-LICENSES.txt"
    source_notice = next(item for item in archive.getmembers()
                         if item.name.split("/", 1)[-1] == f"packaging/release/{filename}")
    assert (bundle / filename).read_bytes() == archive.extractfile(source_notice).read()
    source_licenses = [item for item in archive if item.isfile() and item.name.split("/", 1)[1] == "LICENSE"]
    assert len(source_licenses) == 1, source_licenses
    with archive.extractfile(source_licenses[0]) as source_license:
        assert source_license.read() == license_text
    names = {item.name.split("/", 1)[1] for item in archive if item.isfile()}
    source_runtime = {name for name in names if name.startswith("molt/")}
    assert source_runtime == runtime, (source_runtime - runtime, runtime - source_runtime)
    forbidden = ("attic/", "bench/", "shells/", "scenarios/", "schemas/", "tests/fixtures/")
    assert not [name for name in names if name.startswith(forbidden)]
    assert "docs/protocol.md" not in names and "tests/test_v1_compatibility.py" not in names
print("package boundary: kernel-facing runtime in wheel and sdist")
PYCODE
uv export --project "$repo_root" --locked --extra dev --no-emit-project --no-hashes \
  --output-file "$work/requirements.txt"
uv venv --python 3.12.13 "$work/venv"
uv pip install --python "$work/venv/bin/python" -r "$work/requirements.txt" \
  "$bundle"/wheels/molt-*.whl
for name in test_kernel_transactions test_project_cli test_version_cli; do
  cp "$repo_root/tests/$name.py" "$work/$name.py"
done
cd "$work"
# A successful recovery test must not silently import the checkout's Python package.
"$work/venv/bin/python" -I -c \
  'import molt, pathlib, sys; p = pathlib.Path(molt.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()), p; print("installed molt:", p)'
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
bash "$repo_root/packaging/release/verify-user-install.sh" "$bundle" "$work/user-install"
# Keep UDS paths below SUN_LEN even when the persistent evidence path is long.
test_state=$(mktemp -d /tmp/molt-bundle-tests.XXXXXX)
MOLT_KERNEL_BIN="$bundle/bin/molt-kernel" \
  "$work/venv/bin/python" -I -m pytest -c /dev/null -p no:cacheprovider \
  -o 'markers=integration: real Unix socket integration' --basetemp "$test_state/state" -q \
  "$work/test_kernel_transactions.py" \
  "$work/test_project_cli.py::test_generic_reader_pins_three_files_and_audits_after_restore" \
  "$work/test_project_cli.py::test_project_recovers_lost_reply_with_release_kernel" \
  "$work/test_version_cli.py::test_managed_file_capture_and_merge"
