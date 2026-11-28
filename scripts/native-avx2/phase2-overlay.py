#!/usr/bin/env python3
"""Apply the single-variable Phase 2 H0 overlay to the pinned upstream tree."""
from __future__ import annotations

import argparse
import difflib
import json
import platform
import subprocess
from pathlib import Path

UPSTREAM = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"
LOCALIZATION = "ac1616560eb7d04d2a58afe97dcec88f310a1ad8"
GROUP = "H0"
CONFIG = {
    "cpu": "baseline",
    "target": "win32-x64-baseline",
    "lto": "fat",
    "cgu": 16,
    "llvm": "20.1.7",
}
OLD = '        "@rules_rust//rust/settings:lto": "thin",\n'
NEW = '        "@rules_rust//rust/settings:lto": "fat",\n'


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one anchor, found {count}")
    return source.replace(old, new, 1)


def patched_defs(source: str) -> str:
    return replace_once(source, OLD, NEW, "bazel/defs.bzl Fat LTO transition")


def verify_upstream_layout(root: Path) -> None:
    defs = (root / "bazel/defs.bzl").read_text(encoding="utf-8")
    build = (root / "crates/natives/BUILD.bazel").read_text(encoding="utf-8")
    platforms = (root / "bazel/platforms/BUILD.bazel").read_text(encoding="utf-8")
    if OLD not in defs:
        raise ValueError("Pinned upstream no longer has the expected Thin LTO transition")
    if '"//bazel/variants:baseline": ["-Ctarget-cpu=x86-64-v2"]' not in build:
        raise ValueError("Baseline Rust target anchor is missing")
    if 'name = "win32-x64-baseline"' not in platforms:
        raise ValueError("Baseline Windows platform target is missing")
    if '"win32-x64-baseline"' not in (root / "BUILD.bazel").read_text(encoding="utf-8"):
        raise ValueError("Baseline addon target is missing")


def apply(source: Path, audit: Path) -> None:
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise ValueError("H0 overlay is supported only on the pinned Linux x64 build host")
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != UPSTREAM:
        raise ValueError(f"Wrong upstream source SHA {actual}; expected {UPSTREAM}")
    status = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"], text=True
    ).strip()
    if status or (source / ".bazelrc.user").exists() or (source / ".bazelrc.user").is_symlink():
        raise ValueError("Upstream source must be a clean checkout without local Bazel overrides")
    verify_upstream_layout(source)
    audit.mkdir(parents=True, exist_ok=False)

    file = source / "bazel/defs.bzl"
    before = file.read_text(encoding="utf-8")
    after = patched_defs(before)
    patch = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/bazel/defs.bzl", tofile="b/bazel/defs.bzl"))
    subprocess.run(["git", "-C", str(source), "apply", "--check", "-"], input=patch, text=True, check=True)
    (audit / "h0-overlay.patch").write_text(patch, encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "apply", "-"], input=patch, text=True, check=True)
    subprocess.run(["git", "-C", str(source), "diff", "--check"], check=True)
    (audit / "configuration.json").write_text(json.dumps(CONFIG, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    apply(args.source.resolve(), args.audit.resolve())
