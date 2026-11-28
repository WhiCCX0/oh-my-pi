#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$ROOT/upstream-source"
AUDIT="$ROOT/phase2-audit"
NATIVE="$ROOT/phase2-native"

fail() { echo "::error::$*" >&2; exit 1; }
for command in bun bazelisk python3 jq strace sha256sum objdump; do
  command -v "$command" >/dev/null || fail "missing required command: $command"
done
test -d "$SOURCE/.git" || fail "upstream source checkout missing"
test -n "${RUNNER_TEMP:-}" || fail "RUNNER_TEMP must be set"
test -n "${GITHUB_SHA:-}" || fail "GITHUB_SHA must be set"
test ! -e "$AUDIT" && test ! -e "$NATIVE" || fail "Phase 2 output already exists"

python3 "$ROOT/scripts/native-avx2/phase2-overlay.py" --source "$SOURCE" --audit "$AUDIT"
config="$AUDIT/configuration.json"
jq -e '.cpu == "baseline" and .target == "win32-x64-baseline" and .lto == "fat" and .cgu == 16 and .llvm == "20.1.7"' "$config"
mkdir "$NATIVE"
trace="$(mktemp -d)"
trap 'rm -rf -- "$trace"' EXIT
rc="$RUNNER_TEMP/phase2.bazelrc"
printf 'startup --batch\ncommon --disk_cache=\ncommon --remote_cache=\n' > "$rc"
export OMP_BAZEL_RC="$rc"

(
  cd "$SOURCE"
  bazelisk --bazelrc="$rc" aquery --output=jsonproto --include_commandline --include_param_files \
    'mnemonic("Rustc|CppCompile", deps(//:natives-win32-x64-baseline))'
) > "$AUDIT/aquery.json"
python3 "$ROOT/scripts/native-avx2/check-aquery.py" \
  --input "$AUDIT/aquery.json" --variant baseline --codegen-units 16 --lto fat --allow-cargo-only

output_base="$(cd "$SOURCE" && bazelisk --bazelrc="$rc" info output_base)"
wrapper="$output_base/external/+msvc_cc_repository+msvc_cc/bin/clang-cl"
test -s "$wrapper" || fail "generated baseline clang-cl wrapper missing"
grep -F -- '-msse4.1' "$wrapper" >/dev/null || fail "baseline wrapper missing SSE4.1 floor"
grep -F -- '-msse4.2' "$wrapper" >/dev/null || fail "baseline wrapper missing SSE4.2 floor"
if grep -E -- 'march=x86-64-v3|march=raptorlake|/arch:AVX2|/arch:AVX512' "$wrapper"; then
  fail "baseline wrapper unexpectedly contains a modern ISA override"
fi
cp "$wrapper" "$AUDIT/clang-cl-baseline-wrapper.sh"

