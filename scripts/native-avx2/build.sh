#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="${OMP_SOURCE_DIR:?OMP_SOURCE_DIR must point to the upstream v19.2.1 checkout}"
OMP_BAZEL_RC="${OMP_BAZEL_RC:?OMP_BAZEL_RC must point to the repo Bazel cache rc}"
ARTIFACTS="${OMP_NATIVE_AVX2_ARTIFACTS_DIR:-$ROOT/native-avx2-artifacts}"
VERSION="19.2.1"
EXPECTED_SOURCE_SHA="b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"

fail() {
  echo "::error::$*" >&2
  exit 1
}

command -v bun >/dev/null || fail "bun is not on PATH"
command -v bazelisk >/dev/null || fail "bazelisk is not on PATH"
command -v npm >/dev/null || fail "npm is not on PATH"
command -v python3 >/dev/null || fail "python3 is not on PATH"
command -v sha256sum >/dev/null || fail "sha256sum is not on PATH"
test -f "$OMP_BAZEL_RC" || fail "Bazel cache rc not found: $OMP_BAZEL_RC"
test -d "$SOURCE/.git" || fail "upstream source is not a git checkout: $SOURCE"

SOURCE_SHA="$(git -C "$SOURCE" rev-parse HEAD)"
[[ "$SOURCE_SHA" == "$EXPECTED_SOURCE_SHA" ]] || fail "wrong upstream source SHA: $SOURCE_SHA"
SOURCE_VERSION="$(node -p 'require(process.argv[1]).version' "$SOURCE/packages/natives/package.json")"
[[ "$SOURCE_VERSION" == "$VERSION" ]] || fail "wrong natives package version: $SOURCE_VERSION"

git -C "$SOURCE" diff --quiet && git -C "$SOURCE" diff --cached --quiet || fail "Upstream source has tracked modifications; use a fresh checkout"
[[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all)" && ! -e "$SOURCE/.bazelrc.user" && ! -L "$SOURCE/.bazelrc.user" ]] || fail "Unexpected untracked source or local Bazel override"
ARTIFACTS="$(realpath -m -- "$ARTIFACTS")"
[[ "$ARTIFACTS" == "$ROOT/native-avx2-artifacts" ]] || fail "Refusing unexpected output directory: $ARTIFACTS"
[[ ! -e "$ARTIFACTS" && ! -L "$ARTIFACTS" ]] || fail "Output already exists; move it aside explicitly: $ARTIFACTS"
mkdir -- "$ARTIFACTS"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TRACE="${OMP_NATIVE_TRACE:-false}"
case "$TRACE" in
  true|1)
    command -v strace >/dev/null || fail "strace is required in compiler evidence mode"
    trace_rc="$TMP/trace.bazelrc"
    cp "$OMP_BAZEL_RC" "$trace_rc"
    # Batch mode makes the Bazel server a child of strace. Disable shared
    # caches so the traced build actually executes compiler subprocesses.
    printf '\nstartup --batch\ncommon --disk_cache=\ncommon --remote_cache=\n' >> "$trace_rc"
    OMP_BAZEL_RC="$trace_rc"
    TRACE=true
    ;;
  false|0) TRACE=false ;;
  *) fail "OMP_NATIVE_TRACE must be true/false or 1/0" ;;
esac

