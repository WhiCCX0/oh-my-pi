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
# Only the known Bazel-generated wrapper may defer the ISA check. Its next
# successful exec in the same PID must be the pinned LLVM repository driver.
WRAPPER = re.compile(r'(?:^|/)external/(?:[^/]*\+)?msvc_cc/bin/clang-cl$')
LLVM_DRIVER = re.compile(r'(?:^|/)external/(?:[^/]*\+)?llvm_msvc_tools/bin/clang-cl$')
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


# Exact, version-pinned exceptions observed in the A1/B trace of run
# 45851970391. Keep the list narrow: flags on other TUs are not equivalent.
BLAKE3_PROBE = re.compile(r"(?:^|/)external/rules_rust\+\+crate\+crates__blake3-1\.8\.7/_bs\.out_dir/flag_check\.c$")
OPUS_ROOT = r"(?:^|/)external/rules_rust\+\+crate\+crates__opusic-sys-0\.7\.5/"
OPUS_PROBE = re.compile(OPUS_ROOT + r"_bs\.out_dir/build/CMakeFiles/CMakeScratch/TryCompile-[A-Za-z0-9]+/src\.c$")
OPUS_UNIT = re.compile(OPUS_ROOT + r"_bs\.cargo_runfiles/rules_rust\+\+crate\+crates__opusic-sys-0\.7\.5/opus/(celt/x86/pitch_avx\.c|silk/x86/NSQ_del_dec_avx2\.c|silk/float/x86/inner_product_FLP_avx2\.c)$")


def classify_arch_override(args, overrides, ancestry):
    """Recognize only the observed compile-only probes and isolated Opus TUs.

    An exception is evidence of scope, not a proof of final link/dispatch.
    No blanket allowance for AVX512, nor for /arch:AVX2 on other sources.
    """
    if not overrides:
        return None
    if not ancestry or not CARGO.search(ancestry):
        return None
    # The observed compiler command has a single source after "-c --".
    # Refuse to classify an ambiguous or link invocation as a probe/TU.
    if len(args) < 4 or args[-3:-1] != ["-c", "--"]:
        return None
    source = args[-1]
    if ".." in source.split("/") or "\\" in source:
        return None
    outputs = [a[3:] for a in args[:-3] if a.startswith(("-Fo", "/Fo"))]
    if len(outputs) != 1:
        return None
    output = outputs[0]
    if overrides == ["/arch:avx512"] and BLAKE3_PROBE.search(source):
        if output == source.removesuffix(".c"):
            return "blake3-compiler-capability-probe"
    if overrides == ["/arch:avx2"]:
        if OPUS_PROBE.search(source) and "-DAVX2_SUPPORTED" in args:
            if re.fullmatch(r"CMakeFiles/cmTC_[A-Za-z0-9]+\.dir/src\.c\.obj", output):
                return "opus-cmake-capability-probe"
        unit = OPUS_UNIT.search(source)
        if unit and {"-DOPUS_HAVE_RTCD", "-DOPUS_X86_MAY_HAVE_AVX2"} <= set(args):
            if output == "CMakeFiles/opus.dir/" + unit.group(1) + ".obj":
                return "opus-runtime-dispatch-translation-unit"
    return None


def inspect(directory, variant):
    parents, executables, compilers = {}, {}, []
    errors = []
    wrapper_invocations = 0
    wrapper_handoffs = 0
    for file in sorted(directory.glob("exec.*")):
        if not file.name.rsplit(".", 1)[-1].isdigit():
            continue
        pid = int(file.name.rsplit(".", 1)[-1])
        pending_wrapper = None
        with file.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if CHILD.match(line) and (child := RESULT.search(line)):
                    parents[int(child.group(1))] = pid
                result = parse_exec(line)
                if not result:
                    continue
                path, argv = result
                executables.setdefault(pid, []).append(path)
                wrapper = None
                if pending_wrapper is not None:
                    if LLVM_DRIVER.search(path) and argv is not None and windows_target(argv) and "-cc1" not in argv and "-cc1as" not in argv:
                        wrapper = pending_wrapper
                        wrapper_handoffs += 1
                    else:
                        errors.append(f"Wrapper did not exec the expected Windows LLVM driver in PID {pid}: {pending_wrapper} -> {path}")
                    pending_wrapper = None
                if WRAPPER.search(path):
                    wrapper_invocations += 1
                    if argv is None:
                        errors.append("Truncated wrapper argument vector: " + path)
                    pending_wrapper = path
                    continue
                if not COMPILER.search(path.rsplit("/", 1)[-1]):
                    continue
                if argv is None:
                    errors.append("Truncated compiler argument vector: " + path)
                    continue
                if not windows_target(argv) or "-cc1" in argv or "-cc1as" in argv:
                    continue
                compilers.append((pid, path, argv, wrapper))
        if pending_wrapper is not None:
            errors.append(f"Wrapper has no observed LLVM driver handoff in PID {pid}: {pending_wrapper}")

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
    for pid, path, args, wrapper in compilers:
        flags = [arg for arg in args if ARCH.match(arg)]
        special = [arg for arg in args if SPECIAL.match(arg) and arg not in ("-msse4.1", "-msse4.2")]
        response = [arg for arg in args if arg.startswith("@")]
        ancestry = cargo_ancestor(pid)
        # A resolved Windows driver must receive the selected wrapper defaults.
        if variant == "modern":
            defaults = ("-march=x86-64-v3", "/clang:-march=x86-64-v3")
            if not any(arg.lower() in defaults for arg in flags):
                errors.append("Windows modern compiler missing v3 default: " + path)
        else:
            defaults = ("-march=x86-64", "-march=x86-64-v2", "/clang:-march=x86-64", "/clang:-march=x86-64-v2")
            if not all(flag in args for flag in ("-msse4.1", "-msse4.2")):
                errors.append("Windows baseline compiler missing SSE4 defaults: " + path)
        overrides = [arg.lower() for arg in flags if arg.lower() not in defaults]
        scope = classify_arch_override(args, overrides, ancestry)
        if overrides:
            special.extend(overrides)
            if scope is None:
                errors.append(f"Conflicting {variant} global ISA override: {flags!r}; input: {args[-1] if args else '?'}")
        if response:
            errors.append("Unexpanded Windows compiler response file: " + repr(response))
        records.append({"pid": pid, "compiler": path, "wrapper": wrapper, "argv": args, "cargo_ancestor": ancestry,
                        "specialized_isa_flags": sorted(set(special)), "isa_scope": scope, "response_files": response})
    cargo_count = sum(bool(record["cargo_ancestor"]) for record in records)
    if not records:
        errors.append("No actual Windows clang driver invocations observed")
    if not cargo_count:
        errors.append("No actual Windows compiler invocation descended from an identified Cargo build script")
    return {"variant": variant, "compiler_invocations": len(records), "cargo_descendants": cargo_count,
            "wrapper_invocations": wrapper_invocations, "wrapper_handoffs": wrapper_handoffs,
            "specialized_invocations": sum(bool(record["specialized_isa_flags"]) for record in records),
            "classified_isa_scopes": {scope: sum(record["isa_scope"] == scope for record in records)
                                      for scope in sorted({record["isa_scope"] for record in records if record["isa_scope"]})},
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
