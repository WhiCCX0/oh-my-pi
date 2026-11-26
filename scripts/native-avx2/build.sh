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
SOURCE_VERSION="$(node -p "require('$SOURCE/packages/natives/package.json').version")"
[[ "$SOURCE_VERSION" == "$VERSION" ]] || fail "wrong natives package version: $SOURCE_VERSION"

rm -rf "$ARTIFACTS"
mkdir -p "$ARTIFACTS"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== A0: official npm baseline ==="
mkdir -p "$ARTIFACTS/A0-official-baseline"
npm pack --ignore-scripts --json --pack-destination "$TMP" "@oh-my-pi/pi-natives-win32-x64@$VERSION" > "$TMP/npm-pack.json"
TARBALL="$(node -e 'const fs=require("fs");const p=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(p[0].filename)' "$TMP/npm-pack.json")"
test -n "$TARBALL" || fail "npm pack returned no tarball"
INTEGRITY="$(node -p 'JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"))[0].integrity' "$TMP/npm-pack.json")"
[[ "$INTEGRITY" == "sha512-$(openssl dgst -sha512 -binary "$TMP/$TARBALL" | openssl base64 -A)" ]] || fail "A0 npm tarball integrity mismatch"
tar -xzf "$TMP/$TARBALL" -C "$TMP"
node - "$TMP/package/package.json" <<'NODE'
const p = require(process.argv[2]);
if (p.name !== "@oh-my-pi/pi-natives-win32-x64" || p.version !== "19.2.1") {
  throw new Error("Unexpected npm package identity: " + p.name + "@" + p.version);
}
NODE
A0_FILE="$TMP/package/pi_natives.win32-x64-baseline.node"
test -s "$A0_FILE" || fail "official npm package did not contain the expected baseline addon"
cp "$A0_FILE" "$ARTIFACTS/A0-official-baseline/"
if [[ -f "$TMP/package/THIRD-PARTY-NOTICES.txt" ]]; then
  cp "$TMP/package/THIRD-PARTY-NOTICES.txt" "$ARTIFACTS/A0-official-baseline/"
fi
if ! grep -aFq "PI_NATIVES_VERSION_STAMP:$VERSION" "$A0_FILE" && ! grep -aFq "__piNativesV19_2_1" "$A0_FILE"; then
  fail "official A0 addon has neither the v19.2.1 stamp nor the legacy release sentinel"
fi

build_native() {
  local target="$1"
  local dest="$2"
  mkdir -p "$dest"
  (
    cd "$SOURCE"
    OMP_BAZEL_RC="$OMP_BAZEL_RC" bun scripts/bazel-natives.ts "$target" --dest "$dest" -- --jobs=2
  )
}

verify_stamp() {
  local file="$1"
  test -s "$file" || fail "missing addon: $file"
  grep -aFq "PI_NATIVES_VERSION_STAMP:$VERSION" "$file" || fail "addon is missing its $VERSION post-link stamp: $file"
}

echo "=== A1: self-built upstream baseline ==="
build_native "win32-x64-baseline" "$ARTIFACTS/A1-self-built-baseline"
A1_FILE="$ARTIFACTS/A1-self-built-baseline/pi_natives.win32-x64-baseline.node"
verify_stamp "$A1_FILE"

echo "=== Apply modern v3 overlay to the disposable upstream checkout ==="
python3 "$ROOT/scripts/native-avx2/apply-overlay.py" \
  --source "$SOURCE" --mode modern --evidence "$ARTIFACTS/overlay-modern.patch"

run_isa_gate() {
  local label="$1"
  local cgu="$2"
  local query_out="$ARTIFACTS/aquery-$label.txt"
  (
    cd "$SOURCE"
    bazelisk --bazelrc="$OMP_BAZEL_RC" aquery \
      --output=text --include_commandline --include_param_files \
      'mnemonic("Rustc|CppCompile", deps(//:natives-win32-x64-modern))'
  ) > "$query_out"
  python3 "$ROOT/scripts/native-avx2/check-aquery.py" --input "$query_out" --codegen-units "$cgu"

  local output_base wrapper
  output_base="$(cd "$SOURCE" && bazelisk --bazelrc="$OMP_BAZEL_RC" info output_base)"
  wrapper="$output_base/external/+msvc_cc_repository+msvc_cc/bin/clang-cl"
  test -s "$wrapper" || fail "generated clang-cl wrapper not found: $wrapper"
  grep -Fq -- "-march=x86-64-v3" "$wrapper" || fail "clang-cl wrapper lacks -march=x86-64-v3"
  cp "$wrapper" "$ARTIFACTS/clang-cl-$label.sh"
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

echo "=== Verify modern-only embedding ==="
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/B-v3-thin-cgu16"
OMP_SOURCE_DIR="$SOURCE" bun "$ROOT/scripts/native-avx2/verify-embed.ts" "$ARTIFACTS/C-v3-thin-cgu1"

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

(artifacts / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
PY

echo "=== Experiment artifacts ready ==="
find "$ARTIFACTS" -maxdepth 2 -type f -printf '%P  %s bytes\n' | sort
