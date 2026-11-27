pkgname=molt
pkgver=1.2.0rc10+git3e7d01b
pkgrel=1
pkgdesc='Agent workflow state transactions, versioning, and evidence audit'
arch=('x86_64')
url='https://github.com/FeiSaMa/molt'
license=('MIT')
depends=('python' 'glibc>=2.43')
makedepends=('cargo' 'uv' 'python' 'unzip')
options=(!lto)
_commit=3e7d01bb1ea32f307fbb109553f7bf9b968861b6
source=("molt-${pkgver}.tar.gz::https://github.com/FeiSaMa/molt/archive/${_commit}.tar.gz")
sha256sums=('8b76c05bf6cadb13f0d4a615b375a7caadf71b0d65bdc1cf2a4059068c64a046')

build() {
  cd "$srcdir/molt-${_commit}"
  export PYTHONDONTWRITEBYTECODE=1
  uv lock --check --python 3.12.13
  uv build --python 3.12.13 --require-hashes \
    --build-constraints packaging/release/build-requirements.txt --out-dir "$srcdir/dist"
  export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }-C link-arg=-fuse-ld=bfd"
  cargo build --locked --release -p molt-kernel -p molt-tui
  uv run --no-project --python 3.12.13 python packaging/release/rust-notices.py --check
}

package() {
  cd "$srcdir/molt-${_commit}"
  install -d "$pkgdir/usr/bin" "$pkgdir/usr/lib/systemd/user"
  cat >"$pkgdir/usr/bin/molt" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
  export PYTHONPATH=/usr/lib/molt/molt-python.whl${PYTHONPATH:+:$PYTHONPATH}
exec /usr/bin/python3 -c 'from molt.cli import main; main()' "$@"
EOF
  chmod 755 "$pkgdir/usr/bin/molt"
  install -Dm755 target/release/molt-kernel "$pkgdir/usr/bin/molt-kernel"
  install -Dm755 target/release/molt-tui "$pkgdir/usr/bin/molt-tui"
  install -d "$pkgdir/usr/lib/molt"
  install -m644 "$srcdir"/dist/molt-*.whl "$pkgdir/usr/lib/molt/molt-python.whl"
  cat >"$pkgdir/usr/lib/systemd/user/molt-kernel.service" <<'EOF'
[Unit]
Description=Molt transaction kernel
After=default.target

[Service]
Type=simple
ExecStart=/usr/bin/molt-kernel serve --state %h/.local/share/molt/kernel
ExecStartPost=/usr/bin/molt-kernel ping --socket %h/.local/share/molt/kernel/kernel.sock
Restart=on-failure
RestartSec=2
UMask=0077

[Install]
WantedBy=default.target
EOF
  chmod 644 "$pkgdir/usr/lib/systemd/user/molt-kernel.service"
  install -Dm644 packaging/release/README.md "$pkgdir/usr/share/doc/molt/README.md"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/molt/LICENSE"
  install -Dm644 packaging/release/THIRD-PARTY-LICENSES.txt \
    "$pkgdir/usr/share/licenses/molt/THIRD-PARTY-LICENSES.txt"
}
