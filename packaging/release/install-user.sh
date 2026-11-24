#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
die() {
  printf 'molt installer: %s\n' "$*" >&2
  exit 2
}
exists() {
  [[ -e "$1" || -L "$1" ]]
}
safe_path() {
  [[ $1 =~ ^/[a-zA-Z0-9._/+@-]+$ ]]
}
render_unit() {
  sed "s|%h/.local/bin/molt-kernel|$1/bin/molt-kernel|g" "$bundle/molt-kernel.service"
}
render_launcher() {
  cat <<'EOF'
#!/usr/bin/env bash
prefix=$(dirname "$(dirname "$(realpath -- "$0")")")
# Versions launches the CLI again, and its kernel worker resolves molt-kernel.
export PATH="$prefix/bin:$PATH"
export MOLT_TUI_BIN="${MOLT_TUI_BIN-$prefix/bin/molt-tui}"
exec "$prefix/venv/bin/molt" --root "$prefix/share/molt" "$@"
EOF
}
check_tools() {
  local release=$1 probe status=0
  "$release/bin/molt" version commit-files --help | grep -F -- '--include-request-id' >/dev/null ||
    die "installed CLI lacks the commit receipt option"
  env -u MOLT_NO_TUI -u MOLT_TUI_BIN "$release/bin/molt" tui --selftest --width 80 --height 24 |
    grep -F 'SELFTEST OK' >/dev/null ||
    die "installed TUI selftest failed"
  # A no-argument invocation has a defined protocol error and never opens a state or socket.
  probe=$("$release/bin/molt-kernel" 2>&1) || status=$?
  [[ $status == 1 && $probe == *'"code":"invalid_request"'* ]] || die "installed kernel startup probe failed"
}

