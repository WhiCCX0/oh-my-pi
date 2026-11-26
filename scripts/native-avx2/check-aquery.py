#!/usr/bin/env python3
"""Inspect structured Bazel action arguments, not arbitrary text or comments."""
import argparse
import json
import re
import sys
from pathlib import Path

TRIPLE = "x86_64-pc-windows-msvc"


def expanded_args(action):
    params = {p["execPath"]: p["arguments"] for p in action.get("paramFiles", [])}
    seen = set()

    def expand(args, active):
        for arg in args:
            if arg.startswith("@") and arg[1:] in params:
                path = arg[1:]
                if path in active:
                    raise ValueError("Recursive response file: " + path)
                seen.add(path)
                yield from expand(params[path], active | {path})
            elif arg.startswith("@"):
                raise ValueError("Unresolved response file: " + arg)
            else:
                yield arg
    result = list(expand(action.get("arguments", []), set()))
    if set(params) - seen:
        raise ValueError("Unexpanded response file(s): " + str(set(params) - seen))
    return result


def codegen(args, flag):
    output = []
    for i, arg in enumerate(args):
        if arg == "-C" and i + 1 < len(args) and args[i + 1].startswith(flag + "="):
            output.append(args[i + 1].split("=", 1)[1])
        elif arg.startswith("-C" + flag + "="):
            output.append(arg.split("=", 1)[1])
    return output


def inspect(graph, variant, cgu, allow_cargo_only=False):
    labels = {str(t["id"]): t["label"] for t in graph["targets"]}
    configs = {str(c["id"]): c for c in graph.get("configuration", [])}
    errors = []
    rust_count = 0
    cpp_count = 0
    root_count = 0
    for action in graph["actions"]:
        mnemonic = action["mnemonic"]
        if mnemonic not in ("Rustc", "CppCompile"):
            continue
        label = labels.get(str(action["targetId"]), "<unknown>")
        config = configs.get(str(action.get("configurationId")), {})
        if config.get("isTool"):
            continue
        args = expanded_args(action)
        if mnemonic == "CppCompile":
            if not args or not re.search(r"(?:^|/)\+?msvc_cc_repository\+msvc_cc/bin/clang-cl$", args[0]):
                if "win32-x64" in config.get("platformName", ""):
                    errors.append(label + ": unexpected Windows C/C++ compiler")
                continue
            cpp_count += 1
            for arg in args:
                if re.match(r"(?i)(?:/clang:)?(?:-march=|-mcpu=|/arch:)", arg):
                    if variant == "baseline" or arg.lower() not in ("-march=x86-64-v3", "/clang:-march=x86-64-v3"):
                        errors.append(label + ": unexpected C/C++ ISA override " + arg)
            continue
        target = any(arg == "--target=" + TRIPLE or
                     (arg == "--target" and i + 1 < len(args) and args[i + 1] == TRIPLE)
                     for i, arg in enumerate(args))
        if not target:
            continue
        rust_count += 1
        cpu = codegen(args, "target-cpu")
        units = codegen(args, "codegen-units")
        features = codegen(args, "target-feature")
        helper = label.endswith("//crates/natives:oauth_callback_relay")
        root = label.endswith("//crates/natives:pi_natives")
        root_count += int(root)
        if root and codegen(args, "lto")[-1:] != ["thin"]:
            errors.append(label + ": final addon does not explicitly carry ThinLTO")
        expected_units = "1" if helper else str(cgu)
        if not units or units[-1] != expected_units:
            errors.append(f"{label}: CGU {units}, expected {expected_units}")
        if variant == "modern":
            if not cpu or any(v != "x86-64-v3" for v in cpu):
                errors.append(f"{label}: unexpected modern CPU flags {cpu}")
            if any(re.search(r"(?:^|,)-(?:avx2?|bmi2?|fma|f16c|lzcnt|movbe|sse4\.[12])(?:,|$)", f) for f in features):
                errors.append(label + ": required v3 feature explicitly disabled")
        else:
            if any(v not in ("generic", "x86-64", "x86-64-v2") for v in cpu) or (root and (not cpu or cpu[-1] != "x86-64-v2")):
                errors.append(f"{label}: unexpected baseline CPU flags {cpu}")
            if any(re.search(r"(?:^|,)\+(?:avx[^,]*|bmi2?|fma|f16c|lzcnt|movbe)(?:,|$)", f) for f in features):
                errors.append(label + ": v3 target feature enabled in baseline")
    if rust_count < 5 or root_count < 1:
        errors.append(f"Insufficient Windows Rustc coverage: {rust_count}, root actions {root_count}")
    if cpp_count < 1 and not allow_cargo_only:
        errors.append("No identified Windows CppCompile actions")
    return errors, rust_count, cpp_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "modern"), required=True)
    parser.add_argument("--codegen-units", choices=(1, 16), type=int, required=True)
    parser.add_argument("--allow-cargo-only", action="store_true", help="Require separate actual Cargo compiler evidence; do not infer C/C++ coverage from aquery")
    a = parser.parse_args()
    errors, rust, cpp = inspect(json.loads(a.input.read_text()), a.variant, a.codegen_units, a.allow_cargo_only)
    if errors:
        print("\n".join(errors[:30]), file=sys.stderr)
        return 1
    print(f"Structured gate passed: {rust} Windows Rustc and {cpp} identified Windows CppCompile actions.")
    if cpp == 0:
        print("No CppCompile actions: Cargo compiler trace is required for C/C++ evidence.")
    print("Cargo build-script subprocess invocations and runtime dispatch still require separate evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
