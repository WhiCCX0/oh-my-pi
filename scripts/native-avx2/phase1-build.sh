#!/usr/bin/env bash
set -euo pipefail
root="$PWD"
source="$root/upstream-source"
audit="$root/phase1-audit"
native="$root/phase1-native"
group="$GROUP"
python3 scripts/native-avx2/phase1-overlay.py --source "$source" --group "$group" --audit "$audit"
cpu=$(jq -er .cpu "$audit/configuration.json")
cgu=$(jq -er .cgu "$audit/configuration.json")
lto=$(jq -er .lto "$audit/configuration.json")
llvm=$(jq -er .llvm "$audit/configuration.json")
jobs=2
if [[ "$lto" == fat ]]; then jobs=1; fi
mkdir "$native"
trace=$(mktemp -d)
trap 'rm -rf -- "$trace"' EXIT
rc="$RUNNER_TEMP/phase1.bazelrc"
printf 'startup --batch\ncommon --disk_cache=\ncommon --remote_cache=\n' > "$rc"
export OMP_BAZEL_RC="$rc"
(
  cd "$source"
  bazelisk --bazelrc="$rc" aquery --output=jsonproto --include_commandline --include_param_files \
    'mnemonic("Rustc|CppCompile", deps(//:natives-win32-x64-modern))'
) > "$audit/aquery.json"
python3 scripts/native-avx2/check-aquery.py --input "$audit/aquery.json" --variant modern --codegen-units "$cgu" --modern-cpu "$cpu" --lto "$lto" --allow-cargo-only
output_base=$(cd "$source" && bazelisk --bazelrc="$rc" info output_base)
wrapper="$output_base/external/+msvc_cc_repository+msvc_cc/bin/clang-cl"
test -s "$wrapper"
cp "$wrapper" "$audit/clang-cl-wrapper.sh"
grep -F -- "/clang:-march=$cpu" "$wrapper"
shopt -s nullglob
drivers=("$output_base"/external/*llvm_msvc_tools/bin/clang-cl)
test "${#drivers[@]}" -eq 1
driver="${drivers[0]}"
test -x "$driver"
"$driver" --version > "$audit/clang-version.txt"
grep -F "clang version $llvm" "$audit/clang-version.txt"
"$(dirname "$driver")/lld-link" --version > "$audit/lld-version.txt"
grep -F "$llvm" "$audit/lld-version.txt"
(
  cd "$source"
  bazelisk --bazelrc="$rc" clean
  /usr/bin/time -v -o "$audit/build-resources.txt" \
    strace -ff -qq -s 65536 -e trace=execve,execveat,clone,clone3,fork,vfork -o "$trace/exec" \
    bun scripts/bazel-natives.ts win32-x64-modern --dest "$native" -- --jobs="$jobs" --spawn_strategy=local --remote_accept_cached=false
)
python3 scripts/native-avx2/check-compiler-trace.py --input-dir "$trace" --variant modern --modern-cpu "$cpu" --output "$audit/compiler-trace.json"
file="$native/pi_natives.win32-x64-modern.node"
test -s "$file"
grep -aFq "PI_NATIVES_VERSION_STAMP:19.2.1" "$file"
# Resolve the exact target compiler after the build has materialized toolchain files.
execroot=$(cd "$source" && bazelisk --bazelrc="$rc" info execution_root)
python3 - "$audit/aquery.json" "$execroot" "$audit/rustc-version.txt" <<'PY'
import json, pathlib, subprocess, sys
g = json.load(open(sys.argv[1]))
paths = {a for x in g["actions"] if x["mnemonic"] == "Rustc" for a in x.get("arguments", []) if a.endswith("/bin/rustc") and "x86_64-pc-windows-msvc__nightly_tools/" in a}
if len(paths) != 1:
    raise ValueError("Unexpected target Rustc paths: " + repr(paths))
candidate = pathlib.Path(sys.argv[2])/paths.pop()
if not candidate.is_file():
    raise ValueError("Target Rustc not materialized: " + str(candidate))
pathlib.Path(sys.argv[3]).write_text(subprocess.check_output([str(candidate), "-vV"], text=True))
PY
objdump -p "$file" > "$audit/pe-imports.txt"
if grep -Ei 'DLL Name: (vcruntime[^ ]*\.dll|msvcp[^ ]*\.dll|api-ms-win-crt-[^ ]*\.dll|ucrtbase\.dll)' "$audit/pe-imports.txt"; then
  echo "Unexpected dynamic CRT import" >&2
  exit 1
fi
OMP_SOURCE_DIR="$source" bun scripts/native-avx2/verify-embed.ts "$native" modern > "$audit/embed.json"
# Release the build tree before packaging; no shared cache is used.
(cd "$source" && bazelisk --bazelrc="$rc" clean --expunge)