[[ $# == 0 ]] || die "usage: [MOLT_INSTALL_PREFIX=/absolute/path] [UV_PYTHON=3.12.13] bash install-user.sh"
for tool in uv systemctl flock; do
  command -v "$tool" >/dev/null || die "$tool is required"
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die "Linux x86_64 is required"
shopt -s nullglob
wheels=("$bundle"/wheels/molt-*.whl)
[[ ${#wheels[@]} == 1 ]] || die "expected one molt wheel in $bundle/wheels"
wheel_name=${wheels[0]##*/}
version=${wheel_name#molt-}
version=${version%%-*}
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-z]+[0-9]+)?$ ]] || die "invalid release version: $version"
[[ -x $bundle/bin/molt-kernel && -x $bundle/bin/molt-tui && -s $bundle/molt-kernel.service &&
   -s $bundle/LICENSE && -s $bundle/THIRD-PARTY-LICENSES.txt &&
   -s $bundle/share/molt/README.md ]] || die "incomplete release bundle: $bundle"

bin_dir="$HOME/.local/bin"
unit_dir="$HOME/.config/systemd/user"
unit=molt-kernel.service
state="$HOME/.local/share/molt/kernel"
prefix=${MOLT_INSTALL_PREFIX:-"$HOME/.local/share/molt/releases/$version-linux-x86_64"}
safe_path "$HOME" && safe_path "$prefix" || die "HOME and the install prefix must be absolute paths without spaces or shell or systemd metacharacters"
[[ $prefix == "$(realpath -ms "$prefix")" && ! -L $prefix ]] || die "use a normalized install prefix without a final symlink: $prefix"
prefix=$(realpath -m "$prefix")
safe_path "$prefix" || die "unsupported resolved prefix: $prefix"
for protected in "$state" "$bin_dir" "$unit_dir"; do
  protected=$(realpath -m "$protected")
  [[ $prefix != "$protected" && $prefix != "$protected/"* && $protected != "$prefix/"* ]] ||
    die "install prefix overlaps $protected"
done

# Serialize installers targeting this user's entry points, including custom prefixes.
mkdir -p "$bin_dir" "$unit_dir"
exec {lock}<"$bin_dir"
flock -n "$lock" || die "another molt installer is using $bin_dir"
entries=("$bin_dir/molt" "$bin_dir/molt-kernel" "$unit_dir/$unit")
old=
if exists "${entries[0]}" || exists "${entries[1]}" || exists "${entries[2]}"; then
  [[ -L ${entries[0]} && -L ${entries[1]} && -f ${entries[2]} && ! -L ${entries[2]} ]] ||
    die "conflicting or incomplete entries in $bin_dir and $unit_dir"
  old=$(readlink "${entries[0]}")
  [[ $old == */bin/molt ]] || die "unrecognized molt entry: $old"
  old=${old%/bin/molt}
  # rc4 created the same pair of links and rendered unit but had no receipt.
  safe_path "$old" && [[ $(readlink "${entries[1]}") == "$old/bin/molt-kernel" &&
    -f $old/LICENSE && -f $old/THIRD-PARTY-LICENSES.txt && -x $old/venv/bin/molt &&
    -x $old/bin/molt-kernel ]] && cmp -s "${entries[2]}" <(render_unit "$old") ||
    die "conflicting or inconsistent installed version; inspect $bin_dir and $unit_dir"
  [[ $prefix != "$old/"* && $old != "$prefix/"* ]] || die "install prefixes must not overlap"
fi

if exists "$prefix"; then
  [[ -d $prefix/venv && -x $prefix/venv/bin/molt &&
    -x $prefix/bin/molt && -x $prefix/bin/molt-kernel && -x $prefix/bin/molt-tui &&
    $("$prefix/venv/bin/python" -I -c 'from importlib.metadata import version; print(version("molt"))') == "$version" ]] &&
    cmp -s "$bundle/LICENSE" "$prefix/LICENSE" &&
    cmp -s "$bundle/THIRD-PARTY-LICENSES.txt" "$prefix/THIRD-PARTY-LICENSES.txt" &&
    cmp -s "$prefix/bin/molt" <(render_launcher) &&
    cmp -s "$bundle/bin/molt-kernel" "$prefix/bin/molt-kernel" &&
    cmp -s "$bundle/bin/molt-tui" "$prefix/bin/molt-tui" &&
    cmp -s "${wheels[0]}" "$prefix/wheels/$wheel_name" &&
    diff -qr "$bundle/share/molt" "$prefix/share/molt" >/dev/null &&
    { cmp -s "$prefix/$unit" <(render_unit "$prefix") ||
      { [[ -n $old && $old == "$prefix" ]] &&
        cmp -s "${entries[2]}" <(render_unit "$prefix"); }; } ||
    die "install prefix conflicts with this bundle: $prefix"
  if [[ -n $old && $old == "$prefix" ]]; then
    check_tools "$prefix"
    printf 'molt %s is already installed at %s.\n' "$version" "$prefix"
    exit 0
  fi
else
  mkdir -p "$(dirname "$prefix")"
  stage=$(mktemp -d "$(dirname "$prefix")/.molt-prepare.XXXXXX")
  printf 'Preparing %s in %s\n' "$version" "$stage"
  mkdir -p "$stage/bin" "$stage/share/molt" "$stage/wheels"
  cp -a "$bundle/share/molt/." "$stage/share/molt/"
  install -m 0644 "$bundle/LICENSE" "$stage/LICENSE"
  install -m 0644 "$bundle/THIRD-PARTY-LICENSES.txt" "$stage/THIRD-PARTY-LICENSES.txt"
  render_unit "$prefix" > "$stage/$unit"
  install -m 0755 "$bundle/bin/molt-kernel" "$stage/bin/molt-kernel"
  install -m 0755 "$bundle/bin/molt-tui" "$stage/bin/molt-tui"
  install -m 0644 "${wheels[0]}" "$stage/wheels/$wheel_name"
  uv venv --relocatable --python "${UV_PYTHON:-3.12.13}" "$stage/venv"
  uv pip install --no-index --python "$stage/venv/bin/python" "$stage/wheels/$wheel_name"
  render_launcher > "$stage/bin/molt"
  chmod 0755 "$stage/bin/molt"
  check_tools "$stage"
  printf '%s\n' "$version" > "$stage/.molt-release"
  mv -nT -- "$stage" "$prefix"
  [[ ! -d $stage ]] || die "another installation created $prefix"
fi
check_tools "$prefix"

# Stage replacements next to their destinations for atomic per-file renames.
bin_stage=$(mktemp -d "$bin_dir/.molt-switch.XXXXXX")
unit_stage=$(mktemp -d "$unit_dir/.molt-switch.XXXXXX")
backup="$bin_stage/previous"
mkdir "$backup"
if [[ -n $old ]]; then
  cp -a -- "${entries[@]}" "$backup/"
fi
ln -s "$prefix/bin/molt" "$bin_stage/molt"
ln -s "$prefix/bin/molt-kernel" "$bin_stage/molt-kernel"
cp -- "$prefix/$unit" "$unit_stage/$unit"
staged=("$bin_stage/molt" "$bin_stage/molt-kernel" "$unit_stage/$unit")
printf 'Previous entries saved in %s\n' "$backup"

rollback() {
  local status=$? i entry prior failed=0
  trap - EXIT
  trap '' HUP INT TERM
  for i in "${!entries[@]}"; do
    # A staged file still present means its rename never happened.
    if exists "${staged[i]}"; then
      continue
    fi
    entry=${entries[i]}
    prior="$backup/${entry##*/}"
    if [[ -n $old ]]; then
      cp -a -- "$prior" "${staged[i]}" && mv -Tf -- "${staged[i]}" "$entry" || failed=1
    else
      unlink -- "$entry" || failed=1
    fi
  done
  if ! systemctl --user daemon-reload; then
    failed=1
  fi
  if (( failed )); then
    printf 'Activation failed and restoration was incomplete. Inspect %s and %s; previous entries: %s\n' \
      "$bin_dir" "$unit_dir" "$backup" >&2
  else
    printf 'Activation failed; previous entries restored. Retry with the same bundle.\n' >&2
  fi
  exit "$status"
}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
for i in "${!entries[@]}"; do
  mv -Tf -- "${staged[i]}" "${entries[i]}"
done
systemctl --user daemon-reload
trap - EXIT HUP INT TERM
printf 'Installed molt candidate under %s and reloaded the user unit.\n' "$prefix"
printf 'For a new state directory: %s/bin/molt-kernel init --state %s\n' "$prefix" "$state"
printf 'Enable and start the service with: systemctl --user enable --now %s\n' "$unit"
printf 'Set MOLT_KERNEL_SOCKET=%s/kernel.sock\n' "$state"
printf 'Backup and recovery procedure: %s/share/molt/docs/operations/v2-backup-restore.md\n' "$prefix"