echo "=== A0: official npm baseline ==="
mkdir -p "$ARTIFACTS/A0-official-baseline"
VERIFY_SCRIPT="${OMP_NATIVE_VERIFY_SCRIPT:?Set OMP_NATIVE_VERIFY_SCRIPT to the pinned formal release verifier}"
test -f "$VERIFY_SCRIPT" || fail "Pinned native provenance verifier not found"
(
  cd "$TMP"
  export npm_config_registry=https://registry.npmjs.org
  export npm_config_userconfig="$TMP/.npmrc"
  export npm_config_globalconfig="$TMP/global.npmrc"
  node -e '
    const fs = require("node:fs");
    const name = "@oh-my-pi/pi-natives-win32-x64";
    const version = process.argv[1];
    if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error("Invalid native version");
    fs.writeFileSync("package.json", JSON.stringify({
      name: "native-verification", version: "1.0.0", private: true, dependencies: {[name]: version}
    }));
    fs.mkdirSync("node_modules/" + name, {recursive: true});
    fs.writeFileSync("node_modules/" + name + "/package.json", JSON.stringify({name, version}));
  ' "$VERSION"
  npm audit signatures --json --include-attestations > audit.json
  npm pack --ignore-scripts --json "@oh-my-pi/pi-natives-win32-x64@$VERSION" > npm-pack.json
)
TARBALL="$(node -e 'const fs=require("fs");const p=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(p[0].filename)' "$TMP/npm-pack.json")"
[[ "$TARBALL" =~ ^[A-Za-z0-9._-]+\.tgz$ ]] || fail "npm pack returned an unsafe tarball name"
python3 "$VERIFY_SCRIPT" "$TMP/$TARBALL" "$TMP/audit.json" "$VERSION" "$SOURCE_SHA" "$TMP/verified"
cp "$TMP/audit.json" "$ARTIFACTS/A0-npm-provenance.json"
A0_FILE="$TMP/verified/pi_natives.win32-x64-baseline.node"
test -s "$A0_FILE" || fail "official npm package did not contain the expected baseline addon"
A0_VERIFIED_SHA256="$(sha256sum "$A0_FILE" | cut -d' ' -f1)"
export A0_VERIFIED_SHA256
cp "$A0_FILE" "$ARTIFACTS/A0-official-baseline/"
cp "$TMP/verified/THIRD-PARTY-NOTICES.txt" "$ARTIFACTS/A0-official-baseline/"
if ! grep -aFq "PI_NATIVES_VERSION_STAMP:$VERSION" "$A0_FILE" && ! grep -aFq "__piNativesV19_2_1" "$A0_FILE"; then
  fail "official A0 addon has neither the v19.2.1 stamp nor the legacy release sentinel"
fi

build_native() {
  local target="$1"
  local dest="$2"
  mkdir -p "$dest"
  (
    cd "$SOURCE"
    if [[ "$TRACE" == true ]]; then
      local label variant trace_dir
      label="$(basename "$dest")"
      variant=modern
      [[ "$target" != *-baseline ]] || variant=baseline
      trace_dir="$TMP/trace-$label"
      mkdir "$trace_dir"
      # Reset Bazel's local action cache; the output must not be a cache hit.
      bazelisk --bazelrc="$OMP_BAZEL_RC" clean
      # Local spawn strategy is restricted to this disposable, read-only
      # experimental runner so ptrace observes actual build-script children.
      OMP_BAZEL_RC="$OMP_BAZEL_RC" strace -ff -qq -s 65536 \
        -e trace=execve,execveat,clone,clone3,fork,vfork -o "$trace_dir/exec" \
        bun scripts/bazel-natives.ts "$target" --dest "$dest" -- --jobs=2 --spawn_strategy=local --remote_accept_cached=false
      python3 "$ROOT/scripts/native-avx2/check-compiler-trace.py" --input-dir "$trace_dir" --variant "$variant" \
        --output "$ARTIFACTS/compiler-trace-$label.json"
      rm -rf "$trace_dir"
    else
      OMP_BAZEL_RC="$OMP_BAZEL_RC" bun scripts/bazel-natives.ts "$target" --dest "$dest" -- --jobs=2
    fi
  )
}

verify_stamp() {
  local file="$1"
  test -s "$file" || fail "missing addon: $file"
  grep -aFq "PI_NATIVES_VERSION_STAMP:$VERSION" "$file" || fail "addon is missing its $VERSION post-link stamp: $file"
}

