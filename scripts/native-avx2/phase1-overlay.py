#!/usr/bin/env python3
"""Phase 1 independent arms; only disposable pinned Linux-x64 source."""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

UPSTREAM = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"
LOCALIZATION = "ac1616560eb7d04d2a58afe97dcec88f310a1ad8"
LLVM23_SHA = "bd4d1c434ff69ef06d19e7934c04708da677e5f2eb51f5bcf052ab91818f0094"
GROUPS = {
    f"{kind}{cgu}": {"cpu": "raptorlake" if kind == "D" else "x86-64-v3",
                     "llvm": "23.1.6" if kind == "E" else "20.1.7",
                     "lto": "fat" if kind == "H" else "thin",
                     "cgu": cgu, "reference": "B" if cgu == 16 else "C",
                     "local_only": kind == "D"}
    for kind in ("D", "E", "H") for cgu in (16, 1)
}


def replace(s, before, after, count=1):
    if s.count(before) != count:
        raise ValueError("Unexpected overlay anchor: " + before)
    return s.replace(before, after)


def apply(source, group, audit):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise ValueError("This overlay supports only the Linux x64 CI host")
    if subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() != UPSTREAM:
        raise ValueError("Wrong source commit")
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"], text=True).strip() or (source/".bazelrc.user").exists() or (source/".bazelrc.user").is_symlink():
        raise ValueError("Source not clean")
    audit.mkdir(parents=True, exist_ok=False)
    config = GROUPS[group]
    for mode in ["modern"] + (["cgu1"] if config["cgu"] == 1 else []):
        subprocess.run([sys.executable, str(Path(__file__).with_name("apply-overlay.py")), "--source", str(source), "--mode", mode, "--evidence", str(audit/(mode+".patch"))], check=True)
    def edit(file, before, after, count=1):
        p = source / file
        p.write_text(replace(p.read_text(), before, after, count), encoding="utf-8")
    if config["cpu"] == "raptorlake":
        edit("bazel/defs.bzl", "-Ctarget-cpu=x86-64-v3", "-Ctarget-cpu=raptorlake")
        edit("crates/natives/BUILD.bazel", "-Ctarget-cpu=x86-64-v3", "-Ctarget-cpu=raptorlake", 2)
        edit("bazel/toolchains/msvc/cc.bzl", '"/clang:-march=x86-64-v3"', '"/clang:-march=raptorlake"')
    if config["lto"] == "fat":
        edit("bazel/defs.bzl", '"@rules_rust//rust/settings:lto": "thin"', '"@rules_rust//rust/settings:lto": "fat"')
    if config["llvm"] == "23.1.6":
        file = source/"bazel/toolchains/llvm.bzl"
        s = file.read_text()
        start = s.index("llvm_msvc_tools_repository = _llvm_tools_repository(")
        end = s.index("llvm_darwin_tools_repository = _llvm_tools_repository(", start)
        part = s[start:end]
        before = part[part.index('    version = "20.1.7",'):part.index("    bins = [")]
        after = '    version = "23.1.6",\n    dists = {\n        "linux-x64": ["Linux-X64", "' + LLVM23_SHA + '"],\n    },\n'
        file.write_text(s[:start]+replace(part,before,after)+s[end:], encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "diff", "--check"], check=True)
    (audit/"full-overlay.patch").write_bytes(subprocess.check_output(["git", "-C", str(source), "diff", "--binary"]))
    (audit/"configuration.json").write_text(json.dumps(config, indent=2)+"\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--group", choices=GROUPS, required=True)
    p.add_argument("--audit", type=Path, required=True)
    a = p.parse_args()
    apply(a.source.resolve(), a.group, a.audit.resolve())
