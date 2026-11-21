#!/usr/bin/env bash
set -euo pipefail

bundle=$1
work=$2
previous_bundle=${3:-$bundle}
UV_PYTHON=$(uv python find --no-project 3.12.13)
mkdir "$work"
export HOME="$work/home" PYTHONDONTWRITEBYTECODE=1
export UV_PYTHON
export REAL_MV REAL_SYSTEMD_STAGE
REAL_MV=$(command -v mv)
REAL_SYSTEMD_STAGE="$HOME/.config/systemd/user/molt-kernel.service"
mkdir -p "$HOME" "$work/tools"
cat > "$work/tools/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ $# == 2 && $1 == --user && $2 == daemon-reload ]] || exit 99
if [[ -f $HOME/reload-failure ]]; then
  unlink "$HOME/reload-failure"
  exit 31
fi
exit "${FAIL_RELOAD:-0}"
EOF
cat > "$work/tools/mv" <<'EOF'
#!/usr/bin/env bash
if [[ ${FAIL_SWITCH:-0} == 1 && ${!#} == "$REAL_SYSTEMD_STAGE" ]]; then
  exit 32
fi
exec "$REAL_MV" "$@"
EOF
chmod +x "$work/tools/"{systemctl,mv}
export PATH="$work/tools:$PATH"

install_bundle() {
  local archive=$1 prefix=$2
  shift 2
  (cd "$work" && env MOLT_INSTALL_PREFIX="$prefix" "$@" bash "$archive/install-user.sh")
}
expect_failure() {
  local expected=$1 actual=0
  shift
  "$@" || actual=$?
  [[ $actual == "$expected" ]]
}
installed_version() {
  "$1/venv/bin/python" -I -c 'from importlib.metadata import version; print(version("molt"))'
}
assert_active() {
  local prefix=$1
  [[ $(readlink "$HOME/.local/bin/molt") == "$prefix/bin/molt" ]]
  [[ $(readlink "$HOME/.local/bin/molt-kernel") == "$prefix/bin/molt-kernel" ]]
  sed "s|%h/.local/bin/molt-kernel|$prefix/bin/molt-kernel|g" "$bundle/molt-kernel.service" |
    cmp - "$REAL_SYSTEMD_STAGE"
  "$HOME/.local/bin/molt" --help >/dev/null
}

old="$work/old"
new="$work/new"
install_bundle "$previous_bundle" "$old"
assert_active "$old"
state="$HOME/.local/share/molt/kernel"
mkdir -p "$(dirname "$state")"
"$HOME/.local/bin/molt-kernel" init --state "$state"
"$HOME/.local/bin/molt-kernel" audit --state "$state"
# Include the filesystem entries, file modes, mtimes and bytes in the comparison.
state_tree() {
  tar --sort=name --numeric-owner -cf - -C "$(dirname "$state")" kernel | sha256sum
}
before=$(state_tree)

expect_failure 2 install_bundle "$bundle" "$new" UV_PYTHON="$work/nonexistent-python"
assert_active "$old"
[[ ! -e $new ]]
mkdir "$work/conflict"
printf 'user file\n' > "$work/conflict/keep"
expect_failure 2 install_bundle "$bundle" "$work/conflict"
printf 'user file\n' | cmp - "$work/conflict/keep"
expect_failure 2 install_bundle "$bundle" "$state/overlap"
expect_failure 32 install_bundle "$bundle" "$new" FAIL_SWITCH=1
assert_active "$old"
touch "$HOME/reload-failure"
expect_failure 31 install_bundle "$bundle" "$new"
assert_active "$old"
expect_failure 33 install_bundle "$bundle" "$new" FAIL_RELOAD=33 > "$work/rollback.log" 2>&1
assert_active "$old"
rg -F 'restoration was incomplete' "$work/rollback.log"

install_bundle "$bundle" "$new" | tee "$work/upgrade.log"
assert_active "$new"
new_version=$(installed_version "$new")
[[ $bundle == "$previous_bundle" || $(installed_version "$old") != "$new_version" ]]
cmp "$bundle/LICENSE" "$new/LICENSE"
cmp "$bundle/THIRD-PARTY-LICENSES.txt" "$new/THIRD-PARTY-LICENSES.txt"
install_bundle "$bundle" "$new" UV_PYTHON="$work/nonexistent-python"
assert_active "$new"

previous=$(sed -n 's/^Previous entries saved in //p' "$work/upgrade.log")
[[ -n $previous ]]
cp -aT "$previous/molt" "$HOME/.local/bin/molt"
cp -aT "$previous/molt-kernel" "$HOME/.local/bin/molt-kernel"
install -m 0644 "$previous/molt-kernel.service" "$REAL_SYSTEMD_STAGE"
systemctl --user daemon-reload
assert_active "$old"
[[ $(state_tree) == "$before" ]]

(
  export HOME="$work/conflict-home"
  export REAL_SYSTEMD_STAGE="$HOME/.config/systemd/user/molt-kernel.service"
  mkdir -p "$HOME/.local/bin" "$(dirname "$REAL_SYSTEMD_STAGE")"
  printf 'user entry\n' > "$HOME/.local/bin/molt"
  printf 'user unit\n' > "$REAL_SYSTEMD_STAGE"
  expect_failure 2 install_bundle "$bundle" ""
  printf 'user entry\n' | cmp - "$HOME/.local/bin/molt"
  printf 'user unit\n' | cmp - "$REAL_SYSTEMD_STAGE"
)
(
  export HOME="$work/first-home"
  export REAL_SYSTEMD_STAGE="$HOME/.config/systemd/user/molt-kernel.service"
  mkdir "$HOME"
  touch "$HOME/reload-failure"
  expect_failure 31 install_bundle "$bundle" ""
  [[ ! -L $HOME/.local/bin/molt && ! -L $HOME/.local/bin/molt-kernel && ! -e $REAL_SYSTEMD_STAGE ]]
  install_bundle "$bundle" ""
  assert_active "$HOME/.local/share/molt/releases/$new_version-linux-x86_64"
)
printf 'User installer passed (systemctl substitute): %s\n' "$new_version"