shopt -s nullglob
drivers=("$output_base"/external/*llvm_msvc_tools/bin/clang-cl)
test "${#drivers[@]}" -eq 1 || fail "expected exactly one pinned Windows LLVM clang-cl driver"
driver="${drivers[0]}"
"$driver" --version > "$AUDIT/clang-version.txt"
grep -F 'clang version 20.1.7' "$AUDIT/clang-version.txt" >/dev/null || fail "unexpected external clang version"
"$(dirname "$driver")/lld-link" --version > "$AUDIT/lld-version.txt"
grep -F 'LLD 20.1.7' "$AUDIT/lld-version.txt" >/dev/null || fail "unexpected external lld version"

jobs=1
(
  cd "$SOURCE"
  bazelisk --bazelrc="$rc" clean
  /usr/bin/time -v -o "$AUDIT/build-resources.txt" \
    strace -ff -qq -s 65536 -e trace=execve,execveat,clone,clone3,fork,vfork -o "$trace/exec" \
    bun scripts/bazel-natives.ts win32-x64-baseline --dest "$NATIVE" -- \
      --jobs="$jobs" --spawn_strategy=local --remote_accept_cached=false
)
python3 "$ROOT/scripts/native-avx2/check-compiler-trace.py" \
  --input-dir "$trace" --variant baseline --output "$AUDIT/compiler-trace.json"

file="$NATIVE/pi_natives.win32-x64-baseline.node"
test -s "$file" || fail "H0 native addon missing"
grep -aFq 'PI_NATIVES_VERSION_STAMP:19.2.1' "$file" || fail "H0 addon version stamp missing"

execroot="$(cd "$SOURCE" && bazelisk --bazelrc="$rc" info execution_root)"
python3 - "$AUDIT/aquery.json" "$execroot" "$AUDIT/rustc-version.txt" <<'PY'
import json, pathlib, subprocess, sys
q = json.loads(pathlib.Path(sys.argv[1]).read_text())
paths = {a for action in q["actions"] if action["mnemonic"] == "Rustc"
         for a in action.get("arguments", [])
         if a.endswith("/bin/rustc") and "x86_64-pc-windows-msvc__nightly_tools/" in a}
if len(paths) != 1:
    raise ValueError("Expected exactly one target Rustc toolchain path: " + repr(paths))
rustc = pathlib.Path(sys.argv[2]) / paths.pop()
if not rustc.is_file():
    raise ValueError("Target Rustc was not materialized: " + str(rustc))
pathlib.Path(sys.argv[3]).write_text(subprocess.check_output([str(rustc), "-vV"], text=True))
PY

grep -F 'rustc 1.102.0-nightly' "$AUDIT/rustc-version.txt" >/dev/null || fail "H0 Rustc version differs from Phase 1 toolchain"
grep -F 'commit-hash: f5cedb83a788ea9581097abc36a2a8503a7f541c' "$AUDIT/rustc-version.txt" >/dev/null || fail "H0 Rustc commit differs from Phase 1 toolchain"
grep -F 'LLVM version: 23.1.5' "$AUDIT/rustc-version.txt" >/dev/null || fail "H0 Rust internal LLVM differs from Phase 1 toolchain"

objdump -p "$file" > "$AUDIT/pe-imports.txt"
if grep -Ei 'DLL Name: (vcruntime[^ ]*\.dll|msvcp[^ ]*\.dll|api-ms-win-crt-[^ ]*\.dll|ucrtbase\.dll)' "$AUDIT/pe-imports.txt"; then
  fail "H0 addon unexpectedly imports the dynamic MSVC CRT"
fi
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$NATIVE" baseline > "$AUDIT/embed.json"

(cd "$SOURCE" && bazelisk --bazelrc="$rc" clean --expunge)

python3 - "$SOURCE" "$AUDIT" "$NATIVE" <<'PY'
import hashlib, json, os, pathlib, subprocess, sys
source, audit, native = map(pathlib.Path, sys.argv[1:])
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
config = json.loads((audit / "configuration.json").read_text())
if config != {"cpu":"baseline","target":"win32-x64-baseline","lto":"fat","cgu":16,"llvm":"20.1.7"}:
    raise ValueError("Unexpected H0 configuration")
trace = json.loads((audit / "compiler-trace.json").read_text())
if trace["errors"] or trace["compiler_invocations"] < 1 or trace["variant"] != "baseline":
    raise ValueError("Actual compiler trace failed")
manifest = {
  "schema": 1,
  "source": {"repository":"can1357/oh-my-pi","ref":"v19.2.1","commit":"b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac","version":"19.2.1"},
  "experiment": {
    "repository":"WhiCCX0/oh-my-pi","branch":"phase2/h0-baseline-fat-lto","workflow_commit":os.environ["GITHUB_SHA"],
    "localization_commit":"ac1616560eb7d04d2a58afe97dcec88f310a1ad8","target":"win32-x64",
    "configuration":config,
    "toolchain": {
      "bun":subprocess.check_output(["bun","--version"],text=True).strip(),
      "bazelisk":subprocess.check_output(["bazelisk","--version"],text=True).strip(),
      "rustc":(audit/"rustc-version.txt").read_text().strip(),
      "clang":(audit/"clang-version.txt").read_text().strip(),
      "lld":(audit/"lld-version.txt").read_text().strip()
    },
    "trace_enabled":True,
    "evidence":{p.name:sha(p) for p in sorted(audit.iterdir()) if p.is_file()},
    "groups":{"H0":{"file":"pi_natives.win32-x64-baseline.node","sha256":sha(native/"pi_natives.win32-x64-baseline.node"),
                       "size_bytes":(native/"pi_natives.win32-x64-baseline.node").stat().st_size}}
  }
}
(audit/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
(native/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps({"H0":manifest["experiment"]["groups"]["H0"],"toolchain":{k:manifest["experiment"]["toolchain"][k] for k in ("bun","bazelisk")}},indent=2))
PY

echo "H0 native build and evidence complete."
