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
archive="$out_dir/molt-$version-linux-x86_64.tar.gz"
if [[ -e "$archive" || -e "$archive.sha256" || -e "$out_dir/molt-$version.tar.gz" ||
      -e "$out_dir/molt-$version-py3-none-any.whl" ]]; then
  echo "refusing to overwrite an existing archive or checksum: $archive" >&2
  exit 2
fi
# Retain local build staging for review; no implicit recursive deletion.
stage=$(mktemp -d "$out_dir/stage.XXXXXX")
bundle="$stage/molt-$version-linux-x86_64"
mkdir -p "$bundle/bin" "$bundle/wheels" "$bundle/share/molt"
mkdir -p "$bundle/share/molt/docs/operations"

PYTHONDONTWRITEBYTECODE=1 uv build --out-dir "$stage" "$repo_root"
install -m 0644 "$stage"/molt-*.whl "$bundle/wheels/"
target_dir=$(realpath -m "${CARGO_TARGET_DIR:-$repo_root/target}")
cargo build --locked --manifest-path "$repo_root/Cargo.toml" --target-dir "$target_dir" --release -p molt-kernel
dependencies=$(cargo tree --locked --manifest-path "$repo_root/Cargo.toml" -p molt-kernel \
  --target x86_64-unknown-linux-gnu --edges normal --prefix none --format '{p}' |
  awk '$1 != "molt-kernel" {print $1, $2}' | LC_ALL=C sort -u)
notices="$repo_root/packaging/release/THIRD-PARTY-LICENSES.txt"
if [[ "$dependencies" != "$(awk '/^## / {print $2, $3}' "$notices" | LC_ALL=C sort -u)" ]]; then
  echo "Rust dependency versions differ from THIRD-PARTY-LICENSES.txt" >&2
  exit 2
fi
install -m 0755 "$target_dir/release/molt-kernel" "$bundle/bin/molt-kernel"
install -m 0644 "$repo_root/packaging/systemd/molt-kernel.service" "$bundle/molt-kernel.service"
install -m 0644 "$repo_root/LICENSE" "$bundle/LICENSE"
install -m 0644 "$notices" "$bundle/THIRD-PARTY-LICENSES.txt"
install -m 0644 "$repo_root/packaging/release/README.md" "$bundle/share/molt/README.md"
install -m 0644 "$repo_root/docs/operations/kernel-cli.md" \
  "$repo_root/docs/operations/version-cli.md" \
  "$repo_root/docs/operations/v2-backup-restore.md" \
  "$repo_root/docs/operations/v2-release-checklist.md" \
  "$repo_root/docs/operations/v2-release-1.1.0.md" \
  "$repo_root/docs/operations/molt-kernel-systemd.md" \
  "$bundle/share/molt/docs/operations/"
install -m 0755 "$repo_root/packaging/release/install-user.sh" "$bundle/install-user.sh"
tar -C "$stage" -czf "$archive" "molt-$version-linux-x86_64"
install -m 0644 "$stage/molt-$version.tar.gz" "$out_dir/"
ln -T -- "$stage"/molt-*.whl "$out_dir/molt-$version-py3-none-any.whl"
sha256sum "$out_dir/molt-$version-linux-x86_64.tar.gz" > "$out_dir/molt-$version-linux-x86_64.tar.gz.sha256"
printf '%s\n' "$out_dir/molt-$version-linux-x86_64.tar.gz"
