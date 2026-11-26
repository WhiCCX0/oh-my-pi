#!/usr/bin/env python3
"""Fail closed unless every Windows target Rustc action carries the requested ISA/CGU flags."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--codegen-units", required=True, type=int, choices=(1, 16))
    args = parser.parse_args()

    output = args.input.read_text(encoding="utf-8", errors="replace")
    sections = re.split(r"(?m)^action '", output)[1:]
    rustc_actions = [
        section
        for section in sections
        if re.search(r"(?m)^\s*Mnemonic: Rustc\s*$", section)
    ]
    cpp_actions = [
        section
        for section in sections
        if re.search(r"(?m)^\s*Mnemonic: CppCompile\s*$", section)
    ]

    # Host-side build scripts/proc-macros use the exec configuration. The
    # non-exec extra_rustc_flags setting intentionally leaves those host tools
    # alone, so inspect only Rustc actions targeting the Windows addon triple.
    windows_actions = [
        section for section in rustc_actions if "x86_64-pc-windows-msvc" in section
    ]
    if len(windows_actions) < 5:
        print(
            f"expected at least 5 Windows-target Rustc actions, found {len(windows_actions)} "
            f"(all Rustc={len(rustc_actions)}, CppCompile={len(cpp_actions)}). "
            "Check aquery --include_commandline/--include_param_files output.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    for index, section in enumerate(windows_actions, start=1):
        heading = section.splitlines()[0] if section.splitlines() else f"Rustc action {index}"
        cpu_flags = re.findall(r"-Ctarget-cpu=([^\s\"']+)", section)
        cgu_flags = re.findall(r"-Ccodegen-units=(\d+)", section)

        if not cpu_flags or cpu_flags[-1] != "x86-64-v3":
            failures.append(f"{heading}: final target-cpu flag is {cpu_flags[-1:] or 'missing'}")
        if "x86-64-v2" in cpu_flags:
            failures.append(f"{heading}: found an unexpected x86-64-v2 target flag")
        # The upstream size-optimized executable is not a library codegen unit.
        # It deliberately pins CGU=1 (also opt-level=z, panic=abort). Keep that
        # upstream exception narrow; the ISA check above still applies to it.
        helper = heading.startswith("Compiling Rust bin oauth_callback_relay (") and "//crates/natives:oauth_callback_relay" in section
        expected_cgu = 1 if helper else args.codegen_units
        if not cgu_flags or int(cgu_flags[-1]) != expected_cgu:
            failures.append(
                f"{heading}: final codegen-units flag is {cgu_flags[-1:] or 'missing'}, "
                f"expected {expected_cgu}"
            )

    if failures:
        print("\n".join(failures[:20]), file=sys.stderr)
        print(
            f"ISA gate failed: {len(failures)} issue(s) across {len(windows_actions)} "
            "Windows-target Rustc actions.",
            file=sys.stderr,
        )
        return 1

    print(
        f"ISA gate passed: {len(windows_actions)} Windows-target Rustc actions "
        f"carry x86-64-v3 and final codegen-units={args.codegen_units}. "
        f"CppCompile actions in the query: {len(cpp_actions)}."
    )
    print(
        "C/C++ build-script compiles are additionally gated by the generated "
        "clang-cl wrapper check in build.sh."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