check_wrapper() {
  local variant="$1"
  local output_base wrapper exec_body
  output_base="$(cd "$SOURCE" && bazelisk --bazelrc="$OMP_BAZEL_RC" info output_base)"
  wrapper="$output_base/external/+msvc_cc_repository+msvc_cc/bin/clang-cl"
  test -s "$wrapper" || fail "generated clang-cl wrapper not found: $wrapper"
  exec_body="$(sed -n '/^exec "\$llvm\/bin\/clang-cl"/,$p' "$wrapper")"
  [[ -n "$exec_body" ]] || fail "clang-cl wrapper lacks executable invocation"
  if [[ "$variant" == baseline ]]; then
    grep -Eq '^[[:space:]]*-msse4\.1[[:space:]]*\\[[:space:]]*$' <<< "$exec_body" || fail "baseline wrapper lacks SSE4.1"
    grep -Eq '^[[:space:]]*-msse4\.2[[:space:]]*\\[[:space:]]*$' <<< "$exec_body" || fail "baseline wrapper lacks SSE4.2"
    if grep -Eq -- '(-march=x86-64-v[34]|/arch:AVX)' <<< "$exec_body"; then fail "baseline wrapper contains higher ISA"; fi
  else
    grep -Eq '^[[:space:]]*/clang:-march=x86-64-v3[[:space:]]*\\[[:space:]]*$' <<< "$exec_body" || fail "modern wrapper lacks v3"
  fi
  cp "$wrapper" "$ARTIFACTS/clang-cl-$variant.sh"
}

echo "=== A1: self-built upstream baseline ==="
(
  cd "$SOURCE"
  bazelisk --bazelrc="$OMP_BAZEL_RC" aquery --output=jsonproto --include_commandline --include_param_files \
    'mnemonic("Rustc|CppCompile", deps(//:natives-win32-x64-baseline))'
) > "$ARTIFACTS/aquery-A1-baseline.json"
python3 "$ROOT/scripts/native-avx2/check-aquery.py" --input "$ARTIFACTS/aquery-A1-baseline.json" --variant baseline --codegen-units 16
check_wrapper baseline
build_native "win32-x64-baseline" "$ARTIFACTS/A1-self-built-baseline"
A1_FILE="$ARTIFACTS/A1-self-built-baseline/pi_natives.win32-x64-baseline.node"
verify_stamp "$A1_FILE"

echo "=== Apply modern v3 overlay to the disposable upstream checkout ==="
python3 "$ROOT/scripts/native-avx2/apply-overlay.py" \
  --source "$SOURCE" --mode modern --evidence "$ARTIFACTS/overlay-modern.patch"

run_isa_gate() {
  local label="$1"
  local cgu="$2"
  local query_out="$ARTIFACTS/aquery-$label.json"
  (
    cd "$SOURCE"
    bazelisk --bazelrc="$OMP_BAZEL_RC" aquery \
      --output=jsonproto --include_commandline --include_param_files \
      'mnemonic("Rustc|CppCompile", deps(//:natives-win32-x64-modern))'
  ) > "$query_out"
  python3 "$ROOT/scripts/native-avx2/check-aquery.py" --input "$query_out" --variant modern --codegen-units "$cgu"

  check_wrapper modern
  cp "$ARTIFACTS/clang-cl-modern.sh" "$ARTIFACTS/clang-cl-$label.sh"
}

echo "=== B: full target-graph v3, ThinLTO, CGU=16 ==="
run_isa_gate "B-v3-thin-cgu16" 16
build_native "win32-x64-modern" "$ARTIFACTS/B-v3-thin-cgu16"
B_FILE="$ARTIFACTS/B-v3-thin-cgu16/pi_natives.win32-x64-modern.node"
verify_stamp "$B_FILE"

echo "=== Apply CGU=1 overlay ==="
python3 "$ROOT/scripts/native-avx2/apply-overlay.py" \
  --source "$SOURCE" --mode cgu1 --evidence "$ARTIFACTS/overlay-cgu1.patch"

