#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out_dir=${1:?usage: build-deb-rpm.sh OUTPUT_DIR NFPM_BIN}
nfpm_bin=${2:?usage: build-deb-rpm.sh OUTPUT_DIR NFPM_BIN}
[[ -x $nfpm_bin ]] || { echo "nfpm is not executable: $nfpm_bin" >&2; exit 2; }
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "native packages currently support Linux x86_64 only" >&2
  exit 2
}
app_version=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$repo_root/pyproject.toml" | head -1)
version=${VERSION:-$app_version+git$(git -C "$repo_root" rev-parse --short=7 HEAD)}
[[ -n $version ]] || { echo "could not determine package version" >&2; exit 2; }
mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)
for ext in deb rpm; do
  [[ ! -e $out_dir/molt_${version}_amd64.$ext && ! -e $out_dir/molt-${version}-1.x86_64.$ext ]] || {
    echo "refusing to overwrite an existing $ext package in $out_dir" >&2; exit 2;
  }
done

stage=$(mktemp -d "$out_dir/stage.XXXXXX")
build_dir=$(mktemp -d "$out_dir/build.XXXXXX")
trap 'rm -rf -- "$stage" "$build_dir"' EXIT
config="$build_dir/nfpm.yaml"
sed "s|\${STAGE}|$stage|g; s|\${VERSION}|$version|g" \
  "$repo_root/packaging/native/nfpm.yaml" >"$config"
mkdir -p "$stage/usr/bin" "$stage/usr/lib/molt" \
  "$stage/usr/lib/systemd/user" "$stage/usr/share/doc/molt" "$stage/usr/share/licenses/molt"
export PYTHONDONTWRITEBYTECODE=1
uv lock --project "$repo_root" --check --python "${UV_PYTHON:-3.12.13}"
uv build --python "${UV_PYTHON:-3.12.13}" --require-hashes \
  --build-constraints "$repo_root/packaging/release/build-requirements.txt" \
  --out-dir "$build_dir" "$repo_root"
cargo build --locked --manifest-path "$repo_root/Cargo.toml" \
  --target-dir "${CARGO_TARGET_DIR:-$repo_root/target}" \
  --target x86_64-unknown-linux-gnu --release -p molt-kernel -p molt-tui
rust_target=${CARGO_TARGET_DIR:-$repo_root/target}/x86_64-unknown-linux-gnu/release
install -m 0755 "$repo_root/packaging/native/molt" "$stage/usr/bin/molt"
install -m 0755 "$rust_target/molt-kernel" "$rust_target/molt-tui" "$stage/usr/bin/"
install -m 0644 "$build_dir"/molt-*.whl "$stage/usr/lib/molt/molt-python.whl"
install -m 0644 "$repo_root/packaging/native/molt-kernel.service" "$stage/usr/lib/systemd/user/"
install -m 0644 "$repo_root/packaging/release/README.md" "$stage/usr/share/doc/molt/"
install -m 0644 "$repo_root/LICENSE" "$repo_root/packaging/release/THIRD-PARTY-LICENSES.txt" \
  "$stage/usr/share/licenses/molt/"
uv run --no-project --python "${UV_PYTHON:-3.12.13}" python \
  "$repo_root/packaging/release/rust-notices.py" --check

for format in deb rpm; do
  "$nfpm_bin" package --config "$config" --packager "$format" --target "$out_dir"
done
deb=$(find "$out_dir" -maxdepth 1 -type f -name 'molt_*_amd64.deb' -print -quit)
rpm=$(find "$out_dir" -maxdepth 1 -type f -name 'molt-*.x86_64.rpm' -print -quit)
[[ -n $deb && -n $rpm ]] || { echo "nfpm did not create both packages" >&2; exit 2; }
(cd "$out_dir" && sha256sum "${deb##*/}" "${rpm##*/}" > SHA256SUMS)
printf '%s\n' "$deb" "$rpm"
