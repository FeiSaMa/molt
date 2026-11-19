#!/usr/bin/env bash
set -euo pipefail

bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bundle_name=$(basename "$bundle")
release_id=${bundle_name#molt-}
prefix=${MOLT_INSTALL_PREFIX:-"$HOME/.local/share/molt/releases/$release_id"}
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 2; }
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "this release bundle supports Linux x86_64 only" >&2
  exit 2
}
mkdir -p "$prefix/bin" "$prefix/share/molt" "$prefix/wheels" \
  "$HOME/.local/bin" "$HOME/.config/systemd/user"
cp -a "$bundle/share/molt/." "$prefix/share/molt/"
install -m 0755 "$bundle/bin/molt-kernel" "$prefix/bin/molt-kernel"
install -m 0644 "$bundle"/wheels/*.whl "$prefix/wheels/"
uv venv "$prefix/venv"
uv pip install --python "$prefix/venv/bin/python" "$prefix"/wheels/molt-*.whl
cat > "$prefix/bin/molt" <<EOF
#!/usr/bin/env bash
exec "$prefix/venv/bin/molt" --root "$prefix/share/molt" "\$@"
EOF
chmod 0755 "$prefix/bin/molt"
ln -sfn "$prefix/bin/molt" "$HOME/.local/bin/molt"
ln -sfn "$prefix/bin/molt-kernel" "$HOME/.local/bin/molt-kernel"
sed "s|%h/.local/bin/molt-kernel|$prefix/bin/molt-kernel|g" \
  "$bundle/molt-kernel.service" > "$HOME/.config/systemd/user/molt-kernel.service"
systemctl --user daemon-reload
printf 'Installed molt candidate under %s. Initialize state with:\n' "$prefix"
printf '  %s/bin/molt-kernel init --state %s\n' "$prefix" "$HOME/.local/share/molt/kernel"
printf 'Then enable with: systemctl --user enable --now molt-kernel.service\n'
printf 'Use the kernel backend with MOLT_KERNEL_BACKEND=kernel and MOLT_KERNEL_SOCKET=%s/kernel.sock\n' "$HOME/.local/share/molt/kernel"
printf 'Backup and recovery procedure: %s/share/molt/docs/operations/v2-backup-restore.md\n' "$prefix"
