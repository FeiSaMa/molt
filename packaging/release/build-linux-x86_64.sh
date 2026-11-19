#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out_dir=${1:?usage: build-linux-x86_64.sh OUTPUT_DIR}
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "release bundle build currently supports Linux x86_64 only" >&2
  exit 2
fi
version=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$repo_root/pyproject.toml" | head -1)
if [[ -z "$version" ]]; then
  echo "could not determine package version" >&2
  exit 2
fi

mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/molt-release.XXXXXX")
trap 'rm -rf "$stage"' EXIT
bundle="$stage/molt-$version-linux-x86_64"
mkdir -p "$bundle/bin" "$bundle/wheels" "$bundle/share/molt"
mkdir -p "$bundle/share/molt/docs/operations"

PYTHONDONTWRITEBYTECODE=1 uv build --wheel --out-dir "$bundle/wheels" "$repo_root"
cargo build --manifest-path "$repo_root/Cargo.toml" --release -p molt-kernel
install -m 0755 "$repo_root/target/release/molt-kernel" "$bundle/bin/molt-kernel"
install -m 0644 "$repo_root/packaging/systemd/molt-kernel.service" "$bundle/molt-kernel.service"
cp -a "$repo_root/molt" "$repo_root/schemas" "$repo_root/scenarios" \
  "$repo_root/shells" "$repo_root/bench" "$bundle/share/molt/"
install -m 0644 "$repo_root/README.md" "$bundle/share/molt/README.md"
install -m 0644 "$repo_root/docs/operations/v2-backup-restore.md" \
  "$repo_root/docs/operations/v2-release-checklist.md" \
  "$repo_root/docs/operations/v2-release-1.1.0.md" \
  "$repo_root/docs/operations/molt-kernel-systemd.md" \
  "$bundle/share/molt/docs/operations/"
install -m 0755 "$repo_root/packaging/release/install-user.sh" "$bundle/install-user.sh"
tar -C "$stage" -czf "$out_dir/molt-$version-linux-x86_64.tar.gz" "molt-$version-linux-x86_64"
sha256sum "$out_dir/molt-$version-linux-x86_64.tar.gz" > "$out_dir/molt-$version-linux-x86_64.tar.gz.sha256"
printf '%s\n' "$out_dir/molt-$version-linux-x86_64.tar.gz"
