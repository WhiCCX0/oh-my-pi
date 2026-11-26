#!/usr/bin/env python3
"""Audit actual Windows compiler execs, including Cargo build-script descendants.

The trace is execution evidence, not a proof of all runtime-dispatch paths.
Only compiler argv is exported; the raw system-call trace stays temporary.
"""
import argparse
import ast
import json
import re
from pathlib import Path

TRIPLE = "x86_64-pc-windows-msvc"
QUOTED = r'"(?:[^"\\]|\\.)*"'
EXEC = re.compile(r'^(?:execve\(|execveat\([^,]+,\s*)(' + QUOTED + r'),\s*')
ARGV = re.compile(r'\[\s*(?:' + QUOTED + r'(?:,\s*' + QUOTED + r')*)?\s*\]')
CHILD = re.compile(r'^(?:clone3?|fork|vfork)\(|^<\.\.\. (?:clone3?|fork|vfork) resumed>')
RESULT = re.compile(r'\)\s+=\s+(\d+)\s*$')
CARGO = re.compile(r'(?:cargo[_-]build[_-]script|build[_-]script[_-](?:runner|build)|[/\\]_bs(?:[/\\]|$))', re.I)
COMPILER = re.compile(r'clang(?:-cl|\+\+|-\d+)?(?:\.exe)?$', re.I)
ARCH = re.compile(r'^(?:/clang:)?(?:-march=|-mcpu=|/arch:)', re.I)
SPECIAL = re.compile(r'^(?:/clang:)?-m(?:no-)?(?:avx|bmi|fma|f16c|lzcnt|movbe|sse)', re.I)


def parse_exec(line):
    match = EXEC.match(line)
    if not match or not re.search(r'\)\s+=\s+0\s*$', line):
        return None
    path = ast.literal_eval(match.group(1))
    argv = ARGV.match(line, match.end())
    return path, ast.literal_eval(argv.group()) if argv else None


def windows_target(args):
    for i, arg in enumerate(args):
        if arg in ("--target=" + TRIPLE, "-target=" + TRIPLE):
            return True
        if arg in ("--target", "-target", "-triple") and i + 1 < len(args) and args[i + 1] == TRIPLE:
            return True
    return False


def inspect(directory, variant):
    parents, executables, compilers = {}, {}, []
    errors = []
    for file in sorted(directory.glob("exec.*")):
        if not file.name.rsplit(".", 1)[-1].isdigit():
            continue
        pid = int(file.name.rsplit(".", 1)[-1])
        with file.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if CHILD.match(line) and (child := RESULT.search(line)):
                    parents[int(child.group(1))] = pid
                result = parse_exec(line)
                if not result:
                    continue
                path, argv = result
                executables.setdefault(pid, []).append(path)
                if not COMPILER.search(path.rsplit("/", 1)[-1]):
                    continue
                if argv is None:
                    errors.append("Truncated compiler argument vector: " + path)
                    continue
                if not windows_target(argv) or "-cc1" in argv or "-cc1as" in argv:
                    continue
                compilers.append((pid, path, argv))

    def cargo_ancestor(pid):
        seen = set()
        while pid not in seen:
            seen.add(pid)
            for path in executables.get(pid, []):
                if CARGO.search(path):
                    return path
            if pid not in parents:
                break
            pid = parents[pid]
        return None

    records = []
    for pid, path, args in compilers:
        flags = [arg for arg in args if ARCH.match(arg)]
        special = [arg for arg in args if SPECIAL.match(arg) and arg not in ("-msse4.1", "-msse4.2")]
        response = [arg for arg in args if arg.startswith("@")]
        ancestry = cargo_ancestor(pid)
        # A resolved Windows driver must receive the selected wrapper defaults.
        if variant == "modern":
            if not any(arg.lower() in ("-march=x86-64-v3", "/clang:-march=x86-64-v3") for arg in flags):
                errors.append("Windows modern compiler missing v3 default: " + path)
            if any(arg.lower() not in ("-march=x86-64-v3", "/clang:-march=x86-64-v3") for arg in flags):
                errors.append("Conflicting modern global ISA override: " + repr(flags))
        else:
            if not all(flag in args for flag in ("-msse4.1", "-msse4.2")):
                errors.append("Windows baseline compiler missing SSE4 defaults: " + path)
            if any(arg.lower() not in ("-march=x86-64", "-march=x86-64-v2", "/clang:-march=x86-64", "/clang:-march=x86-64-v2") for arg in flags):
                # A per-TU specialization can be legitimate, but needs dispatch review.
                special.extend(flags)
        if response:
            errors.append("Unexpanded Windows compiler response file: " + repr(response))
        records.append({"pid": pid, "compiler": path, "argv": args, "cargo_ancestor": ancestry,
                        "specialized_isa_flags": sorted(set(special)), "response_files": response})
    cargo_count = sum(bool(record["cargo_ancestor"]) for record in records)
    if not records:
        errors.append("No actual Windows clang driver invocations observed")
    if not cargo_count:
        errors.append("No actual Windows compiler invocation descended from an identified Cargo build script")
    return {"variant": variant, "compiler_invocations": len(records), "cargo_descendants": cargo_count,
            "specialized_invocations": sum(bool(record["specialized_isa_flags"]) for record in records),
            "records": records, "errors": list(dict.fromkeys(errors))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "modern"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = inspect(args.input_dir, args.variant)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in evidence.items() if key != "records"}, indent=2))
    if evidence["errors"]:
        return 1
    print("Observed compiler execution gate passed; specialized ISA flags still require runtime-dispatch review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
