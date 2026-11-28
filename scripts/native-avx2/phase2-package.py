#!/usr/bin/env python3
"""Verify the exact H0 package and its same-run Windows smoke receipt."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

UPSTREAM = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"
LOCALIZATION = "ac1616560eb7d04d2a58afe97dcec88f310a1ad8"
CONFIG = {"cpu":"baseline","target":"win32-x64-baseline","lto":"fat","cgu":16,"llvm":"20.1.7"}
NAMES = ["H0.exe", "H0-native-manifest.json", "H0-LICENSE", "H0-LICENSE-dictionary", "H0.sha256"]
REQUIRED_EVIDENCE = {"aquery.json", "compiler-trace.json", "h0-overlay.patch", "clang-version.txt",
                     "lld-version.txt", "rustc-version.txt", "embed.json", "pe-imports.txt"}


def load_base():
    path = Path(__file__).with_name("collect-verified.py")
    spec = importlib.util.spec_from_file_location("phase2_collect_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load pinned artifact verification helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()


def verify(directory: Path, commit: str) -> dict:
    manifest_path = directory / "H0-native-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exp = manifest["experiment"]
    if (manifest["source"]["commit"] != UPSTREAM or exp["workflow_commit"] != commit
            or exp["localization_commit"] != LOCALIZATION or exp["configuration"] != CONFIG
            or exp["trace_enabled"] is not True or set(exp["groups"]) != {"H0"}):
        raise ValueError("H0 manifest identity/configuration mismatch")
    if not REQUIRED_EVIDENCE <= set(exp["evidence"]):
        raise ValueError("H0 manifest lacks required build evidence")
    if any(not base.HEX.fullmatch(value) for value in exp["evidence"].values()):
        raise ValueError("H0 manifest contains an invalid evidence digest")
    if not base.HEX.fullmatch(exp["groups"]["H0"]["sha256"]):
        raise ValueError("Invalid H0 native addon digest")
    rustc = exp["toolchain"]["rustc"]
    if ("rustc 1.102.0-nightly" not in rustc or
            "commit-hash: f5cedb83a788ea9581097abc36a2a8503a7f541c" not in rustc or
            "LLVM version: 23.1.5" not in rustc):
        raise ValueError("H0 Rust toolchain differs from the pinned Phase 1 Rustc")
    if ("clang version 20.1.7" not in exp["toolchain"]["clang"] or
            "LLD 20.1.7" not in exp["toolchain"]["lld"]):
        raise ValueError("H0 external LLVM toolchain mismatch")
    checksum = (directory / "H0.sha256").read_text(encoding="utf-8").strip()
    if checksum != base.sha(directory / "H0.exe") + "  H0.exe":
        raise ValueError("H0 EXE checksum mismatch")
    return manifest


def prepare(repo: str, run: str, commit: str) -> None:
    items = base.artifacts(repo, run)
    package = base.exact(items, "phase2-candidate-H0")
    target = Path("package-input")
    target.mkdir(exist_ok=False)
    base.download(repo, run, package, target, NAMES)
    verify(target, commit)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"id={package['id']}\ndigest={package['digest']}\n")
    print(f"Verified exact H0 package artifact {package['id']} ({package['digest']})")


def collect(repo: str, run: str, commit: str) -> None:
    items = base.artifacts(repo, run)
    package = base.exact(items, "phase2-candidate-H0")
    with tempfile.TemporaryDirectory() as temp:
        receipt_dir = Path(temp) / "receipt"
        receipt_artifact = base.exact(items, "phase2-smoke-receipt-H0")
        base.download(repo, run, receipt_artifact, receipt_dir, ["smoke-receipt.json"])
        receipt = json.loads((receipt_dir / "smoke-receipt.json").read_text(encoding="utf-8-sig"))
        if (receipt.get("group") != "H0" or receipt.get("runId") != run
                or receipt.get("sourceSha") != commit or receipt.get("artifactId") != str(package["id"])
                or receipt.get("artifactDigest") != package["digest"]
                or receipt.get("status") != "windows-smoke-passed" or set(receipt.get("files", {})) != set(NAMES)):
            raise ValueError("H0 smoke receipt identity/status mismatch")
        dist = Path("dist")
        dist.mkdir(exist_ok=False)
        base.download(repo, run, package, dist, NAMES)
        manifest = verify(dist, commit)
        if any(base.sha(dist / name) != receipt["files"][name] for name in NAMES):
            raise ValueError("H0 package files do not match the Windows smoke receipt")
        if receipt["loadedAddonSha256"] != manifest["experiment"]["groups"]["H0"]["sha256"]:
            raise ValueError("Windows smoke loaded a different H0 addon")
        (dist / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        (dist / "phase2-status.json").write_text(json.dumps({
            "runId": run, "workflowCommit": commit, "sourceCommit": UPSTREAM,
            "group": "H0", "configuration": CONFIG, "smokeStatus": receipt["status"],
            "artifactId": package["id"], "artifactDigest": package["digest"],
            "nativeSha256": manifest["experiment"]["groups"]["H0"]["sha256"],
            "exeSha256": base.sha(dist / "H0.exe")
        }, indent=2) + "\n", encoding="utf-8")
        (dist / "SHA256SUMS.txt").write_text(
            "".join(base.sha(dist / name) + "  " + name + "\n" for name in NAMES), encoding="utf-8")
    print("H0 build, package, and Windows smoke receipt independently verified")


if __name__ == "__main__":
    mode = sys.argv[1]
    repo, run, commit = (os.environ[key] for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_SHA"))
    if mode == "prepare":
        prepare(repo, run, commit)
    elif mode == "collect":
        collect(repo, run, commit)
    elif mode == "verify-local":
        verify(Path(sys.argv[2]), commit)
    else:
        raise ValueError("Usage: phase2-package.py prepare|collect|verify-local <dir>")
