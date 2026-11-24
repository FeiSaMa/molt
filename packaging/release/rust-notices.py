"""Reproduce the bundle's Rust notices from Cargo.lock and downloaded crate archives."""

import argparse
import hashlib
import os
import re
import subprocess
import tarfile
import tomllib
from collections import defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name("THIRD-PARTY-LICENSES.txt")
TARGET = "x86_64-unknown-linux-gnu"
BINARIES = ("molt-kernel", "molt-tui")
NOTICE_FILE = re.compile(
    r"(?:unlicen[cs]e|licen[cs]e|copying|copyright|notice|authors|patents)(?:$|[-_.])", re.I
)
SOURCE_FILES = {".rs", ".c", ".h", ".cpp", ".cc", ".s", ".asm", ".pl", ".inc"}
COPYRIGHT = re.compile(
    rb"^[ \t/*#!@;]*(Copyright[ \t]+(?:\([cC]\)[ \t]+)?[0-9]{4}[^\r\n]*)", re.M | re.I
)


def dependency_graph():
    owners = defaultdict(list)
    for binary in BINARIES:
        result = subprocess.check_output(
            [
                "cargo",
                "tree",
                "--locked",
                "--offline",
                "-p",
                binary,
                "--target",
                TARGET,
                "--edges",
                "normal",
                "--prefix",
                "none",
                "--format",
                "{p}",
            ],
            cwd=ROOT,
            text=True,
        )
        packages = {tuple(line.split()[:2]) for line in result.splitlines()}
        for name, version in packages:
            if name != binary:
                owners[name, version.removeprefix("v")].append(binary)
    return owners


def archive_notices(archive, package):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != package["checksum"]:
        raise ValueError(f"{archive}: SHA-256 differs from Cargo.lock")
    manifest = None
    files = {}
    holders = set()
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            path = PurePosixPath(member.name).relative_to(f"{package['name']}-{package['version']}")
            if path == PurePosixPath("Cargo.toml"):
                manifest = tomllib.loads(tar.extractfile(member).read().decode())["package"]
            elif NOTICE_FILE.match(path.name):
                raw = tar.extractfile(member).read().decode("utf-8-sig")
                files[str(path)] = "\n".join(line.rstrip() for line in raw.splitlines()).strip("\n")
            elif path.suffix.lower() in SOURCE_FILES:
                for match in COPYRIGHT.finditer(tar.extractfile(member).read()):
                    holders.add(match[1].decode().removesuffix("*/").strip())
    if not files or not manifest or not manifest.get("license"):
        raise ValueError(f"{archive}: missing license declaration or notice files")
    return manifest["license"], files, sorted(holders)


def render():
    owners = dependency_graph()
    locked = tomllib.loads((ROOT / "Cargo.lock").read_text())["package"]
    packages = {(p["name"], p["version"]): p for p in locked}
    cache = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "registry/cache"
    lines = [
        "Third-party notices for molt-kernel and molt-tui on x86_64-unknown-linux-gnu",
        "",
        "The index covers Cargo's locked normal dependency graphs with default features.",
        "Each entry identifies its binary, upstream license declaration, notice files",
        "and copyright statements found in source headers. The numbered texts follow",
        "the index. Identical texts are printed once. Line endings and trailing spaces",
        "are normalized. Component-specific terms are preserved in upstream notices.",
        "",
    ]
    texts = {}
    for (name, version), binaries in sorted(owners.items()):
        filename = f"{name}-{version}.crate"
        archive = next(iter(sorted(cache.glob(f"*/{filename}"))), None)
        if archive is None:
            raise FileNotFoundError(f"{filename} missing in Cargo registry cache {cache}")
        license_id, files, holders = archive_notices(archive, packages[name, version])
        lines.extend(
            [f"## {name} v{version}", f"Used by: {', '.join(binaries)}", f"License: {license_id}"]
        )
        for path, body in sorted(files.items()):
            index = texts.setdefault(body, len(texts) + 1)
            lines.append(f"{path} — text {index}")
        lines.extend(holders)
        lines.append("")
    for body, index in texts.items():
        lines.extend([f"=== Text {index} ===", "", body, ""])
    return "\n".join(lines).encode(), len(owners)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare against the shipped notices")
    mode.add_argument("--write", action="store_true", help="regenerate the shipped notices")
    args = parser.parse_args()
    notices, count = render()
    if args.write:
        OUTPUT.write_bytes(notices)
    elif OUTPUT.read_bytes() != notices:
        raise SystemExit(f"{OUTPUT} differs from locked dependencies. Regenerate with --write.")
    print(f"{count} locked packages, {TARGET}, SHA-256 {hashlib.sha256(notices).hexdigest()}")


if __name__ == "__main__":
    main()
