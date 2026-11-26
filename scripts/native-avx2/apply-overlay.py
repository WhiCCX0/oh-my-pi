#!/usr/bin/env python3
"""Apply the Windows x86-64-v3 experiment overlay to a disposable v19.2.1 checkout."""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from pathlib import Path

EXPECTED_SOURCE = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


def edit_file(root: Path, relative: str, replacements: list[tuple[str, str, str]]) -> tuple[str, str]:
    file = root / relative
    before = file.read_text(encoding="utf-8")
    after = before
    for old, new, label in replacements:
        after = replace_once(after, old, new, f"{relative}: {label}")
    if after == before:
        raise RuntimeError(f"{relative}: overlay made no change")
    file.write_text(after, encoding="utf-8", newline="")
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("modern", "cgu1"))
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    source = args.source.resolve()
    actual = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != EXPECTED_SOURCE:
        raise RuntimeError(f"refusing to patch source {actual}; expected {EXPECTED_SOURCE}")

    if args.mode == "modern":
        changes = {
            "BUILD.bazel": [
                (
                    '    "win32-x64-baseline": ("//bazel/platforms:win32-x64-baseline", "pi_natives.win32-x64-baseline.node"),\n',
                    '    "win32-x64-baseline": ("//bazel/platforms:win32-x64-baseline", "pi_natives.win32-x64-baseline.node"),\n'
                    '    "win32-x64-modern": ("//bazel/platforms:win32-x64-modern", "pi_natives.win32-x64-modern.node"),\n',
                    "register experimental addon target",
                )
            ],
            "bazel/platforms/BUILD.bazel": [
                (
                    'platform(\n'
                    '    name = "win32-x64-baseline",\n'
                    '    constraint_values = [\n'
                    '        "@platforms//os:windows",\n'
                    '        "@platforms//cpu:x86_64",\n'
                    '        "//bazel/variants:baseline",\n'
                    '    ],\n'
                    ')\n',
                    'platform(\n'
                    '    name = "win32-x64-baseline",\n'
                    '    constraint_values = [\n'
                    '        "@platforms//os:windows",\n'
                    '        "@platforms//cpu:x86_64",\n'
                    '        "//bazel/variants:baseline",\n'
                    '    ],\n'
                    ')\n\n'
                    'platform(\n'
                    '    name = "win32-x64-modern",\n'
                    '    constraint_values = [\n'
                    '        "@platforms//os:windows",\n'
                    '        "@platforms//cpu:x86_64",\n'
                    '        "//bazel/variants:modern",\n'
                    '    ],\n'
                    ')\n',
                    "add Windows modern platform",
                )
            ],
            "scripts/bazel-natives.ts": [
                (
                    '\t"win32-x64-baseline": "pi_natives.win32-x64-baseline.node",\n',
                    '\t"win32-x64-baseline": "pi_natives.win32-x64-baseline.node",\n'
                    '\t"win32-x64-modern": "pi_natives.win32-x64-modern.node",\n',
                    "register output filename",
                )
            ],
            "bazel/defs.bzl": [
                (
                    '    if "win32" in str(attr.platform):\n'
                    '        features = features + ["static_link_msvcrt"]\n'
                    '    return {\n',
                    '    if "win32" in str(attr.platform):\n'
                    '        features = features + ["static_link_msvcrt"]\n'
                    '    # Apply the ISA to every target-configuration crate. The\n'
                    '    # crate-level rustc_flags on pi_natives do not reach rlib deps.\n'
                    '    isa_flags = ["-Ctarget-cpu=x86-64-v3"] if "win32-x64-modern" in str(attr.platform) else []\n'
                    '    return {\n',
                    "propagate v3 to all target Rust crates",
                ),
                (
                    '"@rules_rust//rust/settings:extra_rustc_flags": _ADDON_RUSTC_FLAGS + (\n',
                    '"@rules_rust//rust/settings:extra_rustc_flags": _ADDON_RUSTC_FLAGS + isa_flags + (\n',
                    "append v3 to non-exec Rust flags",
                ),
            ],
            "bazel/toolchains/msvc/cc.bzl": [
                (
                    '# Per-target knobs. -msse4.1/-msse4.2 only for x64 (see the wrapper comment).\n',
                    '# Per-target knobs. The experiment overlay replaces x64 baseline flags\n'
                    '# with -march=x86-64-v3 for the modern build.\n',
                    "document x64 ISA overlay",
                ),
                (
                    '"cflags": ["-msse4.1", "-msse4.2"],\n',
                    '"cflags": ["/clang:-march=x86-64-v3"],\n',
                    "set clang-cl C/C++ ISA",
                ),
                (
                    '# -msse4.1/-msse4.2: clang-cl enforces per-function target features (real MSVC\n'
                    '# does not); opus\' silk/x86 SSE4.1 units fail without the feature enabled\n'
                    '# globally. The win32-x64 addon floor is x86-64-v2 (SSE4.2 inclusive), so this\n'
                    '# is safe for every C dep — same flags the cargo-xwin pipeline exported.\n',
                    '# The experiment overlay replaces the upstream x86-64-v2 flags with\n'
                    '# -march=x86-64-v3. The unmodified baseline build keeps -msse4.1/-msse4.2.\n',
                    "document clang-cl wrapper ISA",
                ),
                (
                    '                   -msse4.1 -msse4.2 (win32-x64 baseline floor; clang-cl\n'
                    '                   enforces per-function target features, real MSVC does not),\n',
                    '                   -march=x86-64-v3 (modern experiment overlay; upstream\n'
                    '                   baseline keeps -msse4.1 -msse4.2),\n',
                    "document wrapper invocation",
                ),
            ],
        }
    else:
        defs = (source / "bazel/defs.bzl").read_text(encoding="utf-8")
        if "win32-x64-modern" not in defs or "isa_flags" not in defs:
            raise RuntimeError("apply the modern overlay before the CGU=1 overlay")
        changes = {
            "bazel/defs.bzl": [
                (
                    '_ADDON_RUSTC_FLAGS = ["-Ccodegen-units=16"]\n',
                    '_ADDON_RUSTC_FLAGS = ["-Ccodegen-units=1"]\n',
                    "set CGU=1 for group C",
                )
            ]
        }

    diffs: list[str] = []
    for relative, replacements in changes.items():
        before, after = edit_file(source, relative, replacements)
        diffs.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )

    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text("".join(diffs), encoding="utf-8")
    print(f"Applied {args.mode} overlay to disposable source {actual}")
    print(f"Patch evidence: {args.evidence}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
