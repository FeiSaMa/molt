#!/bin/sh
# Pi bash 工具的隔离壳：唯一被 `.pi/settings.json` 的 shellPath 指向的可执行文件。
# 主进程（Pi）保留 provider 网络；本 shell 的子进程无网络、无 proxy，且看不到仓库。
set -eu

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy || true

WORKDIR="${WORKDIR:?WORKDIR required}"
LOG="${MOLT_SHELL_LOG:-}"
if [ -n "$LOG" ]; then
  mkdir -p "$(dirname "$LOG")"
  printf '%s\n' "$*" >> "$LOG"
fi

exec bwrap \
  --unshare-net \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  --ro-bind /lib64 /lib64 \
  --ro-bind /etc /etc \
  --tmpfs /home \
  --tmpfs /root \
  --tmpfs /tmp \
  --bind "$WORKDIR" "$WORKDIR" \
  --chdir "$WORKDIR" \
  --dev /dev \
  --proc /proc \
  /bin/bash "$@"