echo "=== C: full target-graph v3, ThinLTO, CGU=1 ==="
run_isa_gate "C-v3-thin-cgu1" 1
build_native "win32-x64-modern" "$ARTIFACTS/C-v3-thin-cgu1"
C_FILE="$ARTIFACTS/C-v3-thin-cgu1/pi_natives.win32-x64-modern.node"
verify_stamp "$C_FILE"

test "$(sha256sum "$ARTIFACTS/A0-official-baseline/pi_natives.win32-x64-baseline.node" | cut -d' ' -f1)" = "$A0_VERIFIED_SHA256" || fail "A0 changed after verification"
export OMP_NATIVE_TRACE="$TRACE"
echo "=== Verify isolated baseline and modern embedding ==="
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/A0-official-baseline" baseline
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/A1-self-built-baseline" baseline
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/B-v3-thin-cgu16" modern
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/C-v3-thin-cgu1" modern

(
  cd "$ARTIFACTS"
  find A0-official-baseline A1-self-built-baseline B-v3-thin-cgu16 C-v3-thin-cgu1 \
    -type f -name '*.node' -print0 | sort -z | xargs -0 sha256sum
) > "$ARTIFACTS/SHA256SUMS.txt"

python3 - "$SOURCE" "$ARTIFACTS" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

source = Path(sys.argv[1])
artifacts = Path(sys.argv[2])
groups = {
    "A0": ("A0-official-baseline/pi_natives.win32-x64-baseline.node",
           "official npm @oh-my-pi/pi-natives-win32-x64@19.2.1",
           "upstream Windows baseline artifact"),
    "A1": ("A1-self-built-baseline/pi_natives.win32-x64-baseline.node",
           "self-built with the unmodified upstream Bazel recipe",
           "matches upstream baseline configuration: root pi_natives crate selects x86-64-v2; the upstream transition does not globally add v2 to dependency crates"),
    "B": ("B-v3-thin-cgu16/pi_natives.win32-x64-modern.node",
          "self-built with the experiment overlay",
          "x86-64-v3 on every non-exec Windows-target Rustc action; clang-cl C/C++ wrapper uses -march=x86-64-v3; ThinLTO; CGU=16"),
    "C": ("C-v3-thin-cgu1/pi_natives.win32-x64-modern.node",
          "self-built with the experiment overlay",
          "x86-64-v3 on every non-exec Windows-target Rustc action; clang-cl C/C++ wrapper uses -march=x86-64-v3; ThinLTO; CGU=1"),
}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def command_version(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()
    except Exception as exc:
        return "unavailable: " + str(exc)

result = {
    "schema": 1,
    "source": {
        "repository": "can1357/oh-my-pi",
        "ref": "v19.2.1",
        "commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "version": "19.2.1",
    },
    "experiment": {
        "repository": "WhiCCX0/oh-my-pi",
        "branch": "experiment/native-avx2",
        "workflow_commit": os.environ.get("GITHUB_SHA", "local"),
        "target": "win32-x64",
        "bun": command_version(["bun", "--version"]),
        "bazelisk": command_version(["bazelisk", "--version"]),
        "a0_verified_sha256": os.environ["A0_VERIFIED_SHA256"],
        "trace_enabled": os.environ["OMP_NATIVE_TRACE"] == "true",
        "trace_reports": {p.name: sha256(p) for p in sorted(artifacts.glob("compiler-trace-*.json"))},
        "groups": {},
    },
}
for name, (relative, build, configuration) in groups.items():
    file = artifacts / relative
    result["experiment"]["groups"][name] = {
        "file": relative,
        "build": build,
        "configuration": configuration,
        "size_bytes": file.stat().st_size,
        "sha256": sha256(file),
    }

if result["experiment"]["trace_enabled"] and len(result["experiment"]["trace_reports"]) != 3:
    raise ValueError("Missing traced compiler evidence")
(artifacts / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
PY

echo "=== Experiment artifacts ready ==="
find "$ARTIFACTS" -maxdepth 2 -type f -printf '%P  %s bytes\n' | sort
